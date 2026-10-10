"""Bounded, owner-scoped durable jobs for explicitly selected seller insights.

No source fetch or automatic corpus inference. Interrupted paid calls are never
blindly replayed after restart; their receipt is marked completion_unknown.
"""

import asyncio
import json
import re
import secrets

from fastapi import HTTPException

from backend.persona import utcnow
from backend.provider_errors import ProviderUnavailable

TERMINAL = {"completed", "completed_with_errors", "cancelled"}


class InsightCohortService:
    def __init__(self, config, store, insights):
        self.config, self.store, self.insights = config, store, insights
        self.wake = asyncio.Event()
        self.task = None
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS insight_cohort_jobs(
                    id TEXT PRIMARY KEY,owner TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS insight_cohort_owner ON insight_cohort_jobs(owner);
            """)

    @staticmethod
    def public(job):
        return {
            key: value
            for key, value in job.items()
            if key not in {"owner", "active_token"}
        }

    def get(self, identifier, owner):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM insight_cohort_jobs WHERE id=? AND owner=?",
                (identifier, owner),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Insight job not found in this browser.")
        return json.loads(row[0])

    def list(self, owner):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM insight_cohort_jobs WHERE owner=? ORDER BY rowid DESC LIMIT 30",
                (owner,),
            ).fetchall()
        return [self.public(json.loads(row[0])) for row in rows]

    def create(self, owner, seller_ids, use_llm=False):
        if not self.config.values.get("insights", {}).get("enabled", True):
            raise HTTPException(409, "Insight extraction is disabled.")
        maximum = min(
            50,
            int(
                self.config.values.get("insights", {}).get(
                    "max_calls_per_cohort", 10
                )
            ),
        )
        if (
            not isinstance(seller_ids, list)
            or not 1 <= len(seller_ids) <= maximum
        ):
            raise HTTPException(
                422, f"Select between 1 and {maximum} seller identifiers."
            )
        if any(
            not isinstance(x, str) or not re.fullmatch(r"real-\d+", x)
            for x in seller_ids
        ):
            raise HTTPException(
                422,
                "Only existing private seller identifiers may enter a cohort.",
            )
        identifiers = list(dict.fromkeys(seller_ids))
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT COUNT(*) FROM insight_cohort_jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if active >= min(
                20,
                int(
                    self.config.values.get("insights", {}).get(
                        "max_cohort_jobs", 5
                    )
                ),
            ):
                raise HTTPException(429, "Insight cohort queue is full.")
            for seller_id in identifiers:
                row = db.execute(
                    "SELECT payload FROM sellers WHERE id=?", (seller_id,)
                ).fetchone()
                if (
                    not row
                    or json.loads(row[0]).get("source_mode") != "private"
                ):
                    raise HTTPException(
                        422,
                        "Every selected seller must be an existing private record.",
                    )
            job = {
                "id": secrets.token_hex(16),
                "owner": owner,
                "status": "queued",
                "seller_ids": identifiers,
                "use_llm": bool(use_llm),
                "created_at": utcnow(),
                "updated_at": utcnow(),
                "cursor": 0,
                "results": [],
                "active_token": None,
                "active_seller": None,
                "max_new_model_requests": len(identifiers) if use_llm else 0,
                "completed_sellers": 0,
                "failed_sellers": 0,
                "new_model_results": 0,
                "worker": "running"
                if self.task and not self.task.done()
                else "not_running",
                "limitations": "Explicit selected sellers only. Historical machine inferences require review; no upstream source fetches.",
            }
            self._write(db, job)
        self.wake.set()
        return self.public(job)

    @staticmethod
    def _write(db, job):
        job["updated_at"] = utcnow()
        db.execute(
            "INSERT OR REPLACE INTO insight_cohort_jobs VALUES(?,?,?,?)",
            (
                job["id"],
                job["owner"],
                job["status"],
                json.dumps(job, ensure_ascii=False),
            ),
        )

    def cancel(self, identifier, owner):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM insight_cohort_jobs WHERE id=? AND owner=?",
                (identifier, owner),
            ).fetchone()
            if not row:
                raise HTTPException(
                    404, "Insight job not found in this browser."
                )
            job = json.loads(row[0])
            if job["status"] not in TERMINAL:
                job.update(
                    status="cancelled",
                    active_token=None,
                    finished_at=utcnow(),
                    note="Pending work stopped. In-flight provider billing cannot be undone; delayed output cannot commit.",
                )
                self._write(db, job)
        return self.public(job)

    def recover(self):
        """Restart cannot turn an uncertain paid call into automatic duplicate spend."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT payload FROM insight_cohort_jobs WHERE status='running'"
            ).fetchall()
            for row in rows:
                job = json.loads(row[0])
                if job.get("active_seller"):
                    job["results"].append(
                        {
                            "seller_id": job["active_seller"],
                            "status": "completion_unknown",
                            "message": "Worker restarted during this seller. Inspect its indexed insights; explicit resubmission is required for another model attempt.",
                        }
                    )
                    job["failed_sellers"] += 1
                    job["cursor"] += 1
                job.update(active_token=None, active_seller=None)
                self._finish_or_queue(job)
                self._write(db, job)

    @staticmethod
    def _finish_or_queue(job):
        if job["cursor"] >= len(job["seller_ids"]):
            job["status"] = (
                "completed_with_errors"
                if job["failed_sellers"]
                else "completed"
            )
            job["finished_at"] = utcnow()
        else:
            job["status"] = "queued"

    async def start(self):
        self.recover()
        if self.config.values["jobs"]["enabled"] and not (
            self.task and not self.task.done()
        ):
            self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def run(self):
        while True:
            with self.store.connect() as db:
                queued = db.execute(
                    "SELECT id,owner FROM insight_cohort_jobs WHERE status='queued' ORDER BY rowid LIMIT 10"
                ).fetchall()
            for row in queued:
                await self.step(row["id"], row["owner"])
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), 1.0)
            except TimeoutError:
                pass

    async def step(self, identifier, owner):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM insight_cohort_jobs WHERE id=? AND owner=?",
                (identifier, owner),
            ).fetchone()
            if not row:
                return
            job = json.loads(row[0])
            if job["status"] != "queued":
                return
            selected = job["seller_ids"][job["cursor"]]
            token = secrets.token_hex(16)
            job.update(
                status="running",
                active_seller=selected,
                active_token=token,
                started_at=job.get("started_at") or utcnow(),
            )
            self._write(db, job)

        def guard(connection=None):
            if connection is None:
                current = self.get(identifier, owner)
            else:
                current_row = connection.execute(
                    "SELECT payload FROM insight_cohort_jobs WHERE id=? AND owner=?",
                    (identifier, owner),
                ).fetchone()
                current = json.loads(current_row[0]) if current_row else {}
            return (
                current.get("status") == "running"
                and current.get("active_token") == token
            )

        try:
            result = await self.insights.extract(
                selected, use_llm=job["use_llm"], commit_guard=guard
            )
            receipt = {
                "seller_id": selected,
                "status": "completed",
                "method": result["method"],
                "cached": result.get("cached", False),
                "insight_count": len(result.get("items", [])),
                "evidence_count": result.get("evidence_count", 0),
                "extraction_status": result.get("status"),
            }
        except ProviderUnavailable as error:
            receipt = {
                "seller_id": selected,
                "status": "failed",
                "message": str(error),
            }
        except (ValueError, TypeError, KeyError):
            receipt = {
                "seller_id": selected,
                "status": "failed",
                "message": "Insight input or output failed validation; no fabricated result was substituted.",
            }
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not guard(db):
                return
            current = json.loads(
                db.execute(
                    "SELECT payload FROM insight_cohort_jobs WHERE id=?",
                    (identifier,),
                ).fetchone()[0]
            )
            current["results"].append(receipt)
            current["cursor"] += 1
            current[
                "completed_sellers"
                if receipt["status"] == "completed"
                else "failed_sellers"
            ] += 1
            current["new_model_results"] += int(
                receipt.get("method") == "llm_summary_inference"
                and not receipt.get("cached")
            )
            current.update(active_seller=None, active_token=None)
            self._finish_or_queue(current)
            self._write(db, current)

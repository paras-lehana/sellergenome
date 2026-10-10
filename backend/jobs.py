"""Durable source jobs, schedules and transactional indexing receipts.

Remote calls never run inside these transactions. Leased tokens fence late
workers and cancellations; delivery remains at least once after a crash.
"""

import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone

from backend.store import Store
from backend.connectors import ConnectorError

ACTIVE = ("queued", "running", "retry_wait")


class JobConflict(ValueError):
    """A safe, client-visible conflict with persisted work."""


def timestamp(value: float | None) -> str | None:
    """Serialize internal epoch seconds without leaking lease tokens."""
    return (
        datetime.fromtimestamp(value, timezone.utc).isoformat()
        if value
        else None
    )


def observation_time(value: str | None) -> float | None:
    """Unknown, malformed or naive source time is never treated as fresh."""
    try:
        parsed = datetime.fromisoformat(value or "")
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


class JobRepository:
    """Serialize queue decisions and evidence revisions in local WAL SQLite."""

    def __init__(
        self, store: Store, settings: dict, policy_check=None
    ) -> None:
        self.store, self.settings = store, settings
        self.policy_check = policy_check
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sync_jobs(
                    id TEXT PRIMARY KEY,source TEXT NOT NULL,seller_id TEXT NOT NULL,
                    state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,available_at REAL NOT NULL,
                    created_at REAL NOT NULL,updated_at REAL NOT NULL,
                    lease_until REAL NOT NULL DEFAULT 0,lease_token TEXT,
                    schedule_id TEXT,parent_id TEXT,result TEXT,error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_due ON sync_jobs(state,available_at,lease_until);
                CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_pair
                    ON sync_jobs(source,seller_id)
                    WHERE state IN ('queued','running','retry_wait');
                CREATE TABLE IF NOT EXISTS sync_requests(
                    key TEXT PRIMARY KEY,source TEXT NOT NULL,
                    seller_id TEXT NOT NULL,job_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sync_schedules(
                    id TEXT PRIMARY KEY,source TEXT NOT NULL,seller_id TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,enabled INTEGER NOT NULL,
                    next_run REAL NOT NULL,created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,last_job_id TEXT,
                    UNIQUE(source,seller_id)
                );
            """)
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(sync_jobs)")
            }
            if "remote_progress" not in columns:
                db.execute(
                    "ALTER TABLE sync_jobs ADD COLUMN remote_progress TEXT"
                )

    @staticmethod
    def project_job(row: sqlite3.Row) -> dict:
        """Expose safe task metadata, never raw observations or internal tokens."""
        return {
            key: timestamp(row[key])
            if key.endswith("_at")
            else json.loads(row[key])
            if key == "result" and row[key]
            else row[key]
            for key in row.keys()
            if key not in {"lease_token", "lease_until", "remote_progress"}
        }

    def get(self, identifier: str) -> dict | None:
        """Read a durable receipt by opaque job identifier."""
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM sync_jobs WHERE id=?", (identifier,)
            ).fetchone()
        return self.project_job(row) if row else None

    def list_jobs(self, limit: int = 30) -> list[dict]:
        """Bound operator history instead of loading the whole queue."""
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM sync_jobs ORDER BY created_at DESC,id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self.project_job(row) for row in rows]

    def enqueue(
        self,
        source: str,
        seller_id: str,
        request_key: str | None = None,
        *,
        schedule_id: str | None = None,
        parent_id: str | None = None,
    ) -> tuple[dict, bool]:
        """Deduplicate request keys and active pairs under one write lock."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            identifier, created = self._enqueue(
                db,
                source,
                seller_id,
                time.time(),
                request_key,
                schedule_id,
                parent_id,
            )
            row = db.execute(
                "SELECT * FROM sync_jobs WHERE id=?", (identifier,)
            ).fetchone()
        return self.project_job(row), created

    def _enqueue(
        self,
        db: sqlite3.Connection,
        source: str,
        seller_id: str,
        now: float,
        request_key: str | None = None,
        schedule_id: str | None = None,
        parent_id: str | None = None,
    ) -> tuple[str, bool]:
        """Share the exact queue transaction with the schedule dispatcher."""
        if self.policy_check:
            self.policy_check(source, seller_id)
        if request_key:
            previous = db.execute(
                "SELECT * FROM sync_requests WHERE key=?", (request_key,)
            ).fetchone()
            if previous:
                if (previous["source"], previous["seller_id"]) != (
                    source,
                    seller_id,
                ):
                    raise JobConflict(
                        "Request key already belongs to another source or seller."
                    )
                return previous["job_id"], False
        active = db.execute(
            "SELECT id FROM sync_jobs WHERE source=? AND seller_id=? AND state IN ('queued','running','retry_wait')",
            (source, seller_id),
        ).fetchone()
        created = active is None
        if active:
            identifier = active["id"]
        else:
            pending = db.execute(
                "SELECT count(*) FROM sync_jobs WHERE state IN ('queued','running','retry_wait')"
            ).fetchone()[0]
            if pending >= self.settings["max_pending_jobs"]:
                raise JobConflict(
                    "Refresh queue is full; wait for current work to finish."
                )
            identifier = "job-" + secrets.token_hex(12)
            db.execute(
                """INSERT INTO sync_jobs(id,source,seller_id,state,max_attempts,
                   available_at,created_at,updated_at,schedule_id,parent_id)
                   VALUES (?,?,?,'queued',?,?,?,?,?,?)""",
                (
                    identifier,
                    source,
                    seller_id,
                    self.settings["max_attempts"],
                    now,
                    now,
                    now,
                    schedule_id,
                    parent_id,
                ),
            )
        if request_key:
            db.execute(
                "INSERT INTO sync_requests VALUES (?,?,?,?)",
                (request_key, source, seller_id, identifier),
            )
        return identifier, created

    def claim(self, now: float | None = None) -> dict | None:
        """Recover expired leases, then claim one due job with a fresh token."""
        now = time.time() if now is None else now
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE sync_jobs SET state='failed',error_code='lease_exhausted',
                   updated_at=?,lease_token=NULL,lease_until=0
                   WHERE state='running' AND lease_until<=? AND attempts>=max_attempts""",
                (now, now),
            )
            row = db.execute(
                """SELECT * FROM sync_jobs WHERE attempts<max_attempts AND
                   ((state IN ('queued','retry_wait') AND available_at<=?) OR
                    (state='running' AND lease_until<=?))
                   ORDER BY available_at,created_at LIMIT 1""",
                (now, now),
            ).fetchone()
            if not row:
                return None
            token = secrets.token_hex(16)
            db.execute(
                """UPDATE sync_jobs SET state='running',attempts=attempts+1,
                   updated_at=?,lease_until=?,lease_token=? WHERE id=?""",
                (now, now + self.settings["lease_seconds"], token, row["id"]),
            )
            return dict(
                db.execute(
                    "SELECT * FROM sync_jobs WHERE id=?", (row["id"],)
                ).fetchone()
            )

    def heartbeat(self, job: dict) -> bool:
        """Only the current uncancelled worker can renew its lease."""
        now = time.time()
        with self.store.connect() as db:
            return (
                db.execute(
                    """UPDATE sync_jobs SET lease_until=?,updated_at=?
                   WHERE id=? AND lease_token=? AND state='running' AND lease_until>?""",
                    (
                        now + self.settings["lease_seconds"],
                        now,
                        job["id"],
                        job["lease_token"],
                        now,
                    ),
                ).rowcount
                == 1
            )

    def checkpoint(self, job: dict, progress: dict) -> None:
        """Persist only safe Redash polling references under the active lease."""
        with self.store.connect() as db:
            changed = db.execute(
                "UPDATE sync_jobs SET remote_progress=? WHERE id=? AND state='running' AND lease_token=? AND lease_until>?",
                (
                    json.dumps(progress),
                    job["id"],
                    job["lease_token"],
                    time.time(),
                ),
            ).rowcount
            if changed != 1:
                raise JobConflict(
                    "Job was cancelled or its lease expired; polling stopped."
                )

    def fail(self, job: dict, code: str, retryable: bool) -> None:
        """Persist bounded retries with safe codes, never exception messages."""
        now = time.time()
        retry = retryable and job["attempts"] < job["max_attempts"]
        delay = min(
            self.settings["retry_max_seconds"],
            self.settings["retry_base_seconds"] * 2 ** (job["attempts"] - 1),
        )
        with self.store.connect() as db:
            db.execute(
                """UPDATE sync_jobs SET state=?,error_code=?,available_at=?,
                   updated_at=?,lease_token=NULL,lease_until=0
                   WHERE id=? AND lease_token=? AND state='running'""",
                (
                    "retry_wait" if retry else "failed",
                    code,
                    now + delay if retry else now,
                    now,
                    job["id"],
                    job["lease_token"],
                ),
            )

    def release(self, job: dict) -> None:
        """A graceful shutdown returns its claim without consuming a retry."""
        with self.store.connect() as db:
            db.execute(
                """UPDATE sync_jobs SET state='queued',attempts=max(0,attempts-1),
                   available_at=?,updated_at=?,lease_token=NULL,lease_until=0
                   WHERE id=? AND lease_token=? AND state='running'""",
                (time.time(), time.time(), job["id"], job["lease_token"]),
            )

    def cancel(self, identifier: str) -> dict:
        """Fence in-flight results; an already completed job cannot be cancelled."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM sync_jobs WHERE id=?", (identifier,)
            ).fetchone()
            if not row:
                raise KeyError(identifier)
            if row["state"] == "cancelled":
                return self.project_job(row)
            if row["state"] not in ACTIVE:
                raise JobConflict(
                    "Only queued, running or waiting jobs can be cancelled."
                )
            db.execute(
                "UPDATE sync_jobs SET state='cancelled',updated_at=?,lease_token=NULL,lease_until=0 WHERE id=?",
                (time.time(), identifier),
            )
        return self.get(identifier)

    def retry(
        self, identifier: str, request_key: str | None = None
    ) -> tuple[dict, bool]:
        """Create a new bounded job while preserving the failed/cancelled receipt."""
        old = self.get(identifier)
        if not old:
            raise KeyError(identifier)
        if old["state"] not in {"failed", "cancelled"}:
            raise JobConflict(
                "Retry is available for failed or cancelled jobs."
            )
        return self.enqueue(
            old["source"], old["seller_id"], request_key, parent_id=identifier
        )

    def apply_events(
        self,
        seller_id: str,
        events: list[dict],
        job: dict | None = None,
        receipt: dict | None = None,
    ) -> dict:
        """Commit revisions, suppression, invalidation, outbox and completion together."""
        now = time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                job
                and not db.execute(
                    "SELECT 1 FROM sync_jobs WHERE id=? AND state='running' AND lease_token=? AND lease_until>?",
                    (job["id"], job["lease_token"], now),
                ).fetchone()
            ):
                raise JobConflict(
                    "Job was cancelled or its lease expired; result discarded."
                )
            row = db.execute(
                "SELECT payload FROM sellers WHERE id=?", (seller_id,)
            ).fetchone()
            if not row:
                raise JobConflict("Seller no longer exists.")
            seller = json.loads(row[0])
            changed, skipped, suppressed = 0, 0, False
            for event in events:
                event = {**event, "source_mode": seller["source_mode"]}
                if event["seller_id"] != seller_id:
                    raise JobConflict("Observation belongs to another seller.")
                source_time = observation_time(event.get("observed_at"))
                revision_time = (
                    observation_time(event.get("source_updated_at"))
                    or source_time
                )
                if (source_time is not None and source_time > now + 300) or (
                    revision_time is not None and revision_time > now + 300
                ):
                    skipped += 1
                    continue
                prior = db.execute(
                    "SELECT payload FROM evidence WHERE id=?", (event["id"],)
                ).fetchone()
                if prior:
                    previous = json.loads(prior[0])
                    if previous["seller_id"] != seller_id:
                        raise JobConflict(
                            "Observation ID belongs to another seller."
                        )
                    ignore = {"ingested_at", "config_revision"}
                    if (
                        event.get("attributes", {}).get("snapshot_semantics")
                        == "read_time_aggregate"
                    ):
                        ignore |= {"observed_at", "source_updated_at"}
                    compare = lambda item: {
                        k: v for k, v in item.items() if k not in ignore
                    }
                    old_time = observation_time(
                        previous.get("source_updated_at")
                    ) or observation_time(previous.get("observed_at"))
                    # A documented mapping edit may reinterpret unchanged raw
                    # input without pretending the call itself became newer.
                    reinterpretation = (
                        event.get("kind")
                        == previous.get("kind")
                        == "redash_hotlead"
                        and event.get("source_content_hash") is not None
                        and event["source_content_hash"]
                        == previous.get("source_content_hash")
                        and event.get("interpretation_version")
                        != previous.get("interpretation_version")
                        and event.get("config_revision", 0)
                        > previous.get("config_revision", 0)
                    )
                    if compare(previous) == compare(event) or (
                        not reinterpretation
                        and (
                            revision_time is None
                            or (
                                old_time is not None
                                and revision_time <= old_time
                            )
                        )
                    ):
                        skipped += 1
                        continue
                Store.put_document(
                    db, "evidence", event["id"], event, seller_id=seller_id
                )
                changed += 1
                suppressed |= (
                    event.get("attributes", {}).get("contact_suppressed")
                    is True
                )
            if suppressed:
                seller.setdefault("metrics", {})["do_not_call"] = True
                seller["updated_at"] = timestamp(now)
                Store.put_document(db, "sellers", seller_id, seller)
            if changed:
                row = db.execute(
                    "SELECT payload FROM personas WHERE id=?", (seller_id,)
                ).fetchone()
                if row:
                    persona = json.loads(row[0])
                    persona.update(
                        needs_refresh=True, invalidated_at=timestamp(now)
                    )
                    Store.put_document(db, "personas", seller_id, persona)
            result = {
                **(receipt or {}),
                "status": "success",
                "count": len(events),
                "changed_records": changed,
                "skipped_records": skipped,
                "mapped_records": sum(
                    bool(set(e.get("attributes", {})) - {"observed_at"})
                    for e in events
                ),
                "indexing": "queued" if changed else "unchanged",
                "at": timestamp(now),
            }
            if job:
                db.execute(
                    """UPDATE sync_jobs SET state='succeeded',result=?,error_code=NULL,
                       updated_at=?,lease_token=NULL,lease_until=0 WHERE id=?""",
                    (json.dumps(result), now, job["id"]),
                )
                Store.put_document(
                    db, "metadata", "connector-" + job["source"], result
                )
            return result

    @staticmethod
    def project_schedule(row: sqlite3.Row) -> dict:
        """Use the same safe projection for disabled and active schedules."""
        value = dict(row)
        value["enabled"] = bool(value["enabled"])
        for key in ("next_run", "created_at", "updated_at"):
            value[key] = timestamp(value[key])
        return value

    def schedules(self) -> list[dict]:
        """Bound total schedules at creation; list that finite set."""
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM sync_schedules ORDER BY created_at DESC"
            ).fetchall()
        return [self.project_schedule(row) for row in rows]

    def pause_schedule(
        self, identifier: str, interval: int | None = None
    ) -> dict | None:
        """Pause existing work even when its source or identity is now disabled.

        This path cannot change a target, enable work or perform remote calls.
        Optional interval edits retain the same bounds used for new schedules.
        """
        if interval is not None and (
            type(interval) is not int
            or not self.settings["min_interval_seconds"]
            <= interval
            <= self.settings["max_interval_seconds"]
        ):
            raise ValueError(
                "Refresh interval is outside the configured bounds."
            )
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM sync_schedules WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                return None
            now = time.time()
            db.execute(
                """UPDATE sync_schedules SET enabled=0,interval_seconds=?,
                   next_run=?,updated_at=? WHERE id=?""",
                (
                    row["interval_seconds"] if interval is None else interval,
                    row["next_run"] if interval is None else now + interval,
                    now,
                    identifier,
                ),
            )
            row = db.execute(
                "SELECT * FROM sync_schedules WHERE id=?", (identifier,)
            ).fetchone()
        return self.project_schedule(row)

    def save_schedule(
        self, source: str, seller_id: str, interval: int, enabled: bool
    ) -> dict:
        """Create/update one source/seller schedule without enqueuing immediately."""
        if self.policy_check:
            self.policy_check(source, seller_id)
        now = time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT id FROM sync_schedules WHERE source=? AND seller_id=?",
                (source, seller_id),
            ).fetchone()
            if (
                not previous
                and db.execute(
                    "SELECT count(*) FROM sync_schedules"
                ).fetchone()[0]
                >= self.settings["max_schedules"]
            ):
                raise JobConflict(
                    "Schedule limit reached; edit an existing schedule."
                )
            identifier = (
                previous[0]
                if previous
                else "schedule-" + secrets.token_hex(12)
            )
            db.execute(
                """INSERT INTO sync_schedules(id,source,seller_id,interval_seconds,enabled,next_run,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source,seller_id) DO UPDATE SET
                   interval_seconds=excluded.interval_seconds,enabled=excluded.enabled,
                   next_run=excluded.next_run,updated_at=excluded.updated_at""",
                (
                    identifier,
                    source,
                    seller_id,
                    interval,
                    int(enabled),
                    now + interval,
                    now,
                    now,
                ),
            )
            row = db.execute(
                "SELECT * FROM sync_schedules WHERE id=?", (identifier,)
            ).fetchone()
        return self.project_schedule(row)

    def dispatch_schedules(self, now: float | None = None) -> int:
        """Coalesce missed intervals and deduplicate active work in one transaction."""
        now = time.time() if now is None else now
        dispatched = 0
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            due = db.execute(
                "SELECT * FROM sync_schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run LIMIT ?",
                (now, self.settings["schedule_batch_size"]),
            ).fetchall()
            for row in due:
                try:
                    identifier, created = self._enqueue(
                        db,
                        row["source"],
                        row["seller_id"],
                        now,
                        schedule_id=row["id"],
                    )
                except JobConflict:
                    break
                except (ValueError, ConnectorError):
                    # Invalidated policies pause old schedules instead of making
                    # the entire dispatcher unavailable for other sources.
                    db.execute(
                        "UPDATE sync_schedules SET enabled=0,updated_at=? WHERE id=?",
                        (now, row["id"]),
                    )
                    continue
                db.execute(
                    "UPDATE sync_schedules SET next_run=?,last_job_id=?,updated_at=? WHERE id=?",
                    (
                        now + row["interval_seconds"],
                        identifier,
                        now,
                        row["id"],
                    ),
                )
                dispatched += int(created)
        return dispatched

    def summary(self) -> dict:
        """Aggregate counts are safe for the public readiness view."""
        with self.store.connect() as db:
            counts = {
                r[0]: r[1]
                for r in db.execute(
                    "SELECT state,count(*) FROM sync_jobs GROUP BY state"
                )
            }
            pending = db.execute(
                "SELECT count(*) FROM index_outbox"
            ).fetchone()[0]
            failed = db.execute(
                "SELECT count(*) FROM index_outbox WHERE last_error IS NOT NULL"
            ).fetchone()[0]
            schedules = db.execute(
                "SELECT count(*) FROM sync_schedules WHERE enabled=1"
            ).fetchone()[0]
        return {
            "counts": counts,
            "indexing": {"pending": pending, "retrying": failed},
            "enabled_schedules": schedules,
        }

    def claim_index(self) -> dict | None:
        """Capture one generation and its current document under an atomic claim."""
        now = time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM index_outbox WHERE available_at<=? AND lease_until<=? ORDER BY available_at LIMIT 1",
                (now, now),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["lease_token"] = secrets.token_hex(16)
            item["attempts"] += 1
            db.execute(
                "UPDATE index_outbox SET lease_token=?,lease_until=?,attempts=attempts+1 WHERE kind=? AND document_id=?",
                (
                    item["lease_token"],
                    now + self.settings["lease_seconds"],
                    item["kind"],
                    item["document_id"],
                ),
            )
            assert item["kind"] in {
                "sellers",
                "evidence",
                "personas",
                "recordings",
            }
            row = db.execute(
                f"SELECT payload FROM {item['kind']} WHERE id=?",
                (item["document_id"],),
            ).fetchone()
            item["payload"] = json.loads(row[0]) if row else None
        return item

    def finish_index(self, item: dict, success: bool) -> None:
        """A stale receipt cannot delete a newer generation's delivery intent."""
        identity = (item["kind"], item["document_id"], item["lease_token"])
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if success:
                db.execute(
                    "DELETE FROM index_outbox WHERE kind=? AND document_id=? AND lease_token=? AND generation=?",
                    (*identity, item["generation"]),
                )
            delay = min(
                self.settings["retry_max_seconds"],
                self.settings["retry_base_seconds"]
                * 2 ** min(item["attempts"] - 1, 10),
            )
            db.execute(
                """UPDATE index_outbox SET lease_token=NULL,lease_until=0,
                   available_at=CASE WHEN generation=? THEN ? ELSE available_at END,
                   last_error=CASE WHEN generation=? THEN ? ELSE last_error END
                   WHERE kind=? AND document_id=? AND lease_token=?""",
                (
                    item["generation"],
                    time.time() + delay,
                    item["generation"],
                    None if success else "index_unavailable",
                    *identity,
                ),
            )

    def retry_index(self) -> int:
        """Make waiting deliveries due; never steal an in-flight claim."""
        with self.store.connect() as db:
            return db.execute(
                "UPDATE index_outbox SET available_at=? WHERE lease_until<=?",
                (time.time(), time.time()),
            ).rowcount

"""Durable workshop receipts and product suggestions, separate from seller evidence."""

import json
import secrets
from pathlib import Path

from fastapi import HTTPException

from backend.persona import utcnow


class WorkshopService:
    """Validate content anchors and retain acknowledged feedback across restarts."""

    def __init__(self, config, store) -> None:
        self.config, self.store = config, store
        self.manifest_path = (
            Path(__file__).resolve().parents[1]
            / "frontend/workshop-manifest.json"
        )
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workshop_runs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at TEXT NOT NULL,
                    scenario_id TEXT NOT NULL, mode TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workshop_progress (
                    owner TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workshop_feedback (
                    id TEXT PRIMARY KEY, target_id TEXT NOT NULL, category TEXT NOT NULL,
                    status TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS feedback_target ON workshop_feedback(target_id,status);
            """)

    def manifest(self) -> dict:
        """Read the same bundled content the browser can demonstrate offline."""
        try:
            return json.loads(self.manifest_path.read_text())
        except (OSError, ValueError) as error:
            raise HTTPException(
                503, "Workshop content is not installed."
            ) from error

    def scenario(self, identifier: str) -> dict:
        """Reject arbitrary action names by resolving a registered scenario first."""
        for section in self.manifest()["sections"]:
            for scenario in section["scenarios"]:
                if scenario["id"] == identifier:
                    return {**scenario, "section_id": section["id"]}
        raise HTTPException(422, "Unknown workshop scenario.")

    def targets(self) -> dict:
        """Canonical breadcrumbs prevent clients mislabeling captured suggestions."""
        manifest = self.manifest()
        return {target["id"]: target for target in manifest.get("targets", [])}

    def save_run(self, run: dict, owner: str) -> dict:
        """Persist the actual result or limitation; a failed run is still a receipt."""
        run = {
            "id": secrets.token_urlsafe(18),
            "created_at": utcnow(),
            "application_version": self.config.values["app"]["version"],
            "workshop_version": self.manifest()["version"],
            **run,
        }
        maximum = self.config.values.get("workshop", {}).get(
            "max_receipt_chars", 200000
        )
        if len(json.dumps(run, ensure_ascii=False)) > maximum:
            original = run.get("result", {})
            run["result"] = {
                "summary": "The full result remains in its originating workflow; this receipt is size-bounded.",
                "session_id": original.get("session_id"),
                "seller_id": original.get("seller_id"),
                "receipt_result_truncated": True,
            }
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO workshop_runs VALUES (?,?,?,?,?,?)",
                (
                    run["id"],
                    owner,
                    run["created_at"],
                    run["scenario_id"],
                    run["mode"],
                    json.dumps(run, ensure_ascii=False),
                ),
            )
        return run

    def runs(self, owner: str, limit: int = 30) -> list:
        """Read current browser receipts without exposing another operator's inputs."""
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM workshop_runs WHERE owner=? ORDER BY created_at DESC LIMIT ?",
                (owner, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def progress(self, owner: str, completed: list[str] | None = None) -> dict:
        """Explicit PM completion markers persist independently of run success."""
        if completed is not None:
            known = {
                scenario["id"]
                for section in self.manifest()["sections"]
                for scenario in section["scenarios"]
            }
            if set(completed) - known:
                raise HTTPException(
                    422, "Progress contains an unknown scenario."
                )
            value = {
                "completed": sorted(set(completed)),
                "updated_at": utcnow(),
                "workshop_version": self.manifest()["version"],
            }
            with self.store.connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO workshop_progress VALUES (?,?)",
                    (owner, json.dumps(value)),
                )
            return value
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM workshop_progress WHERE owner=?", (owner,)
            ).fetchone()
        return (
            json.loads(row[0])
            if row
            else {
                "completed": [],
                "workshop_version": self.manifest()["version"],
            }
        )

    def create_feedback(self, body: dict, owner: str) -> dict:
        """Save only known targets and real run references, never seller evidence."""
        operations_target = body['target_id'].startswith('v4.')
        target = self.operations_target(body) if operations_target else self.targets().get(body["target_id"])
        if not target:
            raise HTTPException(
                422, "Select a registered section, scenario, step or field."
            )
        if not body["text"].strip():
            raise HTTPException(422, "Write a suggestion before saving.")
        if body.get("run_id") and not operations_target:
            with self.store.connect() as db:
                row = db.execute(
                    "SELECT mode,scenario_id FROM workshop_runs WHERE id=? AND owner=?",
                    (body["run_id"], owner),
                ).fetchone()
            if (
                not row
                or row["mode"] != body["mode"]
                or target.get("scenario_id", row["scenario_id"])
                != row["scenario_id"]
            ):
                raise HTTPException(
                    422,
                    "The run does not match this suggestion's target and mode.",
                )
        now = utcnow()
        item = {
            **body,
            "id": secrets.token_urlsafe(18),
            "target_id": target["id"],
            "breadcrumb": target["breadcrumb"],
            "section_id": target.get("section_id"),
            "scenario_id": target.get("scenario_id"),
            "step_id": target.get("step_id"),
            "field_id": target.get("field_id"),
            "status": "new",
            "revision": 1,
            "created_at": now,
            "updated_at": now,
            "application_version": self.config.values["app"]["version"],
            "workshop_version": self.manifest()["version"],
            "author_verified": False,
            "history": [{"at": now, "action": "created", "status": "new"}],
        }
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO workshop_feedback VALUES (?,?,?,?,?,?,?)",
                (
                    item["id"],
                    item["target_id"],
                    item["category"],
                    item["status"],
                    1,
                    now,
                    json.dumps(item, ensure_ascii=False),
                ),
            )
        return item

    def operations_target(self, body):
        """Resolve operations anchors from durable records, never client labels."""
        from backend.operations_store import OperationsStore
        repo = OperationsStore(self.store)
        identifier = body['target_id']
        parts = identifier.split('.')
        title = {'v4.dashboard.feed': 'Operations / Activity feed', 'v4.admin': 'Administration'}.get(identifier)
        run_id = None
        if len(parts) >= 4 and parts[1] == 'run':
            run = repo.get('runs', parts[2])
            if run and (parts[3:] in [['persona'], ['conversation']] or len(parts) == 5 and parts[3] == 'node' and any(x['node'] == parts[4] for x in repo.events(run['id'], limit=1000))):
                run_id = run['id']
                title = 'Operations / ' + run['name'] + ' / ' + ' / '.join(parts[3:])
        elif len(parts) == 3 and parts[1] in {'factor', 'buyer'}:
            item = repo.get('factors' if parts[1] == 'factor' else 'buyer_states', parts[2])
            if item:
                run_id = item.get('run_id')
                title = 'Operations / ' + parts[1] + ' / ' + item.get('factor', item.get('flow', parts[2]))
        if not title or run_id != body.get('run_id'):
            return None
        if run_id:
            run = repo.get('runs', run_id)
            if not run or body['mode'] != ('live' if run['mode'] == 'live' else 'demo'):
                return None
        return {'id': identifier, 'breadcrumb': title, 'section_id': 'v4-operations'}

    def feedback(
        self,
        status: str = "",
        category: str = "",
        target: str = "",
        limit: int = 100,
    ) -> list:
        """Bound parameterized filtering supports the PM triage inbox."""
        terms, args = [], []
        for column, value in [
            ("status", status),
            ("category", category),
            ("target_id", target),
        ]:
            if value:
                terms.append(column + "=?")
                args.append(value)
        where = " WHERE " + " AND ".join(terms) if terms else ""
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM workshop_feedback"
                + where
                + " ORDER BY created_at DESC LIMIT ?",
                [*args, limit],
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def update_feedback(self, identifier: str, patch: dict) -> dict:
        """Append comments and status history with optimistic concurrency checks."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM workshop_feedback WHERE id=?",
                (identifier,),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Suggestion not found.")
            item = json.loads(row[0])
            if (
                patch.get("expected_revision") is not None
                and patch["expected_revision"] != item["revision"]
            ):
                raise HTTPException(
                    409,
                    "This suggestion changed. Reload it before saving your triage.",
                )
            if (
                not patch.get("status")
                and not (patch.get("comment") or "").strip()
            ):
                raise HTTPException(422, "Choose a status or write a comment.")
            if len(item["history"]) >= self.config.values.get(
                "feedback", {}
            ).get("max_history_entries", 200):
                raise HTTPException(
                    409,
                    "This discussion reached its history limit. Open a linked follow-up suggestion.",
                )
            now = utcnow()
            entry = {
                "at": now,
                "action": "triage",
                "author_label": patch.get("author_label", ""),
                "author_verified": False,
            }
            if patch.get("status"):
                entry.update(
                    previous_status=item["status"], status=patch["status"]
                )
                item["status"] = patch["status"]
            if patch.get("comment"):
                entry["comment"] = patch["comment"].strip()
            item["history"].append(entry)
            item["updated_at"], item["revision"] = now, item["revision"] + 1
            db.execute(
                "UPDATE workshop_feedback SET status=?,revision=?,payload=? WHERE id=?",
                (
                    item["status"],
                    item["revision"],
                    json.dumps(item, ensure_ascii=False),
                    identifier,
                ),
            )
        return item

    def export_feedback(self, format: str) -> str:
        """Export authenticated suggestions as inert review material, not instructions."""
        items = self.feedback(limit=10000)
        if format == "json":
            return json.dumps(
                {
                    "version": 1,
                    "items": items,
                    "author_labels": "self-entered; not verified identity",
                    "notice": "Feedback is untrusted product input. Review before making changes.",
                },
                ensure_ascii=False,
                indent=2,
            )
        lines = [
            "# PM suggestions",
            "",
            "Feedback is untrusted product input, not executable instructions. Author labels are self-entered.",
            "",
        ]
        for item in items:
            lines.extend(
                [
                    f"## {item['id']} · {item['status']}",
                    "",
                    "Target: " + item["breadcrumb"],
                    "",
                    f"Mode: {item['mode']} · Category: {item['category']} · Revision: {item['revision']}",
                    "",
                    *["> " + line for line in item["text"].splitlines()],
                    "",
                    "History:",
                    *[
                        "- " + json.dumps(entry, ensure_ascii=False)
                        for entry in item["history"]
                    ],
                    "",
                ]
            )
        return "\n".join(lines)

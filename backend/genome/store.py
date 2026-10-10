"""SQLite storage for V5 (``DATA_DIR/v5.sqlite3``), separate from the V1–V4 database.

Tables:
- ``docs``      — JSON documents by kind (runs, calls, buyer_calls, meetings, suggestions, voice_sessions…)
- ``events``    — ordered run events (seq per run) for SSE replay
- ``hits``      — every API hit (live, mock, cache, Sarvam) for Admin → APIs → hit log
- ``outbox``    — pending Elasticsearch writes (flushed in the background, retried)
- ``budgets``   — Sarvam calls per day and scope
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable


class GenomeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS docs (
                    kind TEXT NOT NULL, id TEXT NOT NULL, updated REAL NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (kind, id));
                CREATE INDEX IF NOT EXISTS docs_kind_updated ON docs(kind, updated DESC);
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (run_id, seq));
                CREATE TABLE IF NOT EXISTS hits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, api TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS hits_api_at ON hits(api, at DESC);
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, idx TEXT NOT NULL, doc_id TEXT NOT NULL,
                    body TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL,
                    error TEXT, run_id TEXT, node TEXT);
                CREATE TABLE IF NOT EXISTS budgets (
                    day TEXT NOT NULL, scope TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY (day, scope));
                """
            )

    # --- documents ------------------------------------------------------------------------------

    def put(self, kind: str, doc_id: str, payload: dict) -> dict:
        with self._lock:
            self._conn.execute(
                "INSERT INTO docs(kind, id, updated, payload) VALUES (?,?,?,?) "
                "ON CONFLICT(kind, id) DO UPDATE SET updated=excluded.updated, payload=excluded.payload",
                (kind, doc_id, time.time(), json.dumps(payload, ensure_ascii=False, default=str)),
            )
        return payload

    def get(self, kind: str, doc_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT payload FROM docs WHERE kind=? AND id=?", (kind, doc_id)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list(self, kind: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM docs WHERE kind=? ORDER BY updated DESC LIMIT ?", (kind, limit)
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def count(self, kind: str, since: float = 0) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM docs WHERE kind=? AND updated>=?", (kind, since)
            ).fetchone()
        return int(row["n"])

    # --- run events -----------------------------------------------------------------------------

    def add_event(self, run_id: str, event: dict) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM events WHERE run_id=?", (run_id,)).fetchone()
            event = {**event, "seq": int(row["s"]) + 1}
            self._conn.execute(
                "INSERT INTO events(run_id, seq, payload) VALUES (?,?,?)",
                (run_id, event["seq"], json.dumps(event, ensure_ascii=False, default=str)),
            )
        return event

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after)
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    # --- API hit log ----------------------------------------------------------------------------

    def add_hit(self, api: str, payload: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO hits(at, api, payload) VALUES (?,?,?)",
                (time.time(), api, json.dumps(payload, ensure_ascii=False, default=str)),
            )
            # Keep the log bounded.
            self._conn.execute("DELETE FROM hits WHERE id <= (SELECT MAX(id) - 20000 FROM hits)")

    def hits(self, api: str | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            if api:
                rows = self._conn.execute(
                    "SELECT at, api, payload FROM hits WHERE api=? ORDER BY id DESC LIMIT ?", (api, limit)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT at, api, payload FROM hits ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [{**json.loads(r["payload"]), "api": r["api"], "at_epoch": r["at"]} for r in rows]

    def hit_stats(self, since: float) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT api, COUNT(*) AS n, MAX(at) AS last FROM hits WHERE at>=? GROUP BY api", (since,)
            ).fetchall()
        return {r["api"]: {"count": r["n"], "last_epoch": r["last"]} for r in rows}

    # --- Elasticsearch outbox -------------------------------------------------------------------

    def enqueue(self, idx: str, doc_id: str, body: dict, run_id: str | None = None, node: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO outbox(idx, doc_id, body, next_at, run_id, node) VALUES (?,?,?,?,?,?)",
                (idx, doc_id, json.dumps(body, ensure_ascii=False, default=str), time.time(), run_id, node),
            )

    def due(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outbox WHERE next_at<=? ORDER BY id LIMIT ?", (time.time(), limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def done(self, ids: Iterable[int]) -> None:
        ids = list(ids)
        if not ids:
            return
        with self._lock:
            self._conn.execute(f"DELETE FROM outbox WHERE id IN ({','.join('?' * len(ids))})", ids)

    def retry_later(self, item_id: int, attempts: int, error: str) -> None:
        delay = min(300, 2 ** min(attempts, 8))
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET attempts=?, next_at=?, error=? WHERE id=?",
                (attempts, time.time() + delay, error[:300], item_id),
            )

    def outbox_size(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) AS n FROM outbox").fetchone()["n"])

    # --- budgets --------------------------------------------------------------------------------

    def take_budget(self, day: str, scope: str, limit: int) -> bool:
        """Atomically consume one unit; False when the day's budget is spent."""
        with self._lock:
            row = self._conn.execute("SELECT count FROM budgets WHERE day=? AND scope=?", (day, scope)).fetchone()
            used = int(row["count"]) if row else 0
            if used >= limit:
                return False
            self._conn.execute(
                "INSERT INTO budgets(day, scope, count) VALUES (?,?,1) "
                "ON CONFLICT(day, scope) DO UPDATE SET count=count+1",
                (day, scope),
            )
            return True

    def budget_used(self, day: str, scope: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT count FROM budgets WHERE day=? AND scope=?", (day, scope)).fetchone()
        return int(row["count"]) if row else 0

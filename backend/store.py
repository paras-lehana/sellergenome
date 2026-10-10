"""Transactional session state and explicit SQLite retrieval fallback."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class Store:
    """Persist JSON documents in an indexed, single-worker friendly database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sellers(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,seller_id TEXT,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS evidence_seller ON evidence(seller_id);
                CREATE TABLE IF NOT EXISTS recordings(id TEXT PRIMARY KEY,seller_id TEXT,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,payload TEXT);
                CREATE TABLE IF NOT EXISTS personas(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,owner TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rate_limits(bucket TEXT PRIMARY KEY,count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS index_outbox(
                    kind TEXT NOT NULL,document_id TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 1,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0,lease_token TEXT,
                    last_error TEXT,PRIMARY KEY(kind,document_id)
                );
                CREATE INDEX IF NOT EXISTS outbox_due ON index_outbox(available_at,lease_until);
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Commit writes atomically; use a bounded wait during batch ingestion."""
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def put(
        self, table: str, identifier: str, payload: dict, **columns
    ) -> None:
        """Upsert allowlisted entities; never interpolate caller-chosen SQL."""
        with self.connect() as db:
            self.put_document(db, table, identifier, payload, **columns)

    @staticmethod
    def put_document(
        db: sqlite3.Connection,
        table: str,
        identifier: str,
        payload: dict,
        **columns,
    ) -> None:
        """Commit document and indexing intent in the caller's transaction."""
        assert table in {
            "sellers",
            "evidence",
            "recordings",
            "personas",
            "sessions",
            "metadata",
        }
        assert set(columns) <= {"seller_id", "owner"}
        key = "key" if table == "metadata" else "id"
        values = {
            key: identifier,
            "payload": json.dumps(payload, ensure_ascii=False),
            **columns,
        }
        names = list(values)
        db.execute(
            f"INSERT OR REPLACE INTO {table} ({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
            list(values.values()),
        )
        if table in {"sellers", "evidence", "personas", "recordings"}:
            # Keep an in-flight lease; its receipt cannot delete this generation.
            db.execute(
                """INSERT INTO index_outbox(kind,document_id,available_at)
                   VALUES (?,?,?) ON CONFLICT(kind,document_id) DO UPDATE SET
                   generation=generation+1,attempts=0,available_at=excluded.available_at,
                   last_error=NULL""",
                (table, identifier, time.time()),
            )

    def get(self, table: str, identifier: str) -> dict | None:
        """Read a single entity without returning private storage metadata."""
        assert table in {
            "sellers",
            "evidence",
            "personas",
            "sessions",
            "metadata",
            "recordings",
        }
        key = "key" if table == "metadata" else "id"
        with self.connect() as db:
            row = db.execute(
                f"SELECT payload FROM {table} WHERE {key}=?", (identifier,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def seller_evidence(self, identifier: str, limit: int = 30) -> list:
        """Bound evidence reads even for sellers with a long history."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM evidence WHERE seller_id=? ORDER BY json_extract(payload,'$.observed_at') DESC LIMIT ?",
                (identifier, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def sellers(
        self, query: str, persona: str, page: int, size: int, mode: str
    ) -> dict:
        """Filter at the storage boundary to separate demo and private data."""
        condition = "json_extract(payload,'$.source_mode') = ?"
        args = [mode]
        if query:
            condition += " AND (json_extract(payload,'$.name') LIKE ? ESCAPE '\\' OR json_extract(payload,'$.category') LIKE ? ESCAPE '\\' OR json_extract(payload,'$.city') LIKE ? ESCAPE '\\' OR id = ?)"
            escaped = (
                query.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            args.extend(
                [f"%{escaped}%", f"%{escaped}%", f"%{escaped}%", query]
            )
        if persona and persona != "all":
            condition += " AND json_extract(payload,'$.persona_tag') = ?"
            args.append(persona)
        with self.connect() as db:
            count = db.execute(
                f"SELECT COUNT(*) FROM sellers WHERE {condition}", args
            ).fetchone()[0]
            rows = db.execute(
                f"SELECT payload FROM sellers WHERE {condition} ORDER BY id LIMIT ? OFFSET ?",
                [*args, size, (page - 1) * size],
            ).fetchall()
        return {
            "items": [json.loads(row[0]) for row in rows],
            "total": count,
            "page": page,
            "page_size": size,
        }

    def count(self, table: str) -> int:
        """Expose aggregate counts only."""
        assert table in {
            "sellers",
            "evidence",
            "recordings",
            "personas",
            "sessions",
        }
        with self.connect() as db:
            return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def owned_session(self, identifier: str, owner: str) -> dict | None:
        """Opaque IDs alone never authorize a session read."""
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM sessions WHERE id=? AND owner=?",
                (identifier, owner),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def allow_request(self, bucket: str, maximum: int) -> bool:
        """Atomic bounded counters prevent concurrent requests bypassing limits."""
        with self.connect() as db:
            row = db.execute(
                "INSERT INTO rate_limits(bucket,count) VALUES (?,1) ON CONFLICT(bucket) DO UPDATE SET count=count+1 RETURNING count",
                (bucket,),
            ).fetchone()
        return row[0] <= maximum

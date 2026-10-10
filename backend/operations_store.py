"""Durable V4 documents, ordered traces and transactional projection delivery."""
from __future__ import annotations

import json
import time
from uuid import uuid4

from backend.context_contract import now, digest

KINDS = {'runs', 'snapshots', 'established', 'observations', 'factors',
         'buyer_states', 'actions', 'voice_sessions', 'tool_runs', 'registry'}
PROJECTIONS = {'snapshots', 'established', 'observations', 'factors', 'traces', 'actions', 'buyer_states'}


class OperationsStore:
    """Store immutable evidence revisions and recoverable jobs in the app DB."""

    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS v4_documents(
                    kind TEXT NOT NULL,id TEXT NOT NULL,subject TEXT NOT NULL,
                    revision INTEGER NOT NULL,payload TEXT NOT NULL,
                    updated REAL NOT NULL,PRIMARY KEY(kind,id));
                CREATE INDEX IF NOT EXISTS v4_subject ON v4_documents(kind,subject,updated);
                CREATE TABLE IF NOT EXISTS v4_document_history(
                    kind TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,
                    payload TEXT NOT NULL,updated REAL NOT NULL,PRIMARY KEY(kind,id,revision));
                INSERT OR IGNORE INTO v4_document_history SELECT kind,id,revision,payload,updated FROM v4_documents;
                CREATE TABLE IF NOT EXISTS v4_events(
                    run_id TEXT NOT NULL,seq INTEGER NOT NULL,payload TEXT NOT NULL,
                    PRIMARY KEY(run_id,seq));
                CREATE TABLE IF NOT EXISTS v4_outbox(
                    kind TEXT NOT NULL,id TEXT NOT NULL,generation INTEGER NOT NULL,
                    payload TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
                    available REAL NOT NULL DEFAULT 0,error TEXT,
                    PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS v4_claims(
                    key TEXT PRIMARY KEY,created REAL NOT NULL,resource TEXT NOT NULL);
            ''')

    @staticmethod
    def _put(db, kind, document, project=True):
        """An outbox intent commits in the same transaction as the document."""
        identifier = document['id']
        previous = db.execute('SELECT revision FROM v4_documents WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        revision = (previous[0] if previous else 0) + 1
        value = {**document, 'revision': revision}
        raw = json.dumps(value, ensure_ascii=False)
        db.execute('INSERT OR REPLACE INTO v4_documents VALUES(?,?,?,?,?,?)',
                   (kind, identifier, value.get('subject_id', ''), revision, raw, time.time()))
        db.execute('INSERT INTO v4_document_history VALUES(?,?,?,?,?)',(kind,identifier,revision,raw,time.time()))
        if project and kind in PROJECTIONS:
            db.execute('''INSERT INTO v4_outbox(kind,id,generation,payload) VALUES(?,?,?,?)
                ON CONFLICT(kind,id) DO UPDATE SET generation=excluded.generation,
                payload=excluded.payload,attempts=0,available=0,error=NULL''',
                       (kind, identifier, revision, raw))
        return value

    def put(self, kind, document, expected=None):
        """Optimistic revision checks protect operator edits from lost updates."""
        if kind not in KINDS:
            raise ValueError('Unknown document kind.')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM v4_documents WHERE kind=? AND id=?', (kind, document['id'])).fetchone()
            if expected is not None and expected != (row[0] if row else 0):
                raise ValueError('Revision conflict; reload before saving.')
            return self._put(db, kind, document)

    def get(self, kind, identifier):
        """Read exactly one bounded document by its server-generated identifier."""
        with self.store.connect() as db:
            row = db.execute('SELECT payload FROM v4_documents WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        return json.loads(row[0]) if row else None

    def history(self,kind,identifier):
        """Keep superseded observations/receipts available for review and replay."""
        with self.store.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM v4_document_history WHERE kind=? AND id=? ORDER BY revision DESC LIMIT 100',(kind,identifier))]

    def list(self, kind, subject=None, limit=100):
        """Bound list endpoints; private payloads never appear in public feeds."""
        sql, args = 'SELECT payload FROM v4_documents WHERE kind=?', [kind]
        if subject is not None:
            sql += ' AND subject=?'
            args.append(subject)
        with self.store.connect() as db:
            rows = db.execute(sql + ' ORDER BY updated DESC LIMIT ?', [*args, min(limit, 500)]).fetchall()
        return [json.loads(row[0]) for row in rows]

    def event(self, run_id, node, status, **details):
        """Sequence assignment and trace-index intent are one atomic operation."""
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            seq = db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM v4_events WHERE run_id=?', (run_id,)).fetchone()[0]
            event = {'contract_version':'trace/4.0','id': f'{run_id}-{seq}', 'run_id': run_id, 'seq': seq,
                     'node': node, 'status': status, 'at': now(), **details}
            raw = json.dumps(event, ensure_ascii=False)
            db.execute('INSERT INTO v4_events VALUES(?,?,?)', (run_id, seq, raw))
            db.execute('INSERT INTO v4_outbox(kind,id,generation,payload) VALUES(?,?,?,?)', ('traces', event['id'], 1, raw))
        return event

    def events(self, run_id, after=0, limit=250):
        """Support reconnect cursors without duplicating displayed operations."""
        with self.store.connect() as db:
            rows = db.execute('SELECT payload FROM v4_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?', (run_id, after, min(limit, 1000))).fetchall()
        return [json.loads(row[0]) for row in rows]

    def claim(self, key, resource):
        """Reserve an idempotency key before an external action can be attempted."""
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO v4_claims VALUES(?,?,?)', (key, time.time(), resource))
            return db.execute('SELECT resource FROM v4_claims WHERE key=?', (key,)).fetchone()[0]

    def reserve_tool(self, receipt, maximum):
        """Atomically reserve daily provider spend, including failed retries."""
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute("SELECT payload FROM v4_documents WHERE kind='tool_runs' AND id=?",(receipt['id'],)).fetchone()
            previous=json.loads(existing[0]) if existing else {}
            if previous.get('status') in {'started','completion_unknown'}:
                raise ValueError('Previous provider completion is uncertain; automatic retry is blocked.')
            used=db.execute("SELECT COALESCE(SUM(COALESCE(json_extract(payload,'$.budget_attempts'),1)),0) FROM v4_documents WHERE kind='tool_runs' AND json_extract(payload,'$.budget_day')=?",(receipt['budget_day'],)).fetchone()[0]
            if used>=maximum:
                raise ValueError('Daily V4 provider budget reached.')
            attempts=previous.get('budget_attempts',1) if previous.get('budget_day')==receipt['budget_day'] else 0
            return self._put(db,'tool_runs',{**receipt,'status':'started','budget_attempts':attempts+1},False)

    def backlog(self):
        """Summarize pending delivery without revealing payloads."""
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT kind,COUNT(*) AS pending,MAX(attempts) AS attempts FROM v4_outbox GROUP BY kind')]

    def pending(self, limit=20, excluded=()):
        """The single app worker retries bounded generations, never full corpus."""
        with self.store.connect() as db:
            clause = ' AND kind NOT IN (' + ','.join('?' for _ in excluded) + ')' if excluded else ''
            return [dict(row) for row in db.execute('SELECT * FROM v4_outbox WHERE available<=?' + clause + ' ORDER BY available LIMIT ?', (time.time(), *excluded, limit))]

    def delivered(self, row, error=None):
        """A stale receipt can never clear a newer document generation."""
        with self.store.connect() as db:
            if error:
                db.execute('UPDATE v4_outbox SET attempts=attempts+1,available=?,error=? WHERE kind=? AND id=? AND generation=?',
                           (time.time() + min(300, 2 ** min(row['attempts'] + 1, 8)), str(error)[:160], row['kind'], row['id'], row['generation']))
            else:
                db.execute('DELETE FROM v4_outbox WHERE kind=? AND id=? AND generation=?', (row['kind'], row['id'], row['generation']))

"""Private password hashes and revocable server sessions for the PM portal.

The automation key remains an independent existing mechanism. This module never
returns password hashes, cookies or secrets through public session metadata.
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import HTTPException


class AccessService:
    """Persist sessions and throttle counters; keep the hash in private storage."""

    def __init__(self, config, store) -> None:
        self.config = config
        self.store = store
        self.directory = Path(
            os.getenv(
                "PRIVATE_CONFIG_DIR", str(config.data_dir / "private-config")
            )
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self.password_path = self.directory / "password.json"
        self.cookie_name = os.getenv("SESSION_COOKIE_NAME", "persona_access")
        self.owner_cookie = os.getenv("OWNER_COOKIE_NAME", "persona_owner")
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS access_sessions (
                    token_hash TEXT PRIMARY KEY, password_version TEXT NOT NULL,
                    expires_at REAL NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS access_attempts (
                    identity TEXT PRIMARY KEY, started_at REAL NOT NULL,
                    attempts INTEGER NOT NULL
                );
            """)

    @staticmethod
    def hash_password(password: str, salt: str | None = None) -> dict:
        """Use a per-password salt and a deliberately expensive standard KDF."""
        salt = salt or secrets.token_hex(16)
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
        ).hex()
        return {"algorithm": "scrypt-16384-8-1", "salt": salt, "hash": digest}

    def _password(self) -> dict:
        """Read the active hash on every check so external rotations revoke access."""
        try:
            return json.loads(self.password_path.read_text())
        except (OSError, ValueError):
            return {}

    def configured(self) -> bool:
        """Expose readiness without the password verifier."""
        return bool(self._password().get("hash"))

    def verify(self, password: str) -> bool:
        """Compare password verifiers without timing-dependent equality."""
        saved = self._password()
        if not saved:
            return False
        try:
            candidate = self.hash_password(password, saved["salt"])["hash"]
            return hmac.compare_digest(candidate, saved["hash"])
        except (KeyError, ValueError):
            return False

    def set_password(self, password: str) -> None:
        """Atomically replace the private verifier and revoke all issued sessions."""
        record = {
            **self.hash_password(password),
            "version": secrets.token_hex(16),
        }
        temporary = self.password_path.with_name(
            "password-" + secrets.token_hex(8) + ".tmp"
        )
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w") as output:
            json.dump(record, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.password_path)
        with self.store.connect() as db:
            db.execute("DELETE FROM access_sessions")

    def login(self, password: str, identity: str) -> tuple[str, dict]:
        """Enforce a persisted five-attempt window before expensive verification."""
        if not self.configured():
            raise HTTPException(
                503, "Password access has not been configured."
            )
        now = time.time()
        fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM access_attempts WHERE identity=?",
                (fingerprint,),
            ).fetchone()
            if (
                previous
                and previous["started_at"] > now - 300
                and previous["attempts"] >= 5
            ):
                raise HTTPException(
                    429, "Too many login attempts. Try again in five minutes."
                )
            attempts = (
                previous["attempts"] + 1
                if previous and previous["started_at"] > now - 300
                else 1
            )
            start = (
                previous["started_at"]
                if previous and previous["started_at"] > now - 300
                else now
            )
            db.execute(
                "INSERT OR REPLACE INTO access_attempts VALUES (?,?,?)",
                (fingerprint, start, attempts),
            )
        verified_generation = self._password().get("version")
        if not self.verify(password):
            raise HTTPException(401, "The password is incorrect.")
        if self._password().get("version") != verified_generation:
            raise HTTPException(
                409, "Password changed during login. Sign in again."
            )
        token = secrets.token_urlsafe(48)
        lifetime = (
            int(self.config.values.get("access", {}).get("session_hours", 12))
            * 3600
        )
        expires = now + lifetime
        with self.store.connect() as db:
            db.execute(
                "DELETE FROM access_attempts WHERE identity=?", (fingerprint,)
            )
            db.execute(
                "DELETE FROM access_sessions WHERE expires_at < ?", (now,)
            )
            db.execute(
                "INSERT INTO access_sessions VALUES (?,?,?,?)",
                (self.digest(token), verified_generation, expires, now),
            )
        return token, {
            "authenticated": True,
            "operator": True,
            "expires_at": expires,
            "method": "password",
        }

    @staticmethod
    def digest(token: str) -> str:
        """Only token digests are stored in the transactional database."""
        return hashlib.sha256(token.encode()).hexdigest()

    def session(self, token: str | None) -> dict:
        """Validate expiry and password generation, including after restart."""
        anonymous = {
            "authenticated": False,
            "operator": False,
            "expires_at": None,
            "configured": self.configured(),
        }
        if not token or len(token) > 200:
            return anonymous
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM access_sessions WHERE token_hash=?",
                (self.digest(token),),
            ).fetchone()
        if (
            not row
            or row["expires_at"] <= time.time()
            or row["password_version"] != self._password().get("version")
        ):
            return anonymous
        return {
            "authenticated": True,
            "operator": True,
            "expires_at": row["expires_at"],
            "method": "password",
        }

    def revoke(self, token: str | None) -> None:
        """Logout invalidates the server session, not merely its browser cookie."""
        if token:
            with self.store.connect() as db:
                db.execute(
                    "DELETE FROM access_sessions WHERE token_hash=?",
                    (self.digest(token),),
                )

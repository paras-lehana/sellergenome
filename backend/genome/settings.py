"""V5 settings: documented defaults, revisioned admin overrides and write-only secrets.

Defaults live in ``config/genome/settings.yaml``. Admin changes are stored as dotted-path overrides
in ``PRIVATE_CONFIG_DIR/genome-settings.json`` with a revision number and a short history, so a
change can be rolled back. Secrets (API keys) are kept in ``genome-secrets.json`` (mode 0600) and
are never returned — only a fingerprint.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS_PATH = ROOT / "config" / "genome" / "settings.yaml"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def private_dir() -> Path:
    path = Path(os.getenv("PRIVATE_CONFIG_DIR", str(ROOT / "runtime" / "config")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get(tree: dict, dotted: str) -> Any:
    node: Any = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def _set(tree: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = tree
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def jwt_claims(value: str) -> dict:
    """Expiry and issuer of a JWT-shaped key (IndiaMART AKs are JWTs) — claims only, never the value.

    The employee AK lasts about 24 hours, so Admin shows when it expires and the run says why a call failed.
    """
    parts = (value or "").split(".")
    if len(parts) != 3 or not all(parts):
        return {}
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, TypeError):
        return {}
    if not isinstance(claims, dict):
        return {}
    out: dict[str, Any] = {"kind": "jwt", "issuer": str(claims.get("iss", ""))[:40], "subject": str(claims.get("sub", ""))[:40]}
    exp = claims.get("exp")
    if isinstance(exp, (int, float)):
        out["expires_at"] = datetime.fromtimestamp(exp, timezone.utc).isoformat(timespec="seconds")
        out["expired"] = exp < datetime.now(timezone.utc).timestamp()
        out["expires_in_hours"] = round((exp - datetime.now(timezone.utc).timestamp()) / 3600, 1)
    return out


class SettingsConflict(ValueError):
    """Raised when an admin saves against a stale revision."""


class GenomeSettings:
    """Effective V5 settings = YAML defaults + admin overrides."""

    # Paths an admin may never change from the browser.
    LOCKED = {"llm.organization_id", "llm.provider_base_url", "elastic.index_prefix"}

    def __init__(self, directory: Path | None = None) -> None:
        self._lock = threading.Lock()
        self.directory = directory or private_dir()
        self.overrides_path = self.directory / "genome-settings.json"
        self.secrets_path = self.directory / "genome-secrets.json"
        self.defaults = yaml.safe_load(DEFAULTS_PATH.read_text())
        self._state = self._read(self.overrides_path, {"revision": 0, "overrides": {}, "history": []})
        self.values = self._effective()

    @staticmethod
    def _read(path: Path, default: dict) -> dict:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return copy.deepcopy(default)

    # Deployment values that must not live in Git (settings.yaml leaves them empty). Admin overrides still win.
    ENV_DEFAULTS = {"upstream.gateway_base_url": "UPSTREAM_GATEWAY_BASE_URL"}

    def _effective(self) -> dict:
        values = copy.deepcopy(self.defaults)
        for dotted, env_name in self.ENV_DEFAULTS.items():
            if os.getenv(env_name):
                try:
                    if not _get(values, dotted):
                        _set(values, dotted, os.getenv(env_name, "").strip())
                except KeyError:
                    pass
        for dotted, value in self._state["overrides"].items():
            try:
                _get(values, dotted)
            except KeyError:
                continue  # an override for a removed setting is ignored, not fatal
            _set(values, dotted, value)
        return values

    @property
    def revision(self) -> int:
        return int(self._state["revision"])

    def get(self, dotted: str, default: Any = None) -> Any:
        try:
            return _get(self.values, dotted)
        except KeyError:
            return default

    def public(self) -> dict:
        return {
            "revision": self.revision,
            "values": self.values,
            "defaults": self.defaults,
            "overrides": self._state["overrides"],
            "locked": sorted(self.LOCKED),
            "history": [
                {k: h[k] for k in ("revision", "at", "author", "changed")}
                for h in self._state["history"][-20:]
            ],
        }

    def update(self, changes: dict[str, Any], expected_revision: int, author: str = "admin") -> dict:
        """Validate type compatibility against defaults and save a new revision."""
        with self._lock:
            if expected_revision != self.revision:
                raise SettingsConflict("Settings changed since you loaded them; reload and retry.")
            for dotted, value in changes.items():
                if dotted in self.LOCKED:
                    raise ValueError(f"{dotted} is locked.")
                current = _get(self.defaults, dotted)  # KeyError → unknown path
                if isinstance(current, bool) != isinstance(value, bool):
                    raise ValueError(f"{dotted} must be {type(current).__name__}.")
                if isinstance(current, (int, float)) and not isinstance(current, bool):
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        raise ValueError(f"{dotted} must be a number.")
                elif isinstance(current, (str, list, dict)) and not isinstance(value, type(current)):
                    raise ValueError(f"{dotted} must be {type(current).__name__}.")
                if dotted == "upstream.glid_access" and value not in ("dev", "prod"):
                    raise ValueError("upstream.glid_access must be dev (approved test GLIDs) or prod (any GLID).")
                if dotted == "upstream.gateway_base_url" and value:
                    from backend.genome.registry import host_allowed  # local import: registry imports settings
                    if not str(value).startswith(("https://", "http://")) or not host_allowed(str(value)):
                        raise ValueError("Gateway host is not on the allow-list (config/genome/allowed_hosts.yaml).")
            previous = copy.deepcopy(self._state["overrides"])
            overrides = {**previous, **changes}
            state = {
                "revision": self.revision + 1,
                "overrides": overrides,
                "history": (self._state["history"] + [{
                    "revision": self.revision + 1, "at": utc_now(), "author": author[:60],
                    "changed": sorted(changes), "previous": previous,
                }])[-100:],
            }
            self._write(self.overrides_path, state)
            self._state = state
            self.values = self._effective()
            return self.public()

    def rollback(self, revision: int, expected_revision: int, author: str = "admin") -> dict:
        with self._lock:
            if expected_revision != self.revision:
                raise SettingsConflict("Settings changed since you loaded them; reload and retry.")
            target = next((h for h in self._state["history"] if h["revision"] == revision + 1), None)
            if revision == 0:
                restored: dict = {}
            elif target is None:
                raise ValueError("Unknown revision.")
            else:
                restored = target["previous"]
            state = {
                "revision": self.revision + 1,
                "overrides": restored,
                "history": (self._state["history"] + [{
                    "revision": self.revision + 1, "at": utc_now(), "author": author[:60],
                    "changed": [f"rollback to {revision}"], "previous": self._state["overrides"],
                }])[-100:],
            }
            self._write(self.overrides_path, state)
            self._state = state
            self.values = self._effective()
            return self.public()

    # --- secrets -------------------------------------------------------------------------------

    def secret(self, name: str) -> str:
        """Stored admin value first, then the deployment environment."""
        stored = self._read(self.secrets_path, {}).get(name, {})
        return stored.get("value") or os.getenv(name, "")

    def secret_status(self, name: str) -> dict:
        value = self.secret(name)
        stored = self._read(self.secrets_path, {}).get(name, {})
        return {
            "name": name,
            "configured": bool(value),
            "source": "admin" if stored.get("value") else ("environment" if value else "none"),
            "fingerprint": hashlib.sha256(value.encode()).hexdigest()[:10] if value else None,
            "updated_at": stored.get("updated_at"),
            **jwt_claims(value),
        }

    def set_secret(self, name: str, value: str) -> dict:
        if not name.replace("_", "").isalnum() or len(name) > 64:
            raise ValueError("Invalid secret name.")
        if len(value) > 4096:
            raise ValueError("Secret too long.")
        with self._lock:
            secrets = self._read(self.secrets_path, {})
            if value:
                secrets[name] = {"value": value, "updated_at": utc_now()}
            else:
                secrets.pop(name, None)
            self._write(self.secrets_path, secrets, private=True)
        return self.secret_status(name)

    @staticmethod
    def _write(path: Path, data: dict, private: bool = False) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        if private:
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)

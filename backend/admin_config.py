"""Validated file-backed configuration and private immutable credential versions.

The single YAML commit contains its history, avoiding a half-written audit trail.
Secrets live in a separate 0600 JSON file and never enter config exports/history.
"""

import copy
import fcntl
import hashlib
import json
import math
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml


class ConfigurationError(ValueError):
    """A safe validation failure suitable for a protected API response."""


class ConfigurationConflict(ConfigurationError):
    """The editor started from a configuration revision that has changed."""


SECRET_NAMES = frozenset(
    {
        "SARVAM_API_KEY",
        "SARVAM_V4_API_KEY", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
        "GOOGLE_CALENDAR_ACCESS_TOKEN", "GOOGLE_CALENDAR_REFRESH_TOKEN",
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_MAPS_API_KEY",
        "RELATIONSHIPS_API_KEY", "BUYER_DETAILS_API_KEY", "PRODUCT_API_KEY",
        "PACKAGE_API_KEY", "TEST_LEAD_API_KEY", "VOICE_AGENT_SERVICE_KEY",
        "SARVAM_BACKUP_API_KEY",
        "LLM_INTERNAL_KEY",
        "LLM_API_KEY",
        "STS_API_KEY",
        "CONTEXT_API_KEY",
        "LMS_API_KEY",
        "PNS_API_KEY",
        "BUYLEADS_API_KEY",
        "ENQUIRIES_API_KEY",
        "ENQUIRY_API_KEY",
        "CHAT_API_KEY",
        "COMPANY_TOKEN",
        "REDASH_API_KEY",
    }
)
ENV_FIELDS = {
    "app.public_base_path": "PUBLIC_BASE_PATH",
    "llm.service_url": "LLM_SERVICE_URL",
    "llm.provider": "LLM_PROVIDER",
    "llm.model": "LLM_MODEL",
    "voice.stt_model": "SARVAM_STT_MODEL",
    "voice.tts_model": "SARVAM_TTS_MODEL",
    "voice.speaker": "SARVAM_SPEAKER",
    "elasticsearch.index_prefix": "ELASTICSEARCH_INDEX_PREFIX",
    "connectors.sources.chat.contact_glid": "LMS_CONTACT_GLID",
}
BOUNDS = {
    "operations.buyer.product_attempts": (1, 4),
    "operations.buyer.quantity_attempts": (1, 3),
    "operations.buyer.specification_attempts": (1, 2),
    "operations.buyer.identity_attempts": (1, 3),
    "operations.buyer.transfer_attempts": (1, 2),
    "operations.persona.transient_seconds": (1, 3600),
    "operations.eligibility_delay_minutes": (0, 1440),
    "operations.persona.established_weight": (0, 1),
    "operations.persona.incidental_weight": (0, 1),
    "operations.persona.confidence_floor": (0, 1),
    "operations.persona.half_life_days": (0.01, 365),
    "operations.persona.expiry_days": (0.01, 365),
    "operations.persona.numeric_max_change": (0, 0.5),
    "operations.max_concurrent_sources": (1, 4),
    "operations.max_related_buyers": (0, 10),
    "operations.buyer_concurrency": (1, 3),
    "operations.max_active_runs": (1, 8),
    "operations.run_timeout_seconds": (10, 180),
    "operations.sarvam.max_calls_per_day": (0, 200),
    "operations.sarvam.max_tokens": (100, 4000),
    "operations.sarvam.timeout_seconds": (5, 120),
    "operations.voice.token_seconds": (30, 600),
    "persona.evidence_limit": (3, 30),
    "persona.max_turns": (5, 100),
    "persona.context_char_budget": (1000, 50000),
    "persona.confidence_floor": (0, 1),
    "persona.switch_cooldown_turns": (0, 10),
    "persona.affect_cooldown_seconds": (0, 120),
    "voice.pace": (0.5, 2),
    "voice.max_upload_mb": (1, 32),
    "voice.max_text_chars": (1, 5000),
    "voice.batch_max_jobs": (1, 100),
    "voice.batch_poll_seconds": (1, 60),
    "voice.batch_timeout_seconds": (30, 3600),
    "voice.normalize_timeout_seconds": (5, 120),
    "voice.batch_max_upload_mb": (1, 64),
    "voice.batch_max_audio_seconds": (1, 1200),
    "voice.live_max_seconds": (10, 600),
    "voice.live_max_connections": (1, 10),
    "voice.live_ticket_seconds": (10, 120),
    "voice.vad_silence_ms": (100, 3000),
    "voice.vad_min_speech_ms": (50, 1000),
    "voice.vad_threshold": (0.01, 1),
    "connectors.timeout_seconds": (1, 30),
    "connectors.max_pages": (1, 5),
    "connectors.max_records": (1, 100),
    "connectors.redash_poll_seconds": (0.1, 10),
    "connectors.redash_max_polls": (1, 60),
    "connectors.redash_max_age": (0, 3600),
    "insights.max_events_per_request": (1, 30),
    "insights.max_characters": (1000, 30000),
    "insights.max_calls_per_cohort": (1, 50),
    "insights.max_cohort_jobs": (1, 20),
    "llm.timeout_seconds": (1, 180),
    "llm.max_tokens": (64, 16000),
    "security.public_requests_per_minute": (10, 1000),
    "security.operator_requests_per_minute": (10, 1000),
    "security.session_cookie_days": (1, 30),
    "access.session_hours": (1, 168),
    "elasticsearch.batch_size": (1, 5000),
    "elasticsearch.timeout_seconds": (1, 60),
    "jobs.poll_seconds": (0.25, 60),
    "jobs.max_attempts": (1, 5),
    "jobs.retry_base_seconds": (1, 300),
    "jobs.retry_max_seconds": (1, 3600),
    "jobs.lease_seconds": (30, 3600),
    "jobs.max_pending_jobs": (1, 5000),
    "jobs.max_schedules": (1, 1000),
    "jobs.schedule_batch_size": (1, 100),
    "jobs.index_batch_size": (1, 100),
    "jobs.min_interval_seconds": (300, 86400),
    "jobs.max_interval_seconds": (300, 86400),
}
EXPLANATIONS = {
    "connectors": "Each source selects Disabled, Sample or Live independently. Live requests only use the protected approved test identifiers.",
    "persona": "Controls grounded persona generation, context budgets and adaptation. Explicit stop/language requests override affect cooldown.",
    "voice": "Provider-supported audio choices and bounded recognition/playback. Unsupported controls stay disabled.",
    "jobs": "Durable queue, retry and indexing limits. Pausing prevents new claims; already running work remains fenced by source policy.",
    "freshness": "Hours during which a dated source observation may inform current context. Historical evidence retains its original date.",
    "insights": "Bounded evidence extraction and observational pattern analysis, never a claim of causal sales uplift.",
    "llm": "All language inference uses the authenticated platform gateway. Provider secrets are private versioned references.",
    "security": "Request limits and browser lifetime. Shared-password authentication remains server-side.",
    "elasticsearch": "Dedicated indexed evidence and persona namespace; schema/connection changes require restart.",
    "app": "Deployment identity and base path; code version is release-managed.",
}


def private_directory() -> Path:
    """Select an operator-controlled directory, never a browser-supplied path."""
    default = (
        (Path(os.environ["DATA_DIR"]) / "config")
        if os.getenv("DATA_DIR")
        else Path(__file__).resolve().parents[1] / "config/runtime"
    )
    return Path(os.getenv("PRIVATE_CONFIG_DIR", str(default)))


def now() -> str:
    """Use explicit UTC timestamps for durable history."""
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def locked(directory: Path, filename: str):
    """Serialize all process writers without making the code mount writable."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / filename).open("a") as handle:
        os.chmod(handle.name, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_write(path: Path, content: str) -> None:
    """Write, fsync and rename on the same filesystem; partial files are inert."""
    fd, temporary = tempfile.mkstemp(prefix=".persona-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_secrets() -> dict:
    """Read private versions; malformed private storage never falls back silently."""
    path = private_directory() / "secrets.json"
    if not path.exists():
        return {"revision": 0, "values": {}}
    try:
        result = json.loads(path.read_text())
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("values"), dict)
            or type(result.get("revision")) is not int
            or result["revision"] < 0
        ):
            raise ValueError
        for name, entry in result["values"].items():
            if (
                name not in SECRET_NAMES
                or not isinstance(entry, dict)
                or not isinstance(entry.get("versions"), dict)
            ):
                raise ValueError
            if entry.get("active_version") not in entry["versions"]:
                raise ValueError
            if any(
                not isinstance(version, str)
                or not isinstance(item, dict)
                or not isinstance(item.get("value"), str)
                for version, item in entry["versions"].items()
            ):
                raise ValueError
        return result
    except (ValueError, TypeError, OSError) as error:
        raise ConfigurationError(
            "Private credential storage is invalid."
        ) from error


def secret_value(name: str, version: str | None = None) -> str:
    """Resolve immutable versions so in-flight speech jobs survive rotation."""
    if name not in SECRET_NAMES:
        return os.getenv(name, "") if version is None else ""
    entry = read_secrets()["values"].get(name, {})
    selected = version or entry.get("active_version")
    if selected:
        return entry.get("versions", {}).get(selected, {}).get("value", "")
    return os.getenv(name, "")


def secret_version(name: str) -> str | None:
    """Return an opaque content fingerprint, never the credential itself."""
    value = secret_value(name)
    return (
        hashlib.sha256((name + ":" + value).encode()).hexdigest()[:24]
        if value
        else None
    )


def ensure_secret_version(name: str) -> str | None:
    """Persist an environment baseline before a job pins its key version."""
    if name not in SECRET_NAMES:
        raise ConfigurationError("Unknown credential reference.")
    directory = private_directory()
    with locked(directory, ".secrets.lock"):
        document = read_secrets()
        entry = document["values"].get(name, {})
        version = entry.get("active_version")
        if version:
            return version
        value = os.getenv(name, "")
        if not value:
            return None
        version = hashlib.sha256((name + ":" + value).encode()).hexdigest()[
            :24
        ]
        document["values"][name] = {
            "active_version": version,
            "versions": {version: {"value": value, "created_at": now()}},
        }
        document["revision"] += 1
        atomic_write(directory / "secrets.json", json.dumps(document))
        return version


def find_secret_version(name: str, fingerprint: str) -> str | None:
    """Resolve a pre-versioning job's raw-key fingerprint without exposing keys."""
    ensure_secret_version(name)
    versions = read_secrets()["values"].get(name, {}).get("versions", {})
    for version, entry in versions.items():
        if hashlib.sha256(entry["value"].encode()).hexdigest() == fingerprint:
            return version
    return None


def leaves(value: dict, prefix: str = "") -> dict:
    """Represent schema leaves as stable dotted admin field paths."""
    result = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(leaves(item, path))
        else:
            result[path] = item
    return result


def set_path(target: dict, path: str, value) -> None:
    """Assign a previously validated schema path without executable expressions."""
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value


def replace_tree(target: dict, source: dict) -> None:
    """Preserve dict references held by workers when validated values change."""
    for key in list(target):
        if key not in source:
            del target[key]
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            replace_tree(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


class ConfigurationService:
    """One versioned configuration authority for files, admin forms and workers."""

    def __init__(self, defaults: dict, target: dict, on_change=None):
        self.defaults = copy.deepcopy(defaults)
        self.target = target
        self.on_change = on_change
        self.directory = private_directory()
        self.path = self.directory / "overrides.yaml"
        self.revision = 0
        self.restart_pending = []
        self.reload()

    def _read(self) -> dict:
        """An absent override file means documented defaults, not missing config."""
        if not self.path.exists():
            return {"revision": 0, "overrides": {}, "history": []}
        try:
            value = yaml.safe_load(self.path.read_text())
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("overrides"), dict)
                or type(value.get("revision")) is not int
                or value["revision"] < 0
                or not isinstance(value.get("history"), list)
            ):
                raise ValueError
            return value
        except (ValueError, yaml.YAMLError, OSError) as error:
            raise ConfigurationError(
                "Configuration file is invalid; repair or restore a valid revision."
            ) from error

    def _field(self, path: str, default) -> dict:
        """Describe editability and real implementation limits for every leaf."""
        readonly = (
            path
            in {
                "app.version",
                "operations.sarvam.organization_id",
                "operations.sarvam.mcp_version",
                "operations.sarvam.model",
                "operations.index_prefix",
                "operations.actions.test_only",
                "operations.actions.calendar_owner",
                "voice.base_url",
                "voice.tts_model",
                "llm.service_url",
                "voice.live_sample_rate",
                "voice.live_frame_bytes",
                "voice.recording_hosts",
                "voice.live_origins",
            }
            or path.endswith((".endpoint", ".datasource_id", ".secret_env"))
            or (
                path.startswith("voice.tts_profiles.")
                and path.endswith(".model")
            )
        )
        restart = path.startswith(
            ("app.", "elasticsearch.", "security.")
        ) or path in {
            "voice.stt_model",
            "voice.tts_model",
            "voice.realtime_model",
        }
        field = {
            "path": path,
            "label": path.split(".")[-1].replace("_", " ").title(),
            "type": "boolean"
            if isinstance(default, bool)
            else "integer"
            if isinstance(default, int)
            else "number"
            if isinstance(default, float)
            else "array"
            if isinstance(default, list)
            else "string",
            "default": default,
            "description": EXPLANATIONS.get(
                path.split(".")[0], "Validated application setting."
            ),
            "readonly": readonly,
            "restart_required": restart,
            "environment_variable": ENV_FIELDS.get(path),
            "environment_locked": bool(
                os.getenv(ENV_FIELDS.get(path, ""), "")
            ),
        }
        if path in BOUNDS:
            field["minimum"], field["maximum"] = BOUNDS[path]
        if path == "voice.tts_model" or (
            path.startswith("voice.tts_profiles.") and path.endswith(".model")
        ):
            field["description"] = (
                "Provider model identity is fixed by the supported capability "
                "registry. Select voice.tts_profile to choose the active model."
            )
        if path.endswith("pitch_min"):
            field["minimum"], field["maximum"] = -0.75, 0.75
        if path.endswith(".mode"):
            field["options"] = ["disabled", "sample", "live"]
        if path == "voice.tts_profile":
            field["options"] = ["v3", "v2_pitch", "v4_pitch"]
        if path == "persona.default_formality":
            field["options"] = [
                "formal",
                "professional",
                "respectful and conversational",
            ]
        if path == "persona.default_warmth":
            field["options"] = ["warm", "gentle", "neutral"]
        if isinstance(default, list):
            field["items"] = {"type": "string"}
        return field

    def _validated(
        self, overrides: dict, editing: bool = False, complete: bool = True
    ) -> dict:
        """Reject unknown paths, weak types, invalid bounds and lock overrides."""
        defaults = leaves(self.defaults)
        result = copy.deepcopy(self.defaults)
        for path, value in overrides.items():
            if path not in defaults:
                raise ConfigurationError("Unknown configuration path.")
            default = defaults[path]
            field = self._field(path, default)
            if editing and (field["readonly"] or field["environment_locked"]):
                raise ConfigurationError(
                    f"{path} is controlled by deployment configuration."
                )
            valid_type = type(value) is type(default) or (
                type(default) is float and type(value) is int
            )
            if not valid_type or (
                isinstance(value, float) and not math.isfinite(value)
            ):
                raise ConfigurationError(f"{path} has the wrong value type.")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                minimum, maximum = BOUNDS.get(
                    path,
                    (-0.75, 0.75)
                    if path.endswith("pitch_min")
                    else (0, 1000000),
                )
                if not minimum <= value <= maximum:
                    raise ConfigurationError(
                        f"{path} is outside its supported range."
                    )
            if field.get("options") and value not in field["options"]:
                raise ConfigurationError(f"{path} has an unsupported choice.")
            if isinstance(value, str) and (
                len(value) > 1000 or "\x00" in value
            ):
                raise ConfigurationError(f"{path} is too long or invalid.")
            if isinstance(value, list) and (
                len(value) > 100
                or any(not isinstance(x, str) or len(x) > 100 for x in value)
            ):
                raise ConfigurationError(
                    f"{path} requires bounded text choices."
                )
            if path.endswith(
                (".meeting_true_codes", ".meeting_false_codes")
            ) and (
                len(value) > 20
                or any(
                    not item
                    or len(item) > 32
                    or item != item.strip().casefold()
                    for item in value
                )
            ):
                raise ConfigurationError(
                    "Meeting code choices require at most 20 nonempty lowercase codes of 32 characters."
                )
            if path.endswith("contact_glid") and value:
                from backend.source_policy import approved_glids

                if value not in approved_glids():
                    raise ConfigurationError(
                        "Select an approved test contact identifier."
                    )
            set_path(result, path, value)
        operations = result.get("operations", {})
        if operations:
            policy = operations["persona"]
            if not abs(policy["established_weight"] + policy["incidental_weight"] - 1) < 0.000001:
                raise ConfigurationError("Established and incidental weights must sum to one.")
            if policy["half_life_days"] <= 0:
                raise ConfigurationError("Recency half-life must be positive.")
            for name, value in policy["factor_weights"].items():
                if not 0 <= value <= 1:
                    raise ConfigurationError("Factor weights must be between zero and one.")
            if operations["sarvam"]["organization_id"] != "01a110b3-f038-7adb-b627-6863a3785c40" or not operations["actions"]["test_only"]:
                raise ConfigurationError("Hackathon organization and test-only boundary are fixed.")
        jobs = result["jobs"]
        if complete and (
            jobs["retry_base_seconds"] > jobs["retry_max_seconds"]
            or jobs["min_interval_seconds"] > jobs["max_interval_seconds"]
        ):
            raise ConfigurationError(
                "Retry or schedule minimum exceeds its maximum."
            )
        mapping = result["connectors"]["sources"]["redash_hotlead"]
        if complete and set(mapping["meeting_true_codes"]) & set(
            mapping["meeting_false_codes"]
        ):
            raise ConfigurationError(
                "Meeting true/false codes must not overlap."
            )
        return result

    def reload(self) -> None:
        """Activate a complete validated revision, preserving shared dictionaries."""
        document = self._read()
        effective = self._validated(document["overrides"])
        for path, env in ENV_FIELDS.items():
            if os.getenv(env):
                set_path(effective, path, os.environ[env])
        replace_tree(self.target, effective)
        self.revision = int(document["revision"])
        # Connector instances can report the revision without importing Settings.
        self.target["connectors"]["config_revision"] = self.revision
        if self.on_change:
            self.on_change()

    def view(self) -> dict:
        """Return editable groups, provenance, revision history and no secrets."""
        effective = leaves(self.target)
        document = self._read()
        groups = {}
        for path, default in leaves(self.defaults).items():
            group = path.split(".")[0]
            field = self._field(path, default)
            field.update(
                value=effective.get(path),
                origin="environment"
                if field["environment_locked"]
                else "override"
                if path in document["overrides"]
                else "default",
            )
            groups.setdefault(
                group,
                {
                    "id": group,
                    "title": group.title(),
                    "description": EXPLANATIONS.get(
                        group, "Application controls"
                    ),
                    "fields": [],
                },
            )["fields"].append(field)
        return {
            "revision": self.revision,
            "groups": list(groups.values()),
            "config_file": "config/settings.yaml",
            "override_file": "PRIVATE_CONFIG_DIR/overrides.yaml",
            "history": [
                {k: v for k, v in row.items() if k != "overrides"}
                for row in document.get("history", [])
            ],
            "restart_pending": self.restart_pending,
            "credentials": self.secret_status(),
            "credential_revision": read_secrets()["revision"],
        }

    def update(self, changes: dict, expected_revision: int) -> dict:
        """Commit validated dotted-path changes with optimistic concurrency."""
        if not isinstance(changes, dict) or not changes:
            raise ConfigurationError(
                "Provide at least one configuration change."
            )
        # Validate each edited field now; related bounds need the merged state.
        self._validated(changes, editing=True, complete=False)
        with locked(self.directory, ".config.lock"):
            document = self._read()
            if expected_revision != document["revision"]:
                raise ConfigurationConflict(
                    "Configuration changed; reload before saving."
                )
            overrides = {**document["overrides"], **changes}
            self._validated(overrides)
            revision = document["revision"] + 1
            history = document.get("history", []) + [
                {
                    "revision": document["revision"],
                    "saved_at": now(),
                    "overrides": document["overrides"],
                    "changed_paths": sorted(changes),
                }
            ]
            document = {
                "revision": revision,
                "updated_at": now(),
                "overrides": overrides,
                "history": history[-100:],
            }
            atomic_write(
                self.path,
                "# Private admin overrides; secrets are stored separately.\n"
                + yaml.safe_dump(document, sort_keys=True),
            )
        self.restart_pending = sorted(
            set(self.restart_pending)
            | {
                path
                for path in changes
                if self._field(path, leaves(self.defaults)[path])[
                    "restart_required"
                ]
            }
        )
        self.reload()
        return self.view()

    def rollback(self, revision: int, expected_revision: int) -> dict:
        """Restore an earlier full override set as a new auditable revision."""
        with locked(self.directory, ".config.lock"):
            document = self._read()
            if expected_revision != document["revision"]:
                raise ConfigurationConflict(
                    "Configuration changed; reload before rolling back."
                )
            previous = next(
                (
                    row
                    for row in document.get("history", [])
                    if row["revision"] == revision
                ),
                None,
            )
            if previous is None:
                raise ConfigurationError("Requested revision is not retained.")
            self._validated(previous["overrides"])
            changed_paths = {
                path
                for path in set(document["overrides"])
                | set(previous["overrides"])
                if document["overrides"].get(path)
                != previous["overrides"].get(path)
            }
            history = document.get("history", []) + [
                {
                    "revision": document["revision"],
                    "saved_at": now(),
                    "overrides": document["overrides"],
                    "changed_paths": ["rollback"],
                    "rollback_to": revision,
                }
            ]
            atomic_write(
                self.path,
                yaml.safe_dump(
                    {
                        "revision": document["revision"] + 1,
                        "updated_at": now(),
                        "overrides": previous["overrides"],
                        "history": history[-100:],
                    },
                    sort_keys=True,
                ),
            )
        defaults = leaves(self.defaults)
        self.restart_pending = sorted(
            set(self.restart_pending)
            | {
                path
                for path in changed_paths
                if self._field(path, defaults[path])["restart_required"]
            }
        )
        self.reload()
        return self.view()

    def export(self) -> dict:
        """Export only nonsecret defaults/effective values and source references."""
        return {
            "revision": self.revision,
            "format": "yaml",
            "content": yaml.safe_dump({k: v for k,v in self.target.items()}, sort_keys=False),
        }

    def secret_status(self) -> list[dict]:
        """Expose presence and version only; old versions remain private."""
        return [
            {
                "name": name,
                "configured": bool(secret_value(name)),
                "version": secret_version(name),
                "write_only": True,
                "activation": "new_requests; existing speech jobs retain their pinned version",
            }
            for name in sorted(SECRET_NAMES)
        ]

    def replace_secret(
        self,
        name: str,
        value: str,
        expected_revision: int,
        expected_credential_revision: int | None = None,
    ) -> dict:
        """Rotate a known secret, retaining versions for already-pinned jobs."""
        if (
            name not in SECRET_NAMES
            or not isinstance(value, str)
            or not 1 <= len(value) <= 4096
            or any(x in value for x in ("\n", "\r", "\x00"))
        ):
            raise ConfigurationError(
                "A valid known credential reference is required."
            )
        # Use one lock order so a second process cannot rotate against stale
        # in-memory configuration while another writer commits an override.
        with (
            locked(self.directory, ".config.lock"),
            locked(self.directory, ".secrets.lock"),
        ):
            if expected_revision != self._read()["revision"]:
                raise ConfigurationConflict(
                    "Configuration changed; reload before replacing credentials."
                )
            document = read_secrets()
            if (
                expected_credential_revision is not None
                and expected_credential_revision != document["revision"]
            ):
                raise ConfigurationConflict(
                    "Credentials changed; reload before replacing them."
                )
            entry = document["values"].setdefault(name, {"versions": {}})
            baseline = os.getenv(name, "")
            if not entry.get("active_version") and baseline:
                initial = hashlib.sha256(
                    (name + ":" + baseline).encode()
                ).hexdigest()[:24]
                entry["versions"].setdefault(
                    initial, {"value": baseline, "created_at": now()}
                )
            version = hashlib.sha256(
                (name + ":" + value).encode()
            ).hexdigest()[:24]
            entry["versions"].setdefault(
                version, {"value": value, "created_at": now()}
            )
            entry["active_version"] = version
            document["revision"] += 1
            atomic_write(self.directory / "secrets.json", json.dumps(document))
        self.reload()
        return {
            "status": "saved",
            "name": name,
            "version": version,
            "revision": self.revision,
            "credential_revision": document["revision"],
            "credentials": self.secret_status(),
        }

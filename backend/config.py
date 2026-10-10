"""One configuration surface; public projections never include secret values."""

import os
import copy
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator
from backend.admin_config import ConfigurationService, secret_value

ROOT = Path(__file__).resolve().parents[1]


class JobSettings(BaseModel):
    """Validate worker bounds; all actual defaults remain in commented YAML."""

    enabled: bool
    poll_seconds: float = Field(ge=0.25, le=60)
    max_attempts: int = Field(ge=1, le=5)
    retry_base_seconds: int = Field(ge=1, le=300)
    retry_max_seconds: int = Field(ge=1, le=3600)
    lease_seconds: int = Field(ge=30, le=3600)
    max_pending_jobs: int = Field(ge=1, le=5000)
    max_schedules: int = Field(ge=1, le=1000)
    schedule_batch_size: int = Field(ge=1, le=100)
    index_batch_size: int = Field(ge=1, le=100)
    min_interval_seconds: int = Field(ge=300, le=86400)
    max_interval_seconds: int = Field(ge=300, le=86400)

    @model_validator(mode="after")
    def ordered_bounds(self):
        """Reject inverted retry or schedule bounds before starting a worker."""
        if self.retry_base_seconds > self.retry_max_seconds:
            raise ValueError("Retry maximum must be at least the base delay.")
        if self.min_interval_seconds > self.max_interval_seconds:
            raise ValueError("Schedule maximum must be at least its minimum.")
        return self


class Settings:
    """Load documented defaults and deployment-specific environment overrides."""

    def __init__(self) -> None:
        self.values = yaml.safe_load(
            (ROOT / "config/settings.yaml").read_text()
        )
        self.values["jobs"] = JobSettings(**self.values["jobs"]).model_dump()
        # The admin service owns all nonsecret overrides and their revision.
        # Environment defaults remain visible as locked values in its schema.
        defaults = copy.deepcopy(self.values)
        self.admin = ConfigurationService(defaults, self.values)
        # Forward voice choices to the pure persona engine without duplicating defaults.
        self.values["persona"]["speaker_choices"] = self.values["voice"][
            "persona_speakers"
        ]
        self.values["persona"]["default_speaker"] = self.values["voice"][
            "speaker"
        ]
        self.base_path = os.getenv(
            "PUBLIC_BASE_PATH", self.values["app"]["public_base_path"]
        ).rstrip("/")
        self.data_dir = Path(os.getenv("DATA_DIR", str(ROOT / "runtime")))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "persona.sqlite3"
        self.operator_key = os.getenv("OPERATOR_API_KEY", "")
        self.es_url = os.getenv("ELASTICSEARCH_URL", "").rstrip("/")
        self.es_username = os.getenv("ELASTICSEARCH_USERNAME", "")
        self.es_password = os.getenv("ELASTICSEARCH_PASSWORD", "")
        self.es_prefix = os.getenv(
            "ELASTICSEARCH_INDEX_PREFIX",
            self.values["elasticsearch"]["index_prefix"],
        )
        self.llm_url = os.getenv(
            "LLM_SERVICE_URL", self.values["llm"]["service_url"]
        ).rstrip("/")
        self.llm_internal_key = secret_value("LLM_INTERNAL_KEY")
        self.llm_api_key = secret_value("LLM_API_KEY")
        self.llm_provider = os.getenv(
            "LLM_PROVIDER", self.values["llm"]["provider"]
        )
        self.llm_model = os.getenv("LLM_MODEL", self.values["llm"]["model"])
        self.sarvam_key = secret_value("SARVAM_API_KEY")
        self.sarvam_backup_key = secret_value("SARVAM_BACKUP_API_KEY")
        for attr, env in [
            ("stt_model", "SARVAM_STT_MODEL"),
            ("tts_model", "SARVAM_TTS_MODEL"),
            ("speaker", "SARVAM_SPEAKER"),
        ]:
            self.values["voice"][attr] = os.getenv(
                env, self.values["voice"][attr]
            )

        self.values["persona"]["default_speaker"] = self.values["voice"][
            "speaker"
        ]
        preview_origin = os.getenv("PREVIEW_ORIGIN", "")
        if (
            preview_origin
            and preview_origin not in self.values["voice"]["live_origins"]
        ):
            self.values["voice"]["live_origins"].append(preview_origin)
        self.admin.on_change = self.refresh_runtime

    def refresh_runtime(self) -> None:
        """Refresh hot provider/default references after validated admin changes."""
        self.llm_internal_key = secret_value("LLM_INTERNAL_KEY")
        self.llm_api_key = secret_value("LLM_API_KEY")
        self.sarvam_key = secret_value("SARVAM_API_KEY")
        self.sarvam_backup_key = secret_value("SARVAM_BACKUP_API_KEY")
        self.llm_provider = self.values["llm"]["provider"]
        self.llm_model = self.values["llm"]["model"]
        self.values["persona"]["speaker_choices"] = self.values["voice"][
            "persona_speakers"
        ]
        self.values["persona"]["default_speaker"] = self.values["voice"][
            "speaker"
        ]
        preview_origin = os.getenv("PREVIEW_ORIGIN", "")
        if (
            preview_origin
            and preview_origin not in self.values["voice"]["live_origins"]
        ):
            self.values["voice"]["live_origins"].append(preview_origin)

    def public(self) -> dict:
        """Return non-secret values and boolean credential readiness."""
        public_values = copy.deepcopy(self.values)
        # Public consumers need delivery controls, not internal endpoint/identity
        # mappings. Full editable source settings require authenticated admin.
        public_values["connectors"] = {
            "timeout_seconds": self.values["connectors"]["timeout_seconds"],
            "max_pages": self.values["connectors"]["max_pages"],
            "max_records": self.values["connectors"]["max_records"],
        }
        return {
            "settings": public_values,
            "config_revision": self.admin.revision,
            "secret_status": {
                "operator": bool(self.operator_key),
                "llm": bool(self.llm_internal_key and self.llm_api_key),
                "sarvam": bool(self.sarvam_key or self.sarvam_backup_key),
                "sarvam_backup": bool(self.sarvam_backup_key),
                "elasticsearch": bool(self.es_url),
            },
            "schema": {
                "evidence_limit": {
                    "type": "integer",
                    "min": 3,
                    "max": 30,
                    "default": 12,
                },
                "max_turns": {
                    "type": "integer",
                    "min": 5,
                    "max": 100,
                    "default": 40,
                },
                "pace": {
                    "type": "number",
                    "min": 0.5,
                    "max": 2.0,
                    "default": 1.0,
                },
            },
        }

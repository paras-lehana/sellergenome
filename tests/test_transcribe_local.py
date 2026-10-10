"""Offline CLI checks require no optional dependency, model or audio download."""

import json
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts.transcribe_local import (
    parser,
    segment_document,
    write_private_json,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/transcribe_local.py"


def test_help_and_url_rejection_without_site_packages():
    """-S disables installed packages, proving lazy ML imports for CLI validation."""
    help_result = subprocess.run(
        [sys.executable, "-S", str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert help_result.returncode == 0
    assert "--allow-model-download" in help_result.stdout
    invalid = subprocess.run(
        [
            sys.executable,
            "-S",
            str(SCRIPT),
            "https://example.test/private.mp3",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert invalid.returncode == 2
    assert "no URL fetching" in invalid.stderr
    assert "optional ASR dependencies missing" not in invalid.stderr


def test_review_flags_and_private_output(tmp_path):
    """Uncertain machine scores remain visible and output is owner-readable only."""
    args = parser().parse_args(["sample.wav"])
    segment = SimpleNamespace(
        id=0,
        start=0.0,
        end=2.0,
        text="Amount 42",
        avg_logprob=-1.5,
        no_speech_prob=0.8,
        compression_ratio=3.0,
        words=[
            SimpleNamespace(start=1.0, end=1.5, word="42", probability=0.3)
        ],
    )
    result = segment_document(segment, "Amount 42", args)
    assert "numbers_require_audio_verification" in result["review_flags"]
    assert "low_word_probability" in result["review_flags"]
    assert "repeated_adjacent_segment" in result["review_flags"]
    assert result["speaker"] is None
    output = tmp_path / "private.json"
    write_private_json(
        output, {"segments": [result], "human_review_required": True}
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text())["human_review_required"] is True

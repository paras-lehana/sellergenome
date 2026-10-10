"""Create a consistent isolated snapshot without resuming production work.

Run on the host before the first preview start. It refuses to overwrite an
existing database; configuration and passwords are separate private operations.
"""

import argparse
import json
import shutil
import sqlite3
from pathlib import Path


def prepare(source: Path, target: Path) -> dict:
    """SQLite backup includes committed WAL state; copied work is made inert."""
    target.mkdir(parents=True, exist_ok=True)
    destination = target / "persona.sqlite3"
    if destination.exists():
        raise RuntimeError(
            "Preview database already exists; refusing to overwrite it."
        )
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original:
        with sqlite3.connect(destination) as preview:
            original.backup(preview)
            tables = {
                row[0]
                for row in preview.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            # Preserve supplied evidence, not another environment's execution rights.
            for table in [
                "sessions",
                "rate_limits",
                "index_outbox",
                "access_sessions",
                "access_attempts",
                "speech_jobs",
                "sync_requests",
                "insight_cohort_jobs",
            ]:
                if table in tables:
                    preview.execute(f"DELETE FROM {table}")
            for table in ["sync_jobs", "sync_schedules"]:
                if table in tables:
                    preview.execute(f"DELETE FROM {table}")
            preview.execute(
                "DELETE FROM metadata WHERE key LIKE 'speech-job-%' OR key LIKE 'live-%' OR key='runtime_settings'"
            )
            preview.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?,?)",
                (
                    "v3-preview-snapshot",
                    json.dumps(
                        {
                            "source": "production SQLite backup",
                            "copied_jobs_resume": False,
                            "schedules_enabled": False,
                        }
                    ),
                ),
            )
    demos = source.parent / "speech-demos"
    if demos.exists():
        shutil.copytree(demos, target / "speech-demos", dirs_exist_ok=True)
    return {
        "snapshot_created": True,
        "copied_jobs_resume": False,
        "speech_demo_assets": demos.exists(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.target)))

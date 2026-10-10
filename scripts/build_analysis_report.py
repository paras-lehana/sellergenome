"""Generate an aggregate-only evidence atlas from verified canonical inputs."""

import argparse
import json
import os
import tempfile
from pathlib import Path

from backend.research import build_report


def main():
    """Write the reviewed report; stdout contains aggregate counts only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--catalog", type=Path, default=Path("data/catalog.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/analysis-report.json")
    )
    args = parser.parse_args()
    report = build_report(args.dataset, json.loads(args.catalog.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Atomically replace the reviewed public artifact; readers never see half JSON.
    fd, temporary = tempfile.mkstemp(prefix=".atlas-", dir=args.output.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(
            temporary, 0o644
        )  # Aggregate-only artifact must remain app-readable.
        os.replace(temporary, args.output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(
        json.dumps(
            {
                "status": "complete",
                "files": report["file_count"],
                "rows": report["total_rows"],
                "summaries": report["summary"],
                "meeting_conflicts": report["meeting_conflicts"],
                "duplicates": report["duplicates"],
                "recording_exact_subset": report["recording_exact_subset"],
            }
        )
    )


if __name__ == "__main__":
    main()

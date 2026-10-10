"""Build the public PM study and a separate private evidence-review database."""

import argparse
import json
from pathlib import Path

from backend.analysis_study import build_study


def main():
    """Require an explicit private destination; never default raw review into Git."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--catalog", type=Path, default=Path("data/catalog.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/pm-analysis.json")
    )
    parser.add_argument("--private-output", type=Path, required=True)
    args = parser.parse_args()
    report = build_study(
        args.dataset, json.loads(args.catalog.read_text()), args.private_output
    )
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "study_id": report["study_id"],
                "groups": {
                    key: {
                        "calls": group["calls"],
                        "coverage": group["coverage"],
                        "unknown": group["no_detected_reason"],
                    }
                    for key, group in report["groups"].items()
                },
                "quality": report["quality"],
            }
        )
    )


if __name__ == "__main__":
    main()

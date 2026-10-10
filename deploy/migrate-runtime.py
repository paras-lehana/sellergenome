#!/usr/bin/env python3
"""One-time consistent migration from an offline import to the data volume.

Do not copy SQLite WAL files. Run after ingestion completes and before the
first application start. Existing production data is never overwritten.
"""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

source = Path(
    sys.argv[1]
    if len(sys.argv) > 1
    else "/opt/sellergenome/runtime/persona.sqlite3"
)
if not source.is_file():
    raise SystemExit("Imported source database not present.")
running = subprocess.run(
    ["docker", "inspect", "seller-persona", "--format", "{{.State.Running}}"],
    capture_output=True,
    text=True,
)
if running.returncode == 0 and running.stdout.strip() == "true":
    raise SystemExit(
        "Application is running; refusing a live database replacement."
    )
subprocess.run(
    ["docker", "volume", "create", "seller-persona-data"],
    check=True,
    stdout=subprocess.DEVNULL,
)
volume = json.loads(
    subprocess.check_output(
        ["docker", "volume", "inspect", "seller-persona-data"]
    )
)[0]
directory = Path(volume["Mountpoint"])
destination = directory / "persona.sqlite3"
if destination.exists():
    raise SystemExit(
        "Destination database already exists; refusing to overwrite it."
    )
with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
    with sqlite3.connect(destination) as target:
        original.backup(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(
                "Database integrity check failed; do not start app."
            )
        counts = {
            table: target.execute("SELECT COUNT(*) FROM " + table).fetchone()[
                0
            ]
            for table in ("sellers", "evidence", "recordings")
        }
os.chown(directory, 1000, 1000)
directory.chmod(0o750)
os.chown(destination, 1000, 1000)
destination.chmod(0o640)
print(
    json.dumps(
        {
            "destination": str(destination),
            "integrity": integrity,
            "counts": counts,
        }
    )
)

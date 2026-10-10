#!/usr/bin/env bash
# Run the Python suite inside the app image (the host has no project dependencies) and syntax-check every
# frontend module. The worktree is mounted read-only, so tests can never touch live state.
# Usage: run_tests.sh [worktree] [pytest args…]     e.g. run_tests.sh "" tests/test_v55_persona_generator.py -x
set -uo pipefail
wt="${1:-/opt/sellergenome/.worktrees/v5}"; [ -n "$wt" ] || wt=/opt/sellergenome/.worktrees/v5
shift || true
image="${SG_IMAGE:-$(docker inspect -f '{{.Config.Image}}' sellergenome-v4)}"
docker run --rm -v "$wt":/work:ro -w /work -e PYTHONPATH=/work -e DATA_DIR=/tmp/data --entrypoint python "$image" \
  -m pytest -q -p no:cacheprovider "$@"
fail=$?   # keep going: a pytest failure must not hide a syntax error
for f in "$wt"/frontend/cc/*.js "$wt"/frontend/*.js; do [ -e "$f" ] || continue; node --check "$f" || { echo "SYNTAX: $f" >&2; fail=1; }; done
[ "$fail" = 0 ] && echo "node --check: frontend/cc and legacy frontend modules OK"
exit "$fail"

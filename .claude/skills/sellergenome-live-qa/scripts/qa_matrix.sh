#!/usr/bin/env bash
# The standard live matrix after a release: both mirrors × desktop/phone × Chromium/WebKit, signed-in call +
# Admin on a hero, plus the anonymous floor barge-in. Screenshots land in OUT_DIR (keep them out of Git:
# /root/data/qa-artifacts/<date>-<topic>/qa). Usage: qa_matrix.sh OUT_DIR [SELLER_ID]
set -uo pipefail
out="${1:?OUT_DIR}"; sid="${2:-syn-91000105}"; here="$(cd "$(dirname "$0")" && pwd)"; mkdir -p "$out"; rc=0
for base in https://sellergenome.lehana.in https://sellergenome.aidhunik.com; do
  for combo in "chromium 1440x900" "chromium 390x844" "webkit 390x844"; do
    set -- $combo
    python3 "$here/qa_call.py" "$base" "$out" "$1" "$2" "$sid" --admin || rc=1
  done
  python3 "$here/qa_floor_barge.py" "$base" "$out" chromium 390x844 || rc=1
done
echo "matrix done (exit $rc) → $out"; exit "$rc"

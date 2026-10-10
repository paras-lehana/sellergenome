#!/usr/bin/env bash
# Run the persona-generator scenario suite LIVE (Sarvam via llm.lehana.in) against any worktree — including
# unmerged code — inside the app container's network namespace, with the app's env files.
# The worktree and private config are mounted read-only; only the output folder is writable.
# Usage: run_eval.sh OUT_DIR [name filters…]      e.g. run_eval.sh /root/data/qa-artifacts/x Shaikh PNSM
#   WT=<worktree> to test another checkout (default .worktrees/v5). Writes OUT_DIR/eval-<timestamp>.json.
# Cost: ~1–2 LLM calls per case (25 synthetic + 2 real by default), paced 2.5 s for the gateway's 30 requests/min.
set -euo pipefail
out="${1:?OUT_DIR required}"; shift
wt="${WT:-/opt/sellergenome/.worktrees/v5}"
mkdir -p "$out"; chmod 777 "$out"   # the image runs as uid 1000
name="eval-$(date +%Y%m%d-%H%M%S).json"
image="${SG_IMAGE:-$(docker inspect -f '{{.Config.Image}}' sellergenome-v4)}"
E=/opt/secrets/env_files
docker run --rm -i --network container:sellergenome-v4 \
  -v "$wt":/work:ro -v /opt/sellergenome/private/v4/config:/srv/config:ro \
  -v /opt/sellergenome/private/inputs/production:/srv/inputs:ro -v "$out":/out:rw \
  --env-file $E/seller-persona-v4.env --env-file $E/sellergenome-im-apis.env --env-file $E/sellergenome-sarvam-voice.env \
  -e PYTHONPATH=/work -e PRIVATE_CONFIG_DIR=/srv/config -e ELASTICSEARCH_URL=http://elasticsearch:9200 \
  -e ELASTICSEARCH_INDEX_PREFIX=seller-persona-v4 -e DATA_DIR=/tmp/data -w /work --entrypoint python "$image" \
  -W ignore - "/out/$name" "$@" < "$wt/scripts/persona_eval.py"
echo "report: $out/$name"
python3 "$(dirname "$0")/summarize.py" "$out/$name"

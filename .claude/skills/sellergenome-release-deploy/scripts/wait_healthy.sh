#!/usr/bin/env bash
# Wait until the live container reports healthy, then print the version each public mirror serves.
# Traefik routes to the V3 rollback (404 on /api/v5) while sellergenome-v4 restarts, so never test before this passes.
# Usage: wait_healthy.sh [container] [timeout_seconds]
set -euo pipefail
container="${1:-sellergenome-v4}"; timeout="${2:-240}"; waited=0
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null)" = "healthy" ]; do
  sleep 3; waited=$((waited + 3))
  if [ "$waited" -ge "$timeout" ]; then echo "NOT HEALTHY after ${timeout}s — check: docker logs --tail 80 $container" >&2; exit 1; fi
done
echo "healthy after ${waited}s"
for host in sellergenome.lehana.in sellergenome.aidhunik.com; do
  printf '%s → ' "$host"
  curl -s --max-time 10 "https://$host/health" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("version"), d.get("status"), "started", d.get("started"))' || echo "unreachable"
done

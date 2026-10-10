#!/usr/bin/env bash
# Rebuild frontend/cc/vendor/sarvam-conv-ai-sdk.js — Sarvam's conversational SDK (browser entry) as one ES module.
#
# Why vendored: the page CSP is script-src 'self', so no CDN; and there is no build step in this repo, so the SDK is
# bundled once here and committed. Pinned version; the tarball's sha512 is checked against the npm registry entry.
# Usage: scripts/build_sarvam_sdk.sh   (needs node/npm; esbuild is fetched by npx)
set -euo pipefail
VERSION="0.0.42"
ESBUILD="0.24.0"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/frontend/cc/vendor/sarvam-conv-ai-sdk.js"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cd "$WORK"
npm pack "sarvam-conv-ai-sdk@$VERSION" --silent >/dev/null
TARBALL="sarvam-conv-ai-sdk-$VERSION.tgz"
EXPECTED="$(npm view "sarvam-conv-ai-sdk@$VERSION" dist.integrity)"
ACTUAL="sha512-$(openssl dgst -sha512 -binary "$TARBALL" | base64 -w0)"
[ "$EXPECTED" = "$ACTUAL" ] || { echo "integrity mismatch: $ACTUAL != $EXPECTED" >&2; exit 1; }
mkdir pkg && tar xzf "$TARBALL" -C pkg
cat > entry.js <<'EOF'
export { ConversationAgent, BrowserAudioInterface, InteractionType, AgentState } from './pkg/package/dist/browser.js';
EOF
# console.log/debug calls are dropped: the SDK logs the signed WebSocket URL on every call.
npx -y "esbuild@$ESBUILD" entry.js --bundle --format=esm --target=es2020 --minify --legal-comments=none \
  --pure:console.log --pure:console.debug --outfile=out.js --log-level=warning
mkdir -p "$(dirname "$OUT")"
{
  echo "/*! sarvam-conv-ai-sdk $VERSION (browser entry) — MIT License, Copyright (c) 2025 Sarvam AI."
  echo " *  Bundled by scripts/build_sarvam_sdk.sh with esbuild $ESBUILD; npm integrity $EXPECTED */"
  cat out.js
} > "$OUT"
echo "wrote $OUT ($(wc -c < "$OUT") bytes)"

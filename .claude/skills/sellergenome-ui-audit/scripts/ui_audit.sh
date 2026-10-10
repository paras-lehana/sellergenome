#!/usr/bin/env bash
# UI audit of the public (anonymous) Command Center pages with the root next-level-website-designer tools:
# screenshots + DOM audit (overflow, overlap, tiny text, targets, contrast, names) at the breakpoints that matter,
# both themes, reduced motion; contrast of the dark token palette; layout-shift probe on the floor.
# Usage: ui_audit.sh OUT_DIR [BASE]      (default BASE https://sellergenome.lehana.in)
# Signed-in pages (/admin, private sellers) and /call/{run} need qa_call.py (sellergenome-live-qa) — the root tools
# can't log in or barge. Chromium only; run sellergenome-live-qa's WebKit passes for iPhone parity.
set -uo pipefail
out="${1:?OUT_DIR}"; base="${2:-https://sellergenome.lehana.in}"; mkdir -p "$out"
S=/root/.claude/skills/next-level-website-designer/scripts
WT=/opt/sellergenome/.worktrees/v5
rc=0
for scheme in dark light; do
  python3 "$S/qa_shots.py" "$base" / /buyer /genome /sarvam --out "$out/$scheme" --reduced-motion --bypass-csp \
    --color-scheme "$scheme" --storage "sg-theme=$scheme" --viewports 390x844 1180x900 1181x900 1440x900 \
    --wait load --settle 1500 --json "$out/$scheme/report.json" || rc=1
done
# contrast.py `css` reads the first (dark) palette only; light pairs need `ratio` with the light hex values.
python3 "$S/contrast.py" css "$WT/frontend/cc/tokens.css" --on "text on panel" --on "dim on panel" --on "dim on panel-3" \
  --on "accent-ink on accent" --on "text-2 on panel-2" --on "onair-ink on onair" --min 4.5 | tee "$out/contrast-dark.txt" || rc=1
python3 "$S/contrast.py" ratio "#ffffff" "#c52a2a" | tee -a "$out/contrast-dark.txt"   # light theme: onair-ink (white) on onair
python3 "$S/cls_probe.py" "$base/" --viewport 390x844 --viewport 1440x900 --fonts > "$out/cls.txt" 2>&1 || true
echo "audit done (exit $rc) → $out — now LOOK at the crops in $out/*/"
exit "$rc"

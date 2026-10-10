---
name: sellergenome-ui-audit
description: Audit the SellerGenome Command Center's visual quality and accessibility on the live site — screenshots and DOM audit at 390/1180/1181/1440 px in both themes with reduced motion, horizontal overflow and card clipping, tap-target and text-size floors, colour contrast of the token palette (the on-air button pair was fixed in 5.5.1), layout shift (CLS), keyboard/focus, WebKit parity — using the root next-level-website-designer QA scripts adapted to this app. Use when asked for a UI/UX/design/accessibility audit or review, before a judged demo, after a CSS/layout change, or when a page "looks off" on a phone or in light mode.
---

# UI audit

Numbers *and* eyes: the scripts find measurable problems; you must still open the crops and look.

## Run

```bash
OUT=/root/data/qa-artifacts/$(date +%F)-ui-audit; mkdir -p $OUT
.claude/skills/sellergenome-ui-audit/scripts/ui_audit.sh $OUT [https://sellergenome.aidhunik.com]
```

It runs `qa_shots.py` (screenshots + readable crops + console/network/Web Vitals + `audit_dom.js`) on `/`, `/buyer`,
`/genome`, `/sarvam` at **390×844, 1180×900, 1181×900, 1440×900** (both sides of the pane-tab breakpoint) in dark and
light, reduced motion, CSP bypassed for the audit; `contrast.py` on the dark token pairs (+ the on-air button pair);
`cls_probe.py` on the floor. Exit 1 = at least one `error` finding. The contrast step reads `tokens.css` from the
**v5 worktree** while screenshots come from the live BASE — for a live-only audit set `WT` in the script to the
production worktree, or read token results as "what the next release will ship".

Then cover what the root tools can't reach:
- **Signed-in pages and the call workspace** (`/admin`, private sellers, `/call/{run}`): skill `sellergenome-live-qa`
  (`qa_call.py` takes screenshots and checks overflow at any size).
- **WebKit** (iPhone parity): the root tools are Chromium-only — run `qa_call.py … webkit 390x844` and
  `qa_floor_barge.py … webkit 390x844`.
- **Keyboard**: Tab through the top bar, nav, a floor tile → Barge in, pane tabs, inspector (Esc closes, focus
  returns), Admin forms; focus must stay visible and not hide under the sticky top bar.
- **States**: empty search, a seller with little data ("Not in file"), long Devanagari/Telugu text, a failed live node,
  network killed mid-run.

## Reading the findings

| Finding | Usually means | Known debt (don't re-report as new unless worse) |
|---|---|---|
| `overflow-x` / element box wider than parent | missing `min-width: 0` or a table forcing width | fixed: WebKit top bar 3 px, /sarvam try form, 330 px evidence column |
| `text-tiny` (< 11 px) | chips/labels at 9.5–10.5 px | 35 declarations in app.css |
| `target-size-24` | small chips/dots | `.ev-chip` 22 px, `.src-dot` |
| `input-font-ios-zoom` | input < 16 px on phones | search 13.5 px, selects 12 px, textarea 12.5 px |
| contrast < 4.5 | token pair or hard-coded colour | fixed 5.5.1: on-air buttons now `--onair-ink` (6.68:1) |
| `content-cut-off` on `aside#rail` at ≤ 1180 px | the closed navigation drawer (translated off-canvas on purpose) | false positive — ignore |
| inline `style="grid-template-columns…"` breaking phones | inline styles beat `@layer phone` | fixed on `/genome` (5.5.1); move layout to classes |
| CLS > 0.1 | content without reserved space, font swap | skeletons reserve most space |

Light theme: `contrast.py css` only reads the dark palette — use `contrast.py ratio "<fg>" "<bg>"` with the light hex
values from tokens.css (both light blocks).

## Report

Group findings by severity (blocks a user / degrades / polish), each with page, viewport, theme, screenshot crop,
cause (file:line) and fix. Fix what is in scope (skill `sellergenome-ui-command-center` for the rules), add a browser
case per real bug to AGENTS.md, re-run the audit, and attach before/after crops to the delivery document.

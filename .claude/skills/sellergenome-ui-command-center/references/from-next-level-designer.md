# What SellerGenome takes from the root `next-level-website-designer` skill

Source: `/root/.claude/skills/next-level-website-designer/` (SKILL.md + references). The root skill is written for
marketing sites, stores and portfolios; the Command Center is an operations dashboard, so only part of it applies.

## Applies

| Root guidance | How it applies here |
|---|---|
| Non-negotiable 4 — tokens are the contract, measure every pair | `frontend/cc/tokens.css` is the only palette; `contrast.py` results recorded in `docs/DESIGN.md` |
| Non-negotiable 2 — phone first | as a **phone layer** (`@layer phone`), not a rewrite: one pane at a time with a segmented control below 1180 px, `100dvh` |
| Non-negotiable 6 — WCAG 2.2 AA | 24 px targets (44 primary), focus visible and not obscured (2.4.11), status messages in live regions, ARIA APG tabs |
| Non-negotiable 8 — honest, strict CSP | `script-src 'self'`; honest labels (Simulated, MOCK, Rules, guess) are this product's "no dark patterns" |
| Non-negotiable 9 — numbers and eyes on the live URL at 390 and 1440 | skills `sellergenome-ui-audit` + `sellergenome-live-qa` |
| Non-negotiable 10 — leave a map | DESIGN.md, AGENTS browser cases, CHANGELOG + version bump |
| `patterns/tech-tool-dashboard.md` | density; one sans + one mono; tabular numerals; never pure #000; status = dot + word, never colour alone; tinted badge fills measured after blending; command palette; sticky table headers with `aria-sort`; JSON viewer; toasts at z-index 99999, deduplicated, **errors persist**; empty states that teach; polling shows "updated N s ago" and pauses on `visibilitychange`; debounce + AbortController; storage in try/catch; Esc closes everything |
| `02-layout-and-responsive.md` grid traps | `min-width: 0`, `minmax(0, 1fr)`, sticky header → `scroll-padding-top`, `viewport-fit=cover` only with safe-area insets |
| `03-colour-and-type.md` | WCAG ratio is the gate, APCA advisory; Devanagari line-height 1.6–1.8, no letter-spacing, `lang="hi"` |
| `04-motion-and-interaction.md` | transform/opacity only, reduced motion, stop what isn't visible (floor waveforms already pause when hidden) |
| `07-performance-mobile.md` | CLS ≤ 0.1 (skeletons reserve space), JS < ~350 KB (cc ≈ 284 KB raw), ≤ 2 font families |
| `12-testing-and-qa.md` | two-pass rule; WebKit parity diff; main-thread stall probe (< 300 ms) on the floor's animated canvases; Playwright tips (avoid `networkidle` — the floor polls and the call page streams; park the mouse; clear toasts) |
| `15-launch-checklist.md` | the Responsive, Colour/type, Accessibility and Functionality sections |
| Scripts | `qa_shots.py`, `audit_dom.js` (via qa_shots), `contrast.py`, `cls_probe.py` — wrapped by `sellergenome-ui-audit/scripts/ui_audit.sh` |

## Deliberately not used

Layered-type hero, craft/signature moves (gloss, accent-as-surface sections, bento, marquee), the 16-craft playbook
(brand voice, WhatsApp doors, reviews), 3D/WebGL, e-commerce/checkout, SEO/AEO/OG (the app is `noindex`), consent bars,
view transitions and speculation rules (client-side SPA), LCP hero images, Save-Data/bfcache (HTML is `no-store`), the
starter kit, `new_site.sh`, `app_icons.py`, galleries and bottom sheets, and the Tailwind-CDN/Prism stack from the
platform's tech-tool guideline (the CSP forbids CDNs).

## Tool caveats verified on this app

- `qa_shots.py` and `cls_probe.py` are Chromium-only; `qa_shots.py` forces `bypass_csp`.
- `contrast.py css` measures only the first (dark) `:root` palette; measure light pairs with `ratio`.
- Signed-in states and `/call/{run}` can't be reached with `--storage`; script a login/barge (sellergenome-live-qa).
- `audit_dom.js` errors on text < 11 px (`text-tiny`) and targets < 24 px (`.ev-chip` 22 px, `.src-dot`), and warns on
  inputs < 16 px (search 13.5 px, selects 12 px) — known debt, not new regressions; decide per change.

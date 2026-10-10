---
name: sellergenome-ui-command-center
description: Build or change SellerGenome's Command Center web UI (frontend/cc — vanilla ES modules, no build step) the project's way — the night control-room design system in tokens.css (colour = data origin, saffron = action, on-air red = live), the PM pre-call brief components, the three-pane call workspace and its phone layer, the architecture bar, inspector, Admin forms, honesty labels, CSP and ?v= versioning, accessibility — distilled from docs/DESIGN.md and the root next-level-website-designer skill's dashboard guidance. Use for any change to a page, panel, component, CSS or layout, for a new view or Admin section UI, and whenever a screen looks cramped, overflows on a phone or loses a label.
---

# Command Center UI

Read `docs/DESIGN.md` for the contract; this skill is the working summary plus the dashboard rules adopted from
`/root/.claude/skills/next-level-website-designer` (see `references/from-next-level-designer.md` for what applies
and what deliberately doesn't). Audit with skill `sellergenome-ui-audit`; verify live with `sellergenome-live-qa`.

## Design system (re-skin = edit `frontend/cc/tokens.css` only)

- **Idea**: a night-shift control room. **Colour only ever means where a value came from** — never decoration.
  Saffron `--accent` is the single action/selection colour; `--onair` red means "a call is happening now" (text on
  it uses `--onair-ink`: dark in dark mode, white in light). Green `--talk`/`--talk-ink` (10.7:1 dark, 5.4:1 light) is
  the floor's main action *Talk as Seller* (5.6.0); *Barge in* on seller tiles is a secondary button to its left. Signature:
  the per-seller **genome strip** (coloured barcode, `ui.js`, `.genome`).
- Surfaces `--bg/--panel/--panel-2/--panel-3`, lines, text `--text/--text-2/--dim`, states `--ok/--warn/--err`;
  origins `--o-live` (Live API), `--o-cache` (ES cache), `--o-db` (local/supplied), `--o-mock` (Mock/Simulated),
  `--o-llm` (Sarvam), `--o-default`, `--o-blank` (dashed); signals `--s-frustration/-confusion/-interest/-rush/-language`.
  Radius 6/10/14, `--t-fast` 150 ms, rail 232 px, top bar 60 px.
- Light theme = `:root[data-theme="light"]` **and** the duplicated `@media (prefers-color-scheme: light)` block —
  edit both. Theme from `localStorage sg-theme` (first paint is dark).
- Fonts self-hosted (CSP): Plex Sans 400–700, Plex Devanagari, Noto Telugu (unicode-range), JetBrains Mono; body 14 px,
  tabular numerals. No CDN, no icon library (Unicode glyphs + inline SVG).
- Badges never by colour alone: origin chip = colour + word; confidence `conf()` = three dots + H/M/G letter (G
  dashed); `.disp` uses + ~ ×; "Not in file" (`.nif`) for missing; SIM/REAL on KPIs.

## Pages → modules

`/` and `/buyer` → `floor.js` · `/call/{run}` → `call.js` (barge console, persona | call | architecture panes, Engines
picker, Payal panel, meeting desk) · `/buyer/call/{id}` → `buyer.js` · `/genome` → `genome.js` · `/sarvam` →
`sarvam.js` + `voiceagent.js` · `/admin` → `admin.js` (17 sections). Shared: `main.js` (router, nav, search `/` ⌘K,
login dialog, theme, clock), `api.js`, `ui.js` (escape, chips, toasts), `json.js`, `graph.js`, `inspector.js`,
`brief.js` (PM blocks: basics, engagement, buyers, plan, record, quality, full brief), `engines.js`, `voice.js`,
`payal-live.js`. Each view exports `mount(root, ctx)` and returns `{destroy}` — abort fetches and stop timers in destroy.
**Talk as Seller workspace (5.13, `talk.js`, `/call/{run}?view=talk`):**
- **Layout:** a `.ws` grid holding the persona (left, full height), the call (middle) and the live log (right). The
  architecture strip runs across the middle and right at about 30 % of the height.
- **Persona cards** are clamped with *Show more*. A value or chip opens the inspector side bar with the value
  highlighted.
- **Log entries** come from run events (`logFromEvent`) and from `POST /runs/{id}/call-events`, which writes to the
  run and to Elasticsearch. Their classes are `k-<kind>` and never `.call`, which collides with the classic layout.
  New entries flash green and scroll into view.
- **Architecture boxes** with `data-node` open that API's receipt; `data-stage` boxes open their events.
- **Mounting:** `main.js` replaces `#view` with `cloneNode(false)` on every route, so stale listeners cannot block a
  restart.
- **Phones:** ≤ 720 px stacks the panes. Loading uses `ui.busyApis()` chips on the top bar: orange breathing while
  running, green when done.

## Layout and the phone layer

Desktop-first rules with every phone rule inside the final `@layer phone` (a layer, not a fork). Workspace panes
370 | 1fr | 440; ≤1400: 330/400; 721–1280: brief grid 2 columns; **≤1180: rail becomes a drawer and `.pane-tabs`
(Persona / Call / Architecture) show one pane** — JS mirrors this with `matchMedia('(min-width: 1181px)')` in call.js,
keep them in sync; ≤720: 56 px top bar, single column, sticky composer. Give every grid/flex child `min-width: 0`,
use `minmax(0, 1fr)`, wrap long values (`overflow-wrap: anywhere`); panes scroll, the page never scrolls sideways.

**Running bar (5.10.0):** `busy(job, on, label)` in `ui.js` drives the 3 px bar at the top (keyed jobs, 120 s safety
timeout, pulses under reduced motion). Jobs in use: `nav` (set on the floor's Talk as Seller / Open persona / Barge in /
Talk as Buyer click, cleared by the next page), `run` (call page while the run prepares; persona page during the flow).
Any new slow navigation should set `nav` before `navigate()` and the target page should clear it.

**Agent picker (5.10.0, `agent.js`):** `agentBarHtml(cat, debugInfo, {place})` — *built-in* vs *Sarvam* per place
(`call` = seller floor + call page, `buyer` = buyer desk + buyer calls), stored as the `bot` engine override; names, app
ids and versions come from `/api/v5/debug/voice`. `switchAgent` in call.js stops what is live and, if the person was
talking, starts the other agent (the click is the user gesture). Never call `renderCall` before `s.call` exists (WebKit
crash fixed in 5.10.1). The call page's 🛠 Debug panel (`paintDebug`) shows models, agents, Sarvam account and mic numbers.

**Talk as Seller page (5.11, `talk.js`, `/call/{run}?view=talk`):**
- **What it is:** one call card on top (Agent dropdown, Start/End, state, transcript), and below it collapsed Persona,
  Conversation plan, Book a meeting and Debug, plus a link to the full workspace (`?view=full`). Barge in keeps
  call.js.
- **One session invariant:** only the chosen agent ever runs. `startCall` refuses while starting or live, and
  `changeAgent` = `endCall` + `startCall`. A built-in reply that lands after End or a switch is dropped (`live` token
  check). The opening is not awaited inside start.
- **Router rule (5.11.1):** main.js gives every page a fresh `#view` container (clone without listeners). Before this,
  a previous page's root listeners kept firing (the floor's agent handler blocked Talk's restart). Don't rely on
  listeners outliving a route.
- **Debug hook:** `?debug` exposes the state as `window.sgTalk` for QA.

## Rules for any UI change

1. Colours only through tokens; a new colour = a new token in all three blocks; measure pairs (dark `contrast.py css`,
   light `contrast.py ratio`, tinted fills after blending) and record them in DESIGN.md.
2. Targets ≥ 24 px, primary ≥ 44 px; phone inputs ≥ 16 px (iOS zoom); body ≥ 14 px on phones; text < 11 px needs a
   reason (35 such declarations exist — the audit flags them).
3. Motion only for state changes, transform/opacity, `--t-fast`; respect `reducedMotion()` and `document.hidden`.
4. **CSP** `script-src 'self'`: no inline scripts, eval, CDN or `blob:` workers (worklets from `/static`); inline
   styles are allowed. New sockets/hosts need `connect-src` in `backend/main.py`.
5. **Versioning**: CSS, `main.js` and routed views load with `?v=${VERSION}`; bump `app.version` in both settings files
   for any frontend change. Static imports of shared modules are unversioned today — don't add `?v=` to a shared
   module in only some importers (duplicate module instances split `session` state).
6. Accessibility: names on icon buttons, Esc + focus return for drawers/dialogs (`inert`/`aria-hidden`), visible focus
   not hidden under the sticky bar, live regions for streaming text, `lang` on Hindi text when you touch it.
7. Honesty labels stay: Simulated floor, MOCK, Rules, guess, Not in file; no empty chips — normalise data server-side
   (the generator's product strings once rendered as empty chips).
8. Run `node --check`, bump version, deploy, then live QA at 1440 and 390 in Chromium **and** WebKit; add a browser
   case for every visible behaviour.

## Known open UI issues (pick up when touching nearby code)

Fixed in 5.5.1: `/genome` phone layout (inline grid → classes) and on-air button contrast (`--onair-ink`).
Added in 5.6.0: real-seller tiles (`realSellerTile`: no wave, transcript or genome strip — only what a live run
confirmed), the call pane's *Persona switch* banner and `.switchline` transcript markers, the Talk-as-Seller note, and
the Admin search box (`#admin-q` → `renderSearch`: every setting in every section + every API, editable in place).
Added in 5.7.0: buyer-desk tiles with *Barge in* + green *Talk as Buyer*, the buyer call page's *VANI adapted* banner,
requirement lead card and *VANI · Sarvam Voice Agent* card (voice via `payal-live.js` `agent:'buyer'`, text chat), the
Admin VANI build card and the `/sarvam` VANI card.
Added in 5.8.0 (PM): `persona.js` at `/persona/{run}` — the "how it is built" flow (APIs called live from the run stream,
attributes from `/runs/{id}/fields`, generator input) then the clean persona page (brief.js cards + plan, green *Start
call* = Talk as Seller on that run); *Open persona* on every seller tile; *Persona page* on the call header. A new page
path needs a `main.py` route **and** the Traefik rule in both compose copies (then recreate — skill release-deploy).
Still open:

shared modules unversioned; no `scroll-padding-top` under the
sticky bar; `viewport-fit=cover` without safe-area insets; pane tabs lack `aria-controls`/arrow keys; toasts can't be
dismissed and errors vanish after ~8 s; dark flash for light-theme users; floor/status polling continues in hidden
tabs; dial thumbs animate `left`; DESIGN.md's V5 section predates 5.2–5.5 components.

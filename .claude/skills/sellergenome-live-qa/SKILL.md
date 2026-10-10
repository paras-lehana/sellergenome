---
name: sellergenome-live-qa
description: Verify SellerGenome on the live public URLs in real browsers — both mirrors (sellergenome.lehana.in, sellergenome.aidhunik.com), Chromium and WebKit, 1440×900 and 390×844, signed in with the operator key and anonymous — with bundled Playwright scripts that start a call, wait for the barge-in, check the pre-call brief, Why call, teleprompter line, Quality panel, never-mention leaks, overflow and console errors, and save screenshots to look at. Use after every deploy, for any UI or user-facing change, when the owner reports something looks wrong, before saying "done", and when running the AGENTS.md browser test cases.
---

# Live browser QA

The owner judges by the live UI, not by ledgers: a task with a UI effect is done only when the relevant browser cases
pass on the live URL **and you looked at the screenshots**. `curl 200` proves nothing about a page.

## Quick matrix after a release

```bash
OUT=/root/data/qa-artifacts/$(date +%F)-<topic>/qa; mkdir -p $OUT
.claude/skills/sellergenome-live-qa/scripts/qa_matrix.sh $OUT syn-91000105
```

= both mirrors × (Chromium 1440, Chromium 390, WebKit 390) signed-in call + Admin, plus the anonymous floor
barge-in on each mirror. File names and JSON tags start with the mirror (`lehana-…`, `aidhunik-…`) so runs don't
overwrite each other; the matrix exits 1 if any page overflowed, logged a console error, leaked a never-mention item or
rendered an empty product chip (turnover bands are allowed). Then **open the PNGs** (Read tool) — headers, brief, call pane, Admin — before reporting.

## Single checks

| Script | What it does |
|---|---|
| `scripts/qa_call.py BASE OUT ENGINE WxH SELLER [--admin]` | operator-signed run for a seller; JSON line with architecture node label, Why call, teleprompter line, Quality text, never-mention hits, products, chip count, overflow, console errors; 3 screenshots |
| `scripts/qa_floor_barge.py BASE OUT ENGINE WxH` | anonymous: floor → first *Barge in* → persona pane, compact plan, full brief, record drawer |
| `scripts/qa_matrix.sh OUT [SELLER]` | the standard matrix |
| `scripts/qa_persona.py BASE OUT ENGINE WxH [REAL_GLID]` | 5.8 persona page: Open persona → live API rows → persona page (PM cards, no quality panel, overflow 0) → How this persona was built (attributes, inspector) → Start call; with REAL_GLID also the signed-out search (never invented) and the real persona header. `node --check` misses runtime errors (a TDZ broke 5.8.2) — always run this after a persona.js change |
| `scripts/qa_admin_prompts.py BASE OUT ENGINE WxH [SELLER]` | 5.9.2 Admin: *Test every API* (rows, chip "N OK · N errors · N skipped"), Prompts (29 cards, Used/Takes effect, broken aggregator edit refused, locality save → edited → reset), search "GST address" opens the prompt, overflow, console errors. Upstream 502 blips show as errors in one run — re-run before calling it a regression |
| `scripts/qa_agent_loading.py BASE OUT ENGINE WxH [FAKE_MIC_WAV]` | 5.10 running bar (Talk as Seller / Open persona / Talk as Buyer) and the agent picker: floor, call header (with a WAV: Talk live → switch to Payal on Sarvam → "listening"), buyer desk + buyer call (VANI ids), overflow, console errors |
| `scripts/qa_all_agents.py BASE OUT ENGINE WxH SELLER AGENT [WAV]` | 5.10.2+: Agent dropdown lists every workspace agent; real or synthetic seller called through AGENT (e.g. Manjula) → "listening"; Engagement insight opens with cited records. Real-seller screenshots stay in qa-artifacts |
| `scripts/qa_talk_simple.py BASE OUT ENGINE WxH AGENT_APP REAL_SELLER [WAV]` | 5.11 Talk as Seller page: auto-start with the built-in agent (opening + a spoken turn), mid-call switch to AGENT → exactly 1 Sarvam session and 0 built-in listeners, a real seller's start variables carry `seller_context`, sections collapsed, error toasts, overflow. Headless WebKit has no mic (OverconstrainedError is ignored there) |
| `scripts/qa_workspace.py BASE OUT ENGINE WxH SELLER [FAKE_MIC_WAV]` | 5.13 Talk as Seller workspace. It checks the loading chips (orange to green), the four areas on one screen at 1440, Show more, and the side bar from a persona value, a log entry and an architecture box. It closes the side bar by outside click (desktop, x=120) or Escape (phone: the drawer is full width). It also checks the call-start and Elasticsearch log entries, the new flash and that nothing overflows. Pass a WAV for Chromium: without a fake microphone it logs "Requested device not found". In WebKit, "due to access control checks" right after a restart is a cancelled fetch, so re-run before calling it a bug |
| `scripts/qa_buyer.py BASE OUT ENGINE WxH [--no-sarvam]` | 5.7 buyer side: desk buttons, Talk as Buyer opening, "please speak English" → banner, requirement lead card, one text turn with VANI on Sarvam, Admin and /sarvam VANI cards, overflow, console errors |
| `scripts/qa_talk.py BASE OUT ENGINE WxH sim\|pinned` | 5.6 flows: floor buttons (Barge in left of Talk as Seller), pinned real-seller tile (operator only), Talk as Seller (bot opens, Auto off), a rushed reply → short bot reply + persona-switch banner/lines, Admin search (pinned/gateway/cooldown), anonymous floor has no real seller. `pinned` screenshots show real seller data — keep them in qa-artifacts only |

Good sellers to test: `syn-91000105` Shaikh (open ticket → open_request plan), `syn-91000101` Qureshi (follow-up),
`syn-91000104` Tamil and `syn-91000112` Punjabi (regional opening), `syn-91000103` Malhotra (rushed, English);
signed-in only: `real-5678901`, `real-45678901`, the owner-provided `real-12345678` (approved GLIDs; live data only when the gateway is up).

## Rules of the road

- **Operator auth**: the scripts read `OPERATOR_API_KEY` from `/srv/sellergenome-v4/seller-persona-v4.env` and
  send it as `x-operator-key`; never print it. PM-session alternative: `POST /api/auth/login` with an `Origin` header.
- **After a restart wait for healthy** (`sellergenome-release-deploy/scripts/wait_healthy.sh`) — Traefik serves V3
  (404 on /api/v5) meanwhile.
- **CSP has no `unsafe-eval`**: Playwright's string `wait_for_function` fails — poll from Python (the scripts do).
- Chromium binary for headless tools: `~/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome` (snap chromium can't
  open `/tmp` or hidden folders). Playwright lives in the system `python3`.
- **Microphone flows** (Talk live, Call Payal): Chromium only with a fake audio file — see skill
  `sellergenome-voice-latency-lab`. WebKit can't fake a microphone; say so as "not validated".
- Overflow: `document.documentElement.scrollWidth - innerWidth` must be 0, and check the element's box for cards
  that clip (a table once widened a card while its parent hid it).
- **Admin QA leaves overrides** (`private/v4/config/genome-apis.json`): finish any Admin API test with *Reset to
  default* — a leftover "Mock" on `company.alias` once blanked every private seller's identity.
- Every bug you or the owner find becomes a numbered case in AGENTS.md → `## Browser Test Cases` (85–89 are the 5.5 ones).
- Artifacts stay out of Git: `/root/data/qa-artifacts/<date>-<topic>/{qa,qa-aidhunik,raw}`; embed 6–12 compressed
  screenshots in the delivery document (skill `sellergenome-delivery-closeout`).

For visual quality (contrast, tap targets, CLS, reduced motion) use skill `sellergenome-ui-audit`.

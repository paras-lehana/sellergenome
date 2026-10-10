---
name: sellergenome-delivery-closeout
description: Close a SellerGenome work session the way the owner requires — one forwardable delivery document per request in docs/tasks/DATE-SLUG/ (owner's input files + ONE report with embedded screenshots), the docs/prompts.md and docs/tasks.md logs, decisions log, AGENTS.md browser cases, VALIDATION/CHANGELOG/README updates, and the final reply with sections A–F ending in the G manager copy block. Use at the end of every substantive task, when the owner sends a multi-point request or files to work from, when asked for a report/summary/write-up of the work, or when the owner says the closeout or summary was missed.
---

# Delivery and closeout

The owner forwards one document to PMs and judges and one text block to managers. Both must be complete, accurate
and no more optimistic than the evidence. The platform protocol (pre-flight "Instructions to be followed" block, A–G)
applies on top of this project-specific shape.

## 1. The task folder (multi-point requests or owner-supplied inputs)

`docs/tasks/<YYYY-MM-DD>-<short-kebab-slug>/` contains **only** the owner's input file(s), untouched (copy them in if
they came from `/root/tmp` or chat), and **one** document
`<YYYY-MM-DD>-<keywords-of-what-changed>-v<APP_VERSION>.md`. No README, findings/, screenshots/ or PDFs in the folder;
raw material goes to `/root/data/qa-artifacts/<folder>/`. Index it in `docs/tasks/README.md` (date · linked title ·
status line with tests and live QA).

Document skeleton (copy from the latest report, e.g. `docs/tasks/2026-10-10-persona-generator-api-guide/`):
title line with version + live URLs + inputs → 1 At a glance (point · status · where to see it) → 2 Business summary
(DONE / PARTIALLY DONE / NOT DONE in the owner's order; extra findings) → 3 Technical summary (files, commands,
commits) → 4 Point by point (asked / found / did / tested / caveat) → 5 topic tables (e.g. what changed) → 6 Owner
decisions (options + recommendation) → 7 Tests and evidence (baseline vs after; live QA table; screenshots) → 8 Pending
→ 9 Suggestions → Appendix A findings, B test runs, C the request verbatim.

Screenshots: `scripts/embed_screenshots.py DOC.md '{{SCREENSHOTS}}' shot1.png "caption" shot2.png "caption" …`
(ffmpeg → ≤640 px JPEG data URIs, ≈20–40 KB each; keep the document ≲1 MB; `CROP=1500` for tall tables). Never embed
screenshots that show secrets or private sellers. Prefer before/after pairs.

## 2. Logs (append-only)

- `docs/prompts.md`: append a row at the end of the index table (ascending; `| N | date | topic | type | status |
  [Entry](#anchor) |`), update "Last updated", and add an entry at the bottom of the file: metadata, original prompt verbatim, pre-flight block, link to the
  delivery document, sections A–F, `#### G. Manager Copy Block` with the same text block.
- `docs/tasks.md`: `## Session: <date> — <title>` with a task table (✅/⚠️/❌) and an HH:MM log, inserted **before**
  `## Agent Deferred`; append pending items to *Agent Deferred* and suggestions to *Agent Suggestions*; mark older rows
  done when this session resolved them.

## 3. Other files that usually change

- `docs/business/decisions.md`: new D-rows at the top of a new version section (never edit history; supersede).
- `AGENTS.md`: new numbered browser cases for every new behaviour and every bug found; operational notes; a link to the
  delivery report under Known Operational Notes.
- `docs/VALIDATION.md` (top section: tests before→after, live runs, live QA, what was *not* validated),
  `CHANGELOG.md`, `README.md` "What V5.x adds", `PRODUCTION_PLAN.md` section, architecture docs touched.
- `/root/SERVICES.md` sellergenome row if operations changed.
- Version bump + `/health` check (skill `sellergenome-release-deploy`).
- Commit + push after the logs are written; the final reply comes last.

## 4. The final reply

Order: pre-flight block (detected values in parentheses) → A Business summary → B Technical summary → C User pointer
status table (`# | Pointer Task (≤5 words) | Original Pointer Text | Status | What Was Done`, DONE first, then
PARTIALLY DONE, NOT DONE, owner's order inside each) → D Additional findings & unrequested work → E Pending & deferred
table → F Agent suggestions → **G: one ```text block, nothing after it** — plain business language (no paths,
commands, hashes, jargon), "What's done" + "Suggested next steps" (+ "Needs a decision" if any), ≈120–250 words, same
facts as A/F. Start the message with one line naming the audience ("Written for: …") when handing over a document.

## Honesty checks before sending

Numbers match the evidence (copy them from the run JSON / test output); simulations and mocks are labelled; anything
not validated is listed as such (e.g. WebKit microphone, real-seller live data when the gateway is offline); owner
decisions are asked, not assumed.

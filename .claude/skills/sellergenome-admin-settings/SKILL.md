---
name: sellergenome-admin-settings
description: Change SellerGenome behaviour through its revisioned settings and the /admin console — config/genome/settings.yaml defaults, Admin overrides with history and rollback, locked settings, write-only keys, budgets and rate limits, engine choices validation — and add a new setting or Admin section end to end. Use when the owner asks for "an option in admin", a default to change, a toggle, a budget/limit increase, a rollback of a setting, when a setting "doesn't take effect", or when you add any configurable behaviour (owner rule — changes are additive options with an Admin default).
---

# Settings and Admin

## How a value is resolved (`backend/genome/settings.py`)

Effective = `config/genome/settings.yaml` (the only place defaults live, commented) + env defaults (only
`upstream.gateway_base_url` ← `UPSTREAM_GATEWAY_BASE_URL`) + Admin overrides in
`PRIVATE_CONFIG_DIR/genome-settings.json` (production `private/v4/config/`). Code reads live with
`settings.get("section.key", fallback)` — most changes apply on next use, no restart.

YAML sections: `app`, `elastic`, `runs`, `upstream`, `sarvam_voice`, `llm`, `engines`, `budgets`, `persona`, `floor`,
`meetings`, `buyer`, `voice_plugin`. (`config/settings.yaml` is the older V1–V4 config — it also holds
`app.version` for `/health` and the global request caps under `security`.)

## Updating (API `PATCH /api/v5/admin/settings`, Admin forms)

- Needs `expected_revision` (mismatch → 409 "reload"); path must exist in the YAML (else 422); type must match the
  default (bool strict; int/float interchangeable).
- `LOCKED`: `llm.organization_id`, `llm.provider_base_url`, `elastic.index_prefix`.
- Enumerations and ranges are validated by `engines.validate_settings` / `SETTING_CHOICES` (422 on bad values) — the
  generic Admin form renders enums as free text, so the server check is the real guard.
- History keeps 100 entries; `POST /admin/settings/rollback {revision: N, expected_revision: R}` restores the
  overrides as of N (0 = clear all) and writes a new revision; any error (incl. unknown revision) answers 409.
  Admin's "Roll back last change" sends revision − 1.
- Some YAML keys are not read by any code, so changing them does nothing: `meetings.timezone`,
  `sarvam_voice.public_chat_turns_per_minute` (12/40 are hard-coded in `api.py`), `runs.max_buyers`. Check with
  `grep -rn '"section.key"' backend/` before promising an Admin change works.

Keys: `PUT /api/v5/admin/secrets/{name}` → `genome-secrets.json` (0600), checked before env; responses carry only
fingerprint, source and JWT expiry. API endpoint overrides (Admin → APIs) live separately in `genome-apis.json` and
**persist** — reset after testing.

## Budgets and limits

Sarvam calls per UTC day: `budgets.public_sarvam_calls_per_day` 1,200 · `operator_…` 3,000 (D21). Per-visitor
in-memory limits in `api.py` `limit()`: runs 20/min (60 operator), turns 40, speak 20, listen-rt 6, agent-chat 12/40,
payal-live 6/h, agent-tool 120. Global request caps 120/240 per minute in `config/settings.yaml` `security`.

## Add a setting end to end

1. Commented key + default in `config/genome/settings.yaml` (a new behaviour defaults to the old behaviour unless the
   owner asked otherwise — options are additive).
2. Read it where used: `settings.get("sec.key", fallback)` — keep the fallback equal to the YAML default (code/doc
   drift exists today, e.g. VAD frames).
3. Enum → `SETTING_CHOICES` in `engines.py` (and the catalogue if a picker shows it); range → `validate_settings`.
4. Admin: the generic `renderSection` (`frontend/cc/admin.js`) renders every key of a section automatically
   (bool → switch, number → input, list/dict → JSON, str → text) and PATCHes only changed paths. A new top-level
   section needs an entry in `SECTIONS` (key = YAML key) and optional `HELP`; sections with custom renderers
   (`engines`, `personagen`, `sarvamvoice`, `voice`, `apis`) need hand-written fields.
5. Test (`test_settings_update_rollback_and_lock` pattern; bad value → 422).
6. Docs: CHANGELOG; decisions log if it is a policy; the relevant architecture doc; AGENTS browser case if visible.

## Admin sections worth knowing

**Search (5.6.0):** the box above the section list searches every setting of every section (also those with custom
pages — Voice & engines, Sarvam voice agent, Voice plug-in) and every API by name, path, label, help text and value;
matches render as editable fields and save through the same revisioned PATCH; *Open API →* jumps to the API row.
`floor.pinned_real_sellers` (JSON list of approved GLIDs) pins real-seller tiles for signed-in operators — set it here,
never in the committed YAML (default `[]`).
`upstream.glid_access` (5.9.6, D51; Admin → APIs → Gateway select, or search "glid access"): `dev` (default) = live
IndiaMART APIs only for the approved test GLIDs; `prod` = any numeric GLID a signed-in user enters. Only these two
values save (422 otherwise). Sign-in stays required in both.

**Prompts (5.9.2, `backend/genome/prompts.py`):** all 29 model prompts in one catalogue (where used, model call label,
source, when an edit applies). Admin edits are stored as `prompt_override` docs in the V5 store (last 10 kept), win over
the code default until *Reset*, and are validated first (format placeholders / `{{ }}` for literal braces,
`FACTORS_HERE`, generator 16k limit, Sarvam's seven sections, `tool:x .` spacing, known `{{variables}}`). Every call site
reads `prompts.text(id)`; a test fails on a new inline `chat_json("…")` prompt — add new prompts to `CATALOG`.
**Gotcha:** if a prompt was edited in Admin, changing the default in code has no visible effect until it is reset.
Sarvam agent prompts reach Sarvam only on *Build on Sarvam*.

**API health (5.9.2–5.9.5):** APIs → *API health* → *Test every API* (`POST /api/v5/admin/apis/test-all {seller_id}`)
runs every live API once (chained inputs filled like *Test*) and shows status, HTTP code, time, key + expiry, note;
it runs by itself after a key / bulk-key save. Buyer-call tools without a URL and `leads.detail` without an offer id
are *Skipped* (not errors). Use it first when the owner says "I put a new key" or "everything is OFF".

APIs (registry, gateway, keys, hit log, health) · Prompts · Persona generator (engine, prompt, eval suite) · Voice & engines (defaults,
Compare voices / listening board) · Bot policy · Runs & concurrency · Call floor simulation · Meetings · Buyer VANI ·
Sarvam budgets · Upstream fan-out · Sarvam voice agent (connect, build, test key, smoke, eval) · Voice plug-in ·
Earlier versions. Anonymous visitors are refused everywhere in `/admin` (case 58).

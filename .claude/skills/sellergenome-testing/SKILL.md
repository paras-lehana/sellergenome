---
name: sellergenome-testing
description: Run and extend SellerGenome's automated tests — the offline pytest suite (≈300 tests, run inside the app image because the host has no project dependencies) and `node --check` for the vanilla-JS frontend — and decide which test a change needs. Use before every commit or release, when a test fails, when adding a feature/parser/endpoint/setting/check, when someone asks "do tests pass", or when writing a regression test for a bug the owner found.
---

# Testing

## Run

```bash
.claude/skills/sellergenome-testing/scripts/run_tests.sh                               # whole suite + node --check
.claude/skills/sellergenome-testing/scripts/run_tests.sh "" tests/test_v55_persona_generator.py -x -q
.claude/skills/sellergenome-testing/scripts/run_tests.sh /opt/sellergenome/.worktrees/production   # another checkout
```

It runs `python -m pytest` in the live image (`docker inspect … sellergenome-v4` → e.g. `sellergenome:5.3.0`) with the
worktree mounted **read-only** at `/work`, `PYTHONPATH=/work`, `DATA_DIR=/tmp/data`, `-p no:cacheprovider`, then runs
`node --check` on `frontend/cc/*.js` and the legacy `frontend/*.js` even when pytest failed. Use the image rather than
`venv_persona`: the venv lacks `sarvam-conv-ai-sdk` and pins a different pydantic, so results can differ from
production. Everything is offline:
no LLM, network, Elasticsearch or Sarvam (fakes like `NoElastic`; `tmp_path` + `monkeypatch.setenv("PRIVATE_CONFIG_DIR")`).

Record the count before and after (history: 220 at 5.0.1 → 249 → 257 → 271 → 275 → 299 at 5.5) in
`docs/VALIDATION.md`.

## Layout

`pyproject.toml` → `testpaths=["tests"]`, no conftest. One file per release line: `test_v5_genome.py` (executor policy,
settings update/rollback/lock, API access rules), `test_v51_live_apis.py` (gateway, keys, parsers on real shapes),
`test_v52_brief_voice.py`, `test_v53_engines_live.py` (engines, tickets, PCM, keyterms), `test_v55_persona_generator.py`
(guide parsers, STS safety, triggers, checks, polish, opening hook, mock cache keys); legacy `test_v4_*`, `test_v3_*`.

## Which test does a change need

| Change | Add/extend |
|---|---|
| Parser or mock for an IndiaMART API | real-shaped payload test (see enquiry `{DESC, RESPONSE}` and STS channel tests) |
| Persona generator check / polish / trigger | a planted-violation test in `test_v55_persona_generator.py` (`_good_output` helper) and, for triggers, the parametrized expectation; tests that look facts up by label prefix break when labels are renamed |
| New endpoint | access rule in `test_v5_genome.py::test_api_access_rules` (anonymous refused where needed, operator allowed) |
| New setting | `test_settings_update_rollback_and_lock` pattern + validation (bad value → 422) |
| Upstream API registry entry | executor policy test (synthetic/private/off) |
| Cache key / version behaviour | prove live keys unchanged and mock keys version-specific |
| Frontend only | `node --check` + a browser case in AGENTS.md + live QA (skill `sellergenome-live-qa`) |

A bug the owner found becomes **both** a regression test (if it is testable offline) and a numbered browser case in
AGENTS.md. Never weaken an assertion to make a test pass without saying so in the summary.

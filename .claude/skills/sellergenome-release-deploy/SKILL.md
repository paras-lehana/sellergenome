---
name: sellergenome-release-deploy
description: Ship a SellerGenome change to the live site safely — version bump in both settings files, CHANGELOG, tests, staged-secret scan, fast-forward the production worktree, restart (or recreate/rebuild) the sellergenome-v4 container, wait for healthy, verify /health on both mirrors, push origin and the sellergenome remote; plus preview containers, image rebuilds, new page routes in Traefik and rollback. Use whenever the user says deploy, release, ship, push live, merge to production, roll back, "is it live", or after any code change that must reach sellergenome.lehana.in / sellergenome.aidhunik.com.
---

# Release and deploy

## The model

| Checkout | Branch | Role |
|---|---|---|
| `/opt/sellergenome/.worktrees/v5` | `feat/v5-command-center` | develop here |
| `.worktrees/production` | `feat/production-control-room` | **mounted read-only by the live container — never edit; only fast-forward** |
| `.worktrees/v3` | `feat/sellergenome-analysis` | V3.1 rollback target (container `seller-persona-v3`, router priority 140) |
| root checkout | `main` | V2 portfolio |

Remotes: `origin` (gitlab paras-lehana/persona-creation) and `sellergenome` (gitlab paras-lehana/sellergenome,
publish `HEAD:main`). Codex also commits to the production branch — fetch and check the production reflog before
merging; `--ff-only` refuses if it moved. The container is named `sellergenome-v4` but serves V5.

## Release checklist (in order)

1. **Version**: bump `config/settings.yaml` `app.version` (drives `/health`, `?v=` asset cache-busting) **and**
   `config/genome/settings.yaml` `app.version` (drives `/api/v5/status`, mock-cache salt, generator cache key).
2. **CHANGELOG**: `## X.Y.Z — YYYY-MM-DD · title` with user-visible bullets; README "What V5.x adds"; AGENTS browser
   cases for new behaviour; `docs/VALIDATION.md` section; PRODUCTION_PLAN section for a new release line.
3. **Tests**: `.claude/skills/sellergenome-testing/scripts/run_tests.sh` (pytest in the image + `node --check`).
4. **Commit** in v5 after `git add` + `.claude/skills/sellergenome-security-privacy-audit/scripts/scan_staged.sh`;
   end the message with the Co-Authored-By line from the session instructions.
5. **Deploy**: `.claude/skills/sellergenome-release-deploy/scripts/release.sh` (refuses a dirty tree; refuses when
   `origin/feat/production-control-room` has commits v5 lacks; ff-only merge;
   `docker restart sellergenome-v4`; waits for healthy; prints both mirrors' `/health` version).
   Add `--push` to push `origin/feat/v5-command-center`, `origin/feat/production-control-room` and `sellergenome/main`.
6. **Verify on the live URL** with skill `sellergenome-live-qa` (never call a UI change done from curl alone).

During a restart Traefik serves the V3 rollback (priority 140), so `/api/v5` returns 404 until the container is
*healthy* — `scripts/wait_healthy.sh` waits for that. Docs-only commits need no restart: `release.sh --docs-only
--push` fast-forwards and pushes, and refuses if anything outside docs/skills/logs changed.

## Which deploy command

| What changed | Do |
|---|---|
| Python/JS/config in the repo | `docker restart sellergenome-v4` (release.sh) — source is mounted, version comes from config |
| env file, labels, healthcheck, volumes | `docker compose -f /srv/sellergenome-v4/docker-compose.yml config --quiet` then `… up -d --no-deps --force-recreate app` (never print the resolved config — it contains secrets) |
| new page path (`/foo`) | add it to the Traefik path rule in **both** `/srv/sellergenome-v4/docker-compose.yml` and `deploy/docker-compose.production.yml` (kept byte-identical), then recreate |
| `requirements.txt`, `integrations/sarvam_mcp/requirements.lock`, Dockerfile | the build context is the **production** worktree, so fast-forward it first; then bump `image:` in both compose copies, `docker compose -f … build app`, recreate. Images hold dependencies only (5.3.0 image still runs 5.5) |

## Preview before production

`assets/compose.v5-preview.yml` runs the v5 worktree as a separate container `sellergenome-v5-preview` (service
`v5-preview`, 30 s healthcheck) on `127.0.0.1:18090` with scratch state in `private/v5-preview/`, Traefik off and **no
Elasticsearch** (the V5 index prefix is locked, so a preview on ES would write into production's indices). It still
shares the rig's IP for the LLM gateway's 30 requests/min. `wait_healthy.sh` checks the production container and
public mirrors — for the preview use `docker inspect -f '{{.State.Health.Status}}' sellergenome-v5-preview` and
`curl 127.0.0.1:18090/health`. **Do not use**
`/srv/sellergenome-v4/docker-compose.preview.yml`: it reuses the production container name and IP and would
replace production. To test unmerged code against live services without a server, use the `docker run --network
container:sellergenome-v4 … -v <worktree>:/work:ro` pattern (see `sellergenome-persona-evaluation/scripts/run_eval.sh`).

## Rollback

- Code: in the production worktree `git reset --hard <last-good-sha>` (ask first — it discards the newer commits from
  the live branch), restart, wait healthy; or `git revert` in v5 and release forward (preferred).
- Routing: `docker compose -f /srv/sellergenome-v4/docker-compose.yml stop app` → V3.1 serves both mirrors,
  but V5-only paths (`/call/`, `/genome`, `/buyer`, `/admin`, `/sarvam`, `/meetings`, `/studio`, `/operations`, `/v4`)
  404 until V5 is back.
- Never restore an older SQLite backup over newer state, never delete indices or volumes, never `down -v`.
  Backups live in `private/v4/backups/`.

## After deploying

Update `/root/SERVICES.md` (sellergenome row) when behaviour or operations changed, and close out with skill
`sellergenome-delivery-closeout`.

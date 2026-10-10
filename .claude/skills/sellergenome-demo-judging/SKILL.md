---
name: sellergenome-demo-judging
description: Prepare and run the SellerGenome demo for the IndiaMART × Sarvam hackathon judges or PMs — map every PS04 deliverable and scoring weight to something visible on the live site, a five-minute demo path with contrasting sellers and a live mid-call switch, Payal and VANI voice in two languages, the one-slide summary, pre-warming caches, a pre-demo health check, and the honest caveats to state. Use when the user mentions demo, judges, judging, pitch, presentation, submission, slide, "walk me through", or wants a script/checklist for showing the product.
---

# Demo and judging

Judging (PS04): fit 25% · switch timing 25% · distinct personas 20% · voice in 2+ languages 15% · past-call evidence
15%. Deliverables: generator on the sample data, a spec per seller with reasons, a live demo, a switch log, one slide.
Details: skill `sellergenome-business-domain`.

## 30 minutes before

1. `/health` on both mirrors shows the current version; container healthy (skill `sellergenome-release-deploy`).
2. No evaluation suites or batch jobs running (LLM gateway 30 requests/min); Sarvam budget left (`/api/v5/status`).
3. Pre-warm **after the last deploy**: barge into the demo sellers once so personas are cached (generator cache keyed
   by input + model + app version, kept 24 h — any version bump invalidates it).
4. Admin overrides reset (Admin → APIs); engines at defaults; microphone works in the demo browser; headphones for
   live voice (echo guard exists, but speakers + mic invite barge-ins).
5. If showing real sellers: gateway up and AK fresh — otherwise say so and use the cached live data honestly.

## Five-minute path

1. **Floor `/`** — "simulated floor (labelled), real preparation behind every tile": point at SIM/REAL KPIs.
2. **Barge into Shaikh Pharma Pack** (`syn-91000105`, frustrated, open Lead Quality ticket): the barge console streams
   real events (cache, API hits with origin, Sarvam, index writes); the architecture bar shows where we are.
3. **Pre-call brief**: "persona by the Persona Generator v0.3 (Sarvam)" — every insight cites a record or fact (click a
   chip → the exact API response); Why call = open request; Quality panel (score + what code corrected).
4. **Contrast**: Malhotra Precision Castings (`syn-91000103`, rushed, English) vs Sundaram Terry Textiles
   (`syn-91000104`, confused, Tamil — opening left for a native speaker). Point at dials and voice.
5. **Live switch**: take over and type/say "jaldi boliye, time nahi hai" → rush switch logged with reason, shorter
   reply; "Hindi mein boliye" → explicit switch bypasses cooldown. Show the switch log rows.
6. **Voice**: 🎙 Talk live with VANI ("answered in ≈2 s"), then bot = Payal → 📞 Call Payal (Sarvam Voice Agents,
   ≈1.3 s answers) — two engines, Hindi + English.
7. **Evidence at scale**: `/genome` search `primary_objection = price`; Admin → Persona generator suite (98.9/100,
   25/25 no critical); Kibana research dashboards for the 908,856-row corpus (PM login).

## Honest caveats to say out loud

The floor is a simulation; real sellers lack Global Context (upstream 404) and need the IndiaMART gateway; quality
scores are rule checks, not human ratings; "Meeting Fixed" in history is a claim; no audio from the dataset was
listened to; regional openings await a native speaker; the bot is a virtual assistant and says so.

## Slide

`/slide` exists but its copy is from V1 and lacks a sample persona spec (a PS04 requirement). Before a judged demo,
update it with: one seller's spec (voice, language, tone, plan with reasons and evidence chips), the switch log
excerpt, and the workflow (APIs → generator on Sarvam → checks → bot/Payal → ES index). The PM brief screenshot
from the latest delivery document is the fastest source.

## Public repository for judges

Judges get a curated copy at `github.com/paras-lehana/sellergenome` with its own history. It is never edited by hand:
change the worktree, then run `python3 .claude/skills/sellergenome-demo-judging/scripts/export_public_repo.py [--push]`.
The script writes `../sellergenome-public` and commits there.
- **Copied:** only git-tracked files on its allow-list (code, tests, config, deploy, data aggregates, AGENTS, CHANGELOG
  and every skill). It never copies `docs/` or the script itself; the public `.gitignore` hides `docs/`.
- **Replaced:**
  - the README with `assets/public-README.md`, the judge-facing entry point (keep it in step with the product);
  - real GLIDs with placeholders (12345678 …);
  - private paths with neutral ones.
- **Stops before committing** when any of these appears:
  - a real GLID, including every Admin-approved one;
  - a private path or a staff name;
  - a real company or competitor name, from every real-seller run in production and from
    `private/public-export-deny.txt`, which is kept outside git (add new names there, never in the script);
  - a key pattern;
  - any value from the live env file or the Admin secrets store, or the gateway address;
  - a broken relative link (links into `docs/` are expected and only counted).
- **Verify the copy:** run the suite on a writable clone (`cp -a ../sellergenome-public /tmp/x`, then pytest as in skill
  `sellergenome-testing` with `/tmp/x` mounted read-write; 377 passed for 5.13.4).
- **Push:** there is no GitHub API token on the rig. The owner creates the empty repository once; after that, `--push`
  works over SSH as paras-lehana.

## After the demo

Collect judge/PM feedback into `docs/tasks/<date>-<slug>/` and turn each point into a task or browser case (skill
`sellergenome-delivery-closeout`).

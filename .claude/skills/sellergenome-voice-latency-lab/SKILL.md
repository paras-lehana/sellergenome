---
name: sellergenome-voice-latency-lab
description: Measure and tune how fast and natural SellerGenome's voice bots answer — the voice lab (scripts/voice_lab.py llm|tts|stt|payal) on live Sarvam, Payal draft-version sweeps, and real-browser end-to-end timing with a fake microphone ("answered in N s" chips, window.__sgTrace). Use when someone says the bot is slow, laggy, cuts people off, talks over them or sounds unnatural; before changing VAD/silence/pace/streaming settings; when comparing models or voices; or when a latency number is needed for a demo, doc or judge.
---

# Voice latency lab

Rule: measure every stage on live services, change one knob, measure again, and keep the raw JSON. Opinions about
"faster" settings were wrong more than once (per-call VAD overrides looked promising and did nothing).

## Where the time goes (5.4 baseline, from the seller's last sound to the bot's first sound)

| Path | Result | Parts |
|---|---|---|
| VANI (built-in) | **1.8–2.1 s** (was ≈3.2 s) | end of turn ≈1.0 s (the 600 ms pause rule) · LLM through the VPS gateway ≈0.45–0.6 s · first audio ≈0.4 s |
| Payal (Sarvam) | **1.2–2.0 s** in browser; 1.33 s median in lab | hybrid VAD 3 frames |

Remaining levers are owner decisions: the LLM gateway on the VPS (+0.2–0.3 s, 30 requests/min/IP) and the pause rule.

## Run the lab (inside the live container — it has the keys)

```bash
docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - llm  < scripts/voice_lab.py   # model × reasoning × script, gateway vs direct
docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - tts  < scripts/voice_lab.py   # streaming knobs, 24 voices (round-trip WER, chars/s, pauses), pace × temperature
docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - stt  < scripts/voice_lab.py   # silence 300–800 ms, fast/balanced, threshold, v4 vs v3-realtime, hesitant utterances
docker exec -i -e PYTHONPATH=/app -w /app -e LAB_VERSION=<draft> -e LAB_VARIANTS=baseline \
  sellergenome-v4 python - payal < scripts/voice_lab.py                                          # raw runtime client: answer ms, barge-in stop, talk-over, split turns
```

Results land in `/tmp/voice-lab-<part>.json` inside the container (`/tmp/voice-lab-samples` for audio); copy them
out at once (`docker cp`) — the container's `/tmp` is a 128 MB tmpfs wiped on restart — to
`/root/data/qa-artifacts/<date>-voice-lab/raw/`. Payal variants other than `baseline` (frames3, frames10, pace125,
asr_v4) are **per-call overrides, which Sarvam ignores** — use them only to re-test that; for real tuning change the
draft and run `baseline` with `LAB_VERSION`. `LAB_ROUNDS` (2), `LAB_PARALLEL`. The lab reads keys from the container env
only (not from keys stored in Admin). Each part spends Sarvam budget — the owner said credits are fine for
testing, but say how many calls you will make.

## Payal sweeps — always on the draft

Apply candidate settings to the **draft** (`configure_agent` through the Voice MCP inside the container — skill
`sellergenome-sarvam-mcp`), run `payal` with `LAB_VERSION=<draft>`, compare medians and talk-over counts, then commit
the winner by putting its values into Admin → Sarvam voice agent (`sarvam_voice.*`) and pressing Build — Build
re-applies the Admin values to the draft, then commits. Callers never hear an untested setting.

## Browser end-to-end (the number users feel)

Chromium only (WebKit cannot fake a microphone): Playwright with
`~/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome` and
`--use-fake-ui-for-media-stream --use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<seller.wav>
--autoplay-policy=no-user-gesture-required`, grant `microphone`. Make the WAV from Bulbul seller lines (two voices,
pauses between lines). `POST /api/v5/runs {"seller_id":"syn-91000103","origin":"floor"}` → open `/call/{run}` →
click `[data-act="mic"]` → read `#transcript .reply-time` chips and `window.__sgTrace` (timing marks of the voice
path). Payal: bot = Payal → 📞 Call Payal → "Payal answered in … s" chips (energy-based, ignores overlapping speech).
Poll from Python — the CSP forbids Playwright's string `wait_for_function`.

## Findings worth not re-learning

- Bulbul first audio is 184–211 ms for every streaming setting; opening the socket costs ≈125 ms (so open it during the LLM call).
- Bulbul v4 reads romanised Hinglish exactly — transliteration only for v3.
- Realtime final ≈ silence + 145 ms; 450 ms hesitations split 6/6 turns at 300 ms, 1/6 at 500, 0/6 at 600 → keep 600.
- Payal hybrid-vad: 6 frames 1.42 s, 4 → 1.36 s, **3 → 1.33 s (1.23–1.40)**, no talk-over; barge-in stops ≈0.6 s.
- `sarvam-105b-conversations` ≈300 ms via the gateway regardless of reasoning effort (no reasoning tokens).
- Sarvam streams comfort noise (RMS 150–430): detect bot speech with energy > 900.

Write results into `docs/architecture/voice-engines.md` (lab table) and the delivery document.

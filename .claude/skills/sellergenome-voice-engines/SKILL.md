---
name: sellergenome-voice-engines
description: Choose, configure or change the speech engines SellerGenome uses — Sarvam Bulbul TTS (v4-flash, v3; streamed WebSocket or REST; raw PCM via Web Audio), Saaras STT (realtime WebSocket proxy or clip; saaras:v4, v3-realtime, saarika), Mayura/Sarvam Translate, transliteration, recognition keyterms, persona voice mapping, Admin → Voice & engines defaults and the per-page ⚙ Engines pickers. Use when a voice sounds wrong or late, the bot mis-hears words, a model id is rejected, you add a speech feature or engine option, touch voice.js/engines.js/live_speech.py/sarvam.py speech code, or the owner asks for "latest Sarvam models".
---

# Voice engines (Bulbul · Saaras · Mayura)

Owner rule (5.3): every engine change is an **addition** — a new option with an Admin default and a per-place
picker — never a silent replacement. Catalogue: `backend/genome/engines.py`; speech: `backend/genome/sarvam.py`,
`live_speech.py`; browser: `frontend/cc/{voice,engines,audio-worklets}.js`. Measured facts and sources:
`docs/architecture/voice-engines.md`.

## Model ids (from api.sarvam.ai's own validation errors — there is no `bulbul:v4`)

| Kind | Ids | Default |
|---|---|---|
| TTS | `bulbul:v4-flash` (224 voices `<name>_<lang>_<style>`), `bulbul:v3` (37 voices) | v4-flash, streamed |
| STT clip | `saaras:v4`, `saaras:v3`, `saarika:v2.5` | saaras:v4 |
| STT realtime | `saaras:v4` (keyterms), `saaras:v3-realtime` | saaras:v4 |
| STT mode | codemix, transcribe, translit, verbatim, translate | codemix |
| Translate | `mayura:v1` (modern-colloquial), `sarvam-translate:v1` | mayura:v1 |
| LLM | `sarvam-105b-conversations`, `sarvam-105b` | -conversations (skill `sellergenome-sarvam-llm`) |

Bulbul v4 flash: same contract as v3 (pace 0.5–2, temperature ≤ 1, **no pitch/loudness**); v3 voice names are
rejected by v4 — persona voices map v3→v4 (`ritu→ritu_hi_customer`, `priya→ishita_enhi_customer`, …); the simulated
seller always gets a voice of the other gender (`contrast_voice`).

## Settings (`config/genome/settings.yaml` → `engines.*`, validated by `engines.validate_settings`, 422 on bad values)

`bot` (builtin | sarvam_agent) · `tts_model` · `tts_voice` (auto) · `tts_transport` (stream | rest) · `listen`
(realtime | clip) · `stt_model` · `stt_realtime_model` · `stt_mode` · `translate_model` · `transliterate`
(auto | always | never — auto = only Bulbul v3, saves ≈670 ms/line, D29) · `keyterms` (seller calls) ·
`buyer_keyterms` (empty — seller words once made the buyer desk invent "TrustSEAL", D32) ·
`realtime.{stream_type fast, silence_duration_ms 600, threshold 0.4, min_speech_duration_ms 250, prefix_padding_ms
300, max_seconds 90, sessions_per_visitor_per_minute 6}`. Pickers per place (`engines.LOCATIONS`; browser
`localStorage sg-engines`): call workspace (all), buyer desk (speech + listening), `/sarvam` (speech + translation).

## The realtime paths

- **Streamed speech**: `POST /api/v5/speak/stream` (validates model/voice, one budget call, starts an `AudioBuffer`
  on `wss://api.sarvam.ai/text-to-speech/ws?model=…` config→text→flush, 24 kHz) → `GET
  /api/v5/speak/stream/{ticket}.pcm|.mp3` relays chunks (ticket 60 s, 2 uses for Safari). The browser plays **raw PCM
  (`linear16`) through Web Audio** (`PcmStream`) — `<audio>` waited 0.75–0.93 s for streamed MP3, PCM starts ≈0.4 s.
- **Speech inside the turn (D30)**: `/calls/{id}/turn|auto` accept `speak`; the Bulbul socket opens during the LLM call
  and the reply carries `speech` links — no separate `/speak/stream` POST.
- **Realtime listening**: `POST /api/v5/listen/ticket {place}` (keyterms per place, ticket 30 s) → `WS
  /api/v5/listen/realtime` (origin-checked) → proxy to `wss://api.sarvam.ai/speech-to-text-realtime/ws` with frames
  wrapped as `audio_input` (≤16,000 bytes) → relays `vad.speech_start`, partials, finals. Browser worklet `sg-pcm16k`
  sends 16 kHz PCM in 100 ms frames; speaking over the bot pauses it (barge-in) unless it is the bot's own echo.
  The key never reaches the browser (Sarvam's browser option puts it in a WS subprotocol, so we proxy).
- **Clip**: `POST /api/v5/listen` — `stt_mime()` strips `;codecs=opus` (Chrome's label made Sarvam return 400 in 5.2).

Limits: `/speak`, `/speak/stream`, `/translate`, `/listen` and `/sarvam/try` share a 20/min "speak" bucket per
visitor; `/calls/{id}/turn|auto` allow 40/min. Speech links created inside a turn live 90 s. Keyterms are capped at 50
terms × 64 chars and are sent only for `saaras:v4`.

**Talk live diagnosis (5.9.7):** `RealtimeListener.start()` creates and resumes the AudioContext *before* any await (a
context made after the ticket request and the permission prompt could stay suspended → 0 frames, Sarvam hears nothing).
3 s in, `check()` warns "audio engine paused / mic muted / mic silent (device name)" and sends `{event:"stats"}` to the
server; the `sarvam.stt` hit note then reads "N frames from the browser · page check: frames, peak, Hz, audio engine
state, mic state, browser". Read that note first when someone says "mic allowed but nothing happens". Reproduce with
Chromium `--use-file-for-fake-audio-capture=<16 kHz WAV>` (make one with `/api/v5/speak` + ffmpeg).

## Changing something safely

1. Add the option to the catalogue (`engines.py`) and `SETTING_CHOICES`; default in `settings.yaml` with a comment.
2. Keep the old option selectable; wire the picker place(s).
3. Budget: every Sarvam request takes one unit (`sarvam.spend`); rate limits per visitor in `api.py` (`limit`).
4. Test offline (`tests/test_v53_engines_live.py` pattern), then measure live with skill
   `sellergenome-voice-latency-lab`, then browser cases 73–84 (fake microphone; WebKit can't fake one).
5. Record the decision (D-row) and the measurement in `docs/architecture/voice-engines.md`.

Not adopted (documented reasons): pronunciation dictionaries (docs disagree on v4 support), dubbing, document
intelligence. Code fallbacks differ from settings (6 frames; 700 ms/0.5) — settings win when present.

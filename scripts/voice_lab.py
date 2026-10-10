"""Voice lab (5.4): measure what makes SellerGenome's calls fast and natural — run inside the app container.

    docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - llm  < scripts/voice_lab.py
    … - tts | stt | payal  (each part writes /tmp/voice-lab-<part>.json inside the container)

Parts:
- llm   — VANI bot-turn latency through llm.lehana.in for model × reasoning (and Sarvam's direct streaming TTFT).
- tts   — Bulbul streaming: first-audio latency per chunking/codec/rate; naturalness proxies per voice/pace/temperature
          (round-trip intelligibility with Saaras v4, speaking rate); samples saved for a listening board.
- stt   — Saaras realtime: end-of-turn latency, split turns and accuracy per VAD setting on utterances with pauses.
- payal — Payal live calls with per-call overrides (VAD frames, pace, …): answer latency, splits, barge-in stop time.
Only synthetic text is sent; keys come from the container environment and are never printed.
"""

from __future__ import annotations

import asyncio
import audioop
import base64
import io
import json
import os
import re
import statistics
import sys
import time
import wave

import httpx
import websockets

SARVAM = "https://api.sarvam.ai"
KEY = os.environ.get("SARVAM_V4_API_KEY", "")
H = {"api-subscription-key": KEY}
OUT = "/tmp"


def ms(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs)) if xs else None


def words(text: str) -> list[str]:
    return [w for w in re.sub(r"[^\w\s]", " ", (text or "").lower()).split() if w]


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return round(d[len(h)] / max(1, len(r)), 3)


# ---------------------------------------------------------------------------------------------- LLM

VANI_SYSTEM = ("You are Payal, IndiaMART's virtual assistant on a sales call with a seller. Goal: understand the seller's need and book "
               "a meeting with a human IndiaMART executive. Never claim to be human; if asked, say you are IndiaMART's virtual assistant. "
               "Never quote prices, discounts, ROI or guarantees — say the executive will explain. If the seller wants to stop, thank them and end. "
               "Speak in {lang}. Keep the reply under 14 words, one question at most. Tone: warmth medium, empathy medium, formality respectful. "
               "Return JSON {{\"reply\": str, \"stage\": \"greeting|qualifying|package|meeting|wrap_up\", \"meeting\": null | {{\"mode\": "
               "\"online|in_person\", \"when\": str, \"where\": str}}, \"outcome\": null | {{\"label\": \"meeting_fixed|callback|not_interested|escalated\", \"detail\": str}}}}.")
LANG_ROMAN = "romanised Hinglish (Latin script) like 'Ji bilkul, main samajh sakti hoon'"
LANG_DEVA = ("Hinglish as people speak it: Hindi words in Devanagari and English business words in English, like "
             "'जी बिल्कुल, आपकी company के लिए एक meeting रख लेते हैं'")
VANI_USER = json.dumps({"seller_facts": {"company": "Malhotra Precision Castings", "city": "Ludhiana",
                                         "trigger": "Bought a 20-lead top-up yesterday", "summary": "Paid manufacturer, busy in work hours, prefers 6-7 PM calls",
                                         "packages": ["Star Supplier", "Leader Supplier"]},
                        "conversation": [{"who": "bot", "text": "Namaste Rohit ji, main IndiaMART se Payal bol rahi hoon. Kya do minute baat ho sakti hai?"},
                                         {"who": "seller", "text": "हाँ जी बोलिए, अभी थोड़ा busy हूँ, जल्दी बताइए।"}]}, ensure_ascii=False)


async def llm_gateway(client, model, effort, lang, max_tokens=350):
    body = {"provider": "custom", "model": model, "base_url": "https://api.sarvam.ai/v1", "api_key": KEY, "ref": "sellergenome-voice-lab",
            "system_prompt": VANI_SYSTEM.format(lang=lang), "messages": [{"role": "user", "content": VANI_USER}], "temperature": 0.4,
            "max_tokens": max_tokens, "json_mode": True, "extra_body": {"response_format": {"type": "json_object"}}}
    if effort:
        body["extra_body"]["reasoning_effort"] = effort
    t0 = time.monotonic()
    r = await client.post(os.getenv("LLM_SERVICE_URL", "https://llm.lehana.in").rstrip("/") + "/byok",
                          headers={"X-Internal-Key": os.getenv("LLM_INTERNAL_KEY", "")}, json=body)
    took = ms(t0)
    data = r.json()
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    usage = data.get("usage") or {}
    return {"ms": took, "status": r.status_code, "reply": str(msg.get("content") or "")[:200],
            "reasoning_chars": len(msg.get("reasoning_content") or ""), "completion_tokens": usage.get("completion_tokens")}


async def llm_direct_stream(client, model, effort, lang):
    """Reference only (platform rule routes production inference through llm.lehana.in): Sarvam's own streaming TTFT."""
    body = {"model": model, "stream": True, "temperature": 0.4, "max_tokens": 350,
            "messages": [{"role": "system", "content": VANI_SYSTEM.format(lang=lang)}, {"role": "user", "content": VANI_USER}]}
    if effort:
        body["reasoning_effort"] = effort
    t0 = time.monotonic()
    first = first_content = None
    text = ""
    async with client.stream("POST", f"{SARVAM}/v1/chat/completions", headers=H, json=body) as r:
        async for line in r.aiter_lines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            delta = (json.loads(line[5:])["choices"] or [{}])[0].get("delta") or {}
            if first is None:
                first = ms(t0)
            if delta.get("content"):
                if first_content is None:
                    first_content = ms(t0)
                text += delta["content"]
    return {"first_byte_ms": first, "first_content_ms": first_content, "total_ms": ms(t0), "reply": text[:160]}


async def part_llm(n=5):
    out = {"gateway": [], "direct_stream": []}
    async with httpx.AsyncClient(timeout=60) as client:
        for model in ("sarvam-105b-conversations", "sarvam-105b"):
            for effort in (None, "low", "medium"):
                for lang_name, lang in (("roman", LANG_ROMAN), ("devanagari", LANG_DEVA)):
                    if model == "sarvam-105b" and lang_name == "devanagari":
                        continue
                    runs = [await llm_gateway(client, model, effort, lang) for _ in range(n)]
                    row = {"model": model, "reasoning": effort or "omitted", "script": lang_name, "median_ms": med([x["ms"] for x in runs]),
                           "min_ms": min(x["ms"] for x in runs), "max_ms": max(x["ms"] for x in runs),
                           "reasoning_chars": med([x["reasoning_chars"] for x in runs]), "tokens": med([x["completion_tokens"] for x in runs]),
                           "ok": sum(x["status"] == 200 for x in runs), "sample": runs[-1]["reply"]}
                    out["gateway"].append(row)
                    print(json.dumps(row, ensure_ascii=False), flush=True)
        for effort in (None, "low"):
            runs = [await llm_direct_stream(client, "sarvam-105b-conversations", effort, LANG_DEVA) for _ in range(3)]
            row = {"model": "sarvam-105b-conversations", "reasoning": effort or "omitted", "first_content_ms": med([x["first_content_ms"] for x in runs]),
                   "total_ms": med([x["total_ms"] for x in runs]), "sample": runs[-1]["reply"]}
            out["direct_stream"].append(row)
            print("direct", json.dumps(row, ensure_ascii=False), flush=True)
    return out



# ---------------------------------------------------------------------------------------------- TTS

LINES = {
    "short": "जी बिल्कुल, शाम 6-7 PM का time ठीक रहेगा?",
    "medium": "जी Rohit जी, आपने कल 20 buy leads का top-up लिया है। उसी के बारे में दो मिनट बात कर सकते हैं क्या?",
    "roman": "Ji bilkul, main samajh sakti hoon. Kya kal subah gyarah baje online meeting rakh lein?",
}
V4_VOICES = ["ritu_hi_customer", "ritu_hi_conversation", "ritu_hi_customer_warm", "ishita_enhi_customer", "ishita_enhi_customer_expressive",
             "simran_enhi_customer", "shalini_enhi_customer", "kavya_hi_conversational", "aarti_hi_customer", "sanchita_hi_assistant",
             "roopa_hi_conversational", "priya_hi_recovery", "shubh_hi_customer", "shubh_enhi_banking", "sunny_enhi_customer",
             "rahul_hi_conversational", "aditya_hi_sales", "tarun_hi_conversational"]
V3_VOICES = ["ritu", "priya", "kavya", "ishita", "shubh", "rahul"]
SAMPLES = "/tmp/voice-lab-samples"


async def tts_stream(text, cfg, model="bulbul:v4-flash"):
    """One streamed synthesis; returns connect/first/total ms and the audio bytes (or the error)."""
    t0 = time.monotonic()
    try:
        async with websockets.connect(f"wss://api.sarvam.ai/text-to-speech/ws?model={model}&send_completion_event=true",
                                      additional_headers=H, open_timeout=10) as ws:
            connect = ms(t0)
            data = {"language_code": "hi-IN", "speaker": "ritu_hi_customer", "output_audio_codec": "mp3", "speech_sample_rate": 24000, **cfg}
            await ws.send(json.dumps({"type": "config", "data": data}))
            t1 = time.monotonic()
            await ws.send(json.dumps({"type": "text", "data": {"text": text}}))
            await ws.send(json.dumps({"type": "flush"}))
            first, audio, chunks = None, b"", 0
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 20))
                if m["type"] == "audio":
                    chunks += 1
                    audio += base64.b64decode(m["data"]["audio"])
                    first = first if first is not None else ms(t1)
                elif m["type"] == "event":
                    break
                elif m["type"] == "error":
                    return {"error": str(m.get("data", {}).get("message", m))[:160]}
            return {"connect_ms": connect, "first_ms": first, "total_ms": ms(t1), "chunks": chunks, "bytes": len(audio)}
    except Exception as error:  # noqa: BLE001
        return {"error": f"{type(error).__name__}: {str(error)[:120]}"}


async def tts_rest(client, text, speaker, model="bulbul:v4-flash", pace=1.0, temperature=None):
    body = {"text": text, "target_language_code": "hi-IN", "speaker": speaker, "model": model, "pace": pace, "speech_sample_rate": 16000}
    if temperature is not None:
        body["temperature"] = temperature
    t0 = time.monotonic()
    r = await client.post(f"{SARVAM}/text-to-speech", headers=H, json=body)
    took = ms(t0)
    if r.status_code != 200:
        return None, {"error": r.text[:160], "ms": took}
    return base64.b64decode(r.json()["audios"][0]), {"ms": took}


def audio_stats(wav: bytes) -> dict:
    """Duration, leading silence and the share of quiet 20 ms frames (a proxy for pauses / choppiness)."""
    with wave.open(io.BytesIO(wav)) as w:
        rate, frames = w.getframerate(), w.readframes(w.getnframes())
    step = int(rate * 0.02) * 2
    levels = [audioop.rms(frames[i:i + step], 2) for i in range(0, len(frames) - step, step)]
    loud = [l > 300 for l in levels]
    lead = next((i for i, x in enumerate(loud) if x), len(loud)) * 20
    inner = loud[loud.index(True): len(loud) - loud[::-1].index(True)] if any(loud) else []
    pauses, run = [], 0
    for x in inner:
        if not x:
            run += 1
        elif run:
            pauses.append(run * 20)
            run = 0
    return {"duration_s": round(len(frames) / 2 / rate, 2), "lead_silence_ms": lead,
            "quiet_share": round(1 - sum(inner) / max(1, len(inner)), 3), "longest_pause_ms": max(pauses or [0])}


async def stt_rest(client, wav: bytes, mode="transcribe", model="saaras:v4"):
    r = await client.post(f"{SARVAM}/speech-to-text", headers=H, files={"file": ("a.wav", wav, "audio/wav")},
                          data={"model": model, "language_code": "hi-IN", "mode": mode})
    return r.json().get("transcript", "") if r.status_code == 200 else ""


async def part_tts():
    os.makedirs(SAMPLES, exist_ok=True)
    out = {"latency": [], "voices": [], "pace_temperature": [], "script": []}
    text = LINES["medium"]
    # 1. Streaming knobs (first audio after the text is sent; connect measured separately).
    grid = [{}, {"speech_sample_rate": 16000}, {"speech_sample_rate": 22050}, {"output_audio_bitrate": "32k"},
            {"output_audio_bitrate": "128k"}, {"output_audio_codec": "opus", "speech_sample_rate": 24000},
            {"output_audio_codec": "linear16", "speech_sample_rate": 16000}]
    grid += [{"min_buffer_size": b} for b in (20, 30, 50, 100)] + [{"max_chunk_length": c} for c in (30, 50, 100, 150, 250)]
    for model in ("bulbul:v4-flash", "bulbul:v3"):
        for cfg in grid if model == "bulbul:v4-flash" else [{}, {"min_buffer_size": 30, "max_chunk_length": 50}]:
            c = dict(cfg) if model == "bulbul:v4-flash" else {**cfg, "speaker": "ritu"}
            runs = [await tts_stream(text, c, model) for _ in range(3)]
            ok = [r for r in runs if "error" not in r]
            row = {"model": model, "cfg": cfg, "connect_ms": med([r["connect_ms"] for r in ok]), "first_ms": med([r["first_ms"] for r in ok]),
                   "total_ms": med([r["total_ms"] for r in ok]), "chunks": med([r["chunks"] for r in ok]), "bytes": med([r["bytes"] for r in ok]),
                   "errors": [r["error"] for r in runs if "error" in r][:1]}
            out["latency"].append(row)
            print("latency", json.dumps(row, ensure_ascii=False), flush=True)
    async with httpx.AsyncClient(timeout=60) as client:
        # 2. Voices: intelligibility (round trip through Saaras v4) and pacing.
        for model, voices in (("bulbul:v4-flash", V4_VOICES), ("bulbul:v3", V3_VOICES)):
            for v in voices:
                wav, meta = await tts_rest(client, text, v, model)
                if not wav:
                    out["voices"].append({"model": model, "voice": v, **meta})
                    continue
                with open(f"{SAMPLES}/voice-{model.split(':')[1]}-{v}.wav", "wb") as f:
                    f.write(wav)
                heard = await stt_rest(client, wav)
                st = audio_stats(wav)
                row = {"model": model, "voice": v, "rest_ms": meta["ms"], "wer": wer(text, heard), "chars_per_s": round(len(text) / st["duration_s"], 1), **st,
                       "heard": heard[:120]}
                out["voices"].append(row)
                print("voice", json.dumps(row, ensure_ascii=False), flush=True)
        # 3. Pace × temperature on three candidate voices.
        for v in ("ritu_hi_customer", "ishita_enhi_customer", "simran_enhi_customer"):
            for pace in (0.95, 1.05, 1.15):
                for temp in (0.3, 0.6, 0.9):
                    wav, meta = await tts_rest(client, text, v, pace=pace, temperature=temp)
                    if not wav:
                        continue
                    with open(f"{SAMPLES}/pt-{v}-p{pace}-t{temp}.wav", "wb") as f:
                        f.write(wav)
                    heard = await stt_rest(client, wav)
                    st = audio_stats(wav)
                    row = {"voice": v, "pace": pace, "temperature": temp, "wer": wer(text, heard), "chars_per_s": round(len(text) / st["duration_s"], 1), **st}
                    out["pace_temperature"].append(row)
                    print("pt", json.dumps(row, ensure_ascii=False), flush=True)
        # 4. Script: Devanagari code-mix vs romanised Hinglish sent straight to Bulbul (would skip the transliteration call).
        for v in ("ritu_hi_customer", "ishita_enhi_customer"):
            for name in ("roman",):
                wav, meta = await tts_rest(client, LINES[name], v)
                with open(f"{SAMPLES}/script-{v}-{name}.wav", "wb") as f:
                    f.write(wav)
                heard = await stt_rest(client, wav, mode="translit")
                out["script"].append({"voice": v, "input": name, "wer_vs_roman": wer(LINES[name], heard), "heard": heard[:120], **audio_stats(wav)})
                print("script", json.dumps(out["script"][-1], ensure_ascii=False), flush=True)
            t0 = time.monotonic()
            r = await client.post(f"{SARVAM}/transliterate", headers=H, json={"input": LINES["roman"], "source_language_code": "hi-IN", "target_language_code": "hi-IN"})
            deva = r.json().get("transliterated_text", "")
            translit_ms = ms(t0)
            wav, meta = await tts_rest(client, deva, v)
            with open(f"{SAMPLES}/script-{v}-transliterated.wav", "wb") as f:
                f.write(wav)
            heard = await stt_rest(client, wav, mode="translit")
            out["script"].append({"voice": v, "input": "transliterated", "transliterate_ms": translit_ms, "devanagari": deva,
                                  "wer_vs_roman": wer(LINES["roman"], heard), "heard": heard[:120], **audio_stats(wav)})
            print("script", json.dumps(out["script"][-1], ensure_ascii=False), flush=True)
    return out


# ---------------------------------------------------------------------------------------------- STT

UTTERANCES = [("हाँ जी बोलिए", "अभी थोड़ा busy हूँ, जल्दी बताइए।"), ("ठीक है", "कल सुबह ग्यारह बजे online meeting रख लीजिए।"),
              ("देखिए मुझे leads तो मिल रहे हैं", "पर quality अच्छी नहीं है।")]


def tokens(text: str) -> list[str]:
    import unicodedata
    return "".join(c if unicodedata.category(c)[0] in "LMN" else " " for c in (text or "").lower()).split()


def wer_u(ref: str, hyp: str) -> float:
    r, h = tokens(ref), tokens(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return round(d[len(h)] / max(1, len(r)), 3)


async def seller_clips(client):
    """Each utterance as 16 kHz PCM with a 250 ms or 450 ms hesitation between its two halves."""
    out = []
    for voice in ("rahul_hi_conversational", "kavya_hi_conversational"):
        for a, b in UTTERANCES:
            pa, pb = [], []
            for part, store in ((a, pa), (b, pb)):
                wav, _ = await tts_rest(client, part, voice)
                with wave.open(io.BytesIO(wav)) as w:
                    store.append(w.readframes(w.getnframes()))
            for gap in (250, 450):
                out.append({"voice": voice, "text": f"{a} {b}", "gap_ms": gap, "pcm": pa[0] + b"\0" * (32 * gap) + pb[0]})
    return out


async def realtime_once(clip, model, stream_type, silence, threshold):
    q = {"language_code": "hi-IN", "model": model, "mode": "codemix", "encoding": "linear16", "sample_rate": "16000", "stream_type": stream_type,
         "silence_duration_ms": str(silence), "threshold": str(threshold), "min_speech_duration_ms": "250", "prefix_padding_ms": "300"}
    if model == "saaras:v4":
        q["keyterms"] = json.dumps(["IndiaMART", "Payal", "BuyLead", "leads", "online meeting"], ensure_ascii=False)
    from urllib.parse import urlencode
    pcm = clip["pcm"] + b"\0" * 64000  # 2 s of silence after the seller stops
    t0 = time.monotonic()
    finals, first_partial, speech_end_at = [], None, None
    async with websockets.connect("wss://api.sarvam.ai/speech-to-text-realtime/ws?" + urlencode(q), additional_headers=H, open_timeout=10) as ws:
        async def send():
            nonlocal speech_end_at
            for i in range(0, len(pcm), 3200):
                if i >= len(clip["pcm"]) and speech_end_at is None:
                    speech_end_at = time.monotonic()
                await ws.send(json.dumps({"event": "audio_input", "audio": base64.b64encode(pcm[i:i + 3200]).decode()}))
                await asyncio.sleep(0.1)
        task = asyncio.create_task(send())
        try:
            while True:
                ev = json.loads(await asyncio.wait_for(ws.recv(), 3.5 if task.done() else 15))
                if ev.get("event") == "transcript.partial" and first_partial is None:
                    first_partial = ms(t0)
                elif ev.get("event") == "transcript.final":
                    finals.append({"at": time.monotonic(), "text": ev.get("text", "")})
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            pass
        await asyncio.gather(task, return_exceptions=True)
    last = finals[-1]["at"] if finals else None
    return {"finals": len(finals), "final_after_speech_ms": round((last - speech_end_at) * 1000) if last and speech_end_at else None,
            "first_partial_ms": first_partial, "wer": wer_u(clip["text"], " ".join(f["text"] for f in finals)),
            "heard": " | ".join(f["text"] for f in finals)[:160]}


async def part_stt():
    async with httpx.AsyncClient(timeout=60) as client:
        clips = await seller_clips(client)
    grid = [("saaras:v4", "fast", s, 0.4) for s in (300, 400, 500, 600, 800)]
    grid += [("saaras:v4", "balanced", 500, 0.4), ("saaras:v3-realtime", "fast", 500, 0.4), ("saaras:v4", "fast", 500, 0.3), ("saaras:v4", "fast", 500, 0.6)]
    out = []
    for model, stype, silence, threshold in grid:
        runs = []
        for i in range(0, len(clips), 4):  # four sessions at a time
            runs += await asyncio.gather(*(realtime_once(c, model, stype, silence, threshold) for c in clips[i:i + 4]))
        row = {"model": model, "stream_type": stype, "silence_ms": silence, "threshold": threshold,
               "final_after_speech_ms": med([r["final_after_speech_ms"] for r in runs]),
               "split_250": sum(r["finals"] > 1 for r, c in zip(runs, clips) if c["gap_ms"] == 250),
               "split_450": sum(r["finals"] > 1 for r, c in zip(runs, clips) if c["gap_ms"] == 450),
               "missed": sum(r["finals"] == 0 for r in runs), "wer": round(statistics.mean(r["wer"] for r in runs), 3),
               "first_partial_ms": med([r["first_partial_ms"] for r in runs]), "sample": runs[1]["heard"]}
        out.append(row)
        print("stt", json.dumps(row, ensure_ascii=False), flush=True)
    return out


# ---------------------------------------------------------------------------------------------- Payal

ORG = "01a110b3-f038-7adb-b627-6863a3785c40"
APP = "Payal---Ind-7ae5e937-a82f"
PAYAL_TURNS = [("हाँ जी बोलिए", "अभी थोड़ा busy हूँ, जल्दी बताइए।"), ("देखिए मुझे leads तो मिल रहे हैं", "पर quality अच्छी नहीं है।"),
               ("ठीक है", "कल सुबह ग्यारह बजे online meeting रख लीजिए।")]
LOUD = 900  # Payal's speech RMS 1.2k–7.6k; the line's comfort noise 150–430


async def payal_clips(client, gap_ms=450):
    clips = []
    for a, b in PAYAL_TURNS:
        parts = []
        for part in (a, b):
            wav, _ = await tts_rest(client, part, "rahul_hi_conversational")
            with wave.open(io.BytesIO(wav)) as w:
                parts.append(w.readframes(w.getnframes()))
        clips.append(parts[0] + b"\0" * (32 * gap_ms) + parts[1])
    return clips


async def payal_call(clips, overrides=None, version=None, label=""):
    """One live call: opening → turn 1 → seller barges in on Payal's answer with turn 2 → turn 3. Line audio is continuous."""
    key, ws_id = os.environ["SARVAM_VOICE_AGENTS_API_KEY"], os.environ.get("SARVAM_VOICE_WORKSPACE_ID", "01a110b3-f03e-79cd-b765-aefbf9b8b01f")
    params = {"interaction_type": "call", **({"version": version} if version else {})}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"https://apps.sarvam.ai/api/app-runtime/orgs/{ORG}/workspaces/{ws_id}/apps/{APP}/url", params=params,
                             headers={"X-API-Key": key})
    if r.status_code >= 400:
        return {"label": label, "error": f"signed url {r.status_code}: {r.text[:120]}"}
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
    signed = urlparse(r.json()["url"])
    q = parse_qs(signed.query)  # rebuilt like the SDK does: repeated keys would break the signature
    q.update({"user_identifier": [f"sg-lab-{label}"], "user_identifier_type": ["custom"], "sample_rate": ["16000"], "interaction_type": ["call"]})
    url = urlunparse(signed._replace(query=urlencode(q, doseq=True)))
    t0 = time.monotonic()
    rep = {"label": label, "overrides": overrides or {}, "events": {}, "transcript": [], "turns": []}
    st = {"loud_at": None, "first_loud": None, "clip": b"", "pos": 0, "closed": None, "watch": False, "talkover": 0}
    start = {"type": "client.action.interaction_start", "origin": "client", "timestamp": time.time(),
             "agent_variables": {"seller_id": "91000103", "seller_name": "Rohit", "company_name": "Malhotra Precision Castings", "seller_city": "Ludhiana"},
             "initial_language_name": "Hindi", "speech_hotwords": ["IndiaMART", "Payal", "BuyLead", "leads"], **(overrides or {})}
    try:
        async with websockets.connect(url, open_timeout=15, max_size=2 ** 22) as ws:
            await ws.send(json.dumps(start))

            async def receive():
                async for raw in ws:
                    m = json.loads(raw)
                    kind = m.get("type", "")
                    if kind == "server.media.audio_chunk":
                        try:
                            loud = audioop.rms(base64.b64decode(m.get("audio_base64") or ""), 2) > LOUD
                        except Exception:  # noqa: BLE001
                            loud = False
                        if loud:
                            if st["watch"] and st["pos"] < len(st["clip"]):
                                st["talkover"] += 1  # Payal speaking while the seller is still mid-utterance (e.g. in a hesitation)
                            st["loud_at"] = time.monotonic()
                            st["first_loud"] = st["first_loud"] or ms(t0)
                    elif kind == "server.system.ping":
                        await ws.send(json.dumps({"type": "client.system.pong", "origin": "client", "timestamp": time.time(), "event_id": m.get("event_id")}))
                    else:
                        rep["events"][kind] = rep["events"].get(kind, 0) + 1
                        if kind == "server.event.transcription":
                            rep["transcript"].append({"at": ms(t0), "who": m.get("role"), "text": str(m.get("content", ""))[:200]})
                        if kind == "server.action.interaction_end":
                            st["closed"] = f"ended by Sarvam at {ms(t0)} ms"

            async def pump():
                while not st["closed"]:
                    frame = b"\0" * 3200
                    if st["pos"] < len(st["clip"]):
                        frame = st["clip"][st["pos"]:st["pos"] + 3200].ljust(3200, b"\0")
                        st["pos"] += 3200
                    await ws.send(json.dumps({"type": "client.media.audio_chunk", "origin": "client", "timestamp": time.time(),
                                              "audio_base64": base64.b64encode(frame).decode(), "format": "audio/wav", "sample_rate": 16000}))
                    await asyncio.sleep(0.1)

            async def payal_quiet(cap):
                began = time.monotonic()
                while time.monotonic() - began < cap and not st["closed"]:
                    if st["loud_at"] and st["loud_at"] > began - 0.1 and time.monotonic() - st["loud_at"] > 1.2:
                        return True
                    await asyncio.sleep(0.05)
                return False

            async def say(clip):
                st["clip"], st["pos"], st["watch"] = clip, 0, True
                while st["pos"] < len(clip) and not st["closed"]:
                    await asyncio.sleep(0.02)
                st["watch"] = False
                return time.monotonic()

            async def answer_after(ended, cap=12):
                st["loud_at"] = None
                while time.monotonic() - ended < cap and not st["closed"]:
                    if st["loud_at"]:
                        return round((st["loud_at"] - ended) * 1000)
                    await asyncio.sleep(0.02)
                return None

            tasks = [asyncio.create_task(receive()), asyncio.create_task(pump())]
            await payal_quiet(20)
            rep["opening_first_loud_ms"] = st["first_loud"]
            # Turn 1: normal.
            n0 = sum(1 for t in rep["transcript"] if t["who"] == "user")
            ended = await say(clips[0])
            rep["turns"].append({"turn": 1, "answer_ms": await answer_after(ended)})
            # Turn 2: the seller cuts in 0.8 s after Payal starts answering; time until Payal's audio stops.
            await asyncio.sleep(0.8)
            barge_at = time.monotonic()
            st["clip"], st["pos"] = clips[1], 0
            last_loud = barge_at
            while time.monotonic() - barge_at < 4 and not st["closed"]:
                if st["loud_at"] and st["loud_at"] > last_loud:
                    last_loud = st["loud_at"]
                if time.monotonic() - last_loud > 0.6:
                    break
                await asyncio.sleep(0.02)
            rep["barge_stop_ms"] = round((last_loud - barge_at) * 1000)
            while st["pos"] < len(clips[1]) and not st["closed"]:
                await asyncio.sleep(0.02)
            ended = time.monotonic()
            rep["turns"].append({"turn": 2, "answer_ms": await answer_after(ended)})
            await payal_quiet(15)
            ended = await say(clips[2])
            rep["turns"].append({"turn": 3, "answer_ms": await answer_after(ended)})
            await payal_quiet(15)
            rep["user_lines"] = sum(1 for t in rep["transcript"] if t["who"] == "user") - n0
            rep["talkover_chunks"] = st["talkover"]
            rep["bot_lines"] = sum(1 for t in rep["transcript"] if t["who"] == "bot")
            await ws.send(json.dumps({"type": "client.action.interaction_end", "origin": "client", "timestamp": time.time()}))
            for t in tasks:
                t.cancel()
    except Exception as error:  # noqa: BLE001
        rep["error"] = f"{type(error).__name__}: {str(error)[:160]}"
    rep["closed"] = st["closed"]
    rep["total_ms"] = ms(t0)
    return rep


PAYAL_VARIANTS = {
    "baseline": {},
    "frames3": {"vad_config": {"speech_frame_config": {"silence_duration_frames": 3}}},
    "frames10": {"vad_config": {"speech_frame_config": {"silence_duration_frames": 10}}},
    "pace125": {"text_to_speech_config": {"speech_settings": {"pace": 1.25}}},
    "asr_v4": {"asr_model_name": "saaras:v4"},
}


async def part_payal():
    variants = (os.environ.get("LAB_VARIANTS") or ",".join(PAYAL_VARIANTS)).split(",")
    version = os.environ.get("LAB_VERSION") or None
    async with httpx.AsyncClient(timeout=60) as client:
        clips = await payal_clips(client)
    out = []
    rounds = int(os.environ.get("LAB_ROUNDS", "2"))
    for batch in ([list(range(rounds))] if os.environ.get("LAB_PARALLEL") else [[r] for r in range(rounds)]):
        reps = await asyncio.gather(*(payal_call(clips, PAYAL_VARIANTS.get(v, {}), version, f"{v}-{rnd}") for rnd in batch for v in variants))
        for r in reps:
            out.append(r)
            print("payal", json.dumps({k: r.get(k) for k in ("label", "error", "closed", "opening_first_loud_ms", "turns", "barge_stop_ms", "user_lines",
                                                         "talkover_chunks", "bot_lines", "events", "total_ms")},
                                      ensure_ascii=False), flush=True)
            for t in r.get("transcript", []):
                print("   ", t["at"], t["who"], t["text"][:110], flush=True)
    return out

PARTS = {"llm": part_llm, "tts": part_tts, "stt": part_stt, "payal": part_payal}

if __name__ == "__main__":
    part = sys.argv[1] if len(sys.argv) > 1 else "llm"
    result = asyncio.run(PARTS[part]())
    with open(f"{OUT}/voice-lab-{part}.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

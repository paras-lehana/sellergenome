"""V5.3: engine choices (additive, Admin defaults + per-place overrides), streamed Bulbul, realtime Saaras listening,
live browser calls to Payal through Sarvam's conversational SDK. No network: Sarvam's sockets and runtime are faked."""

import asyncio
import json
from pathlib import Path

import pytest

from backend.genome import engines, live_speech

ROOT = Path(__file__).resolve().parents[1]


class FakeSettings:
    def __init__(self, **values):
        import yaml
        self.tree = yaml.safe_load((ROOT / "config" / "genome" / "settings.yaml").read_text())
        for k, v in values.items():
            node = self.tree
            *parts, last = k.split(".")
            for p in parts:
                node = node.setdefault(p, {})
            node[last] = v

    def get(self, dotted, default=None):
        node = self.tree
        for p in dotted.split("."):
            if not isinstance(node, dict) or p not in node:
                return default
            node = node[p]
        return node


def test_defaults_are_the_latest_models_and_old_ones_stay_selectable():
    d = engines.defaults(FakeSettings())
    assert (d["tts_model"], d["tts_transport"], d["listen"], d["stt_model"], d["stt_realtime_model"]) == \
        ("bulbul:v4-flash", "stream", "realtime", "saaras:v4", "saaras:v4")
    assert d["bot"] == "builtin"                                   # the built-in bot stays the default; Payal is an option
    assert {"bulbul:v3", "bulbul:v4-flash"} <= set(engines.TTS_MODELS) and {"saaras:v3", "saaras:v4"} <= set(engines.STT_MODELS)
    assert {"rest", "stream"} == set(engines.TTS_TRANSPORTS) and {"clip", "realtime"} == set(engines.LISTEN)


def test_place_overrides_are_validated_against_the_catalogue():
    s = FakeSettings()
    r = engines.resolve(s, {"tts_model": "bulbul:v3", "tts_transport": "rest", "stt_model": "bulbul:zzz", "bot": "sarvam_agent", "x": 1})
    assert (r["tts_model"], r["tts_transport"], r["stt_model"], r["bot"]) == ("bulbul:v3", "rest", "saaras:v4", "sarvam_agent")
    with pytest.raises(ValueError):
        engines.check("tts_model", "bulbul:v9")


def test_persona_voices_map_into_each_model():
    assert engines.voice_for("bulbul:v4-flash", "ritu") == "ritu_hi_customer"
    assert engines.voice_for("bulbul:v4-flash", "priya") == "ishita_enhi_customer"
    assert engines.voice_for("bulbul:v3", "ritu") == "ritu"
    assert engines.voice_for("bulbul:v3", "ritu_hi_customer") == "ritu"             # a v4 name never reaches v3
    assert engines.voice_for("bulbul:v4-flash", "ritu", "simran_enhi_customer") == "simran_enhi_customer"
    assert engines.contrast_voice("bulbul:v4-flash", "ritu_hi_customer") == "rahul_hi_conversational"
    assert engines.contrast_voice("bulbul:v3", "shubh") == "priya"


def test_admin_cannot_save_a_model_sarvam_would_reject():
    engines.validate_settings({"engines.tts_model": "bulbul:v3", "llm.reasoning_effort": "low"}, "bulbul:v4-flash")
    for bad in ({"engines.tts_model": "bulbul:v4"}, {"engines.stt_mode": "loud"}, {"llm.model": "gpt-4"},
                {"engines.tts_voice": "ritu_hi_customer", "engines.tts_model": "bulbul:v3"}, {"engines.keyterms": ["x" * 65]}):
        with pytest.raises(ValueError):
            engines.validate_settings(bad, "bulbul:v4-flash")


def test_realtime_query_carries_vad_settings_and_keyterms_only_for_v4():
    q = live_speech.realtime_query(FakeSettings(), {"model": "saaras:v4", "mode": "codemix", "language": "hi-IN"})
    assert "keyterms=" in q and "silence_duration_ms=600" in q and "stream_type=fast" in q and "encoding=linear16" in q
    assert "keyterms=" not in live_speech.realtime_query(FakeSettings(), {"model": "saaras:v3-realtime", "mode": "codemix", "language": "hi-IN"})


def test_tickets_are_single_purpose_and_expire():
    t = live_speech.Tickets()
    tok = t.issue("tts", {"a": 1}, ttl=60, uses=2)
    assert t.take(tok, "listen") is None and t.take(tok, "tts") == {"a": 1} and t.take(tok, "tts") == {"a": 1} and t.take(tok, "tts") is None
    old = t.issue("listen", {}, ttl=-1)
    assert t.take(old, "listen") is None


# ---- API (TestClient) ----------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SARVAM_V4_API_KEY", "test-key")
    monkeypatch.setenv("SARVAM_VOICE_AGENTS_API_KEY", "voice-key")
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    from fastapi.testclient import TestClient
    import backend.main as main
    with TestClient(main.app) as c:
        yield c


def test_engines_catalogue_is_public_and_csp_allows_the_sarvam_runtime(client):
    r = client.get("/api/v5/engines")
    body = r.json()
    assert body["defaults"]["tts_model"] == "bulbul:v4-flash" and "call" in body["locations"] and body["voices"]["bulbul:v3"]
    assert body["payal"]["available"] is False                      # nothing built in a fresh config
    assert "wss://apps.sarvam.ai" in r.headers["content-security-policy"]


def test_speech_rejects_unknown_models(client):
    assert client.post("/api/v5/speak", json={"text": "नमस्ते", "model": "bulbul:v9"}).status_code == 422
    assert client.post("/api/v5/listen/ticket", json={"model": "saaras:v9"}).status_code == 422
    assert client.post("/api/v5/translate", json={"text": "hi", "target": "hi-IN", "model": "zzz"}).status_code == 422


def test_streamed_speech_relays_chunks_through_a_short_lived_link(client):
    import backend.main as main
    g = main.app.state.genome
    sent = {}

    async def fake_stream(p, scope):
        sent.update(p)
        for chunk in (b"ID3-first", b"-second"):
            yield chunk

    g.sarvam.tts_stream = fake_stream
    out = client.post("/api/v5/speak/stream", json={"text": "नमस्ते जी", "speaker": "ritu", "model": "bulbul:v4-flash"}).json()
    assert out["transport"] == "stream" and out["speaker"] == "ritu_hi_customer" and out["url"].endswith(".mp3")
    audio = client.get(out["url"])
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg" and audio.content == b"ID3-first-second"
    assert sent["model"] == "bulbul:v4-flash"
    client.get(out["url"])                                           # Safari may fetch twice
    assert client.get(out["url"]).status_code == 404                 # then the link is gone


class FakeUpstream:
    """Stands in for Sarvam's realtime STT socket: answers audio with a partial and a final transcript."""

    def __init__(self):
        self.sent, self.queue = [], asyncio.Queue()

    async def __aenter__(self):
        await self.queue.put(json.dumps({"event": "session.begin", "request_id": "r"}))
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, raw):
        self.sent.append(json.loads(raw))
        if self.sent[-1]["event"] == "audio_input" and len(self.sent) == 1:
            await self.queue.put(json.dumps({"event": "vad.speech_start", "utterance_idx": 0}))
            await self.queue.put(json.dumps({"event": "transcript.partial", "utterance_idx": 0, "text": "हाँ"}))
            await self.queue.put(json.dumps({"event": "transcript.final", "utterance_idx": 0, "text": "हाँ जी बोलिए", "language": "hi-IN", "request_id": "x"}))

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()


def test_realtime_listening_relays_events_and_needs_a_ticket(client, monkeypatch):
    import websockets
    from starlette.websockets import WebSocketDisconnect
    upstream = FakeUpstream()
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: upstream)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v5/listen/realtime?ticket=nope") as ws:
            ws.receive_text()
    t = client.post("/api/v5/listen/ticket", json={"language": "hi-IN"}).json()
    assert (t["model"], t["mode"]) == ("saaras:v4", "codemix")
    with client.websocket_connect(f"/api/v5/listen/realtime?ticket={t['ticket']}") as ws:
        ws.send_bytes(b"\0" * 3200)
        events = [json.loads(ws.receive_text()) for _ in range(4)]
        ws.send_text(json.dumps({"event": "end"}))
    kinds = [e["event"] for e in events]
    assert kinds == ["session.begin", "vad.speech_start", "transcript.partial", "transcript.final"]
    assert events[-1]["text"] == "हाँ जी बोलिए" and "request_id" not in events[-1]
    assert upstream.sent[0]["event"] == "audio_input" and upstream.sent[-1]["event"] == "end"
    with pytest.raises(WebSocketDisconnect):                         # tickets are single-use
        with client.websocket_connect(f"/api/v5/listen/realtime?ticket={t['ticket']}") as ws:
            ws.receive_text()


def test_realtime_listening_refuses_foreign_origins(client):
    t = client.post("/api/v5/listen/ticket", json={}).json()
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/v5/listen/realtime?ticket={t['ticket']}", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()


def test_live_payal_call_keeps_the_key_server_side_and_lands_on_the_floor(client, monkeypatch):
    import backend.main as main
    from backend.genome import sarvam_voice as sv
    g = main.app.state.genome
    assert client.post("/api/v5/sarvam-live/sessions", json={"seller_id": "real-67890123"}).status_code == 401   # real seller: sign in first
    assert client.post("/api/v5/sarvam-live/sessions", json={"seller_id": "syn-91000101"}).status_code == 409   # not built
    sv._write(sv.SarvamVoice.STATE, {"agent": {"id": "Payal---Ind-test"}})
    live = client.post("/api/v5/sarvam-live/sessions", json={"seller_id": "syn-91000101"}).json()
    cfg = live["config"]
    assert cfg == {"org_id": sv.ORGANIZATION, "workspace_id": "01a110b3-f03e-79cd-b765-aefbf9b8b01f", "app_id": "Payal---Ind-test"}
    assert live["start"]["agent_variables"]["seller_id"] == "91000101" and "IndiaMART" in live["start"]["speech_hotwords"]
    assert "voice-key" not in json.dumps(live)                       # the Voice Agents key never reaches the page
    base = f"/api/v5/sarvam-live/s/{live['session_id']}/orgs/{cfg['org_id']}/workspaces/{cfg['workspace_id']}"
    assert client.get(f"{base}/apps/{cfg['app_id']}/url", headers={"X-Live-Token": "wrong"}).status_code == 401
    assert client.get(f"{base}/apps/someone-else/url", headers={"X-Live-Token": live["token"]}).status_code == 403

    async def fake_signed(session, interaction_type="call"):
        return {"url": "wss://apps.sarvam.ai/api/app-runtime/signed", "expires_at": 1, "reference_id": "ref"}

    monkeypatch.setattr(g.sarvam_voice, "live_signed_url", fake_signed)
    signed = client.get(f"{base}/apps/{cfg['app_id']}/url?interaction_type=call", headers={"X-Live-Token": live["token"]}).json()
    assert signed["url"].startswith("wss://apps.sarvam.ai/") and signed["reference_id"] == "ref"
    ev = client.post(f"/api/v5/sarvam-live/s/{live['session_id']}/events", headers={"X-Live-Token": live["token"]},
                     json={"events": [{"type": "transcript", "speaker": "bot", "text": "Namaste ji, Payal bol rahi hoon"},
                                      {"type": "transcript", "speaker": "seller", "text": "jaldi boliye, time nahi hai"},
                                      {"type": "tool", "name": "book_meeting"}, {"type": "bogus"}]})
    assert ev.json()["accepted"] == 3
    tiles = client.get("/api/v5/floor").json()["tiles"]
    tile = next(t for t in tiles if t.get("provider") == "Sarvam Voice Agent · browser")
    assert tile["real"] and tile["turns"][-1]["who"] == "seller"
    session = client.get(f"/api/v5/voice/sessions/{live['session_id']}").json()
    assert "live" not in session and session["signals"]             # SellerGenome's own signal detection ran on the seller line


def test_vendored_sdk_and_worklets_are_csp_safe():
    sdk = (ROOT / "frontend" / "cc" / "vendor" / "sarvam-conv-ai-sdk.js").read_text()
    assert sdk.startswith("/*! sarvam-conv-ai-sdk 0.0.42") and "ConversationAgent" in sdk and "BrowserAudioInterface" in sdk
    worklets = (ROOT / "frontend" / "cc" / "audio-worklets.js").read_text()
    assert "registerProcessor('sg-pcm16k'" in worklets and "registerProcessor('capture'" in worklets
    live = (ROOT / "frontend" / "cc" / "payal-live.js").read_text()
    assert "loadWorklet" in live and "audio-worklets.js" in live and "apiKey: 'server-held'" in live


def test_audio_buffer_starts_early_and_replays_for_a_second_reader():
    async def source():
        for c in (b"a", b"b", b"c"):
            await asyncio.sleep(0)
            yield c

    async def scenario():
        buf = live_speech.AudioBuffer().start(source())
        first = [c async for c in buf.read()]
        second = [c async for c in buf.read()]
        return first, second

    assert asyncio.run(scenario()) == ([b"a", b"b", b"c"], [b"a", b"b", b"c"])


def test_transliteration_is_only_needed_for_bulbul_v3_by_default():
    assert engines.needs_transliteration("bulbul:v3", "auto") and not engines.needs_transliteration("bulbul:v4-flash", "auto")
    assert engines.needs_transliteration("bulbul:v4-flash", "always") and not engines.needs_transliteration("bulbul:v3", "never")
    with pytest.raises(ValueError):
        engines.validate_settings({"engines.transliterate": "sometimes"}, "bulbul:v4-flash")


def test_turn_starts_the_reply_speech_itself_and_skips_transliteration_on_v4(client, monkeypatch):
    """5.4: the turn response carries an already-streaming link; the socket opened during the LLM call is used."""
    import time as _time
    import backend.main as main
    from backend.genome.sarvam import SarvamError
    g = main.app.state.genome

    async def offline(*a, **k):
        raise SarvamError("offline in tests")
    monkeypatch.setattr(g.sarvam, "chat_json", offline)
    rid = client.post("/api/v5/runs", json={"seller_id": "syn-91000105"}).json()["run_id"]
    for _ in range(80):
        _time.sleep(0.1)
        if client.get(f"/api/v5/runs/{rid}").json()["status"] != "running":
            break
    call = client.post("/api/v5/calls", json={"run_id": rid}).json()

    async def reply(*a, **k):
        return {"data": {"reply": "Ji bilkul, kal subah gyarah baje online meeting rakh lete hain?", "stage": "meeting"}, "ms": 300.0, "model": "x"}

    used = {"translit": 0, "ws": []}

    class FakeWs:
        async def close(self):
            pass

    async def connect(model):
        ws = FakeWs()
        used["ws"].append(ws)
        return ws

    async def stream(p, scope, ws=None):
        used["stream"] = (p["model"], p["speaker"], p["text"], ws)
        yield b"ID3-a"
        yield b"-b"

    async def translit(*a, **k):
        used["translit"] += 1
        return {"text": "जी बिल्कुल", "ms": 1}

    monkeypatch.setattr(g.sarvam, "chat_json", reply)
    monkeypatch.setattr(g.sarvam, "tts_connect", connect)
    monkeypatch.setattr(g.sarvam, "tts_stream", stream)
    monkeypatch.setattr(g.sarvam, "transliterate", translit)
    out = client.post(f"/api/v5/calls/{call['id']}/turn", json={"text": "haan boliye", "speak": {"model": "bulbul:v4-flash"}}).json()
    item = out["speech"][0]
    assert item["turn"] == out["call"]["transcript"][-1]["i"] and item["prestarted"] and item["preopened"] and not item["transliterated"]
    audio = client.get(item["url"])
    assert audio.status_code == 200 and audio.content == b"ID3-a-b"
    assert used["translit"] == 0 and used["stream"][0] == "bulbul:v4-flash" and used["stream"][2].startswith("Ji bilkul")
    assert used["stream"][3] is used["ws"][0]                       # the pre-opened socket carried the speech
    v3 = client.post(f"/api/v5/calls/{call['id']}/turn", json={"text": "theek hai", "speak": {"model": "bulbul:v3"}}).json()["speech"][0]
    assert v3["transliterated"] and client.get(v3["url"]).status_code == 200 and used["translit"] == 1
    assert "speech" not in client.post(f"/api/v5/calls/{call['id']}/turn", json={"text": "achha"}).json()   # opt-in only


def test_pcm_format_streams_raw_chunks_for_web_audio(client):
    import backend.main as main
    g = main.app.state.genome
    seen = {}

    async def fake_stream(p, scope, ws=None):
        seen.update(p)
        yield b"\x01\x00\x02\x00"

    g.sarvam.tts_stream = fake_stream
    out = client.post("/api/v5/speak/stream", json={"text": "Ji bilkul", "format": "pcm"}).json()
    assert out["url"].endswith(".pcm") and out["format"] == "pcm" and out["transliterated"] is False   # v4 needs no transliteration
    audio = client.get(out["url"])
    assert audio.headers["content-type"].startswith("audio/L16") and audio.content == b"\x01\x00\x02\x00" and seen["format"] == "pcm"
    assert client.post("/api/v5/speak/stream", json={"text": "x", "format": "ogg"}).status_code == 422


def test_buyer_desk_listens_without_seller_keyterms():
    q = live_speech.realtime_query(FakeSettings(), {"model": "saaras:v4", "mode": "codemix", "language": "hi-IN", "keyterms": []})
    assert "keyterms=" not in q   # seller words biased a clipped buyer phrase into "TrustSEAL." in 5.4 live QA
    q = live_speech.realtime_query(FakeSettings(), {"model": "saaras:v4", "mode": "codemix", "language": "hi-IN", "keyterms": ["Pune"]})
    assert "keyterms=" in q and "Pune" in __import__("urllib.parse").parse.unquote(q)

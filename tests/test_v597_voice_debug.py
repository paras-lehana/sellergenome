"""5.9.7 — call-page Debug panel data and the microphone check in realtime listening."""
import pytest


@pytest.fixture()
def op_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    from fastapi.testclient import TestClient
    import backend.main as main
    monkeypatch.setattr(main.config, "operator_key", "synthetic-operator-key-for-tests")
    with TestClient(main.app) as c:
        yield c, {"x-operator-key": "synthetic-operator-key-for-tests", "Origin": "http://testserver"}


def test_debug_voice_shows_agents_models_and_account_only_to_operators(op_client):
    c, h = op_client
    anon = c.get("/api/v5/debug/voice").json()
    assert anon["builtin"]["llm_model"] and anon["builtin"]["stt_realtime_model"] and "payal" in anon and "vani" in anon
    assert "keys" not in anon and "account" not in anon["sarvam"] and not anon["operator"]
    op = c.get("/api/v5/debug/voice", headers=h).json()
    assert op["operator"] and set(op["keys"]) == {"SARVAM_V4_API_KEY", "SARVAM_VOICE_AGENTS_API_KEY"}
    text = str(op)
    assert "access_token" not in text and "refresh_token" not in text


def test_account_reads_claims_never_the_token(tmp_path, monkeypatch):
    import base64, json
    from backend.genome import sarvam_voice as sv
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path))
    claims = base64.urlsafe_b64encode(json.dumps({"email": "pm@example.com", "org_id": "org-1", "workspace_id": "ws-1", "phone": "+910000000000"}).encode()).decode().rstrip("=")
    auth = sv.VoiceAuth()
    monkeypatch.setattr(auth, "state", lambda: {"access_token": f"x.{claims}.y"})
    assert auth.account() == {"email": "pm@example.com", "org_id": "org-1", "workspace_id": "ws-1"}   # no phone, no token
    monkeypatch.setattr(auth, "state", lambda: {"access_token": "opaque"})
    assert auth.account() == {}


def test_page_mic_check_is_summarised_for_the_log():
    from backend.genome.live_speech import _client_note
    note = _client_note({"frames": 0, "peak": 0, "rate": 48000, "ctx": "suspended", "track": {"muted": False, "state": "live"},
                         "ua": "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15"})
    assert "0 frames" in note and "audio engine suspended" in note and note.endswith("Safari")

"""5.13.7 — the workspace agents never disappear because of Sarvam's sign-in: one token refresh at a time (two at once
revoked the sign-in after a restart), and the last good agent list and call specs are kept on disk and used when the
Voice MCP fails."""
import asyncio
import json
import time
import types

import pytest

from backend.genome import sarvam_voice as sv


@pytest.fixture()
def private(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(sv, "_REFRESH_LOCK", asyncio.Lock())
    return tmp_path


def test_concurrent_requests_refresh_the_token_once(private, monkeypatch):
    sv._write(sv.VoiceAuth.FILE, {"access_token": "a0", "refresh_token": "r0", "client_id": "c", "expires_at": time.time() + 60})
    calls = []

    async def fake_token(self, form):
        calls.append(form["refresh_token"])
        await asyncio.sleep(0.05)
        s = self.state()
        s.update(access_token="a1", refresh_token="r1", expires_at=time.time() + 3600)
        sv._write(self.FILE, s)
    monkeypatch.setattr(sv.VoiceAuth, "_token", fake_token)

    async def both():
        a, b = sv.VoiceAuth(), sv.VoiceAuth()     # Payal's and another client's auth objects share the state file
        return await asyncio.gather(a.token(), b.token(), a.token())
    assert asyncio.run(both()) == ["a1", "a1", "a1"] and calls == ["r0"]


def test_a_failed_refresh_says_how_to_reconnect_and_is_shown(private, monkeypatch):
    sv._write(sv.VoiceAuth.FILE, {"access_token": "a0", "refresh_token": "gone", "client_id": "c", "expires_at": time.time() + 60})

    async def refused(self, form):
        raise sv.VoiceAgentError('Sarvam token endpoint answered 400: {"error":"invalid_grant"}')
    monkeypatch.setattr(sv.VoiceAuth, "_token", refused)
    with pytest.raises(sv.VoiceAgentError, match="Get sign-in link"):
        asyncio.run(sv.VoiceAuth().token())
    assert "invalid_grant" in sv.VoiceAuth().status()["refresh_failed"]["error"]


def fake_voice(list_result):
    v = types.SimpleNamespace(auth=None)

    async def lister():
        if isinstance(list_result, Exception):
            raise list_result
        return list_result
    v._list_workspace_agents = lister

    async def save_specs(ids):
        v.saved_ids = ids
    v._save_specs = save_specs
    return v


def test_agent_list_is_saved_and_used_when_the_sign_in_fails(private):
    items = [{"app_id": "Manjula-Exc-1", "name": "Manjula Excutive", "callable": True},
             {"app_id": "Payal-6-0-p-2", "name": "Payal 6.0 preview", "callable": True}]
    ok = fake_voice(items)

    async def list_then_settle():
        got = await sv.SarvamVoice.workspace_agents(ok)
        await asyncio.sleep(0)
        return got
    assert asyncio.run(list_then_settle()) == items
    assert json.loads((private / sv.AGENTS_FILE).read_text())["items"] == items
    broken = fake_voice(sv.VoiceAgentError("refresh token does not exist"))
    got = asyncio.run(sv.SarvamVoice.workspace_agents(broken))
    assert [a["app_id"] for a in got] == ["Manjula-Exc-1", "Payal-6-0-p-2"] and all(a["stale"] for a in got)
    assert "refresh token" in broken._agents_error


def test_no_saved_list_still_reports_the_failure(private):
    with pytest.raises(sv.VoiceAgentError):
        asyncio.run(sv.SarvamVoice.workspace_agents(fake_voice(sv.VoiceAgentError("refresh token does not exist"))))

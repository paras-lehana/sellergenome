# SellerGenome voice plug-in kit

For the developer building the voice bot (LiveKit Agents or Sarvam Voice Agents). The contract is
documented in [docs/architecture/voice-plugin.md](../../docs/architecture/voice-plugin.md).

| File | What it is |
|---|---|
| `client.py` | ~80-line dependency-light client (`httpx`): register a call, pull context, push events |
| `harness.py` | Simulates a bot call end to end against a real server — the call shows on the floor as a **REAL CALL** tile |
| `livekit_agent_example.py` | Where each call goes inside a LiveKit `AgentSession` (sketch; fill in your STT/LLM/TTS) |

## Quick start

1. In **Admin → Voice plug-in**, set a plug-in key (write-only).
2. Run the harness:

```bash
export SELLERGENOME_URL=https://sellergenome.lehana.in
export VOICE_PLUGIN_KEY=...            # the key from step 1
python integrations/voice_plugin/harness.py --glid 91000103
```

3. Open the seller floor: a REAL CALL tile appears; click it to watch transcript and signals arrive.

## Rules your bot must keep

- Introduce yourself as IndiaMART's **virtual assistant**; never claim to be human (decision D10).
- Never speak a price, discount, ROI or guarantee. Stop/do-not-call ends the call immediately.
- Apply a new `context_revision` only at a turn boundary; cancel queued audio when the caller barges in.
- Report signals with `method` (`text`, `acoustic`, `llm`) and `confidence`; text cues are not acoustic emotion.

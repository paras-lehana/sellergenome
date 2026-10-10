# LiveKit developer handoff

Run `python -m integrations.livekit.contract_harness` from the checkout with portal dependencies. It is an offline contract test, not a connected audio claim. Install `requirements.txt` in a separate worker environment to use `worker.py`.

Required private environment: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, SELLERGENOME_URL, SELLERGENOME_SERVICE_KEY. The latter is the existing automation operator key supplied server-to-server, never browser code. Named agent defaults to `sellergenome-agent` and must match Admin. Dispatch metadata contains only run_id and voice_session_id. There is no default seller. No private context is logged.

The reference worker connects and announces context availability. The other developer plugs their AgentSession into the indicated boundary; no audio agent is claimed until it is connected and tested. Use GenomeContext.apply_at_turn_boundary(agent.update_instructions), then emit transcript/speech/signal/tool/outcome events. Interrupt cancels queued audio before incrementing generation. Include that generation on speech_started/finished. Context ACK must match the server's current voice-session revision. The browser token is short-lived and room-scoped.

Portal endpoints: POST /api/voice-adapters/sessions; GET /api/operations/runs/{run_id}; POST /api/voice-adapters/sessions/{voice_id}/events. Schema: GET /api/operations/schema. Existing Sarvam WebSocket rehearsal remains independently usable in Studio.

Do not infer validated acoustic emotion from transcript text. Signals must identify method/confidence. Explicit language, pace and stop requests override inference cooldown. On interruption cancel obsolete generated speech; a stale generation is rejected by the portal.

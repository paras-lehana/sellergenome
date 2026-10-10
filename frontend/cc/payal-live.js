// Live voice call with Payal (5.3) or the VANI buyer agent (5.7) on Sarvam Voice Agents, in the browser. Sarvam's conversational SDK (vendored in
// ./vendor) owns the microphone, playback, interruptions and turn-taking; this server only signs the WebSocket URL
// (the Voice Agents key never reaches the page) and records the call as a REAL CALL on the floor.
import { BASE, api } from './api.js';
import { ConversationAgent, BrowserAudioInterface, InteractionType } from './vendor/sarvam-conv-ai-sdk.js';

/** The SDK loads its microphone worklet from a blob: URL, which the page CSP blocks; the same processor is served here.
 *  It also notes when the seller last made a sound, so Payal's reply time can be measured like VANI's. */
class CspSafeAudio extends BrowserAudioInterface {
  async loadWorklet(ctx) { await ctx.audioWorklet.addModule(`${BASE}/static/cc/audio-worklets.js`); }

  async handleCapturedSamples(samples) {
    let sum = 0;
    for (let i = 0; i < samples.length; i += 4) sum += samples[i] * samples[i];
    if (Math.sqrt(sum / Math.max(1, samples.length / 4)) > 0.02) this.lastVoiceAt = performance.now();
    return super.handleCapturedSamples(samples);
  }
}

/** Payal's speech starts when an audio chunk carries energy (Sarvam streams comfort noise between words). */
function loud(b64) {
  try {
    const bin = atob(b64 || '');
    let sum = 0, n = 0;
    for (let i = 0; i + 1 < bin.length; i += 8) { const v = ((bin.charCodeAt(i + 1) << 8) | bin.charCodeAt(i)) << 16 >> 16; sum += v * v; n++; }
    return n > 0 && Math.sqrt(sum / n) > 900;
  } catch { return false; }
}

const label = ev => String(ev?.type || '').split('.').pop();

/**
 * Start a call. Callbacks: onLine({who, text}) for each transcript line, onEvent(type, event), onState(state), onEnd().
 * agent 'payal' (seller outreach, sellerId) or 'buyer' (the VANI buyer-requirement agent, buyerCallId — 5.7).
 * Returns { stop, session, audio }.
 */
export async function startPayalCall({ sellerId = '', runId = null, agent: kind = 'payal', buyerCallId = null, appId = null, onLine, onEvent, onState, onEnd, onReply, onSignal }) {
  // appId: any agent of the Sarvam workspace (Manjula, Payal 6.0 …); null = SellerGenome's own (5.10.2)
  const live = await api('/api/v5/sarvam-live/sessions', { method: 'POST', body: { seller_id: sellerId, run_id: runId, agent: kind, buyer_call_id: buyerCallId, app_id: appId } });
  const userRole = kind === 'buyer' ? 'buyer' : 'seller';
  const pending = [];
  let flushTimer = 0, ended = false, interaction = null, opened = false;
  const flush = async () => {
    if (!pending.length) return;
    const events = pending.splice(0, 50);
    try {
      const r = await fetch(`${BASE}/api/v5/sarvam-live/s/${live.session_id}/events`, { method: 'POST', credentials: 'same-origin', keepalive: true,
        headers: { 'Content-Type': 'application/json', 'X-Live-Token': live.token }, body: JSON.stringify({ events }) });
      // Signals SellerGenome read in the seller's words (rush, confusion, language …) → the page shows the persona switch.
      const out = await r.json().catch(() => null);
      (out?.new_signals || []).forEach(x => onSignal?.(x));
    } catch { /* the floor misses a line; the call itself is unaffected */ }
  };
  const post = e => { pending.push(interaction ? { ...e, interaction_id: interaction } : e); clearTimeout(flushTimer); flushTimer = setTimeout(flush, e.speaker && e.speaker !== 'bot' ? 150 : 700); };

  const audio = new CspSafeAudio(16000, { outputGain: 1.0 });
  // Reply time as the seller hears it: their last sound at the microphone → Payal's first audible chunk.
  let botLoudAt = 0, botTurnEndedAt = 0, botSpeaking = false;
  const quiet = setInterval(() => { if (botSpeaking && performance.now() - botLoudAt > 700) { botSpeaking = false; botTurnEndedAt = performance.now(); } }, 100);
  const agent = new ConversationAgent({
    apiKey: 'server-held',  // ignored: baseUrl points at our proxy, which adds the real key server-side
    platform: 'browser',
    baseUrl: `${location.origin}${BASE}/api/v5/sarvam-live/s/${live.session_id}/`,
    customHeaders: { 'X-Live-Token': live.token },
    config: { user_identifier_type: 'custom', user_identifier: `sellergenome-${kind}-${live.session_id}`, ...live.config,
      interaction_type: InteractionType.CALL, input_sample_rate: 16000, output_sample_rate: 16000, ...live.start },
    audioInterface: audio,
    audioCallback: async m => {
      if (!loud(m.audio_base64 || m.audio)) return;
      const now = performance.now();
      if (!botSpeaking) {
        botSpeaking = true;
        const said = audio.lastVoiceAt;
        // Only a real reply: the seller had stopped (≥200 ms quiet) — not Payal resuming while the seller still talks.
        if (said && said > botTurnEndedAt && now - said >= 200 && now - said < 8000) {
          const ms = Math.round(now - said);
          onReply?.(ms);
          post({ type: 'tool', name: 'reply_time', details: `${ms} ms` });
        }
      }
      botLoudAt = now;
    },
    transcriptCallback: async m => {
      const who = m.role === 'user' ? userRole : 'bot';
      const text = String(m.content || '').trim();
      if (!text) return;
      onLine?.({ who, text });
      post({ type: 'transcript', speaker: who, text });
    },
    eventCallback: async ev => {
      const kind = label(ev);
      onEvent?.(kind, ev);
      if (kind === 'tool_call' || kind === 'kb_query') post({ type: 'tool', name: ev.tool_name || ev.name || (kind === 'kb_query' ? 'knowledge_base' : 'tool'), details: JSON.stringify(ev).slice(0, 280) });
      else if (kind === 'user_interrupt') post({ type: 'signal', signal: 'barge_in' });
      else if (kind === 'language_change') post({ type: 'signal', signal: 'language_switch' });
    },
    stateCallback: state => {
      // The SDK handles "interaction connected" itself (no event callback), so the connected state marks the start.
      if (state === 'connected' && !opened) {
        opened = true;
        interaction = agent.getInteractionId?.() || null;
        post({ type: 'tool', name: 'connected', details: `interaction ${interaction}` });
        // Sarvam speaks the configured opening line as audio without a transcript event; show and record it.
        if (live.opening) { onLine?.({ who: 'bot', text: live.opening, opening: true }); post({ type: 'transcript', speaker: 'bot', text: live.opening }); }
      }
      onState?.(state);
    },
    endCallback: async () => {
      if (ended) return;
      ended = true;
      post({ type: 'ended' });
      clearTimeout(flushTimer); await flush();
      onEnd?.();
    },
  });
  // The SDK's start message carries variables and language only; Sarvam's runtime also accepts per-call speech hotwords
  // and voice pace (CustomAppConfig). Send the server's full start overrides for this seller instead.
  const inner = agent.agent;
  if (inner && typeof inner.sendInteractionStart === 'function') {
    inner.sendInteractionStart = async function sendFullStart() {
      if (!this.ws) throw new Error('WebSocket not connected');
      this.ws.send(JSON.stringify({ type: 'client.action.interaction_start', origin: 'client', timestamp: Date.now() / 1000, ...live.start }));
    };
  }
  onState?.('connecting');
  await agent.start();
  const ok = await agent.waitForConnect(12);
  if (!ok) { await agent.stop().catch(() => {}); throw new Error('Sarvam did not answer within 12 s — try again.'); }
  const cap = setTimeout(() => agent.stop().catch(() => {}), live.max_seconds * 1000);
  return {
    session: live, audio,
    stop: async () => { clearTimeout(cap); clearInterval(quiet); await agent.stop().catch(() => {}); },
  };
}

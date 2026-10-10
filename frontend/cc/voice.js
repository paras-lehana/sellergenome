// Speaking and listening for every page (5.3). Which engine is used comes from engines.js (Admin default or the
// place's picker): Bulbul v4/v3 over REST or streamed, Saaras clips or realtime streaming with barge-in.
import { api, BASE } from './api.js';

/**
 * Speak one line. Returns { audio, out, firstSound } — firstSound resolves with ms from the request to the first audible
 * sound (streamed audio starts after Bulbul's first chunk instead of after the whole line).
 */
export async function speakLine({ text, language = 'hi-IN', speaker = 'ritu', pace = 1, eng, signal }) {
  const t0 = performance.now();
  const body = { text: text.slice(0, 1200), language, speaker: eng?.tts_voice && eng.tts_voice !== 'auto' ? eng.tts_voice : speaker,
    pace: Number(pace) || 1, transliterate: true, model: eng?.tts_model || null };
  let out, audio;
  if (eng?.tts_transport === 'stream') {
    out = await api('/api/v5/speak/stream', { method: 'POST', body: { ...body, format: pcmSupported() ? 'pcm' : 'mp3' }, signal });
    audio = player(out.url);
  } else {
    out = await api('/api/v5/speak', { method: 'POST', body, signal });
    audio = new Audio(`data:${out.content_type};base64,${out.audio_base64}`);
  }
  const firstSound = new Promise(resolve => {
    audio.addEventListener('playing', () => resolve(Math.round(performance.now() - t0)), { once: true });
    audio.addEventListener('error', () => resolve(null), { once: true });
  });
  return { audio, out, firstSound };
}

// ---- Raw PCM streaming through Web Audio (5.4) -------------------------------------------------------------------------
// An <audio> element buffers ≈0.75–0.9 s of a streamed MP3 before it starts; Web Audio can play each 24 kHz PCM chunk the
// moment it arrives (first chunk ≈0.2 s after Bulbul gets the text). Same surface as an <audio> element for this code.
let sharedCtx = null;
export function audioCtx() {
  const C = window.AudioContext || window.webkitAudioContext;
  if (!C) return null;
  sharedCtx ||= new C();
  return sharedCtx;
}
// Browsers start an AudioContext only after a user gesture; resume it on the first tap anywhere.
if (typeof document !== 'undefined') {
  const unlock = () => { audioCtx()?.resume().catch(() => {}); };
  document.addEventListener('pointerdown', unlock, { capture: true });
  document.addEventListener('keydown', unlock, { capture: true });
}
export const pcmSupported = () => !!(window.AudioContext || window.webkitAudioContext) && !!window.ReadableStream;

export class PcmStream extends EventTarget {
  constructor(url, rate = 24000) {
    super();
    Object.assign(this, { url, rate, sources: new Set(), queue: [], scheduled: 0, next: 0, paused: true, ended: false, done: false, started: false });
    this.abort = new AbortController();
    this.load();
  }

  async load() {
    try {
      const res = await fetch(this.url, { signal: this.abort.signal, credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader();
      let carry = null;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        let bytes = value;
        if (carry) { const m = new Uint8Array(carry.length + bytes.length); m.set(carry); m.set(bytes, carry.length); bytes = m; carry = null; }
        if (bytes.length % 2) { carry = bytes.slice(-1); bytes = bytes.slice(0, -1); }
        const pcm = new Int16Array(bytes.slice().buffer);
        const f = new Float32Array(pcm.length);
        for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 32768;
        this.queue.push(f);
        if (this.queue.length === 1) this.dispatchEvent(new Event('canplay'));
        if (!this.paused) this.flush();
      }
      this.done = true;
      this.maybeEnd();
    } catch (err) {
      if (err.name !== 'AbortError') { this.failed = true; this.dispatchEvent(new Event('error')); }
    }
  }

  flush() {
    const ctx = audioCtx();
    while (this.scheduled < this.queue.length) {
      const f = this.queue[this.scheduled++];
      const buf = ctx.createBuffer(1, f.length, this.rate);
      buf.copyToChannel(f, 0);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      const at = Math.max(ctx.currentTime + 0.02, this.next);
      src.start(at);
      this.next = at + buf.duration;
      this.sources.add(src);
      src.onended = () => { this.sources.delete(src); this.maybeEnd(); };
      if (!this.started) {
        this.started = true;
        setTimeout(() => this.dispatchEvent(new Event('playing')), Math.max(0, (at - ctx.currentTime) * 1000));
      }
    }
  }

  async play() {
    const ctx = audioCtx();
    if (!ctx) throw new Error('Web Audio unavailable');
    if (ctx.state !== 'running') await ctx.resume();
    if (ctx.state !== 'running') throw new Error('The browser blocked audio — tap once.');
    this.paused = false;
    this.flush();
  }

  pause() {
    if (this.paused && this.started) return;
    this.paused = true;
    this.abort.abort();
    this.sources.forEach(s => { try { s.stop(); } catch { /* already stopped */ } });
    this.sources.clear();
    this.dispatchEvent(new Event('pause'));
  }

  maybeEnd() {
    if (this.done && !this.paused && this.started && !this.sources.size && this.scheduled >= this.queue.length && !this.ended) {
      this.ended = true;
      this.dispatchEvent(new Event('ended'));
    }
  }
}

const player = url => (url.endsWith('.pcm') ? new PcmStream(BASE + url) : new Audio(BASE + url));

/** Timing trace of the speaking/listening path (window.__sgTrace, last 200 marks) — read by QA and visible in devtools. */
export function trace(kind, data = {}) {
  try {
    const t = (window.__sgTrace ||= []);
    t.push({ kind, t: Math.round(performance.now()), ...data });
    if (t.length > 200) t.splice(0, t.length - 200);
  } catch { /* tracing never breaks a call */ }
}

/** Options for turn requests: the server starts the reply's speech itself when streaming is chosen (5.4). */
export function speakOpts(eng) {
  return eng?.tts_transport === 'stream' ? { model: eng.tts_model || null, voice: eng.tts_voice || 'auto', format: pcmSupported() ? 'pcm' : 'mp3' } : null;
}

/** An already-streaming line returned with a turn. Same shape as speakLine's result. */
export function prestartedLine(item) {
  const t0 = performance.now();
  trace('audio_created', { turn: item.turn });
  const audio = player(item.url);
  audio.addEventListener('canplay', () => trace('audio_canplay', { turn: item.turn }), { once: true });
  const firstSound = new Promise(resolve => {
    audio.addEventListener('playing', () => { trace('audio_playing', { turn: item.turn }); resolve(Math.round(performance.now() - t0)); }, { once: true });
    audio.addEventListener('error', () => resolve(null), { once: true });
  });
  return { audio, out: { ...item, spoken_text: '' }, firstSound };
}

/** A pre-started line if it loads, otherwise a normal speech request (the link may have expired or failed). */
export async function prestartedOr(item, fallback) {
  if (item) {
    const line = prestartedLine(item);
    const ok = await Promise.race([
      new Promise(r => line.audio.addEventListener('canplay', () => r(true), { once: true })),
      new Promise(r => line.audio.addEventListener('error', () => r(false), { once: true })),
      new Promise(r => setTimeout(() => r(true), 4000)),
    ]);
    if (ok) return line;
  }
  return fallback();
}

/** Play to the end (or until paused/failed). Rejects only when autoplay is blocked. */
export function playToEnd(audio) {
  return new Promise((resolve, reject) => {
    const done = () => resolve();
    audio.addEventListener('ended', done, { once: true });
    audio.addEventListener('error', done, { once: true });
    audio.addEventListener('pause', () => { if (!audio.ended) resolve(); }, { once: true });
    audio.play().catch(reject);
  });
}

/** Human label for a spoken line's metadata. */
export function speechMeta(out, firstMs) {
  const model = (out.model || '').replace('bulbul:', 'Bulbul ');
  return `${model} · ${out.transport === 'stream' ? 'streamed' : 'REST'} · ${out.speaker} · pace ${out.pace}${firstMs ? ` · first sound ${firstMs} ms` : out.ms ? ` · ${Math.round(out.ms)} ms` : ''}${out.transliterated ? ' · transliterated' : ''}`;
}

/**
 * Realtime listening: microphone → this server → Sarvam's streaming STT. Words arrive while the person speaks; a pause
 * ends the turn. ``onSpeechStart`` fires the moment speech is detected — pages stop the bot's audio there (barge-in).
 */
export class RealtimeListener {
  constructor({ language = 'hi-IN', eng, place = 'call', onPartial, onFinal, onSpeechStart, onSpeechEnd, onState, onError, onCheck } = {}) {
    Object.assign(this, { language, eng, place, onPartial, onFinal, onSpeechStart, onSpeechEnd, onState, onError, onCheck });
    this.live = false;
  }

  async start() {
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) throw new Error('This browser cannot stream the microphone.');
    // Owner, 2026-10-10: "mic access is granted but something breaks after that" — an AudioContext created after the
    // ticket request and the permission prompt can stay suspended (the click no longer counts), so no audio frames ever
    // left the page and Sarvam heard nothing. Create and resume it first, while the click is still current.
    this.ctx = new AudioContext();
    const resumed = this.ctx.resume().catch(() => {});
    this.frames = 0; this.peak = 0;
    this.onState?.('connecting');
    let ticket;
    try {
      ticket = await api('/api/v5/listen/ticket', { method: 'POST',
        body: { language: this.language, model: this.eng?.stt_realtime_model || null, mode: this.eng?.stt_mode || null, place: this.place } });
      this.info = ticket;
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } })
        .catch(() => { throw new Error('Microphone permission was not given.'); });
    } catch (err) { this.ctx?.close().catch(() => {}); this.ctx = null; throw err; }
    await resumed;
    await this.ctx.audioWorklet.addModule(`${BASE}/static/cc/audio-worklets.js`);
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, 'sg-pcm16k');
    const mute = this.ctx.createGain(); mute.gain.value = 0;
    source.connect(this.node); this.node.connect(mute); mute.connect(this.ctx.destination);
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.ws = new WebSocket(`${proto}//${location.host}${BASE}${ticket.path}?ticket=${encodeURIComponent(ticket.ticket)}`);
    this.ws.binaryType = 'arraybuffer';
    this.node.port.onmessage = e => {
      if (this.ws?.readyState === 1) this.ws.send(e.data);
      // When did the person last make a sound? (local energy, so reply times can be measured from their real end of speech)
      const pcm = new Int16Array(e.data);
      let sum = 0;
      for (let i = 0; i < pcm.length; i += 4) sum += pcm[i] * pcm[i];
      const rms = Math.sqrt(sum / (pcm.length / 4));
      this.frames++; this.peak = Math.max(this.peak, rms);
      if (rms > 700) this.lastVoiceAt = performance.now();
    };
    this.ws.onopen = () => {
      this.live = true; this.onState?.('listening');
      this.watch = setTimeout(() => this.check(true), 3000);
      this.watch2 = setTimeout(() => this.check(false), 10000);
    };
    this.ws.onmessage = e => this.handle(e.data);
    this.ws.onerror = () => this.onError?.('The listening connection failed.');
    this.ws.onclose = () => { this.cleanup(); this.onState?.('closed'); };
    this.cap = setTimeout(() => this.stop(), (ticket.max_seconds || 90) * 1000);
    await this.ctx.resume().catch(() => {});
    return ticket;
  }

  handle(raw) {
    let ev;
    try { ev = JSON.parse(raw); } catch { return; }
    if (ev.event === 'vad.speech_start') this.onSpeechStart?.();
    else if (ev.event === 'vad.speech_end') this.onSpeechEnd?.();
    else if (ev.event === 'transcript.partial') this.onPartial?.(ev.text || '');
    else if (ev.event === 'transcript.final') {
      if ((ev.text || '').trim()) { trace('final', { after_voice_ms: this.lastVoiceAt ? Math.round(performance.now() - this.lastVoiceAt) : null }); this.onFinal?.(ev.text.trim(), ev.language, this.lastVoiceAt); }
    }
    else if (ev.event === 'error') { this.onError?.(ev.message || 'Listening error.'); if (ev.is_fatal) this.stop(); }
  }

  /** Is microphone audio really flowing? Tells the person why not, and sends the numbers to the server log. */
  check(warn) {
    const track = this.stream?.getAudioTracks?.()[0];
    const label = (track?.label || 'the microphone').slice(0, 60);
    const stats = { event: 'stats', frames: this.frames, peak: Math.round(this.peak), rate: this.ctx?.sampleRate || null, ctx: this.ctx?.state || null,
                    track: track ? { muted: track.muted, state: track.readyState, enabled: track.enabled, label } : null,
                    ua: navigator.userAgent.slice(0, 160) };
    if (this.ws?.readyState === 1) this.ws.send(JSON.stringify(stats));
    trace('mic_check', stats);
    this.onCheck?.({ ...stats, model: this.info?.model, mode: this.info?.mode, language: this.info?.language });
    if (!warn || !this.live) return;
    if (!this.frames) {
      this.ctx?.resume().catch(() => {});
      this.onError?.('No microphone audio is reaching the page — the browser paused its audio engine. Click anywhere on the page, then press Talk live again.');
    } else if (track?.muted || track?.readyState === 'ended') {
      this.onError?.(`"${label}" is muted or was taken by another app — unmute it or close the other app, then try again.`);
    } else if (this.peak < 3) {
      this.onError?.(`"${label}" is sending silence — choose another input in the browser's site settings (🔒 in the address bar) or the system sound settings.`);
    }
  }

  flush() { if (this.ws?.readyState === 1) this.ws.send(JSON.stringify({ event: 'flush' })); }

  stop() {
    clearTimeout(this.cap); clearTimeout(this.watch); clearTimeout(this.watch2);
    this.stream?.getTracks().forEach(t => t.stop());
    if (this.ws?.readyState === 1) {
      this.ws.send(JSON.stringify({ event: 'end' }));  // the server waits briefly for the last final transcript
      setTimeout(() => { try { this.ws?.close(); } catch { /* closed */ } }, 3000);
    } else { try { this.ws?.close(); } catch { /* closed */ } }
    this.live = false;
  }

  cleanup() {
    clearTimeout(this.cap); clearTimeout(this.watch); clearTimeout(this.watch2);
    this.stream?.getTracks().forEach(t => t.stop());
    this.node?.disconnect();
    this.ctx?.close().catch(() => {});
    this.ctx = null; this.node = null; this.stream = null; this.ws = null; this.live = false;
  }
}

/** Record a clip (press to start, again to stop; 20 s cap) and transcribe it with the chosen clip model. */
export async function recordClip({ onRecording } = {}) {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('This browser cannot record audio.');
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch { throw new Error('Microphone permission was not given.'); }
  const chunks = [];
  const rec = new MediaRecorder(stream);
  rec.ondataavailable = e => chunks.push(e.data);
  const done = new Promise(resolve => { rec.onstop = () => { stream.getTracks().forEach(t => t.stop()); resolve(new Blob(chunks, { type: rec.mimeType || 'audio/webm' })); }; });
  rec.start();
  onRecording?.(rec);
  setTimeout(() => rec.state === 'recording' && rec.stop(), 20000);
  return { recorder: rec, done };
}

export async function transcribe(blob, eng, language = 'unknown') {
  const form = new FormData();
  form.append('file', blob, 'clip.webm');
  form.append('language', language);
  if (eng?.stt_model) form.append('model', eng.stt_model);
  if (eng?.stt_mode) form.append('mode', eng.stt_mode);
  return api('/api/v5/listen', { method: 'POST', form });
}

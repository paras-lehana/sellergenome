/* Microphone worklets (5.3), served from /static so the page CSP (script-src 'self') allows them.
 *
 * - "sg-pcm16k": mono 16-bit PCM at 16 kHz in 100 ms frames (3200 bytes) for realtime listening. The device rate is
 *   resampled here, never assumed.
 * - "capture": the processor Sarvam's conversational SDK uses (it posts raw float frames). The SDK normally loads it
 *   from a blob: URL, which the CSP blocks; payal-live.js loads this file instead.
 */
class SgPcm16k extends AudioWorkletProcessor {
  constructor() { super(); this.samples = new Int16Array(1600); this.used = 0; this.phase = 0; this.previous = 0; this.first = true; }
  process(inputs, outputs) {
    for (const output of outputs) for (const channel of output) channel.fill(0);
    const input = inputs[0]?.[0];
    if (!input) return true;
    const step = sampleRate / 16000;
    for (let i = 0; i < input.length; i++) {
      const current = input[i];
      if (this.first) { this.previous = current; this.first = false; }
      while (this.phase < 1) {
        const value = this.previous + (current - this.previous) * this.phase;
        this.samples[this.used++] = Math.round(Math.max(-1, Math.min(1, value)) * 32767);
        if (this.used === 1600) {
          const frame = new ArrayBuffer(3200), view = new DataView(frame);
          for (let n = 0; n < 1600; n++) view.setInt16(n * 2, this.samples[n], true);
          this.port.postMessage(frame, [frame]);
          this.used = 0;
        }
        this.phase += step;
      }
      this.phase -= 1;
      this.previous = current;
    }
    return true;
  }
}
registerProcessor('sg-pcm16k', SgPcm16k);

class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor('capture', Capture);

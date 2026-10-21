// AudioWorklet: mono Float32 at the context's sample rate -> 16 kHz int16, posted as exact
// 20 ms frames (320 samples = 640 bytes), the format /ws/audio expects.
// When the context already runs at 16 kHz (preferred) this is a straight conversion; otherwise it
// low-passes (moving average over the decimation span) and resamples linearly.

const TARGET_RATE = 16000;
const FRAME_SAMPLES = 320;

class PcmFramer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / TARGET_RATE; // input samples per output sample
    this.pos = 0; // fractional read position into `pending`
    this.pending = new Float32Array(0);
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
    this.span = Math.max(1, Math.round(this.step));
  }

  emit(sample) {
    const s = Math.max(-1, Math.min(1, sample));
    this.frame[this.filled++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    if (this.filled === FRAME_SAMPLES) {
      this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
      this.frame = new Int16Array(FRAME_SAMPLES);
      this.filled = 0;
    }
  }

  process(inputs) {
    const channels = inputs[0];
    if (!channels || channels.length === 0) return true;
    const input = channels[0];
    if (this.step === 1) {
      for (let i = 0; i < input.length; i++) this.emit(input[i]);
      return true;
    }
    const buf = new Float32Array(this.pending.length + input.length);
    buf.set(this.pending);
    buf.set(input, this.pending.length);
    let pos = this.pos;
    while (pos + this.span < buf.length) {
      const i = Math.floor(pos);
      let acc = 0;
      for (let k = 0; k < this.span; k++) acc += buf[i + k];
      this.emit(acc / this.span);
      pos += this.step;
    }
    const keep = Math.floor(pos);
    this.pending = buf.slice(keep);
    this.pos = pos - keep;
    return true;
  }
}

registerProcessor("pcm-framer", PcmFramer);

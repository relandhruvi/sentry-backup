// Audio sources for a call. Both produce 16 kHz mono int16 PCM chunks for CallSession.sendPcm.

const TARGET_RATE = 16000;
const FRAME_SAMPLES = 320; // 20 ms

export interface Source {
  stop(): void;
  done: Promise<void>; // resolves when the source ends (file finished, or stopped)
  label: string;
}

/** Browser microphone -> AudioWorklet (pcm-framer) -> 20 ms int16 frames. */
export async function startMic(onPcm: (chunk: ArrayBuffer) => void): Promise<Source> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  let ctx: AudioContext;
  try {
    ctx = new AudioContext({ sampleRate: TARGET_RATE }); // browser resamples the mic for us
  } catch {
    ctx = new AudioContext(); // the worklet resamples instead
  }
  await ctx.audioWorklet.addModule("/pcm-worklet.js");
  const source = ctx.createMediaStreamSource(stream);
  const framer = new AudioWorkletNode(ctx, "pcm-framer");
  framer.port.onmessage = (m) => onPcm(m.data as ArrayBuffer);
  const sink = ctx.createGain();
  sink.gain.value = 0; // keeps the graph pulling without echoing the mic to the speakers
  source.connect(framer).connect(sink).connect(ctx.destination);

  let resolveDone!: () => void;
  const done = new Promise<void>((r) => (resolveDone = r));
  const track = stream.getAudioTracks()[0];
  return {
    label: track?.label || "microphone",
    done,
    stop() {
      stream.getTracks().forEach((t) => t.stop());
      void ctx.close();
      resolveDone();
    },
  };
}

/** Decode an audio file, resample to 16 kHz mono, and send it in real time while playing it. */
export async function streamFile(file: File, onPcm: (chunk: ArrayBuffer) => void): Promise<Source> {
  const ctx = new AudioContext();
  const decoded = await ctx.decodeAudioData(await file.arrayBuffer());
  const length = Math.ceil(decoded.duration * TARGET_RATE);
  const offline = new OfflineAudioContext(1, length, TARGET_RATE);
  const src = offline.createBufferSource();
  src.buffer = decoded;
  src.connect(offline.destination);
  src.start();
  const mono = (await offline.startRendering()).getChannelData(0);
  const pcm = new Int16Array(mono.length);
  for (let i = 0; i < mono.length; i++) {
    const s = Math.max(-1, Math.min(1, mono[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }

  const player = ctx.createBufferSource(); // the audience hears the call
  player.buffer = decoded;
  player.connect(ctx.destination);
  player.start();

  let resolveDone!: () => void;
  const done = new Promise<void>((r) => (resolveDone = r));
  const t0 = performance.now();
  let sent = 0;
  const timer = window.setInterval(() => {
    const due = Math.min(pcm.length, Math.floor(((performance.now() - t0) / 1000) * TARGET_RATE));
    const upto = due - (due % FRAME_SAMPLES);
    if (upto > sent) {
      onPcm(pcm.slice(sent, upto).buffer);
      sent = upto;
    }
    if (sent >= pcm.length - (pcm.length % FRAME_SAMPLES)) finish();
  }, 20);

  function finish() {
    window.clearInterval(timer);
    try {
      player.stop();
    } catch {
      /* already ended */
    }
    void ctx.close();
    resolveDone();
  }
  return { label: file.name, done, stop: finish };
}

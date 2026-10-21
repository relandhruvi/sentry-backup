// One call session against the backend: create, subscribe to events, stream audio, end.
// Mic and file upload both end up in `sendFrame`, i.e. the same /ws/audio path.

import type { Envelope } from "./types";

const FRAME_BYTES = 640;

function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

export class CallSession {
  readonly id: string;
  private events?: WebSocket;
  private audio?: WebSocket;
  private pending: Uint8Array[] = [];

  private constructor(id: string) {
    this.id = id;
  }

  static async create(): Promise<CallSession> {
    let res: Response;
    try {
      res = await fetch("/sessions", { method: "POST" });
    } catch {
      throw new Error("Sentry's backend is not responding on port 8000. Start the backend in a second terminal, then try again.");
    }
    if (!res.ok) {
      if (res.status >= 500) {
        throw new Error("Sentry's backend is not responding correctly on port 8000. Check that the backend is running, then try again.");
      }
      throw new Error(`Could not create an inspection session (${res.status}).`);
    }
    return new CallSession((await res.json()).session_id);
  }

  subscribe(onEvent: (e: Envelope) => void, onClose: (reason: string) => void): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl(`/ws/events/${this.id}`));
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error("event stream failed"));
      ws.onmessage = (m) => onEvent(JSON.parse(m.data));
      ws.onclose = (c) => onClose(c.reason || `closed (${c.code})`);
      this.events = ws;
    });
  }

  /** Open /ws/audio. The server holds the socket while STT warms up and refuses it (4503) if
   *  the model still isn't ready. */
  openAudio(onClose: (code: number, reason: string) => void): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl(`/ws/audio/${this.id}`));
      ws.binaryType = "arraybuffer";
      let opened = false;
      ws.onopen = () => {
        opened = true;
        resolve();
      };
      ws.onerror = () => {
        if (!opened) reject(new Error("audio stream failed"));
      };
      ws.onclose = (c) => {
        if (!opened) reject(new Error(c.reason || `audio refused (${c.code})`));
        onClose(c.code, c.reason);
      };
      this.audio = ws;
    });
  }

  /** Send int16 PCM; buffered into exact 640-byte frames. */
  sendPcm(chunk: ArrayBuffer): void {
    if (!this.audio || this.audio.readyState !== WebSocket.OPEN) return;
    this.pending.push(new Uint8Array(chunk));
    const total = this.pending.reduce((n, p) => n + p.length, 0);
    if (total < FRAME_BYTES) return;
    const all = new Uint8Array(total);
    let off = 0;
    for (const p of this.pending) {
      all.set(p, off);
      off += p.length;
    }
    const whole = total - (total % FRAME_BYTES);
    this.audio.send(all.slice(0, whole));
    this.pending = whole < total ? [all.slice(whole)] : [];
  }

  /** Stop audio and end the session; the server drains in-flight transcriptions first. */
  async end(): Promise<void> {
    this.audio?.close();
    this.audio = undefined;
    await fetch(`/sessions/${this.id}/end`, { method: "POST" }).catch(() => undefined);
  }

  closeEvents(): void {
    this.events?.close();
  }
}

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import Check from "./Check";
import { AlertPanel, HealthStrip, RiskChart, RiskMeter, TacticChain, Transcript, WhyPanel } from "./components/Panels";
import { startMic, streamFile, type Source } from "./lib/audio";
import { CallSession } from "./lib/session";
import { initialState, reduce } from "./lib/state";
import WaitlistLanding from "./WaitlistLanding";
import DashboardShell from "./components/ui/dashboard-shell";

type Phase = "idle" | "starting" | "live" | "ending";

export default function App() {
  const path = location.pathname;
  if (path === "/waitlist") {
    window.history.replaceState(null, "", "/");
    return <WaitlistLanding />;
  }
  if (path === "/") return <WaitlistLanding />;
  if (path === "/demo") return <Demo />;
  if (path === "/check" || path === "/past/check") return <Check />;
  if (path === "/inspect") return <ChooseInspection />;
  if (path === "/past") return <PastCall />;
  return <WaitlistLanding />;
}

function StepHeader({ step, title, subtitle }: { step: string; title: string; subtitle: string }) {
  return <><div className="step-label"><a href="/">Sentry</a><span>/</span><span>{step}</span></div><h1 className="flow-title">{title}</h1><p className="flow-subtitle">{subtitle}</p></>;
}

function ChooseInspection() {
  return <DashboardShell><div className="flow-shell"><main className="flow-main wrap"><StepHeader step="Start inspection" title="What would you like to check?" subtitle="Choose the situation that best matches what’s happening."/><div className="choice-grid">
    <a className="choice-card choice-live" href="/demo"><div className="choice-icon">◉</div><span className="choice-kicker">HAPPENING NOW</span><h2>Live call</h2><p>Listen through this device’s microphone and get an alert if concerning signals appear.</p><div className="choice-meta"><span className="mini-green"/> Browser microphone required</div><span className="choice-arrow">↗</span></a>
    <a className="choice-card choice-past" href="/past"><div className="choice-icon">↺</div><span className="choice-kicker">HAPPENED ALREADY</span><h2>Past call</h2><p>Upload a recording, describe what happened, or check a caller’s details.</p><div className="choice-meta">3 ways to review <span>·</span> Choose what feels easiest</div><span className="choice-arrow">↗</span></a>
  </div><div className="help-strip"><span>✳</span><p><b>Not sure yet?</b> That’s okay. You can start with a description and decide what to do next.</p><a href="/check">Tell us what happened →</a></div><div className="flow-privacy">Sentry gives guidance based on available signals. No automated result can guarantee a caller is safe.</div></main></div></DashboardShell>;
}

function PastCall() {
  const input = useRef<HTMLInputElement>(null);
  const [recording, setRecording] = useState<File | null>(null);
  if (recording) return <Demo initialFile={recording}/>;
  return <DashboardShell particleCount={180} context={<a href="/inspect">← Back to inspection types</a>}><div className="flow-shell"><main className="flow-main wrap"><StepHeader step="Past call" title="How would you like to review it?" subtitle="Pick one. You only need to share the details you’re comfortable with."/><div className="past-grid">
    <button className="past-option" onClick={() => input.current?.click()}><span className="past-icon">♫</span><span className="past-tag">AUDIO</span><h2>Play a recording</h2><p>Choose an audio file and Sentry will analyze the conversation as it plays.</p><span className="past-action">Choose recording <b>↑</b></span><input ref={input} type="file" accept="audio/*,.wav" hidden onChange={(e) => { const f = e.currentTarget.files?.[0]; if (f) setRecording(f); }}/></button>
    <a className="past-option" href="/check?mode=story"><span className="past-icon">☷</span><span className="past-tag">YOUR STORY</span><h2>Tell us what happened</h2><p>Describe what the caller said, what they asked you to do, and what felt unusual.</p><span className="past-action">Write a description <b>→</b></span></a>
    <a className="past-option" href="/check"><span className="past-icon">⌕</span><span className="past-tag">QUICK CHECK</span><h2>Use the checkers</h2><p>Check the claimed organization, phone number, link, UPI ID, or requested action.</p><span className="past-action">Check caller details <b>→</b></span></a>
  </div><div className="flow-privacy">Recording analysis is sent to the Sentry backend for transcription and risk analysis. You can stop playback at any time.</div></main></div></DashboardShell>;
}

function Demo({ initialFile }: { initialFile?: File }) {
  const [state, dispatch] = useReducer(reduce, initialState);
  const [phase, setPhase] = useState<Phase>("idle");
  const [source, setSource] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [alertDismissed, setAlertDismissed] = useState(false);
  const session = useRef<CallSession | null>(null);
  const audio = useRef<Source | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadHandled = useRef(false);

  const stop = useCallback(async () => {
    setPhase("ending");
    audio.current?.stop(); audio.current = null;
    await session.current?.end();
    setPhase("idle");
  }, []);

  const start = useCallback(async (open: (onPcm: (b: ArrayBuffer) => void) => Promise<Source>) => {
    setError(""); setAlertDismissed(false); setPhase("starting");
    try {
      const s = await CallSession.create(); session.current = s;
      dispatch({ type: "reset", sessionId: s.id });
      await s.subscribe((event) => dispatch({ type: "event", event }), () => undefined);
      await s.openAudio((code, reason) => { if (code !== 1000 && code !== 1005) setError(`Audio stream closed: ${reason || code}`); });
      const src = await open((chunk) => s.sendPcm(chunk)); audio.current = src; setSource(src.label); setPhase("live");
      void src.done.then(() => { if (audio.current === src) void stop(); });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)); audio.current?.stop(); await session.current?.end(); setPhase("idle");
    }
  }, [stop]);

  const onFile = (f: File | undefined) => { if (f) void start((onPcm) => streamFile(f, onPcm)); if (fileInput.current) fileInput.current.value = ""; };
  useEffect(() => { if (initialFile && !uploadHandled.current) { uploadHandled.current = true; void start((onPcm) => streamFile(initialFile, onPcm)); } }, [initialFile, start]);
  const busy = phase !== "idle";
  const danger = state.risk?.level === "HIGH" || state.risk?.level === "CRITICAL";

  return <DashboardShell particles={false} context={<><span className="dashboard-context-label">/ LIVE INSPECTION</span><a href="/inspect" className="dashboard-context-link">← Change inspection</a></>}>
    <main className="dashboard-main"><div className="dashboard-title-row"><div><div className="live-overline"><span className={phase === "live" ? "live-dot" : ""}/>{phase === "live" ? "LIVE INSPECTION" : "CALL SAFETY"}</div><h1>Stay a step ahead.</h1><p>Listen for pressure and get clear signals while the conversation unfolds.</p></div><div className="dashboard-controls">
      {!busy && <><button className="dash-button dash-primary" onClick={() => void start(startMic)}><span>◉</span> Start live listening</button><button className="dash-button dash-secondary" onClick={() => fileInput.current?.click()}><span>↥</span> Upload recording</button><input ref={fileInput} type="file" accept="audio/*,.wav" className="hidden" onChange={(e) => onFile(e.target.files?.[0])}/></>}
      {busy && <button className="dash-button dash-stop" disabled={phase !== "live"} onClick={() => void stop()}>{phase === "starting" ? "Connecting securely…" : phase === "ending" ? "Finishing inspection…" : "■  Stop inspection"}</button>}
    </div></div>
    <div className="privacy-banner"><span>ⓘ</span><p><b>For this demo, live inspection uses your browser microphone.</b> It does not connect to or control ordinary cellular calls. Recording playback is analyzed by the same Sentry pipeline.</p></div>
    <div className="health-card"><div className="health-heading"><span>System status</span><small>Live backend health check</small></div><HealthStrip/></div>
    {phase === "live" && <div className="source-banner"><span className="live-dot"/> {source.startsWith("microphone") ? "Listening through your microphone" : `Analyzing recording: ${source}`} <span className="source-note">Results update as speech is recognized</span></div>}
    {error && <div className="error-banner">⚠ {error}</div>}
    {state.alert && <AlertPanel alert={state.alert}/>}
    <div className="dashboard-grid"><section className="dashboard-card risk-card"><div className="card-heading"><div><span className="card-kicker">ASSESSMENT</span><h2>Call risk</h2></div><span className={`risk-status ${state.risk ? `risk-${state.risk.level.toLowerCase()}` : ""}`}>{state.risk?.level ?? "WAITING"}</span></div><RiskMeter risk={state.risk}/><WhyPanel reasons={state.risk?.top_reasons ?? []} mode={state.mode}/></section>
      <section className="dashboard-card"><div className="card-heading"><div><span className="card-kicker">WHAT WE HEAR</span><h2>Conversation signals</h2></div></div><TacticChain stages={state.stages} chainTactics={state.chainTactics}/><div className="transcript-wrap"><Transcript utterances={state.utterances} hits={state.hits}/></div></section>
      <section className="dashboard-card chart-card"><div className="card-heading"><div><span className="card-kicker">OVER TIME</span><h2>Risk trend</h2></div></div><RiskChart history={state.history}/></section>
    </div>
    <div className="next-step-note"><span>✳</span><div><b>Use your judgment, even if no warning appears.</b><p>Never share OTPs, PINs, or passwords. If someone asks you to transfer money urgently, pause and verify through an official number.</p></div></div>
    </main>
    {danger && !alertDismissed && <div className="alert-scrim" role="dialog" aria-modal="true" aria-labelledby="live-warning-title"><div className="alert-modal"><button className="modal-close" onClick={() => setAlertDismissed(true)} aria-label="Close warning">×</button><div className="warning-icon">!</div><span className="modal-eyebrow">SENTRY DETECTED CONCERNING SIGNS</span><h2 id="live-warning-title">Pause this call.</h2><p>{state.risk?.top_reasons?.[0]?.en ?? "The conversation may include pressure or a risky request."} Take a moment before you respond.</p><div className="warning-tips"><b>Do not share an OTP, PIN, password, or send money.</b><span>Verify the caller using a number you find independently.</span></div><div className="modal-actions"><button className="end-call-button" onClick={() => { setAlertDismissed(true); void stop(); }}>End inspection now</button><button className="continue-button" onClick={() => setAlertDismissed(true)}>Keep listening</button></div><small>Sentry cannot end a phone call. This only stops the browser inspection.</small></div></div>}
  </DashboardShell>;
}

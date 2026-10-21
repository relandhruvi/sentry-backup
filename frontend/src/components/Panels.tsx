import { useEffect, useState } from "react";
import { LEVEL_STYLE, TACTIC_STYLE } from "../lib/state";
import type { Alert, Entity, Hit, HealthComponent, Level, Reason, Risk, Utterance } from "../lib/types";

// ---------------------------------------------------------------- health strip

const SHOWN = ["stt", "classifier", "lexicon", "voice", "verify", "alerts"] as const;
const LABEL: Record<string, string> = {
  stt: "Speech-to-text",
  classifier: "Classifier",
  lexicon: "Lexicon",
  voice: "Voice check",
  verify: "Verification",
  alerts: "Alerts",
};

function statusColor(s: string): string {
  if (s === "ok") return "bg-emerald-500";
  if (s === "loading") return "bg-sky-500 animate-pulse";
  if (s === "degraded") return "bg-amber-500";
  if (s === "disabled") return "bg-slate-500";
  return "bg-red-500"; // unavailable, unconfigured, unreachable
}

/** Polls /health. Shows exactly what the backend reports; "unreachable" if it can't be asked. */
export function HealthStrip() {
  const [health, setHealth] = useState<Record<string, HealthComponent> | null>(null);
  const [overall, setOverall] = useState<string>("…");
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch("/health");
        const body = await res.json();
        if (alive) {
          setHealth(body.components);
          setOverall(body.status);
        }
      } catch {
        if (alive) {
          setHealth(null);
          setOverall("backend unreachable");
        }
      }
    };
    void poll();
    const id = window.setInterval(poll, 2000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);
  return (
    <div className="health-status-list">
      <span className="health-overall">System: {overall}</span>
      {SHOWN.map((name) => {
        const c = health?.[name];
        const status = c?.status ?? "unreachable";
        return (
          <span key={name} className="health-status-item" data-status={status} title={c?.detail ?? "no answer from backend"}>
            <span className={`health-status-dot ${statusColor(status)}`} />
            <span>{LABEL[name]}</span>
            <span className="health-status-value">{status}</span>
          </span>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- risk meter + chart

export function RiskMeter({ risk }: { risk: Risk | null }) {
  if (!risk) {
    return (
      <div className="risk-meter">
        <div className="eyebrow-label">Risk assessment</div>
        <div className="risk-empty">No speech analysed yet</div>
      </div>
    );
  }
  const pct = Math.round(risk.score * 100);
  return (
    <div className={`risk-meter risk-${risk.level.toLowerCase()}`}>
      <div className="risk-heading-row">
        <div className="eyebrow-label">Risk assessment</div>
        <div className="risk-score">Score {risk.score.toFixed(2)}</div>
      </div>
      <div className="risk-level">{risk.level}</div>
      <div className="risk-progress-track" role="meter" aria-label="Risk score" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <div className="risk-progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="risk-scale">
        <span>LOW</span>
        <span>MEDIUM 0.30</span>
        <span>HIGH 0.55</span>
        <span>CRITICAL 0.80</span>
      </div>
    </div>
  );
}

export function RiskChart({ history }: { history: { t: number; score: number; level: Level }[] }) {
  const W = 600;
  const H = 160;
  const tMax = Math.max(30, ...history.map((p) => p.t));
  const x = (t: number) => (t / tMax) * W;
  const y = (s: number) => H - s * H;
  const path = history.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.score).toFixed(1)}`).join(" ");
  const last = history[history.length - 1];
  return (
    <div className="risk-chart">
      <div className="eyebrow-label">Risk over the call</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="risk-chart-svg" preserveAspectRatio="none">
        {[0.3, 0.55, 0.8].map((v) => (
          <line key={v} x1={0} x2={W} y1={y(v)} y2={y(v)} stroke="#334155" strokeDasharray="6 6" />
        ))}
        {history.length > 0 && (
          <path d={path} fill="none" stroke={LEVEL_STYLE[last.level].stroke} strokeWidth={4} />
        )}
        {history.map((p, i) => (
          <circle key={i} cx={x(p.t)} cy={y(p.score)} r={5} fill={LEVEL_STYLE[p.level].stroke} />
        ))}
      </svg>
      <div className="risk-chart-scale">
        <span>0 s</span>
        <span>{Math.round(tMax)} s</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- tactic chain

const CHAIN = [
  { stage: 1, label: "AUTHORITY", sub: "claims to be an official" },
  { stage: 2, label: "THREAT", sub: "threat or urgency" },
  { stage: 3, label: "SECRECY", sub: "secrecy or isolation" },
  { stage: 4, label: "PAYMENT", sub: "money or OTP demand" },
];

export function TacticChain({ stages, chainTactics }: { stages: number[]; chainTactics: Record<number, string[]> }) {
  return (
    <div className="tactic-chain">
      <div className="eyebrow-label">Scam pattern</div>
      <div className="tactic-chain-steps">
        {CHAIN.map((c, i) => {
          const on = stages.includes(c.stage);
          return (
            <div key={c.stage} className="tactic-step-wrap">
              <div
                className={`tactic-step${on ? " is-active" : ""}`}
              >
                <div className="tactic-step-label">{c.label}</div>
                <div className="tactic-step-detail">{on ? (chainTactics[c.stage] ?? []).join(" + ") : c.sub}</div>
              </div>
              {i < CHAIN.length - 1 && <div className="tactic-arrow" aria-hidden="true">→</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- transcript

function highlight(text: string, hits: Hit[]) {
  // Mark every tactic span that is a real part of the text (not a whole-utterance model span).
  const ranges: { start: number; end: number; tactic: string }[] = [];
  for (const h of hits) {
    if (h.negated || !h.span || h.span === text) continue;
    const start = text.indexOf(h.span);
    if (start >= 0) ranges.push({ start, end: start + h.span.length, tactic: h.tactic });
  }
  ranges.sort((a, b) => a.start - b.start);
  const out: JSX.Element[] = [];
  let pos = 0;
  for (const r of ranges) {
    if (r.start < pos) continue; // overlapping: keep the first
    if (r.start > pos) out.push(<span key={`t${pos}`}>{text.slice(pos, r.start)}</span>);
    out.push(
      <mark key={`h${r.start}`} className={`rounded-md px-1 ring-1 ${TACTIC_STYLE[r.tactic] ?? ""}`} title={r.tactic}>
        {text.slice(r.start, r.end)}
      </mark>,
    );
    pos = r.end;
  }
  if (pos < text.length) out.push(<span key={`t${pos}`}>{text.slice(pos)}</span>);
  return out;
}

export function Transcript({ utterances, hits }: { utterances: Utterance[]; hits: Record<string, Hit[]> }) {
  return (
    <div className="live-transcript">
      <div className="eyebrow-label">Live transcript · newest first</div>
      {utterances.length === 0 && <div className="transcript-empty">Waiting for speech…</div>}
      <ul className="transcript-list">
        {utterances.map((u) => {
          const h = hits[u.utterance_id] ?? [];
          const tactics = [...new Set(h.filter((x) => x.counted && x.score > 0).map((x) => x.tactic))];
          return (
            <li key={u.utterance_id} className="transcript-item">
              <div className="transcript-meta">
                <span>{u.t_start_s.toFixed(1)} s</span>
                {u.lang && <span>· {u.lang}</span>}
                {u.stt_latency_ms != null && <span>· STT {(u.stt_latency_ms / 1000).toFixed(1)} s</span>}
                {tactics.map((t) => (
                  <span key={t} className={`rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${TACTIC_STYLE[t] ?? ""}`}>
                    {t}
                  </span>
                ))}
              </div>
              {u.stt_status === "ok" && u.text ? (
                <div className="text-2xl leading-relaxed">{highlight(u.text, h)}</div>
              ) : (
                <div className="text-xl italic text-amber-400">
                  speech not transcribed: {u.stt_status}
                  {u.detail ? ` (${u.detail})` : ""}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- why + alert

export function WhyPanel({ reasons, mode }: { reasons: Reason[]; mode: string | null }) {
  return (
    <div className="why-panel">
      <div className="why-heading-row">
        <div className="eyebrow-label">Why</div>
        {mode && (
          <div className={`why-mode ${mode === "model" ? "is-model" : "is-fallback"}`}>
            {mode === "model" ? "classifier" : "lexicon fallback (classifier unavailable)"}
          </div>
        )}
      </div>
      {reasons.length === 0 && <div className="reason-empty">No warning signs yet.</div>}
      <ol className="reason-list">
        {reasons.map((r, i) => (
          <li key={`${r.code}${i}`} className="reason-item">
            <div className="reason-title">{r.en}</div>
            <div className="reason-translation">{r.hi}</div>
            {r.evidence_text && <div className="reason-evidence">“{r.evidence_text}”</div>}
          </li>
        ))}
      </ol>
    </div>
  );
}

function claimLabel(e: Entity): string {
  const kinds: Record<string, string> = {
    institution: "Claims to be",
    phone: "Number given",
    upi: "UPI ID given",
    url: "Link given",
    case_id: "Case number",
  };
  return `${kinds[e.kind] ?? e.kind}: ${e.value}`;
}

export function AlertPanel({ alert }: { alert: Alert }) {
  const critical = alert.level === "CRITICAL";
  return (
    <div className={`inspection-alert${critical ? " is-critical" : ""}`}>
      <div className="eyebrow-label">Family alert sent · {alert.level}</div>
      <div className="inspection-alert-title">{alert.title.en}</div>
      <div className="inspection-alert-translation">{alert.title.hi}</div>
      <div className="inspection-alert-action">
        📞 {alert.action.en} · {alert.action.hi}
      </div>
      {alert.claims.length > 0 && (
        <ul className="inspection-alert-claims">
          {alert.claims.map((c) => (
            <li key={`${c.kind}${c.value}`}>• {claimLabel(c)}</li>
          ))}
        </ul>
      )}
      <div className="inspection-alert-verification">
        {alert.verified_official_number
          ? `Official number: ${alert.verified_official_number}`
          : `Official number check: ${alert.verification_status}`}
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import InstitutionPicker, { type Institution } from "./components/InstitutionPicker";
import DashboardShell from "./components/ui/dashboard-shell";
import type { Level } from "./lib/types";

type Lang = "en" | "hi";
type Bi = { en: string; hi: string };

interface Source {
  url: string;
  title: string;
  snippet: string;
  fetched_at: string;
}
interface CheckResult {
  check: "institution_contact" | "advisory" | "public_reports";
  input: Record<string, string>;
  verdict: string;
  status: "ok" | "unavailable" | "skipped";
  official_contact: string | null;
  sources: Source[];
  latency_ms: number;
  cached: boolean;
  fetched_at: string | null;
  confidence: string | null;
  note: Bi;
}
interface Response {
  level: Level;
  score: number;
  tactics: string[];
  top_reasons: (Bi & { code: string })[];
  checks: CheckResult[];
  text_analysis: { label: Bi; mode: string; tactics: string[]; protective: string[] } | null;
  contributions: { signal: string; value: number; weight: number }[];
  coercion_score: number;
  protective_strength: number;
  caps_applied: string[];
  next_steps: Bi[];
  institution_match: {
    status: "resolved" | "ambiguous" | "suggest" | "unknown" | "other" | "none";
    institution: Institution | null;
    candidates: Institution[];
  };
  institution_note: Bi | null;
  verification_status: string;
  verification_capped: boolean;
}

const T = {
  title: { en: "Check this caller", hi: "इस कॉल करने वाले की जाँच करें" },
  intro: {
    en: "Tell us what the caller said. We check official websites and tell you what to do.",
    hi: "बताइए कॉल करने वाले ने क्या कहा। हम आधिकारिक वेबसाइटें जाँचकर बताएँगे कि क्या करें।",
  },
  who: { en: "Who did they say they are?", hi: "उन्होंने खुद को कौन बताया?" },
  name: { en: "Name of the bank / office / company", hi: "बैंक / दफ़्तर / कंपनी का नाम" },
  phone: { en: "Number they called from", hi: "जिस नंबर से कॉल आया" },
  upi: { en: "UPI ID they gave (optional)", hi: "उन्होंने जो यूपीआई आईडी दी (वैकल्पिक)" },
  url: { en: "Website or link they gave (optional)", hi: "उन्होंने जो वेबसाइट या लिंक दिया (वैकल्पिक)" },
  asked: { en: "What did they ask you to do?", hi: "उन्होंने आपसे क्या करने को कहा?" },
  message: {
    en: "Paste their message (optional, experimental)",
    hi: "उनका संदेश यहाँ चिपकाएँ (वैकल्पिक, प्रायोगिक)",
  },
  submit: { en: "Check now", hi: "अभी जाँचें" },
  checking: { en: "Checking official sources…", hi: "आधिकारिक स्रोत जाँचे जा रहे हैं…" },
  steps: { en: "What to do now", hi: "अब क्या करें" },
  why: { en: "Why", hi: "क्यों" },
  official: { en: "Official number", hi: "आधिकारिक नंबर" },
  sources: { en: "Sources", hi: "स्रोत" },
  cached: { en: "cached, fetched at", hi: "पहले से सहेजा गया, लिया गया" },
  unavailable: {
    en: "Online checks are unavailable right now. The advice below is based only on what you told us.",
    hi: "ऑनलाइन जाँच अभी उपलब्ध नहीं है। नीचे की सलाह सिर्फ़ आपकी बताई बातों पर आधारित है।",
  },
  risk: { en: "Risk", hi: "खतरा" },
  lowConf: { en: "low confidence", hi: "कम भरोसेमंद" },
} satisfies Record<string, Bi>;

const TYPES: [string, Bi][] = [
  ["bank", { en: "Bank", hi: "बैंक" }],
  ["police", { en: "Police / CBI", hi: "पुलिस / सीबीआई" }],
  ["government", { en: "Government office", hi: "सरकारी दफ़्तर" }],
  ["courier", { en: "Courier", hi: "कूरियर" }],
  ["telecom", { en: "Mobile company", hi: "मोबाइल कंपनी" }],
  ["company", { en: "Other company", hi: "दूसरी कंपनी" }],
  ["relative", { en: "Relative / friend", hi: "रिश्तेदार / दोस्त" }],
  ["other", { en: "Someone else", hi: "कोई और" }],
];
const ASKS: [string, Bi][] = [
  ["otp", { en: "Tell an OTP / PIN", hi: "ओटीपी / पिन बताना" }],
  ["money", { en: "Send money", hi: "पैसे भेजना" }],
  ["video_call", { en: "Join a video call", hi: "वीडियो कॉल पर आना" }],
  ["secrecy", { en: "Keep it secret", hi: "बात छिपाकर रखना" }],
  ["install_app", { en: "Install an app", hi: "कोई ऐप डालना" }],
  ["go_alone", { en: "Go somewhere alone", hi: "अकेले कहीं जाना" }],
];
const CHECK_TITLE: Record<string, Bi> = {
  institution_contact: { en: "Official phone number", hi: "आधिकारिक फ़ोन नंबर" },
  advisory: { en: "Government warnings", hi: "सरकारी चेतावनियाँ" },
  public_reports: { en: "Public fraud reports", hi: "धोखाधड़ी की सार्वजनिक शिकायतें" },
};
const VERDICT_STYLE: Record<string, string> = {
  MISMATCH: "bg-red-600",
  REPORTS_FOUND: "bg-orange-600",
  ADVISORY_FOUND: "bg-sky-700",
  MATCH: "bg-emerald-700",
  NO_REPORTS_FOUND: "bg-slate-600",
  UNKNOWN: "bg-slate-600",
  UNAVAILABLE: "bg-slate-700",
};
const VERDICT_LABEL: Record<string, Bi> = {
  MATCH: { en: "MATCH", hi: "नंबर सही" },
  MISMATCH: { en: "MISMATCH", hi: "नंबर मेल नहीं खाता" },
  UNKNOWN: { en: "UNKNOWN", hi: "पता नहीं" },
  ADVISORY_FOUND: { en: "ADVISORY FOUND", hi: "चेतावनी मिली" },
  REPORTS_FOUND: { en: "REPORTS FOUND", hi: "शिकायतें मिलीं" },
  NO_REPORTS_FOUND: { en: "NO REPORTS FOUND", hi: "कोई शिकायत नहीं मिली" },
  UNAVAILABLE: { en: "UNAVAILABLE", hi: "उपलब्ध नहीं" },
};
const LEVEL_BG: Record<Level, string> = {
  LOW: "bg-emerald-700",
  MEDIUM: "bg-amber-600",
  HIGH: "bg-orange-600",
  CRITICAL: "bg-red-700",
};
const LEVEL_TEXT: Record<Level, Bi> = {
  LOW: { en: "Low risk", hi: "कम खतरा" },
  MEDIUM: { en: "Be careful", hi: "सावधान रहें" },
  HIGH: { en: "Likely a scam", hi: "शायद धोखाधड़ी" },
  CRITICAL: { en: "Scam: stop now", hi: "धोखाधड़ी: अभी रुकें" },
};

const TACTIC_LABEL: Record<string, Bi> = {
  AUTHORITY: { en: "Claimed authority", hi: "अधिकारी होने का दावा" },
  THREAT: { en: "Threats", hi: "धमकी" },
  URGENCY: { en: "Rushing you", hi: "जल्दबाज़ी का दबाव" },
  SECRECY: { en: "Secrecy", hi: "बात छिपाने को कहना" },
  ISOLATION: { en: "Keeping you alone", hi: "अकेला रखना" },
  PAYMENT: { en: "Money demand", hi: "पैसे की माँग" },
  CREDENTIAL: { en: "OTP / PIN demand", hi: "ओटीपी / पिन की माँग" },
};
const SOURCE_LABEL: Record<string, Bi> = {
  form: { en: "you reported it", hi: "आपने बताया" },
  model: { en: "found in the message", hi: "संदेश में मिला" },
  lexicon: { en: "found in the message", hi: "संदेश में मिला" },
};
const CAP_LABEL: Record<string, Bi> = {
  identity_only: {
    en: "Capped at LOW: a claimed identity alone is not a warning sign",
    hi: "LOW तक सीमित: सिर्फ़ पहचान का दावा खतरे का संकेत नहीं है",
  },
  verification_alone: {
    en: "Capped below HIGH: online checks alone can't make it HIGH",
    hi: "HIGH से नीचे सीमित: सिर्फ़ ऑनलाइन जाँच से HIGH नहीं होता",
  },
  single_tactic_or_utterance: {
    en: "Capped below HIGH: only one warning sign so far",
    hi: "HIGH से नीचे सीमित: अभी तक सिर्फ़ एक खतरे का संकेत",
  },
  critical_gate: {
    en: "Capped below CRITICAL: no money/OTP demand together with threats or secrecy",
    hi: "CRITICAL से नीचे सीमित: धमकी या गोपनीयता के साथ पैसे/ओटीपी की माँग नहीं",
  },
};
const VERIFY_LABEL: Record<string, Bi> = {
  MISMATCH: { en: "Number not on the official website", hi: "नंबर आधिकारिक वेबसाइट पर नहीं" },
  REPORTS_FOUND: { en: "Fraud reports found online", hi: "इंटरनेट पर धोखाधड़ी की शिकायतें मिलीं" },
};

function Breakdown({ res, t }: { res: Response; t: (b: Bi) => string }) {
  const rows: { label: string; effect: string; tone: string }[] = [];
  for (const c of res.contributions) {
    const parts = c.signal.split(".");
    if (parts[0] === "coercion" && parts.length === 3 && TACTIC_LABEL[parts[1]]) {
      const src = SOURCE_LABEL[parts[2]] ? ` (${t(SOURCE_LABEL[parts[2]])})` : "";
      rows.push({ label: `${t(TACTIC_LABEL[parts[1]])}${src}`, effect: `+${(c.value * c.weight).toFixed(2)}`, tone: "text-red-300" });
    } else if (c.signal === "coercion.chain_factor") {
      rows.push({
        label: t({ en: "Scam-pattern stages reached (multiplier)", hi: "धोखाधड़ी के तरीके के चरण (गुणक)" }),
        effect: `× ${c.value.toFixed(2)}`,
        tone: "text-slate-300",
      });
    } else if (c.signal === "coercion.protective") {
      rows.push({
        label: t({ en: "Genuine advice heard (lowers risk)", hi: "असली सलाह सुनी गई (खतरा कम)" }),
        effect: `${(c.value * 100).toFixed(0)}%`,
        tone: "text-emerald-300",
      });
    } else if (parts[0] === "verify") {
      rows.push({ label: t(VERIFY_LABEL[parts[1]] ?? { en: parts[1], hi: parts[1] }), effect: `+${c.weight.toFixed(2)}`, tone: "text-orange-300" });
    }
  }
  return (
    <div className="rounded-3xl bg-slate-900 p-6 ring-1 ring-slate-700">
      <div className="mb-3 text-2xl text-slate-300">{t({ en: "Why this level", hi: "यह स्तर क्यों" })}</div>
      <table className="w-full text-xl">
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td className="py-1 text-slate-400">{t({ en: "No warning signs reported", hi: "कोई खतरे का संकेत नहीं बताया गया" })}</td>
            </tr>
          )}
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-slate-800">
              <td className="py-2">{r.label}</td>
              <td className={`py-2 text-right font-mono ${r.tone}`}>{r.effect}</td>
            </tr>
          ))}
          <tr>
            <td className="pt-3 text-slate-400">{t({ en: "Warning-sign score", hi: "खतरे के संकेतों का स्कोर" })}</td>
            <td className="pt-3 text-right font-mono">{res.coercion_score.toFixed(2)}</td>
          </tr>
          <tr>
            <td className="text-slate-300">{t({ en: "Final score (with online checks)", hi: "अंतिम स्कोर (ऑनलाइन जाँच सहित)" })}</td>
            <td className="text-right font-mono font-bold">{res.score.toFixed(2)} → {res.level}</td>
          </tr>
        </tbody>
      </table>
      {res.caps_applied.map((c) => (
        <div key={c} className="mt-2 text-lg text-amber-300">
          • {t(CAP_LABEL[c] ?? { en: c, hi: c })}
        </div>
      ))}
      <div className="mt-2 text-base text-slate-500">LOW &lt; 0.30 ≤ MEDIUM &lt; 0.55 ≤ HIGH &lt; 0.80 ≤ CRITICAL</div>
    </div>
  );
}

const input = "check-field";

export default function Check() {
  const [lang, setLang] = useState<Lang>("en");
  const [type, setType] = useState("bank");
  const [name, setName] = useState("");
  const [institutionId, setInstitutionId] = useState<string | null>(null);
  const [directory, setDirectory] = useState<Institution[]>([]);
  const [phone, setPhone] = useState("");
  const [upi, setUpi] = useState("");
  const [url, setUrl] = useState("");
  const [asked, setAsked] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [res, setRes] = useState<Response | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const t = (b: Bi) => b[lang];
  const searchable = !["relative", "other"].includes(type);

  useEffect(() => {
    fetch("/verify/institutions")
      .then((r) => r.json())
      .then(setDirectory)
      .catch(() => setDirectory([]));
  }, []);

  const submit = async (pickedId: string | null = institutionId) => {
    setBusy(true);
    setError("");
    setRes(null);
    const t0 = performance.now();
    try {
      const r = await fetch("/verify/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          claimed_identity_type: type,
          claimed_identity_name: searchable && !pickedId ? name : "",
          institution_id: searchable ? pickedId : null,
          phone: phone || null,
          upi: upi || null,
          url: url || null,
          asked_to: asked,
          message_text: message || null,
        }),
      });
      const body = await r.json();
      if (r.status === 422) {
        const fields = (body.detail ?? []).map((d: { loc: string[]; msg: string }) => `${d.loc.at(-1)}: ${d.msg}`);
        setError(fields.join(" · ") || "invalid input");
      } else if (!r.ok) {
        setError(body.detail ?? `error ${r.status}`);
      } else {
        setRes(body);
      }
    } catch {
      setError("backend unreachable");
    } finally {
      setElapsed((performance.now() - t0) / 1000);
      setBusy(false);
    }
  };

  return (
    <DashboardShell context={<a href="/past">← Past call</a>}>
    <main className="check-shell mx-auto max-w-5xl space-y-8 p-6">
      <header className="check-page-heading">
        <div>
          <h1 className="text-5xl font-black">{t(T.title)}</h1>
          <p className="mt-2 text-2xl text-slate-300">{t(T.intro)}</p>
        </div>
        <div className="check-language-toggle">
          {(["en", "hi"] as Lang[]).map((l) => (
            <button
              key={l}
              onClick={() => setLang(l)}
              aria-pressed={lang === l}
              className={`check-language-option${lang === l ? " is-active" : ""}`}
            >
              {l === "en" ? "English" : "हिन्दी"}
            </button>
          ))}
        </div>
      </header>

      <section className="check-form space-y-6 rounded-3xl bg-slate-900 p-6 ring-1 ring-slate-700">
        <div>
            <div className="check-question">{t(T.who)}</div>
          <div className="flex flex-wrap gap-3">
            {TYPES.map(([v, label]) => (
              <button
                key={v}
                onClick={() => {
                  setType(v);
                  setInstitutionId(null);
                }}
                aria-pressed={type === v}
                className={`check-choice-chip${type === v ? " is-selected" : ""}`}
              >
                {t(label)}
              </button>
            ))}
          </div>
        </div>
        {searchable ? (
          <div>
            <div className="check-question">{t(T.name)}</div>
            <InstitutionPicker
              items={directory}
              type={type}
              value={institutionId}
              text={name}
              onPick={setInstitutionId}
              onText={setName}
              t={t}
            />
          </div>
        ) : (
          <div className="check-note">
            {t({
              en: "We never search people's names online. We'll check the number and what they asked you to do.",
              hi: "हम लोगों के नाम कभी ऑनलाइन नहीं खोजते। हम नंबर और उनकी माँगों की जाँच करेंगे।",
            })}
          </div>
        )}
        <label className="block">
          <div className="check-question">{t(T.phone)}</div>
          <input className={input} inputMode="tel" value={phone} placeholder="98765 43210" onChange={(e) => setPhone(e.target.value)} />
        </label>
        <div className="grid gap-6 md:grid-cols-2">
          <label className="block">
            <div className="check-question">{t(T.upi)}</div>
            <input className={input} value={upi} placeholder="name@bank" onChange={(e) => setUpi(e.target.value)} />
          </label>
          <label className="block">
            <div className="check-question">{t(T.url)}</div>
            <input className={input} value={url} placeholder="example.com" onChange={(e) => setUrl(e.target.value)} />
          </label>
        </div>
        <div>
          <div className="check-question">{t(T.asked)}</div>
          <div className="grid gap-3 md:grid-cols-2">
            {ASKS.map(([v, label]) => {
              const on = asked.includes(v);
              return (
                <button
                  key={v}
                  onClick={() => setAsked(on ? asked.filter((a) => a !== v) : [...asked, v])}
                  aria-pressed={on}
                  className={`check-multi-option${on ? " is-selected" : ""}`}
                >
                  <span className="check-option-indicator" aria-hidden="true">{on ? "✓" : ""}</span>
                  {t(label)}
                </button>
              );
            })}
          </div>
        </div>
        <label className="block">
          <div className="check-question">{t(T.message)}</div>
          <textarea className={`${input} check-textarea`} value={message} onChange={(e) => setMessage(e.target.value)} />
        </label>
        <button
          disabled={busy}
          onClick={() => void submit()}
          className="check-submit"
        >
          {busy ? t(T.checking) : t(T.submit)}
        </button>
        {error && <div className="rounded-xl bg-red-900/60 p-4 text-2xl">⚠ {error}</div>}
      </section>

      {res && (
        <section className="check-results space-y-6">
          <div className={`check-result-risk ${LEVEL_BG[res.level]}`}>
            <div className="eyebrow-label">
              {t(T.risk)} · {res.level} · {res.score.toFixed(2)}
              {elapsed != null && ` · ${elapsed.toFixed(1)} s`}
            </div>
            <div className="check-result-title">{t(LEVEL_TEXT[res.level])}</div>
            <div className="check-result-progress" role="meter" aria-label={t(T.risk)} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(res.score * 100)}><span style={{ width: `${Math.max(0, Math.min(100, res.score * 100))}%` }} /></div>
            {res.top_reasons[0] && <p className="check-result-reason">{t(res.top_reasons[0])}</p>}
          </div>

          <div className="check-next-steps">
            <div className="eyebrow-label">{t(T.steps)}</div>
            <ol>
              {res.next_steps.map((s, i) => (
                <li key={i}>{t(s)}</li>
              ))}
            </ol>
          </div>

          {res.verification_status !== "ok" && (
            <div className="check-verification-note">{t(T.unavailable)}</div>
          )}

          {res.top_reasons.length > 0 && (
            <div className="check-result-card check-reasons">
              <div className="eyebrow-label">{t(T.why)}</div>
              <ul>
                {res.top_reasons.map((r, i) => (
                  <li key={i}>• {t(r)}</li>
                ))}
              </ul>
            </div>
          )}

          {res.institution_note && (
            <div className="check-result-card check-institution-note">
              <p>{t(res.institution_note)}</p>
              {["ambiguous", "suggest"].includes(res.institution_match.status) && (
                <div className="mt-4 flex flex-wrap gap-3">
                  {res.institution_match.candidates.map((c) => (
                    <button
                      key={c.id}
                      className="rounded-2xl bg-white px-5 py-3 text-2xl font-bold text-slate-900 hover:bg-sky-100"
                      onClick={() => {
                        setInstitutionId(c.id);
                        void submit(c.id);
                      }}
                    >
                      {t({ en: "Did you mean", hi: "क्या आपका मतलब" })} {lang === "en" ? c.name : c.name_hi}?
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          <Breakdown res={res} t={t} />

          {res.checks.map((c, i) => (
            <div key={i} className="check-result-card check-verdict-card">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="text-2xl font-bold">{t(CHECK_TITLE[c.check])}</div>
                <div className="flex items-center gap-3">
                  {c.confidence === "low" && <span className="text-lg text-amber-300">{t(T.lowConf)}</span>}
                  <span className={`rounded-full px-4 py-1 text-xl font-bold ${VERDICT_STYLE[c.verdict] ?? "bg-slate-600"}`}>
                    {VERDICT_LABEL[c.verdict] ? t(VERDICT_LABEL[c.verdict]) : c.verdict}
                  </span>
                </div>
              </div>
              <p className="check-verdict-note">{t(c.note)}</p>
              {c.official_contact && (
                <a
                  href={`tel:${c.official_contact.replace(/\s/g, "")}`}
                  className="mt-4 inline-block rounded-2xl bg-emerald-600 px-6 py-3 text-3xl font-black"
                >
                  📞 {t(T.official)}: {c.official_contact}
                </a>
              )}
              {c.sources.length > 0 && (
                <div className="mt-4">
                  <div className="text-lg text-slate-400">{t(T.sources)}</div>
                  <ul className="mt-1 space-y-3">
                    {c.sources.map((s, j) => (
                      <li key={j}>
                        <a href={s.url} target="_blank" rel="noreferrer" className="text-xl text-sky-300 underline">
                          {s.title || s.url}
                        </a>
                        <div className="text-base text-slate-400">{new URL(s.url).hostname}</div>
                        {s.snippet && <div className="text-lg italic text-slate-300">“{s.snippet}”</div>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="mt-3 text-base text-slate-500">
                {c.cached && c.fetched_at
                  ? `${t(T.cached)} ${new Date(c.fetched_at).toLocaleString()}`
                  : c.status === "ok" && `${(c.latency_ms / 1000).toFixed(1)} s`}
              </div>
            </div>
          ))}

          {res.text_analysis && (
            <div className="check-result-card check-text-analysis">
              <div className="text-xl text-amber-300">{t(res.text_analysis.label)}</div>
              <div className="mt-2 text-2xl">
                {res.text_analysis.tactics.length ? res.text_analysis.tactics.join(" · ") : "—"}
              </div>
              {res.text_analysis.protective.length > 0 && (
                <div className="mt-2 text-xl text-emerald-300">
                  {t({ en: "Genuine advice found:", hi: "असली सलाह मिली:" })} “{res.text_analysis.protective.join("”, “")}”
                </div>
              )}
            </div>
          )}
        </section>
      )}
    </main>
    </DashboardShell>
  );
}

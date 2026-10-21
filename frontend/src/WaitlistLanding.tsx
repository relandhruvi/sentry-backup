import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import AtmosphericFriction from "./components/ui/atmospheric-friction";
import SiteFooter from "./components/ui/site-footer";
import SiteHeader, { type SiteHeaderLink } from "./components/ui/site-header";
import "./WaitlistLanding.css";

const AUTH_PATH = "/auth";
// TEMP: remove once auth is connected.
const landingHeaderLinks: SiteHeaderLink[] = [
  { label: "How it works", href: "#how-it-works" },
  { label: "Privacy", href: "#privacy" },
  { label: "Log in", href: AUTH_PATH, style: "login" },
  { label: "Sign up", href: AUTH_PATH, style: "button" },
  { label: "TEMP DASH", href: "/inspect", style: "button" },
];

const SpiderParticles = lazy(() => import("./components/ui/spider-particles"));

const footerColumns = [
  { title: "Product", links: [
    { label: "Live inspection", href: "/inspect" },
    { label: "Review a past call", href: "/past" },
    { label: "Check a caller", href: "/check" },
  ] },
  { title: "Explore", links: [
    { label: "How it works", href: "#how-it-works" },
    { label: "Privacy", href: "#privacy" },
    { label: "Log in", href: AUTH_PATH },
  ] },
];

const steps = [
  { title: "Authority", detail: "A caller claims to represent a bank, police service, or government office." },
  { title: "Threat or urgency", detail: "They create fear or pressure you to act before you can check." },
  { title: "Secrecy", detail: "They try to isolate you from family, friends, or official advice." },
  { title: "Payment or OTP", detail: "They ask for money, a code, or sensitive account access." },
];
const patternIntro = "Not every call follows this sequence. Sentry looks for multiple signals and explains uncertainty.";

function SignalMark() {
  return <svg className="wl-signal-mark" viewBox="0 0 540 430" role="img" aria-label="Abstract call waveform contained by a protective arc">
    <defs><linearGradient id="wl-wave" x1="0" x2="1" y1="0" y2="1"><stop stopColor="#CC8066"/><stop offset="1" stopColor="#334155"/></linearGradient><radialGradient id="wl-glow"><stop stopColor="#CC8066" stopOpacity=".24"/><stop offset="1" stopColor="#CC8066" stopOpacity="0"/></radialGradient></defs>
    <ellipse cx="276" cy="218" rx="224" ry="186" fill="url(#wl-glow)"/>
    <path d="M113 302c-33-27-53-69-53-115 0-83 67-150 150-150 31 0 61 10 84 26" fill="none" stroke="#334155" strokeOpacity=".16" strokeWidth="2"/>
    <path d="M427 127c33 27 53 69 53 115 0 83-67 150-150 150-31 0-61-10-84-26" fill="none" stroke="#CC8066" strokeOpacity=".46" strokeWidth="2"/>
    <path d="M86 216h88l24-52 33 105 37-147 36 185 30-91 23 52h94" fill="none" stroke="url(#wl-wave)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round"/>
    <circle cx="270" cy="218" r="11" fill="#CC8066"/><circle cx="270" cy="218" r="23" fill="none" stroke="#CC8066" strokeOpacity=".25"/>
  </svg>;
}

export default function WaitlistLanding() {
  const root = useRef<HTMLDivElement>(null);
  const [activeStep, setActiveStep] = useState(0);
  const [coarsePointer, setCoarsePointer] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches,
  );

  useEffect(() => {
    const query = window.matchMedia("(pointer: coarse)");
    const update = () => setCoarsePointer(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  useGSAP(() => {
    gsap.registerPlugin(ScrollTrigger);
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      const words = gsap.utils.toArray<HTMLElement>(".wl-reveal-word");
      gsap.fromTo(words, { autoAlpha: 0.9 }, {
        autoAlpha: 1,
        stagger: 0.055,
        ease: "none",
        scrollTrigger: { trigger: ".wl-pattern-copy", start: "top 78%", end: "bottom 38%", scrub: 0.6 },
      });
      const cards = gsap.utils.toArray<HTMLElement>(".wl-stack-card");
      gsap.fromTo(cards, { y: 70, scale: 0.86, autoAlpha: 0.65 }, {
        y: 0,
        scale: 1,
        autoAlpha: 1,
        stagger: 0.15,
        ease: "power2.out",
        scrollTrigger: { trigger: ".wl-product", start: "top 74%", end: "center 40%", scrub: 0.7 },
      });
    });
    return () => media.revert();
  }, { scope: root });

  return <div className="waitlist-page" ref={root}>
    <SiteHeader links={landingHeaderLinks} />

    <main className="wl-main overflow-x-hidden w-full max-w-full">
      <section className="wl-hero" aria-labelledby="wl-title">
        {coarsePointer ? <div className="wl-spider-fallback absolute inset-0" aria-hidden="true" /> : (
          <Suspense fallback={<div className="wl-spider-fallback absolute inset-0" aria-hidden="true" />}>
            <SpiderParticles
              className="wl-spider-particles absolute inset-0 z-0"
              showHud={false}
              title=""
              subtitle=""
              particleColor="#CC8066"
              webColor="#CC8066"
              centerColor="#CC8066"
              bgColor="#101725"
              particleCount={128}
            />
          </Suspense>
        )}
        <div className="wl-hero-copy">
          <p className="wl-kicker">A steadier moment in a difficult call</p>
          <h1 id="wl-title">When a call feels wrong, <span>take a moment to check.</span></h1>
          <p className="wl-hero-lede">Sentry helps you notice pressure patterns in VoIP calls and recordings, understand the signals, and decide what to do next.</p>
          <div className="wl-hero-actions"><a className="wl-button wl-button-primary" href={AUTH_PATH}>Get started <span aria-hidden="true">↗</span></a><a className="wl-text-link" href="#how-it-works">See how it works <span aria-hidden="true">↓</span></a></div>
        </div>
        <div className="wl-hero-art"><div className="wl-art-grid"/><SignalMark/><div className="wl-hero-caption"><span className="wl-caption-dot"/> Pause. Check. Decide.</div></div>
      </section>

    </main>

    <div className="wl-dark-area">
      <AtmosphericFriction className="wl-atmospheric-friction" particleCount={650} accent="#CC8066" viewport />
      <div className="wl-dark-content">
      <section className="wl-section wl-how" id="how-it-works" aria-labelledby="wl-how-title">
        <div className="wl-section-heading"><p className="wl-kicker">How pressure can build</p><h2 id="wl-how-title">Notice the pattern.<br/><span>Keep your choices.</span></h2></div>
        <div className="wl-bento" aria-label="Common scam pressure pattern">
          <article className="wl-bento-card wl-pattern-card">
            <div className="wl-pattern-copy"><p className="wl-mono-label">A pressure pattern</p><h3>Scams often move through recognizable tactics.</h3><p className="wl-pattern-intro" aria-label={patternIntro}>{patternIntro.split(" ").map((word, index) => <span className="wl-reveal-word" aria-hidden="true" key={`${word}-${index}`}>{word} </span>)}</p></div>
            <img className="wl-pattern-illustration" src="/assets/pattern-sequence.svg" alt="Four connected stages in a pressure pattern" />
            <div className="wl-step-accordion" aria-label="Scam pressure stages">
              {steps.map((step, index) => <button key={step.title} className={`wl-step ${activeStep === index ? "is-active" : ""}`} type="button" aria-pressed={activeStep === index} aria-controls="wl-step-detail" onClick={() => setActiveStep(index)}>
                <span className="wl-step-index">{String(index + 1).padStart(2, "0")}</span><span className="wl-step-title">{step.title}</span>
              </button>)}
            </div>
            <p className="wl-step-detail" id="wl-step-detail" aria-live="polite">{steps[activeStep].detail}</p>
          </article>
          <article className="wl-bento-card wl-mini-card wl-pressure-card"><p className="wl-mono-label">Take a pause</p><div className="wl-mini-orbit" aria-hidden="true"/><h3>Urgency is a reason to slow down.</h3><p>Pressure should never replace independent verification.</p></article>
          <article className="wl-bento-card wl-mini-card wl-signal-card"><p className="wl-mono-label">Look for evidence</p><div className="wl-mini-wave" aria-hidden="true">{[18,30,14,42,22,54,17,36,25,46,16,31,21].map((height, i) => <i key={i} style={{ height }} />)}</div><h3>One signal is not the whole story.</h3><p>Risk assessments combine cues from the conversation.</p></article>
        </div>
      </section>

      <section className="wl-section wl-product" aria-labelledby="wl-product-title">
        <div className="wl-product-heading"><p className="wl-kicker">A clearer view of the conversation</p><h2 id="wl-product-title">See what Sentry<br/>can help you notice.</h2><p>Use a browser microphone for a live VoIP conversation or upload a recording. The existing analysis view shows recognized speech, pattern signals, and a changing risk assessment.</p></div>
        <div className="wl-product-visual">
          <div className="wl-product-image"><img src="/assets/product-preview.svg" alt="Abstract illustration of a call waveform and changing risk signals" /></div>
          <div className="wl-app-stack" aria-label="Current Sentry analysis capabilities">
            <div className="wl-stack-card wl-stack-top"><span className="wl-stack-dot"/><div><b>Call audio</b><small>Browser microphone or uploaded recording</small></div></div>
            <div className="wl-stack-card wl-stack-middle"><span className="wl-stack-wave" aria-hidden="true">▁▃▆▂▅▇▃▁</span><div><b>Conversation signals</b><small>Transcript and detected pressure cues</small></div></div>
            <div className="wl-stack-card wl-stack-bottom"><span className="wl-stack-risk">RISK</span><div><b>Changing assessment</b><small>Reasons are shown with the result</small></div></div>
          </div>
        </div>
      </section>

      <section className="wl-section wl-audience" aria-labelledby="wl-audience-title">
        <div className="wl-audience-image"><img src="/assets/audience-support.svg" alt="Abstract illustration of a connected home and an open line of support" /></div>
        <div className="wl-audience-copy"><p className="wl-kicker">For the moments you want a second look</p><h2 id="wl-audience-title">More clarity for you.<br/>A steadier way to help.</h2><p>Sentry is being built for ordinary people who may face impersonation or social engineering, and for families helping relatives navigate suspicious calls.</p><p className="wl-audience-note">Family messaging and alerts are not available in the current MVP.</p></div>
      </section>

      <section className="wl-section wl-privacy" id="privacy" aria-labelledby="wl-privacy-title">
        <div className="wl-privacy-copy"><p className="wl-kicker">Privacy, stated plainly</p><h2 id="wl-privacy-title">The recording stays<br/>out of permanent storage.</h2><p>Audio is processed in memory and is not written to disk. Transcript retention depends on the backend setting and is enabled by default; set <code>STORE_TRANSCRIPTS=false</code> to redact transcript text before persistence.</p></div>
        <div className="wl-privacy-image"><img src="/assets/privacy-boundary.svg" alt="Abstract illustration of audio held inside a temporary processing boundary" /></div>
      </section>

      <section className="wl-final-cta" aria-labelledby="wl-join-title">
        <div className="wl-final-inner">
          <div className="wl-cta-art"><img src="/assets/waitlist-cta.svg" alt="" /></div>
          <div className="wl-cta-content"><p className="wl-kicker">Take a calmer next step</p><h2 id="wl-join-title">Start your first inspection.</h2><div className="wl-final-actions"><a className="wl-button wl-button-primary" href={AUTH_PATH}>Sign up <span aria-hidden="true">↗</span></a><a className="wl-button wl-button-secondary" href={AUTH_PATH}>Log in</a></div></div>
        </div>
      </section>
      </div>
    </div>

    <SiteFooter brand="Sentry" columns={footerColumns} />
  </div>;
}

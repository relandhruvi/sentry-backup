# Scam Shield

A real-time safety layer for VoIP calls that protects older adults from digital-arrest,
impersonation (fake police or bank) and voice-clone "relative in trouble" scams.
**Detect the manipulation. Alert the family. Stop the transfer before it happens.**

The MVP is a browser-to-browser WebRTC call (`/caller` ↔ `/elder`). The backend analyzes the
caller's audio live. Streaming a WAV through the same WebSocket is the fallback and the test path.
Apps can't tap ordinary cellular calls; never claim otherwise in UI or docs.

## Non-negotiable principles

1. **Nothing hardcoded.** No canned transcripts, no per-scenario if/else, no fixture fallbacks in
   the app, no seeded demo data, no default or fake scores. Every number shown comes from the
   pipeline running on real audio input.
   - Domain knowledge (lexicon patterns, institution lists, weights, reason templates) lives in YAML
     and describes tactics, never specific calls.
2. **Every risk change carries evidence:**
   `contributions: [{signal, value, weight, evidence_utterance_id}]` plus `top_reasons` in English
   and Hindi.
3. **Risk rules** (configured in `app/risk/weights.yaml`):
   - Levels: LOW < 0.3 ≤ MEDIUM < 0.55 ≤ HIGH < 0.8 ≤ CRITICAL.
   - Time decay (half-life about 90 s) and hysteresis.
   - A single keyword can never reach HIGH.
   - Voice evidence alone is capped at HIGH.
   - CRITICAL needs PAYMENT or CREDENTIAL, plus THREAT, SECRECY, or a verification mismatch.
4. **Data minimisation.**
   - TinyFish receives only extracted entities (kind + value), never audio or transcripts.
   - Audio is never written to disk. It lives in an in-memory ring buffer only.
   - With `STORE_TRANSCRIPTS=false`, transcript text is redacted before it's persisted.
5. **Graceful degradation.** Every external dependency has a timeout from `.env` and reports its
   status as `ok | loading | degraded | unavailable | disabled | unconfigured`, both on `/health` and
   as `status` events. When a signal is missing, the UI shows "signal unavailable", never a guess.
6. **No secrets in code.** All config comes from `backend/.env` via pydantic-settings. Keep
   `.env.example` current. Never commit `.env`.
7. **Don't invent APIs.** For TinyFish, Twilio and any other vendor, use only what the official docs
   say. TinyFish docs will be pasted by the team at B6.
8. **Never block the event loop.** Model inference runs in dedicated worker threads with timeouts.
9. **Synthetic audio is for pipeline tests only.** Files named `synthetic_*` are macOS TTS. Never
   use them to train, calibrate or evaluate the voice anti-spoof model.

## Build order (stop, show the acceptance result and commit after each)

| # | Milestone | Status |
|---|---|---|
| B1 | Skeleton: config, events, bus, sessions, `/health`, Makefile | ✅ done |
| B2 | Audio ingest + VAD + STT, `tools/stream_wav.py`, STT benchmark | ✅ done |
| B2.1 | Never translate, Hindi fallback, warm-up gate, shared text normalization, prompt experiment | ✅ done |
| B3 | Coercion engine (lexicon + sequence) + entity extraction | ✅ done |
| B3.5 | Train our coercion classifier | trained (e5-small); held-out eval pending "training frozen" |
| B4 | Risk fusion + alert dispatcher with the **dashboard** alert channel | B4-lite done (contacts API in B9) |
| F1 | WebRTC call `/caller` ↔ `/elder` streaming to the backend | |
| F2 | `/elder` live risk banner + full-screen warning (English + Hindi) | |
| F3 | `/family` dashboard: risk gauge, timeline, tactic chain, live alerts | |
| F4 | `/ops` judge view: transcript highlights, signal contributions, latency, health, signal toggles | |
| B5 | Voice anti-spoof model + calibration | |
| B6 | TinyFish verification (from the pasted official docs) | B6-lite done: manual /check + verifier; live-call wiring later |
| B7 | Eval harness, ablation, learned fusion weights | |
| B8 | Stage hardening: `make demo`, model warm-up, offline degradation | |
| B9 | SMS + WhatsApp alert channels via Twilio | last |

### Alerts (B4 design, final)

- `AlertChannel` interface. The first channel is **dashboard**: it emits an `alert` event on
  `/ws/events/{id}`, which `/family` shows instantly.
- The alert contains:
  - the level
  - `top_reasons`
  - the caller's claims (institution, numbers, UPI IDs, case numbers)
  - the verified official number when verification provides one
  - a "Call <name> now" action
- The dispatcher fires on the **first transition to HIGH or CRITICAL**, sends **one alert per
  contact per call**, builds the content from fusion output, and logs latency.
- A `contacts` table (name, E.164 phone, opt-in flag, revocable) exists from B4, so SMS and
  WhatsApp (B9) plug in as additional channels without changing the dispatcher.

## Stack

- **Backend:**
  - Python 3.11, FastAPI, uvicorn, asyncio, websockets
  - pydantic v2 + pydantic-settings, aiosqlite, PyYAML, numpy, httpx
  - pytest + pytest-asyncio, ruff
- **Audio:**
  - webrtcvad (`webrtcvad-wheels`)
  - faster-whisper on CPU, int8: `small` for transcription, `tiny` for Hindi/English language ID
- **Text:**
  - `app/text/normalize.py` is the single normalizer for WER, the lexicon and the classifier.
    It romanizes Devanagari (`indic-transliteration` ITRANS, then Hindi schwa deletion), applies a
    phonetic key, then the spelling variants and context-dependent STT confusions in `variants.yaml`.
  - The original text and character spans are kept: evidence shown to users always uses the
    original text.
  - rapidfuzz for fuzzy matching on normalized text.
- **Frontend (F1–F4):** React + Vite + TypeScript + Tailwind, WebRTC, and an AudioWorklet that
  downsamples to 16 kHz mono int16.

## Layout

```
backend/
  app/main.py            FastAPI app, routes, startup model warm-up
  app/config.py          Settings from backend/.env
  app/events.py          Event envelope, payload base types, per-session bus
  app/components.py      Component status registry (/health + status events)
  app/session.py         Per-call sessions, idle cleanup, drains the pipeline on end
  app/pipeline.py        Per-call: frames -> ring buffer -> VAD -> STT worker -> utterance events
  app/storage.py         SQLite: sessions, events, alerts (no audio)
  app/audio/             buffer.py (ring buffer), vad.py (segmenter), stt.py (providers)
  app/text/              normalize.py + variants.yaml (shared normalizer)
  app/analysis.py        Per-call analyzer: coercion + entities on each utterance
  app/coercion/          lexicon.yaml, lexicon.py (fuzzy matcher), sequence.py (chain + caps), engine.py,
                         model_tagger.py (trained tactic classifier, ONNX int8)
  app/entities/          extract.py, institutions.yaml (gazetteer + UPI handles)
  app/risk/              weights.yaml (levels, coercion, matching, fusion), levels.py, fusion.py,
                         reasons.yaml (English + Hindi reason and alert texts)
  app/alerts/            dispatcher.py (once per call, latency logged), channels.py (dashboard; B9 adds SMS/WhatsApp)
  app/voice/             antispoof.py (interface; model in B5)
  app/verify/            tinyfish_client.py (Search + Fetch, httpx, timeout/retry/rate limits),
                         verifier.py (institution_contact, advisory, public_reports; 24 h SQLite cache),
                         sources.yaml (trusted directory: 50 RBI-listed banks, agencies, couriers,
                         telecoms; each domain with its confirming source), institutions.py
                         (resolver: aliases, Hindi, typos, "Did you mean"), inputs.py, manual.py
  tools/                 stream_wav, make_test_audio, bench_stt, stt_report, wer
  training/coercion/     classifier data + training: DATA.md, generator_instructions.md, prompts/,
                         generated/ (LLM batches), data/ (dataset + STT-noised copy), train/export
  models/coercion/       built classifier (git-ignored): model.onnx, tokenizer.json, model.json
  tests/                 unit + pipeline tests; tests/audio_scripts/ = synthetic call scripts
frontend/                React + Vite + TS + Tailwind; /demo page (mic or WAV -> AudioWorklet -> /ws/audio)
  public/pcm-worklet.js  16 kHz mono int16, exact 20 ms frames
  src/lib/               session.ts (REST + both sockets), audio.ts (mic, file), state.ts (event reducer)
  src/components/        Panels.tsx (health strip, risk meter/chart, chain, transcript, why, alert)
scripts/demo.sh          `make demo` (repo root): backend + warm-up + frontend; DEMO.md has the run sheet
samples/                 local audio (gitignored); samples/synthetic_tts/ = rendered TTS calls
```

## API

- `POST /sessions` → `{session_id}`
- `WS /ws/audio/{id}`: binary 20 ms frames, 16 kHz mono int16 (exactly 640 bytes, or whole multiples)
- `WS /ws/events/{id}`: JSON events. Late subscribers first receive recent events again.
- `POST /sessions/{id}/end`: drains in-flight transcriptions, then emits `status session ended`
- `GET /verify/institutions`: the trusted directory (id, English + Hindi name, kind, aliases,
  checkable) for the /check dropdown.
- `POST /verify/manual`: "Check this caller" form → tactics + TinyFish checks → level, reasons,
  checks (with sources), next steps (EN/HI, always ending "Report at cybercrime.gov.in or call
  1930"). 422 on an invalid phone, UPI ID or URL.
- `GET /health`: `stt, vad, lexicon, classifier, llm, voice, verify, alerts, storage`. Overall
  `degraded` unless the core (`stt, vad, lexicon, classifier, storage`) is ok.

## Events

Every event uses the envelope `{v, type, session_id, seq, ts, payload}`.
`type` is one of `utterance | coercion | entities | risk | alert | status`.

- **`status`:** `{component, status, detail}`. `component="session"` uses `started` and `ended`.
- **`utterance`:**
  - Always present: `utterance_id`, `t_start_s`, `t_end_s` (call time), `end_reason`, `speech_ms`,
    `stt_status`.
  - `stt_status` is one of `ok | unavailable | timeout | dropped | error`.
  - When `stt_status=ok`: `text`, `lang`, `detected_language`, `lang_probs`, `stt_latency_ms`,
    `inference_ms`, `stt_model`, `avg_logprob`, `no_speech_prob`.
  - When it isn't `ok`, `text` is `null` and is never guessed.
- **`coercion`** (after every transcribed utterance):
  - `hits[]`: `{tactic, pattern, span, weight, confidence, score, negated, source}`. `span` is the
    original transcript text.
  - Call-level fields: `score`, `raw_score`, `level`, `tactic_strengths`, `stages_reached`,
    `chain[]` (`{tactic, stage, utterance_id, t_s}`), `ordered`, `protective_strength`,
    `caps_applied`, `contributions[]` (`{signal, value, weight, evidence_utterance_id}`),
    `mode`, `model_probs`, `model_status`, `llm_status` (`disabled | ok | timeout | error`).
  - Hit `source` is `lexicon | model | llm`, and `counted` says whether it fed the score.
    Contributions have `coercion.<TACTIC>` (combined) plus `coercion.<TACTIC>.<source>`.
- **Coercion fusion (B3.5 decision, final until B7 learns weights):**
  - The trained classifier is the **primary** signal (`mode=model`): only its detections count as
    tactic evidence. Lexicon **protective** cues ("visit your branch", "never share your OTP")
    lower risk in both modes, capped at `protective_max_reduction`.
  - The lexicon supplies evidence spans and plain-language explanations (`counted=false`).
  - It may add `lexicon_boost` to a tactic only when the classifier already scores it at or above
    the val-tuned gate, and only if that doesn't raise hard-negative false alarms. Currently off:
    no gain of at least 0.005.
  - **Never combine lexicon and classifier with max().**
  - If the classifier is missing or fails, `mode=lexicon_fallback` and `/health` reports
    `degraded`, because `classifier` is a core component.
- **`entities`** (when an utterance contains any):
  - `entities[]`: `{kind: phone|upi|url|case_id|institution, value, raw, detail}`
  - `claims_so_far[]`: every distinct claim in the call so far.
- **`risk`** (after every analysed utterance): `utterance_id`, `t_s`, `score`, `level`,
  `previous_level`, `level_changed`, `contributions[]` (`risk.coercion` plus the coercion ones),
  `top_reasons[]` (`{code, en, hi, evidence_text, utterance_id, weight}`; `code` is a tactic or
  `CHAIN`), `signals` (`coercion`: `model | lexicon_fallback`; `voice`, `verify`: their live
  component status).
- **`alert`** (once per call, on the first HIGH/CRITICAL): `alert_id`, `utterance_id`, `level`,
  `score`, `title{en,hi}`, `top_reasons`, `claims[]`, `verified_official_number` (null until B6),
  `verification_status`, `action{en,hi}` ("Call <ELDER_NAME> now"), `contacts[]`.
- **Evidence decay:** tactic evidence halves every `coercion.decay_half_life_s` (90 s).
  **Hysteresis:** a level drops only once the score is `levels.hysteresis` (0.05) below its
  threshold.
- **VAD long speech:** split at the first word gap after `VAD_SOFT_MAX_MS` (8 s), else at the
  quietest frame before `VAD_MAX_UTTERANCE_MS` (12 s); `end_reason` is `silence | pause |
  max_length | flush`.

## STT facts that shape later milestones (B2 benchmark, Apple M3, synthetic TTS)

- **Model choice.** `base` writes Hindi in Urdu script, so it's unusable for the lexicon. `small`
  writes Devanagari.
  - Language ID with `small` costs about 1 s per utterance; with `tiny` it costs about 0.1 s.
  - 4 CPU threads beat 8, because the extra threads land on efficiency cores.
- **Hinglish comes out in Devanagari.** Whisper transcribes romanized Hinglish speech in
  Devanagari, including English words ("सेफ अकाउंट", "वेरिफिकेशन", "क्राइम ब्रांच").
  - The B3 lexicon needs English, Devanagari Hindi, romanized Hinglish **and Devanagari
    transliterations of English terms**.
  - Matching must be fuzzy, because spellings drift ("मैद बताइए" for "मत बताइए").
- **Forced English can translate.** When language ID picks `en` for Hindi speech, Whisper sometimes
  *translates* it to English instead of transcribing. The coercion engine must work on English
  paraphrases too.
  - Mitigation (B2.1): `task="transcribe"`, and `en` is used only when language ID is at least
    `STT_LANG_MIN_CONFIDENCE` (0.5) confident; otherwise the fallback is `hi`.
- **`initial_prompt` is rejected.** A 32-term scam/bank/police prompt raised English WER from 4% to
  87%, raised p95 latency by about 70%, and inserted 62 scam words into benign calls. Never re-enable
  it without re-running `make bench-stt --prompt-file ...`.
- **Greeting loss is not a VAD problem.** Hindi-mode Whisper drops English-accented romanized
  greetings ("Hello Nani, main…"). Pre-roll 300 ms and a lower start threshold didn't recover them.
- **Normalization matters.** B2.1 live run: Hinglish WER 89% raw vs 49% normalized; Hindi 47% vs
  32%; overall 46% vs 27%.
- **Latency is load-sensitive.** On an idle M3: median about 1.3 s, p95 about 2.0 s. With Chrome
  and macOS asset downloads running, the same segments took 2–3x longer.

## Commands (run from `backend/`)

```bash
make setup                 # venv (python3.11) + deps + .env from .env.example
make run                   # server on HOST:PORT; STT warms up in the background (/health shows loading -> ok)
make test                  # pytest (never loads Whisper; uses fakes)
make lint / make fmt       # ruff
make test-audio            # render tests/audio_scripts/*.yaml -> samples/synthetic_tts/*.wav (+ .json timings)
make bench-stt             # benchmark tiny/base/small on this machine
make stt-report            # live: stream all synthetic WAVs through the running server, WER + latency
make coercion-report       # coercion trajectories: fixtures + latest live STT run
make demo-wav FILE=...     # stream any audio file in real time and print events
make setup-train           # + torch/transformers/onnx (training only; the app needs onnxruntime)
make train-coercion        # rebuild models/coercion/ from training/coercion/generated
```

## Held-out data: deny-list (read this first)

`eval/human/` (team-written held-out test sets) is **off-limits**. Do not open, read, list,
grep, glob, summarize or copy anything in it, and don't let subagents do so either, until the user
explicitly says **"training frozen"** in the current session. It's the only clean measure of the
coercion classifier and lexicon. Searches across the repo must exclude it (for example
`grep --exclude-dir=human`, `rg -g '!eval/human'`). `.claude/settings.json` also denies tool
access to it.

## Working rules for agents

- Before putting a number in code, put it in `.env`/settings or a YAML config.
- A new signal or rule needs a unit test **and** must appear in risk `contributions`.
- A new external call needs a timeout, `status` reporting, and a test for the unavailable path.
- Keep upload, live call and test paths identical downstream of `/ws/audio`.
- Commit messages: imperative and scoped (`coercion: add isolation cues for hi`).
- **YAML gotcha:** quote words like `no`, `on`, `off`, `yes`. YAML 1.1 parses them as booleans,
  which has bitten the lexicon twice.
- **Lexicon changes:** run `make coercion-report` before and after, and report both. Fixtures in
  `tests/fixtures/conversations.yaml` informed some B3 lexicon entries, so they're no longer a clean
  held-out set. Keep new held-out conversations separate (B7).
- **TinyFish:** only what docs.tinyfish.ai documents (Search `GET api.search.tinyfish.ai`, Fetch
  `POST api.fetch.tinyfish.ai`, `X-API-Key`). It receives only a listed institution name, phone,
  UPI ID or URL: never personal names, message text, transcripts or the elder's details.
  Verification alone can never reach HIGH.
- **Trusted directory:** only institutions in `sources.yaml` are searched, by canonical name,
  and only on domains marked `confirmed: true` by an official source:
  - banks: RBI "Banks in India"
  - NPCI: RBI "Important Websites"
  - government bodies: the gov.in / nic.in registry
  - couriers: CBIC's courier register
  - telecoms: TRAI

  `make check-sources` re-checks the banks against RBI. Ambiguous input returns candidates
  ("Did you mean …?") and searches nothing.
- **/check rules:**
  - A number that MATCHES the official helpline is never searched for fraud reports.
  - A claimed identity alone stays LOW.
  - Pasted-message tactics count at `manual_check.message_weight` (0.5).
- To stop a server you started, use `kill $(lsof -tnP -iTCP:8000 -sTCP:LISTEN)`. `pkill -f "python -m app"`
  misses it, because macOS reports the process as `Python`.

# Repository Guidelines

Whenever Codex can start the app, run checks, inspect the browser, or verify a build itself, do so instead of asking the user.

## Project Overview
Sentry (also called Scam Shield) analyzes VoIP-call audio for coercive scam patterns and helps users verify caller claims. It is intended for ordinary users vulnerable to impersonation and social engineering, and families supporting relatives. It cannot inspect ordinary cellular calls.

## Features and Status
Recording Mode (implemented) streams uploaded recordings through the analysis path. Live Mode (implemented MVP) analyzes browser-microphone audio, not a cellular call. “Is This True?” is partial: `/check` manually checks supplied claims and caller details; it is not wired into live calls. Risk alerts use an in-app dashboard channel; family messaging is planned. Voice-clone detection has an interface but is unavailable. Emergency guidance is informational (including India's 1930); Sentry does not contact services for the user.

## Architecture and Stack
`backend/app/` uses Python 3.11/FastAPI REST and WebSockets. `audio/` buffers audio in memory, applies WebRTC VAD and faster-whisper STT; `text/` normalizes Hindi, English, and Hinglish; `coercion/` combines a lexicon and ONNX classifier; `entities/` extracts checkable values; `risk/` fuses weighted evidence. Manual claim-check input calls `verify/`; no LLM claim extraction is enabled (`LLM_ENABLED=false`). `frontend/` is React/TypeScript/Vite/Tailwind; `frontend/src/lib/session.ts` connects REST/WebSockets via Vite proxy on port 8000. SQLite stores sessions/events/alerts/contacts/cache. Redis, PostgreSQL, Firebase, Firestore, and authentication are not present; do not claim there are security rules.

## TinyFish
TinyFish is a web-agent/retrieval platform, not an LLM. Implemented Search discovers pages and Fetch retrieves selected pages for configured trusted domains; the verifier checks institution contacts, advisories, and public reports, cached in SQLite for 24 hours by default. TinyFish Agent navigation is not integrated. Only extracted/resolved claim entities may be sent—never raw audio or full transcripts. Failed/uncertain results are “Not verified,” never “Confirmed fraudulent.” Never hardcode or present an unverified official number as live-verified.

## Privacy, Security, and Risk
Audio is held in an in-memory ring buffer and never written to disk; recordings are not retained. Transcripts persist by default; `STORE_TRANSCRIPTS=false` redacts transcript fields before persistence. Keep secrets in ignored `backend/.env`, based on `.env.example`; never commit them or collect Aadhaar/PAN, bank credentials, OTPs, or passwords. API auth/authorization and Firebase security rules do not exist; do not expose this MVP publicly. Risk weights/levels are defined in `backend/app/risk/weights.yaml`: LOW < 0.30, MEDIUM 0.30–<0.55, HIGH 0.55–<0.80, CRITICAL ≥0.80. Risk changes must cite evidence; one keyword/utterance cannot reach HIGH, voice alone is capped at HIGH, and CRITICAL requires PAYMENT/CREDENTIAL plus THREAT, SECRECY, or verification mismatch. Avoid false positives; voice-clone suspicion is not implemented and, if added, is one weighted signal, never a standalone verdict.

## Structure and Development
Backend entry/config: `backend/app/__main__.py`, `backend/app/main.py`, `backend/app/config.py`; modules include `audio/`, `text/`, `coercion/`, `entities/`, `risk/`, `verify/`, `alerts/`; tests: `backend/tests/test_*.py`. Frontend entry: `frontend/src/main.tsx`; route selection: `frontend/src/App.tsx` (`/`, `/demo`, `/check`, `/past/check`, `/inspect`, `/past`). Vite proxy config: `frontend/vite.config.ts`. See `DEMO.md`, `PROGRESS.md`, `CLAUDE.md`. Do not inspect `backend/eval/human/` until user says “training frozen.” Python is `>=3.11,<3.12`. Root: `make demo`, `make test`. In `frontend/`: `npm ci`, `npm run dev`, `npm run build`, `npm run typecheck`. Live verification needs `TINYFISH_API_KEY`; otherwise show unavailable. Setup targets are in `backend/Makefile`; override its macOS-default `PYTHON` path on other systems.

## Contribution Rules
Inspect code/docs first; preserve existing behavior, routes, auth flows, contracts, and architecture. Work incrementally; avoid unrelated refactors or dependencies. Never weaken security rules. Do not work on the deferred FastAPI Firebase ID-token authorization gap unless asked. Ask before deleting files or changing schemas. Never commit/push. After each task, report changes, files, verification, test results, and remaining issues. Frontend has no test script. History has no clear commit convention; use concise imperative subjects.

## UI and Assets
App screens are product UI, not marketing pages. For design tasks, use only the skill named by the user; never use the sites skill. Follow root `DESIGN.md` consistently when present. Store generated/added images in `frontend/public/assets/` and reference them as `/assets/<filename>`. `WaitlistLanding` is a separate public landing route; its current five SVG illustrations are hand-authored replaceable slots documented in `frontend/public/assets/README.md`.

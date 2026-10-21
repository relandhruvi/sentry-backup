# Progress: Scam Shield backend

Last updated: 2026-10-19. B3.5 is trained, and the model is frozen pending held-out evaluation. CLAUDE.md has the principles, build order and rules; this
file tracks where the work stands.

## Hard rule: held-out data

**`eval/human/` stays unread** (no open, read, list, grep or summary, by any agent or subagent)
until the user says **"training frozen"**. It holds the team-written held-out test sets
(`test_human.jsonl`, `test_handwritten.jsonl`, `conversations_human.yaml`, `VALIDATION.md`).

Two places enforce this:
- the deny-list section in CLAUDE.md
- `.claude/settings.json`

Training, threshold tuning and model selection never touch it.

## Demo

`make demo` runs the live /demo page (B4-lite + frontend); see DEMO.md.

## B6-lite (verification)

`/check` page + `POST /verify/manual` + `app/verify/` (TinyFish Search + Fetch per the docs).
Mocked tests pass; the live smoke test and acceptance need `TINYFISH_API_KEY` in backend/.env.
Verification is not yet wired into live calls.

## Done (committed)

| Milestone | Commit | What |
|---|---|---|
| B1 | 2ef17bd | config, events, bus, sessions, `/health`, SQLite |
| B2 | 1e069b2 | audio ingest, VAD, faster-whisper `small` + `tiny` language ID |
| B2.1 | 5b2278d | never translate, Hindi fallback, warm-up gate, `app/text/normalize.py` |
| B3 | 0e07ac2 | lexicon + escalation chain + caps; entity extraction |

## B3.5: coercion classifier (in progress)

### Decisions

- **Data source: Option A**, an LLM writes the utterances. The generator is isolated Claude Code
  subagents (`claude-opus-5-5`).
  - Six ran in parallel, two per language, all with identical instructions.
  - Each was allowed to read only `training/coercion/generator_instructions.md` and its batch
    prompts.
  - None of them read `app/coercion/`, `lexicon.yaml`, `tests/` or `eval/human/`.
- **Labels** (multi-label): AUTHORITY, THREAT, URGENCY, SECRECY, ISOLATION, PAYMENT, CREDENTIAL,
  plus BENIGN, which is exclusive.
  - **Video-call rule:** being asked to join or stay on a video call is SECRECY or ISOLATION,
    never CREDENTIAL.
  - Full definitions are in `training/coercion/generator_instructions.md`; the data is described in
    `training/coercion/DATA.md`.
- **Prompts:** 33 batches of 100 rows, 11 per language (en / hi Devanagari / hinglish romanized).
  - Mix: 45 scam, 30 hard negatives, 25 chat.
  - Every row records `prompt_id`, `prompt_hash` and `generator`.
- **Near-duplicates:** rows above 0.75 similarity (rapidfuzz ratio on `normalize_key`) are
  removed.
- **Noisy copy:** a stratified sample of 1,000 rows was rendered with `say` and transcribed by
  calling `FasterWhisperProvider.transcribe_blocking` directly, with the same model, settings and
  language rules as live (no real-time streaming).
  - The rows are normalized and tagged `source=stt_noisy`.
  - Each keeps its source row's labels and `prompt_id`.
  - Audio exists only in a temp dir.
- **Split** by prompt id: `VAL_PROMPTS = p07 p08 p09 p22 p23 p24`, two batches per language. A
  noisy row follows its source's prompt.
- **Fusion (decided by the user):**
  - The classifier is the primary signal. The lexicon gives evidence spans and explanations, a
    gated boost (off), and a fallback that shows as degraded in `/health`.
  - Lexicon and classifier are never combined with max().
- **Model:**
  - Architecture: encoder, then mean pooling, then 7 sigmoid outputs. Input is the caller
    utterance only, because the live pipeline hears only the caller.
  - Training: head learning rate 1e-3, encoder 5e-5, 10 epochs.
  - Selection: best epoch by val macro-F1 at 0.5, then per-tactic thresholds tuned on val (max
    F1). Because both are chosen on val, **val metrics are optimistic**.
- **Encoders**, verified on Hugging Face and pinned to a commit:
  - `intfloat/multilingual-e5-small` (MIT)
  - `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (Apache-2.0)
  - Each is trained on original text (`raw`) and on normalized text (`norm`).
- **Deployment:**
  - The model is exported to ONNX and quantized to int8. Thresholds are re-tuned on the int8
    model's own val scores.
  - The app needs only `onnxruntime` and `tokenizers`; torch and transformers are a `[train]`
    extra.
- **Integration:**
  - `app/coercion/model_tagger.py` runs behind `CoercionEngine` as the `model` slot, in its own
    worker thread, with a 1.0 s timeout.
  - A hit's score is weight × probability; the weight is `model_tagger.weight` (0.8) in
    `weights.yaml`.
  - The caps still apply. Status is reported as `classifier` on `/health` and as `model_status` on
    coercion events.
  - A missing model shows as "unavailable"; the lexicon then runs alone.

### Data counts

**Generated:** 3,300 valid rows, all passed validation. 22 near-duplicates were removed (one with
a label conflict), leaving **3,278**.

| | Count |
|---|---|
| By language: en / hi / hinglish | 1,096 / 1,091 / 1,091 |
| By category: scam / hard_negative / chat | 1,477 / 977 / 824 |
| AUTHORITY / THREAT / URGENCY | 332 / 420 / 274 |
| SECRECY / ISOLATION | 218 / 265 |
| PAYMENT / CREDENTIAL | 396 / 204 |
| BENIGN | 1,965 |
| Multi-label rows | 658 |
| Rows with spoken-style noise | 1,073 |

Some generators reached the noise and context quotas with patch scripts, so part of the noise is
formulaic.

**STT-noised:** 1,000 rows (334 en / 333 hi / 333 hinglish), none empty. Normalized WER against
the source text:

| Language | Normalized WER |
|---|---|
| English | 12.6% |
| Hindi | 37.0% |
| Hinglish | 54.0% |

The run averaged 4.7 s per row, slowed down by macOS background services competing for the CPU.

### Results (val, split by prompt id: 784 rows, 190 of them STT-noised)

Thresholds, the best epoch and the model choice were all selected on val, so these numbers are
optimistic. The real test is `eval/human/`, after "training frozen".

**Columns:**
- **F1 (all):** macro-F1 over the seven tactics, on all val rows.
- **F1 (STT):** macro-F1 on the STT-noised val rows only, which is closest to live input.
- **AUTH … CRED:** per-tactic F1 for AUTHORITY, THREAT, URGENCY, SECRECY, ISOLATION, PAYMENT and
  CREDENTIAL.
- **False alarms:** the share of hard-negative or chat rows where at least one tactic fired.

| Run | Train data | F1 (all) | F1 (STT) | AUTH | THRT | URG | SECR | ISOL | PAY | CRED | False alarms, hard neg. | False alarms, chat |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **e5-small, original text (shipped)** | clean + noisy | **0.714** | **0.599** | 0.707 | 0.707 | 0.628 | 0.652 | 0.800 | 0.715 | 0.786 | 0.068 | 0.020 |
| e5-small, original text | clean only | 0.677 | 0.507 | 0.730 | 0.670 | 0.539 | 0.582 | 0.759 | 0.689 | 0.769 | 0.089 | 0.005 |
| MiniLM-L12, original text | clean + noisy | 0.719 | 0.586 | 0.723 | 0.630 | 0.625 | 0.830 | 0.773 | 0.678 | 0.776 | 0.084 | 0.030 |
| MiniLM-L12, original text | clean only | 0.709 | 0.491 | 0.725 | 0.663 | 0.631 | 0.731 | 0.769 | 0.679 | 0.764 | 0.059 | 0.035 |
| e5-small, normalized text | clean + noisy | 0.544 | 0.449 | | | | | | | | 0.101 | 0.020 |
| MiniLM-L12, normalized text | clean + noisy | 0.652 | 0.578 | | | | | | | | 0.101 | 0.030 |
| MuRIL-base, try 1 | clean + noisy | collapsed at encoder lr 3e-5 / head 1e-3: loss flat at 0.30, F1 0.0 after 4 epochs | | | | | | | | | | |
| MuRIL-base, try 2 | clean + noisy | not completed at encoder lr 2e-5 / head 5e-4 (stopped by the user) | | | | | | | | | | |
| Lexicon alone | none | 0.335 | 0.244 | 0.453 | 0.246 | 0.261 | 0.339 | 0.173 | 0.325 | 0.549 | 0.165 | 0.025 |
| Lexicon + classifier, combined by max() (rejected) | | 0.702 | 0.593 | 0.649 | 0.696 | 0.612 | 0.737 | 0.809 | 0.688 | 0.722 | 0.219 | 0.045 |

**Choices:**
- **Model:** we ship `e5-small-raw`, exported as fp32 ONNX. `compare.py` picks the run with the
  best F1 on the STT-noised val rows.
- **Precision:** int8 would cost e5 0.047 macro-F1, while fp32 matches torch exactly. For
  reference, MiniLM in int8 scores 0.723 overall and 0.594 on STT-noised rows, at 6 ms.
- **Latency** (single utterance, CPU, 1 thread, including tokenization):
  - Lightly loaded machine: 17.7 ms median, 22.0 ms p95.
  - Under STT load: 28.5 ms median, 39.4 ms p95.
  - The budget is 150 ms.
- **Lexicon boost:** off. The best gate (0.35) gained only +0.001 macro-F1, below the 0.005
  minimum.

**Call level** (`coercion_report`: 20 fixtures plus 8 live synthetic STT calls):
- **Lexicon fallback:** 28 of 28 calls meet expectations. This is leaky, because the fixtures
  informed the lexicon.
- **Classifier primary:** 26 of 28. For the classifier the fixtures are clean held-out data.
- **The two misses** are scams that stopped at MEDIUM:
  - `scam_electricity_disconnection` (Hinglish): it missed "bijli kaat di jayegi" as THREAT and
    "Abhi turant" as URGENCY.
  - `synthetic_scam_relative_in_trouble` (STT): it missed "उनको मैद बताना" as SECRECY. The STT
    garbled "मत" (don't) as "मैद".
- In both calls the lexicon caught these phrases, but in model mode lexicon hits don't count.
- `benign_bank_fraud_alert` peaked at MEDIUM 0.51. That is still within the benign rule.

**Direct vs streaming STT check (20 rows, idle machine): FAIL** on the "≤ 5% word difference"
criterion.

- **No timeouts.** There were 0 timeouts, drops or errors in 20 streamed rows.
- **Direct STT is deterministic.** It gave the same text on two runs for 20 of 20 rows, and
  matched the saved training row for 19 of 20.
- **Rows VAD kept as one segment (17):**
  - 3 were identical, and 7 were within 5% normalized word difference.
  - Overall difference was 9.7%; the worst row differed by 33%, the worst being Hinglish.
- **Rows VAD split at the 8 s maximum (3):** the outputs diverged completely, with word
  differences of 7/24, 29/20 and 27/16 (a Hinglish row came back in English when streamed).
- **Cause:** the live path hands Whisper a different audio clip (pre-roll, trimmed trailing
  silence, cuts at 8 s), so the same speech decodes differently. It isn't pacing or
  randomness. The noisy rows have similar noise to live output but aren't identical to it.
- **Proposed fix (not done):** have `make_noisy.py` run each clip through the same
  `VadSegmenter` offline, without real-time pacing, so segmentation matches live by
  construction. Then regenerate the 1,000 rows (about 45 min) and retrain.

### Left in B3.5

1. **Wait for "training frozen".** Then evaluate the shipped model and the lexicon on
   `eval/human/`.
2. **Later improvement (deferred for the demo, user decision):** make the noisy copy match live
   segmentation (VadSegmenter offline), regenerate, retrain. The current e5-small model stays.
3. Decide how to close the recall gaps: the lexicon caught the two call-level misses, but it can't
   count in model mode.

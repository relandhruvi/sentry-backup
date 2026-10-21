# Coercion classifier training data

Training data for the tactic classifier (`app/coercion/model_tagger.py`). Each row is one caller
utterance with multi-label tactic tags. **This is synthetic LLM-written text.** It's good for
teaching phrasing variety, but it isn't a measure of real-world accuracy. That measure is the
team-written held-out set in `eval/human/`, which training never reads (see CLAUDE.md deny-list).

## Labels

`AUTHORITY, THREAT, URGENCY, SECRECY, ISOLATION, PAYMENT, CREDENTIAL, BENIGN`. Full definitions are
in `generator_instructions.md`, which was given to the generator verbatim.

- A row can carry several tactics.
- **BENIGN is never combined with a tactic.**
- The model has 7 sigmoid outputs, one per tactic. An utterance is benign when no tactic clears
  its threshold.

### Labeling rules

1. Label only what the caller is doing in `text`. Context lines are earlier turns, used only to
   disambiguate.
2. A tactic counts only when it's used as pressure on the listener. Scary or official words used
   innocently are BENIGN: a real hospital, a real bank fraud alert, real police verification.
3. **Being asked to join or stay on a video call is NOT CREDENTIAL.** It is SECRECY or ISOLATION,
   depending on the wording:
   - "stay on video, don't leave the room, keep the camera on" → ISOLATION
   - "come on video call so nobody else hears, this is confidential" → SECRECY
   - Both labels can apply.
4. A delivery agent at the door asking for the delivery OTP of a parcel the listener ordered is
   BENIGN. An OTP asked for "to cancel / verify / refund / stop a parcel" is CREDENTIAL.
5. A scam utterance with no tactic in it, such as an opening greeting, is BENIGN. Its `category`
   stays `scam`.

## Generation

- **Generator.** Isolated Claude Code subagents running `claude-opus-5-5`, recorded per row as
  `generator`.
  - Each subagent was allowed to read only `generator_instructions.md` and its own batch prompts.
  - None of them read `app/coercion/`, `lexicon.yaml`, `tests/` (including fixtures) or
    `eval/human/`.
  - Six subagents ran in parallel, two per language, to keep each one's context small. They all
    received the same instructions.
- **Prompts.** Prompts are `prompts/pNN.txt`, rendered by `make_prompts.py`.
  - There are 33 batches, 11 per language, with about 100 rows each.
  - Each batch has the same mix: 45 scam, 30 hard negatives, 25 ordinary chat.
  - Batches rotate the scam types, hard-negative domains, chat topics, listener persona and the
    emphasised scam stage.
  - Every row records `prompt_id` and `prompt_hash` (a sha256 of the instructions plus the batch
    prompt).
- **Languages.**
  - `en`: Indian English.
  - `hi`: Devanagari only, with no Latin letters.
  - `hinglish`: romanized only, with no Devanagari.
  - `build_dataset.py` enforces the script rules and drops violators.
- **Spoken-style noise** (fillers, typos, half sentences) appears in about one row in three
  (`spoken_noise: true`).

## Processing (`build_dataset.py`)

1. Validate each row: known labels, BENIGN exclusive, negatives carry only BENIGN, script rules,
   and at most 3 context lines.
2. Add the `id`, `prompt_id`, `prompt_hash`, `generator`, `source=generated` and
   `text_norm = normalize(text).text` fields.
3. Remove near-duplicates whose similarity is above 0.75, using the rapidfuzz ratio on
   `normalize_key(text)` across all rows. The first row in prompt order is kept.
4. Write `data/dataset.jsonl` and `data/counts.json` (counts by label, language and category).

## STT-noised copy (`make_noisy.py`)

1. Take a stratified sample of about 1,000 rows by language and category.
2. Speak each row with macOS `say`:
   - Lekha for `hi`.
   - Rishi, Aman or Tara for `en` and `hinglish`.
   - Rate 165, 180 or 195.
3. Transcribe it with the app's own `FasterWhisperProvider` (same settings as live), then
   normalize it.
4. Write it to `data/stt_noisy.jsonl` with `source=stt_noisy` and `noisy_of=<source id>`. The row
   keeps its source's labels and `prompt_id`.

Audio exists only in a temporary directory while a row is rendered. These TTS clips are used for
the *text* classifier only, never for the voice anti-spoof model.

## Split and training (`train.py`)

- **Split.** train/val split by prompt id: `VAL_PROMPTS = p07 p08 p09 p22 p23 p24`, two batches
  per language. A noisy row follows its source's prompt id.
- **Input.** The caller utterance alone, because the live pipeline only hears the caller. Context
  lines aren't used as input.
- **Thresholds and model choice.** The best epoch, the per-tactic thresholds and the
  encoder/input choice are all selected on val, so val metrics are optimistic.

## Rebuild

From `backend/`:

```
make setup-train
make train-coercion   # SKIP_NOISY=1 to reuse data/stt_noisy.jsonl
```

Outputs go to `models/coercion/` (git-ignored): `model.onnx` (int8), `tokenizer.json`,
`model.json` (labels, thresholds, encoder and input mode, dataset hashes) and `metrics.json`.

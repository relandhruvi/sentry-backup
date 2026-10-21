#!/usr/bin/env bash
# Rebuild the coercion classifier in models/coercion/ from the committed generated batches.
#
#   1. validate + dedupe generated/*.jsonl          -> data/dataset.jsonl, data/counts.json
#   2. STT-noised copy (say -> Whisper -> normalize) -> data/stt_noisy.jsonl (resumable; slow)
#   3. train every encoder x input variant          -> models/coercion/runs/<run>/
#   4. export the best run (ONNX int8, thresholds re-tuned on the int8 model, latency)
#
# The generated batches themselves come from an LLM (see DATA.md) and are not regenerated here.
# Run from backend/: make train-coercion   (SKIP_NOISY=1 reuses data/stt_noisy.jsonl as is)
set -euo pipefail
PY=${PY:-.venv/bin/python}

$PY -m training.coercion.build_dataset >/dev/null
echo "dataset: $(wc -l < training/coercion/data/dataset.jsonl) rows"
if [[ "${SKIP_NOISY:-0}" != "1" ]]; then
  $PY -m training.coercion.make_noisy --n 1000
fi
for enc in e5-small minilm-l12; do
  for input in raw norm; do
    $PY -m training.coercion.train --encoder "$enc" --input "$input" | grep -E "^(train=|epoch)"
  done
done
$PY -m training.coercion.compare
$PY -m training.coercion.export --run "$($PY -m training.coercion.compare --best)" --precision "${PRECISION:-fp32}"
$PY -m training.coercion.tune_boost
$PY -m training.coercion.vs_lexicon

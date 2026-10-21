PYTHON ?= /opt/homebrew/bin/python3.11
VENV   := .venv
BIN    := $(VENV)/bin

.PHONY: setup setup-train run test lint fmt clean test-audio bench-stt stt-report coercion-report demo-wav train-coercion prewarm-verify check-sources

setup:
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"
	test -f .env || cp .env.example .env

# Training-only dependencies (torch, transformers, onnx). The app itself only needs onnxruntime.
setup-train:
	$(BIN)/pip install -e ".[dev,train]"

run:
	$(BIN)/python -m app

test:
	$(BIN)/pytest -q

test-audio:
	$(BIN)/python -m tools.make_test_audio

bench-stt:
	$(BIN)/python -m tools.bench_stt

# Needs a running server (make run in another terminal).
stt-report:
	$(BIN)/python -m tools.stt_report

# Coercion trajectories for the fixtures and the latest live STT run.
coercion-report:
	$(BIN)/python -m tools.coercion_report -v

# Rebuild models/coercion/ from training/coercion/generated (see training/coercion/DATA.md).
train-coercion:
	PY=$(BIN)/python training/coercion/rebuild.sh

# Cache real TinyFish results for common institutions (demo works if Wi-Fi drops). Needs the key.
prewarm-verify:
	$(BIN)/python -m tools.prewarm_verify

# Re-check sources.yaml banks against RBI's official "Banks in India" list (network).
check-sources:
	$(BIN)/python -m tools.check_sources

demo-wav:
	@test -n "$(FILE)" || (echo "usage: make demo-wav FILE=path/to/audio.wav" && exit 1)
	$(BIN)/python -m tools.stream_wav "$(FILE)"

lint:
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

fmt:
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

clean:
	rm -rf .pytest_cache .ruff_cache

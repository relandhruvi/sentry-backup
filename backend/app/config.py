"""Settings loaded from backend/.env (and the process environment)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    db_path: Path = Path("data/scamshield.db")
    store_transcripts: bool = True

    session_idle_timeout_s: float = Field(600, gt=0)
    event_queue_size: int = Field(256, gt=0)
    event_replay_size: int = Field(200, ge=0)

    # Audio ingest: 16 kHz mono int16, 20 ms frames. Kept in memory only.
    audio_ring_seconds: float = Field(30, gt=0)

    # Voice activity detection (webrtcvad)
    vad_aggressiveness: int = Field(2, ge=0, le=3)
    vad_start_speech_ms: int = Field(40, gt=0)
    vad_end_silence_ms: int = Field(600, gt=0)
    # Long speech: split at the first word gap after the soft limit, else at the quietest frame
    # before the hard limit (the old fixed 8 s cut split words and changed Whisper's output).
    vad_soft_max_ms: int = Field(8000, gt=0)
    vad_soft_pause_ms: int = Field(60, gt=0)
    vad_max_utterance_ms: int = Field(12000, gt=0)
    vad_preroll_ms: int = Field(300, ge=0)
    vad_min_speech_ms: int = Field(300, ge=0)

    # Speech-to-text (faster-whisper, CPU)
    stt_model: str = "small"
    # Smaller model used only for language ID (empty = use stt_model).
    stt_detect_model: str = "tiny"
    stt_compute_type: str = "int8"
    stt_cpu_threads: int = Field(4, gt=0)
    stt_beam_size: int = Field(1, gt=0)
    # Guards against Whisper's repetition loops ("क्राँँँँँ..."), which also blow up latency.
    # no_repeat_ngram_size works on byte-level tokens and hurt Hindi WER (42% -> 47%), so it's off.
    stt_repetition_penalty: float = Field(1.1, ge=1.0)
    stt_no_repeat_ngram_size: int = Field(0, ge=0)
    stt_languages: list[str] = ["hi", "en"]
    # Use a detected language only if its probability is at least this; otherwise fall back.
    # Hindi mode handles Hinglish better, and English mode on Hindi speech can translate.
    stt_lang_min_confidence: float = Field(0.5, ge=0.0, le=1.0)
    stt_lang_fallback: str = "hi"
    # Optional decoding prompt (vocabulary hint). Empty = none. See `make bench-stt --prompt`.
    stt_initial_prompt: str = ""
    # /ws/audio waits up to this long for the STT model to finish warming up.
    stt_ready_wait_s: float = Field(120, gt=0)
    stt_timeout_s: float = Field(8, gt=0)
    stt_max_backlog: int = Field(4, gt=0)
    stt_download_root: Path = Path("models/whisper")
    # Whisper's own no-speech rule: drop when no_speech_prob > X and avg_logprob < Y.
    stt_no_speech_threshold: float = 0.6
    stt_logprob_threshold: float = -1.0
    stt_load_on_startup: bool = True

    # Trained coercion-tactic classifier (ONNX int8, built by `make train-coercion`).
    coercion_model_enabled: bool = True
    coercion_model_dir: Path = Path("models/coercion")
    coercion_model_threads: int = Field(1, gt=0)
    coercion_model_timeout_s: float = Field(1.0, gt=0)

    # Alerts. ELDER_NAME fills "Call <name> now" in family alerts (empty = "Call them now").
    elder_name: str = ""
    alert_send_timeout_s: float = Field(5.0, gt=0)

    # Verification (TinyFish Search + Fetch). No key -> verify is "unconfigured", never faked.
    tinyfish_api_key: SecretStr | None = None
    tinyfish_timeout_s: float = Field(8.0, gt=0)
    tinyfish_search_per_minute: int = Field(30, gt=0)  # documented default account limit
    tinyfish_fetch_per_minute: int = Field(150, gt=0)  # documented default (URLs/minute)
    tinyfish_location: str = "IN"
    verify_cache_ttl_s: float = Field(86400, gt=0)
    # Overall budget for one manual check (all checks run in parallel).
    verify_budget_s: float = Field(12.0, gt=0)

    llm_enabled: bool = False

    @property
    def resolved_db_path(self) -> Path:
        return self.db_path if self.db_path.is_absolute() else BACKEND_DIR / self.db_path

    @property
    def resolved_coercion_model_dir(self) -> Path:
        d = self.coercion_model_dir
        return d if d.is_absolute() else BACKEND_DIR / d

    @property
    def resolved_stt_download_root(self) -> Path:
        root = self.stt_download_root
        return root if root.is_absolute() else BACKEND_DIR / root


@lru_cache
def get_settings() -> Settings:
    return Settings()

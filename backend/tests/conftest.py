import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    # _env_file=None keeps tests independent of a developer's local .env
    return Settings(
        _env_file=None,
        db_path=tmp_path / "test.db",
        stt_load_on_startup=False,  # never download/load Whisper in unit tests
        coercion_model_enabled=False,  # unit tests never depend on a locally trained model
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c

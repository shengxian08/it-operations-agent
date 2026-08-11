import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize(
    "missing_field",
    ["openai_base_url", "openai_api_key", "openai_model"],
)
def test_settings_rejects_openai_mode_when_a_credential_is_missing(
    missing_field: str,
) -> None:
    values: dict[str, str | None] = {
        "model_mode": "openai",
        "openai_base_url": "https://models.example.test/v1",
        "openai_api_key": "test-key-not-a-secret",
        "openai_model": "test-model",
    }
    values[missing_field] = None

    with pytest.raises(ValidationError):
        Settings(**values)


def test_settings_accepts_mock_mode_without_openai_credentials() -> None:
    settings = Settings(
        model_mode="mock",
        openai_base_url=None,
        openai_api_key=None,
        openai_model=None,
    )

    assert settings.model_mode == "mock"


def test_settings_rejects_synchronous_postgres_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(
            model_mode="mock",
            database_url="postgresql://itops:local@postgres:5432/itops",
        )

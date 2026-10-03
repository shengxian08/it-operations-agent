import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_rejects_demo_defaults():
    with pytest.raises(ValidationError, match="production"):
        Settings(_env_file=None, environment="production", model_mode="mock")


def test_test_database_cannot_equal_application_database():
    with pytest.raises(ValidationError, match="test_database_url"):
        Settings(_env_file=None, environment="development", model_mode="mock", database_url="postgresql+asyncpg://itops:itops-local-only@postgres:5432/itops", test_database_url="postgresql+asyncpg://itops:itops-local-only@postgres:5432/itops")


def test_limits_are_validated_instead_of_ignored():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, model_mode="mock", worker_concurrency=0)


def test_test_environment_can_disable_demo_without_external_models():
    settings = Settings(_env_file=None, environment="test", demo_enabled=False, model_mode="mock")
    assert settings.environment == "test"
    assert settings.demo_enabled is False
    assert settings.worker_concurrency == 10


def production_values():
    return dict(_env_file=None, environment="production", demo_enabled=False, model_mode="openai",
        public_base_url="https://it.example.com", oidc_issuer="https://it.example.com/identity/realms/itops",
        oidc_client_secret="long-identity-secret", session_secret="x" * 48,
        database_url="postgresql+asyncpg://app:password@postgres/itops", redis_url="redis://:password@redis:6379/0",
        qdrant_api_key="write-key", qdrant_read_api_key="read-key", embedding_mode="sentence-transformer",
        embedding_revision="a" * 40, openai_base_url="https://approved.example/v1", openai_model="approved",
        openai_api_key="approved-key", model_monthly_budget=100, model_input_price=1, model_output_price=2,
        test_database_url=None, test_redis_url=None, test_qdrant_url=None)


@pytest.mark.parametrize("change", [{"redis_url":"redis://localhost:6379/0"},
    {"openai_base_url":"http://approved.example/v1"}, {"embedding_revision":"release-tag"}])
def test_production_rejects_unauthenticated_dependencies_and_unpinned_model(change):
    with pytest.raises(ValidationError, match="production"):
        Settings(**(production_values() | change))


def test_api_can_start_with_only_its_read_credential(monkeypatch, tmp_path):
    values = production_values()
    values.pop("qdrant_api_key")
    values.pop("qdrant_read_api_key")
    credential = tmp_path / "query-key"
    credential.write_text("read-only-test-credential")
    monkeypatch.delenv("QDRANT_API_KEY_FILE", raising=False)
    monkeypatch.setenv("QDRANT_READ_API_KEY_FILE", str(credential))
    settings = Settings(**values)
    assert settings.qdrant_api_key is None
    assert settings.qdrant_read_api_key.get_secret_value() == "read-only-test-credential"
    with pytest.raises(ValidationError, match="workers require"):
        Settings(**(values | {"runtime_role": "worker"}))

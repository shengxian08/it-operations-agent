from functools import lru_cache
from decimal import Decimal
from pathlib import Path
import os
import re
from urllib.parse import urlsplit
from typing import Literal, Self

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    model_mode: Literal["mock", "openai"] = "mock"
    openai_base_url: HttpUrl | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None

    database_url: str = (
        "postgresql+asyncpg://itops:itops-local-only@postgres:5432/itops"
    )
    redis_url: str = "redis://redis:6379/0"
    qdrant_url: str = "http://qdrant:6333"
    environment: Literal["development", "test", "staging", "production"] = "development"
    demo_enabled: bool = True
    runtime_role: Literal["api", "worker"] = "api"
    public_base_url: str = "http://localhost:5173"
    oidc_issuer: str = "http://localhost:8080/realms/itops"
    oidc_client_id: str = "itops-web"
    oidc_client_secret: SecretStr | None = None
    oidc_scopes: str = "openid profile email"
    oidc_roles_claim: str = "realm_access.roles"
    oidc_allowed_roles: tuple[str, ...] = ("employee", "support", "admin")
    oidc_http_timeout_seconds: float = Field(default=10, gt=0, le=60)
    oidc_state_ttl_seconds: int = Field(default=300, ge=60, le=600)
    oidc_clock_skew_seconds: int = Field(default=30, ge=0, le=60)
    session_secret: SecretStr = SecretStr("development-only-change-before-deployment")
    session_cookie_name: str = "it_assistant_session"
    cookie_secure: bool = True
    session_ttl_seconds: int = Field(default=28800, ge=60, le=86400)
    worker_concurrency: int = Field(default=10, ge=1, le=10)
    run_timeout_seconds: float = Field(default=120, ge=10, le=600)
    lease_seconds: int = Field(default=30, ge=5, le=120)
    user_rate_limit: int = Field(default=5, ge=1, le=100)
    queue_limit: int = Field(default=50, ge=1, le=1000)
    model_timeout_seconds: float = Field(default=45, gt=0, le=120)
    model_max_retries: int = Field(default=1, ge=0, le=2)
    model_max_output_tokens: int = Field(default=1500, ge=64, le=8192)
    budget_max_input_tokens: int = Field(default=16000, ge=1000, le=128000)
    model_monthly_budget: Decimal = Field(default=Decimal("0"), ge=0)
    model_input_price: Decimal = Field(default=Decimal("0"), ge=0)
    model_output_price: Decimal = Field(default=Decimal("0"), ge=0)
    embedding_mode: Literal["deterministic", "sentence-transformer"] = "deterministic"
    embedding_hash_dim: int = Field(default=256, ge=16)
    embedding_dimensions: int = Field(default=512, ge=16, le=4096)
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_revision: str = ""
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    qdrant_collection: str = "knowledge_chunks"
    qdrant_api_key: SecretStr | None = None
    qdrant_read_api_key: SecretStr | None = None
    knowledge_storage_path: Path = Path("../data/production-knowledge")
    knowledge_max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    knowledge_max_pdf_pages: int = Field(default=100, ge=1, le=500)
    knowledge_parse_timeout_seconds: float = Field(default=30, ge=1, le=120)
    knowledge_index_timeout_seconds: float = Field(default=600, ge=30, le=1800)
    knowledge_index_lease_seconds: int = Field(default=30, ge=5, le=120)
    ticket_confirmation_ttl_seconds: int = Field(default=600, ge=60, le=1800)
    minimum_evidence_score: float = Field(default=0.35, ge=0, le=10)
    test_database_url: str | None = None
    test_redis_url: str | None = None
    test_qdrant_url: str | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_directory: Path | None = None
    otel_exporter_otlp_endpoint: str | None = None

    @model_validator(mode="before")
    @classmethod
    def read_secret_files(cls, values):
        values = dict(values)
        for field in ("openai_api_key", "oidc_client_secret", "session_secret", "qdrant_api_key", "qdrant_read_api_key", "database_url", "redis_url"):
            secret_file = os.getenv(f"{field.upper()}_FILE")
            if secret_file and field not in values:
                path = Path(secret_file)
                if not path.is_file():
                    raise ValueError(f"configured secret file for {field} is unavailable")
                values[field] = path.read_text(encoding="utf-8").strip()
        return values

    @field_validator("database_url")
    @classmethod
    def require_async_postgres_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use the postgresql+asyncpg driver")
        return value

    @model_validator(mode="after")
    def require_openai_credentials(self) -> Self:
        if self.model_mode != "openai":
            return self

        has_api_key = bool(
            self.openai_api_key
            and self.openai_api_key.get_secret_value().strip()
        )
        if not self.openai_base_url or not has_api_key or not self.openai_model:
            raise ValueError(
                "openai mode requires openai_base_url, openai_api_key, and openai_model"
            )
        if not self.openai_model.strip():
            raise ValueError("openai_model must not be blank")
        return self

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        if self.environment == "development" and self.test_database_url and self.test_database_url == self.database_url:
            raise ValueError("test_database_url must be isolated from database_url")
        if self.environment not in {"production", "staging"}:
            return self
        problems = []
        if self.demo_enabled or self.model_mode != "openai":
            problems.append("disable demo and configure an approved real model")
        public=urlsplit(self.public_base_url)
        issuer=urlsplit(self.oidc_issuer)
        if (not self.cookie_secure or public.scheme != "https" or issuer.scheme != "https"
            or not public.hostname or not issuer.hostname or public.username or issuer.username
            or public.query or public.fragment or issuer.query or issuer.fragment or public.path not in {"","/"}):
            problems.append("HTTPS and Secure cookies are required")
        if len(self.session_secret.get_secret_value()) < 32 or "development" in self.session_secret.get_secret_value() or not self.oidc_client_secret:
            problems.append("set non-demo session and OIDC secrets")
        if "itops-local-only" in self.database_url or not urlsplit(self.database_url).password or not urlsplit(self.redis_url).password:
            problems.append("set authenticated database and Redis connections")
        if (not self.qdrant_read_api_key or self.qdrant_api_key == self.qdrant_read_api_key
            or (self.runtime_role == "worker" and not self.qdrant_api_key)):
            problems.append("Qdrant read key is required; workers require a separate write key")
        if (self.embedding_mode != "sentence-transformer" or self.embedding_model != "BAAI/bge-small-zh-v1.5"
            or not re.fullmatch(r"[0-9a-f]{40}", self.embedding_revision)):
            problems.append("pin a semantic embedding model revision")
        if not self.openai_base_url or self.openai_base_url.scheme != "https":
            problems.append("approved model endpoint must use HTTPS")
        if min(self.model_monthly_budget, self.model_input_price, self.model_output_price) <= 0:
            problems.append("configure positive monthly budget and prices per million tokens")
        if self.test_database_url or self.test_redis_url or self.test_qdrant_url:
            problems.append("test service URLs must not be present")
        if problems:
            raise ValueError("production configuration invalid: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=None) if os.getenv("ENVIRONMENT") in {"test", "production", "staging"} else Settings()

from functools import lru_cache
from typing import Literal, Self

from pydantic import HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
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


@lru_cache
def get_settings() -> Settings:
    return Settings()

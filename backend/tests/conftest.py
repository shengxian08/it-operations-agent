"""Integration tests may never silently connect to a developer/business database."""
import os
from urllib.parse import urlsplit
import pytest


def pytest_configure(config):
    database = os.getenv("TEST_DATABASE_URL")
    if database:
        name = urlsplit(database).path.rsplit("/", 1)[-1]
        if not any(word in name.lower() for word in ("test", "testing")):
            raise pytest.UsageError("TEST_DATABASE_URL database name must explicitly identify a disposable test database")
        if os.getenv("ENVIRONMENT") in {"production", "staging"}:
            raise pytest.UsageError("Tests cannot execute in a production/staging environment")
        os.environ.update(ENVIRONMENT="test", DATABASE_URL=database, MODEL_MODE="mock", DEMO_ENABLED="true")
        for component in ("REDIS", "QDRANT"):
            if os.getenv(f"TEST_{component}_URL"):
                os.environ[f"{component}_URL"] = os.environ[f"TEST_{component}_URL"]


def pytest_collection_modifyitems(items):
    required = ("TEST_DATABASE_URL", "TEST_REDIS_URL", "TEST_QDRANT_URL")
    if all(os.getenv(name) for name in required):
        return
    for item in items:
        if "/integration/" in str(item.path).replace("\\", "/") or (
            "/production/" in str(item.path).replace("\\", "/") and "ctx" in item.fixturenames
        ):
            item.add_marker(pytest.mark.skip(reason="Explicit isolated TEST_DATABASE_URL/TEST_REDIS_URL/TEST_QDRANT_URL required"))

FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e AS base
COPY --from=ghcr.io/astral-sh/uv:0.11.10@sha256:bca7f6959666f3524e0c42129f9d8bbcfb0c180d847f5187846b98ff06125ead /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
WORKDIR /app/backend
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable
ENV PATH="/app/backend/.venv/bin:$PATH"
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app

FROM base AS development
COPY tests ./tests
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --extra dev --no-editable
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]

FROM base AS worker
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra rag --no-editable
USER app
CMD ["python", "-m", "app.production.worker"]

FROM base AS production
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]

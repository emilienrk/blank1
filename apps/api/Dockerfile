# Une seule image pour api ET worker : seule la commande de démarrage diffère
# (uvicorn app.main:app vs celery -A app.worker.celery_app worker).
# Contexte de build : la racine du repo (workspace uv).

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Couche dépendances (mise en cache tant que les manifestes ne bougent pas)
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
RUN uv sync --frozen --no-install-workspace --no-dev

# Couche sources
COPY apps/api apps/api
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app/apps/api
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/apps/api /app/apps/api
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

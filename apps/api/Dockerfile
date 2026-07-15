# Une seule image pour api ET worker : seule la commande de démarrage diffère
# (uvicorn app.main:app vs celery -A app.worker.celery_app worker).
# Contexte de build : la racine du repo (workspace uv).

FROM python:3.12-slim-bookworm AS builder
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

FROM python:3.12-slim-bookworm AS runtime
# postgresql-client-17 (dépôt PGDG — Debian bookworm n'embarque que la 15) : requis par
# l'export RGPD (`pg_dump`/`pg_restore` en sous-processus, Phase 4 T4) — doit matcher la
# version du serveur (Postgres 17) pour un dump/restore fiable.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
        https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 \
    && apt-get purge -y curl gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app/apps/api
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/apps/api /app/apps/api
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN mkdir -p /var/lib/saas/gdpr-exports && chown appuser:appuser /var/lib/saas/gdpr-exports
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

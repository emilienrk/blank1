# Point d'entrée unique : humains, CI et assistants IA passent par ces cibles.

.PHONY: install dev infra api web worker lint format typecheck test openapi generate-client build smoke

install: ## Installe toutes les dépendances (Python + Node)
	uv sync --all-packages
	pnpm install

infra: ## Démarre les services d'infrastructure (Postgres, Valkey, Loki, Grafana, Alloy, Uptime Kuma)
	docker compose up -d postgres valkey loki grafana alloy uptime-kuma

dev: infra ## Infra + rappel des commandes de dev
	@echo "Infra démarrée. Dans deux terminaux : 'make api' et 'make web'."

api: ## Lance l'API en mode rechargement
	uv run uvicorn app.main:app --reload --app-dir apps/api --port 8000

worker: ## Lance le worker Celery
	cd apps/api && uv run celery -A app.worker.celery_app worker --loglevel=INFO

web: ## Lance la SPA en mode dev (proxy /api -> localhost:8000)
	pnpm --filter web dev

lint:
	uv run ruff check .
	uv run ruff format --check .
	pnpm --filter web lint

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run pyright
	pnpm --filter web typecheck
	pnpm --filter @app/api-client typecheck
	pnpm --filter @app/ui typecheck

test:
	uv run pytest
	pnpm --filter web test

openapi: ## Exporte openapi.json depuis l'app FastAPI (sans serveur)
	uv run python scripts/export_openapi.py openapi.json

generate-client: openapi ## Régénère packages/api-client depuis le contrat OpenAPI
	pnpm --filter @app/api-client generate

build: ## Construit les images Docker (api+worker : une seule image ; web : statiques + Caddy)
	docker compose build

smoke: ## Vérifie le health à travers Caddy (SMOKE_URL surchargable, ex. https://staging.exemple.fr)
	./scripts/smoke.sh $${SMOKE_URL:-http://localhost:8080}

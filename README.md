# Socle SaaS B2B multi-tenant

Socle commun réutilisable (auth multi-tenant, connecteurs externes, couche IA
multi-fournisseurs, logs, RGPD) pour des modules d'automatisation métier.
Plans : [architecture globale](docs/architecture-plan.md) ·
[Phase 0 — Fondations](docs/phase-0-fondations-plan.md).

## Stack

Python 3.12 · FastAPI · Celery (broker Valkey) · PostgreSQL 17 ·
React/Vite/TypeScript · TanStack Router/Query · Tailwind ·
Docker Compose · Caddy · Loki + Grafana + Alloy · Uptime Kuma.

## Démarrage local

Prérequis : [uv](https://docs.astral.sh/uv/), Node 22 (`corepack enable`), Docker.

```bash
cp .env.example .env
make install     # dépendances Python (uv) + Node (pnpm)
make dev         # infra Docker (Postgres, Valkey, Loki, Grafana, Alloy, Uptime Kuma)
# puis, dans deux terminaux :
make api         # API sur http://localhost:8000 (docs : /api/v1/docs)
make web         # SPA sur http://localhost:5173 (proxy /api -> :8000)
```

Environnement complet conteneurisé (SPA servie par Caddy sur http://localhost:8080) :
`docker compose up -d --build`.

## Commandes

| Commande | Effet |
|---|---|
| `make lint` / `make format` | ruff + eslint |
| `make typecheck` | pyright strict + tsc |
| `make test` | pytest + vitest |
| `make generate-client` | régénère `packages/api-client` depuis l'OpenAPI |
| `make build` | build des images Docker |
| `make smoke` | vérifie le health à travers Caddy |

## Carte du repo

```
apps/api/            # Backend FastAPI + worker Celery (même image Docker)
apps/web/            # SPA client React
packages/api-client/ # Client TS généré depuis l'OpenAPI — ne pas éditer
packages/ui/         # Composants React partagés
infra/               # Caddy, Loki, Grafana, Alloy
scripts/             # export OpenAPI, smoke test
.github/workflows/   # CI bloquante + déploiement continu staging
```

Les invariants du projet (multi-tenant, sécurité, logs) sont dans [CLAUDE.md](CLAUDE.md).

## Déploiement staging (modèle pull)

Chaque push sur `main` : la CI passe, puis `staging-images.yml` publie les images
vers GHCR (`:sha` + `:latest`). La machine de staging **tire elle-même** les
nouveautés : un timer systemd exécute `scripts/deploy-pull.sh` toutes les 5 minutes,
qui redéploie uniquement si une image a changé, puis lance le smoke test HTTPS.
Aucun accès entrant, aucun runner : la machine ne fait que des connexions sortantes.

Mise en place initiale de la machine staging (une seule fois) :

1. Installer Docker, puis cloner ce repo dans `/srv/saas/app`.
2. `docker login ghcr.io` avec un PAT fine-grained **lecture seule** (`packages:read` ;
   ajouter `contents:read` si le clone utilise ce même PAT).
3. Créer `/srv/saas/.env` à partir de `.env.example` avec au minimum :
   `APP_ENV=staging`, `SITE_ADDRESS=staging.<domaine>` (le DNS doit pointer sur la
   machine, wildcard `*.staging.<domaine>` recommandé pour la suite),
   `API_IMAGE=ghcr.io/<owner>/<repo>-api:latest`, `WEB_IMAGE=ghcr.io/<owner>/<repo>-web:latest`,
   `POSTGRES_PASSWORD` et `GRAFANA_ADMIN_PASSWORD` robustes.
4. Installer le timer :
   `sudo cp infra/systemd/saas-deploy.* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now saas-deploy.timer`.
   Suivi : `journalctl -u saas-deploy.service`.
5. Les ports 80/443 doivent être joignables depuis Internet (TLS automatique Caddy).
   Grafana (:3000) et Uptime Kuma (:3001) restent liés à 127.0.0.1 : accès via
   WireGuard/tunnel SSH uniquement.

## Critère de démo — Phase 0

Un `git push` sur `main` déclenche CI verte + publication GHCR, puis la machine
staging se met à jour d'elle-même (≤ 5 minutes) ;
`https://staging.<domaine>` sert la SPA qui affiche le statut de
`GET /api/v1/health` via le client TS généré ; la requête est visible dans
Grafana/Loki corrélée par `request_id` ; le worker Celery tourne ;
Uptime Kuma surveille le health.

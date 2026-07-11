# Socle SaaS B2B multi-tenant — conventions du monorepo

Plans de référence : `docs/architecture-plan.md` (global) et `docs/phase-2-auth-annuaire-plan.md` (phase courante — plan en attente de validation utilisateur).

## Carte du repo

```
apps/api/            # Backend FastAPI + worker Celery (même code, même image Docker)
apps/web/            # SPA client React/Vite/TS
packages/api-client/ # Client TS GÉNÉRÉ depuis l'OpenAPI — ne jamais éditer à la main
packages/ui/         # Composants React partagés
infra/               # Caddy, Loki, Grafana, Alloy
scripts/             # export OpenAPI, smoke test
docs/                # Plans d'architecture et de phase
```

## Commandes

Tout passe par le `Makefile` : `make install`, `make dev`, `make lint`, `make typecheck`,
`make test`, `make generate-client`, `make build`, `make smoke`.

## Invariants — à respecter absolument

1. **Jamais de requête métier sans contexte tenant résolu.** Exécutable depuis la Phase 1 :
   tout accès à une DB tenant passe par `app.tenancy.session.get_tenant_session()`, qui lève
   `TenantContextError` sans contexte posé. Ne jamais créer d'engine tenant à la main.
2. **Une seule image Docker pour `api` et `worker`** — seule la commande de démarrage diffère.
3. **Config exclusivement par variables d'environnement** (pydantic-settings). Aucun secret
   dans le repo ; `.env.example` committé, `.env` ignoré.
4. **Logs : JSON sur stdout uniquement**, corrélés par `request_id`. Jamais de PII ni de
   contenu métier dans les logs techniques. Pas de fichiers de log.
5. **Le client TS (`packages/api-client`) est toujours généré** via `make generate-client`,
   jamais édité à la main. La dérive contrat/client casse la CI.
6. **CI bloquante** : pas de merge sans ruff + pyright strict + pytest + eslint + tsc + vitest verts.
7. **Surfaces admin jamais exposées publiquement** (Grafana, Uptime Kuma, futur back-office) :
   réseau local/WireGuard uniquement, même en staging.
8. **Typage strict dès la première ligne** : pyright `strict`, TypeScript `strict: true`.

## Conventions

- Python 3.12, Node 22 LTS (pnpm via corepack), PostgreSQL 17 — versions figées, jamais implicites.
- Backend : un package Python par module métier sous `apps/api/app/` (voir `apps/api/CLAUDE.md`).
- Toutes les routes API sous `/api/v1`.
- Frontend : TanStack Router/Query, Tailwind ; les composants réutilisables vont dans `packages/ui`.
- Commits : messages impératifs courts ; une préoccupation par commit.

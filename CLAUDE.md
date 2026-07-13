# Socle SaaS B2B multi-tenant — conventions du monorepo

Plans de référence : `docs/architecture-plan.md` (global) et `docs/phase-7-automation-runtime-plan.md` (phase courante — plan validé, implémenté).

## Carte du repo

```
apps/api/            # Backend FastAPI + worker Celery (même code, même image Docker)
apps/api/app/modules/ # Modules métier (Phase 7) — un package par module, s'ajoute sans toucher au cœur
apps/web/            # SPA client React/Vite/TS
apps/admin/           # SPA back-office React/Vite/TS — jamais exposée publiquement (invariant n°7)
packages/api-client/ # Client TS GÉNÉRÉ depuis l'OpenAPI — ne jamais éditer à la main
packages/ui/         # Composants React partagés (consommés par apps/web ET apps/admin)
infra/               # Caddy (Caddyfile public + Caddyfile.admin interne), Loki, Grafana, Alloy
scripts/             # export OpenAPI, smoke test, déploiement staging
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
7. **Surfaces admin jamais exposées publiquement** (Grafana, Uptime Kuma, `apps/admin` +
   `/api/v1/admin/*`) : réseau local/WireGuard uniquement, même en staging — double
   barrière pour le back-office (`require_platform_admin` + vhost Caddy dédié, voir
   `infra/caddy/Caddyfile` vs `Caddyfile.admin`).
8. **Typage strict dès la première ligne** : pyright `strict`, TypeScript `strict: true`.
9. **Aucune route métier sans `require_permission`** (Phase 2) : auth + membership exigés
   partout ; les seules routes anonymes sont health, login (+TOTP), OAuth start/callback
   et acceptation d'invitation. Aucun secret en clair en base (argon2id, tokens hachés,
   secrets TOTP chiffrés) ; inscription publique désactivée — tout compte naît d'une invitation.
10. **Toute route `/api/v1/admin/*` exige `require_platform_admin`** (Phase 3), sans
    exception. `is_platform_admin` ne se pose JAMAIS via l'API : uniquement
    `saas admin grant/revoke` (CLI, accès shell machine requis).
11. **L'audit est append-only** (Phase 4) : toute action métier significative du socle
    écrit son événement via `record_audit_event`/`record_audit_event_for_tenant`
    (`app.audit.service`) dans la même transaction que l'action auditée — table
    `audit_events` en **DB tenant**, jamais dupliquée en clair dans les logs techniques
    (invariant n°4). Aucune route de modification/suppression en dehors de la politique
    de rétention (`app.gdpr.retention`).
12. **L'effacement RGPD passe toujours par le délai de grâce** (`app.gdpr.erasure`) :
    aucun chemin de `DROP DATABASE` direct hors `execute_pending_erasures` (et le
    `retry-provision` de la Phase 1, qui ne droppe que des bases jamais devenues
    `active`). Les exports RGPD sont chiffrés au repos, à durée de vie bornée, et ne
    sont jamais servis par la surface publique.
13. **Aucun token de connecteur en clair, nulle part** (Phase 5) : chiffré `KeyProvider`
    en **DB tenant** (`app.connectors.tenant_models`), jamais en control-plane (qui ne
    porte que le routage `webhook_routes`), jamais dans les logs (même tronqué), jamais
    dans une réponse API (la SPA ne voit que statuts et labels). Le reste du code ne
    consomme QUE les capabilities (`app.connectors.capabilities`) — tout accès direct aux
    APIs Google/Graph hors `app/connectors/providers/` est une violation. Tout appel
    provider passe par l'enveloppe throttle/backoff (`app.connectors.throttle`), aucun
    appel lourd dans le cycle requête/réponse HTTP. Cycle de vie audité (`connector.*`).
    Deux nouvelles routes anonymes (liste fermée, invariant n°9) :
    `connectors/{provider}/callback` (OAuth tiers) et `webhooks/{provider}/{route_key}`
    (notifications providers, authentifiées avant tout traitement).
14. **Tout appel IA passe par `AIGateway`** (Phase 6, `app.ai.gateway`) : aucun import de
    `litellm` ni d'un SDK provider hors de `app/ai/` ; jamais d'appel IA sans contexte
    tenant. **Jamais de contenu de prompt ni de complétion** dans les logs techniques ni
    dans `ai_usage_events` — uniquement des métriques (tokens, latence, coût, statut).
    Chaque appel produit exactement un événement d'usage (succès comme échec, fondation
    facturation). La **politique zéro-rétention** est infranchissable par configuration
    (liste ZDR en code, refus explicite hors liste). Clés provider (plateforme via env,
    BYOK chiffré `KeyProvider`) jamais en clair en base ni dans les logs.
15. **Ajouter un module ne modifie JAMAIS le cœur** (Phase 7, `app/automation/`) :
    uniquement `app/modules/<name>/` + une ligne au registre (`app.automation.registry`)
    + une migration tenant — garanti par le test structurel `test_module_isolation`. Un
    module ne consomme QUE les briques socle (capabilities, `AIGateway`,
    `get_tenant_session`, `record_audit_event`) et **jamais un autre module**. Toute route
    de module porte `require_permission("<name>.…")` (vérifié au démarrage) **et** exige le
    module actif pour le tenant (`require_module_enabled`). Permissions/tâches/actions
    d'audit d'un module namespacées `<name>.*` (jamais `core.*`). Activation par tenant en
    **control-plane** (`tenant_modules`, gouvernance) contrôlée par les
    `required_capabilities` ; désactivation conserve les données tenant. Tâches
    périodiques : beat statique + fan-out sur les tenants actifs, contexte tenant posé,
    verrou anti-chevauchement, échec isolé par tenant. Metering IA ventilé `module=<name>`.

## Conventions

- Python 3.12, Node 22 LTS (pnpm via corepack), PostgreSQL 17 — versions figées, jamais implicites.
- Backend : un package Python par module métier sous `apps/api/app/` (voir `apps/api/CLAUDE.md`).
- Toutes les routes API sous `/api/v1`.
- Frontend : TanStack Router/Query, Tailwind, react-hook-form + zod pour les formulaires ;
  les composants réutilisables vont dans `packages/ui` (jamais dupliqués entre `apps/web`
  et `apps/admin`) ; un seul client TS généré consommé par les deux SPA.
- Commits : messages impératifs courts ; une préoccupation par commit.

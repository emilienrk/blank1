# Socle SaaS B2B multi-tenant — conventions du monorepo

Architecture simplifiée MVP (juillet 2026) : voir `docs/adr/` (décisions + procédure de
réintroduction) et le tag `archive/pre-mvp-simplification` (état complet d'avant).
Plans de phase historiques : `docs/architecture-plan.md` et `docs/phase-*.md`.

## Carte du repo

```
apps/api/            # Backend FastAPI + worker/beat Celery (même code, même image Docker)
apps/api/app/modules/ # Modules métier — un package par module, s'ajoute sans toucher le cœur
apps/web/            # SPA client React/Vite/TS
packages/api-client/ # Client TS GÉNÉRÉ depuis l'OpenAPI — ne jamais éditer à la main
packages/ui/         # Composants React partagés
infra/               # Caddy (vhost public)
scripts/             # export OpenAPI, smoke test, déploiement staging
docs/                # ADR, plans d'architecture et de phase
```

## Commandes

Tout passe par le `Makefile` : `make install`, `make dev`, `make lint`, `make typecheck`,
`make test`, `make generate-client`, `make build`, `make smoke`, `make migrate`,
`make revision m="..."`.

## Invariants — à respecter absolument

1. **Jamais de requête métier sans contexte tenant résolu** (ADR 0001). Base unique :
   toute table métier hérite de `(Base, TenantScoped)` et les garde-fous de session
   (`app.tenancy.tenant_base`) injectent le filtre `tenant_id = contexte courant` sur
   chaque SELECT/UPDATE/DELETE, estampillent les INSERT et lèvent `TenantContextError`
   sans contexte. Accès via `get_tenant_session()` (HTTP) / `tenant_session()`
   (CLI/tâches) ; jamais d'engine à la main, jamais de `text(...)` sur une table métier.
   Garde-fou : `tests/test_tenant_isolation_db.py`.
2. **Une seule image Docker pour `api` et `worker`** — seule la commande de démarrage diffère.
3. **Config exclusivement par variables d'environnement** (pydantic-settings). Aucun secret
   dans le repo ; `.env.example` committé, `.env` ignoré.
4. **Logs : JSON sur stdout uniquement**, corrélés par `request_id`. Jamais de PII ni de
   contenu métier dans les logs techniques. Pas de fichiers de log. (Observabilité MVP =
   `docker compose logs` + `make smoke`, ADR 0004.)
5. **Le client TS (`packages/api-client`) est toujours généré** via `make generate-client`,
   jamais édité à la main. La dérive contrat/client casse la CI.
6. **CI bloquante** : pas de merge sans ruff + pyright strict + pytest + eslint + tsc + vitest verts.
7. **Typage strict dès la première ligne** : pyright `strict`, TypeScript `strict: true`.
8. **Aucune route métier sans `require_permission`** : auth + membership exigés partout ;
   les seules routes anonymes sont health, login (+TOTP), OAuth start/callback,
   acceptation d'invitation, `connectors/{provider}/callback` et
   `webhooks/{provider}/{route_key}` (authentifiées avant tout traitement). Aucun secret
   en clair en base (argon2id, tokens hachés, secrets TOTP chiffrés) ; inscription
   publique désactivée — tout compte naît d'une invitation.
9. **L'audit est append-only par design** : toute action métier significative écrit son
   événement via `record_audit_event`/`record_audit_event_for_tenant`
   (`app.audit.service`), dans la même transaction que l'action quand c'est possible —
   table `audit_events` scopée tenant, jamais dupliquée en clair dans les logs
   (invariant n°4). Actions namespacées par convention (`core.*`, `connector.*`,
   `<module>.*` — validé au démarrage pour les modules). Aucune route ni fonction de
   modification/suppression.
10. **Suppression de tenant = soft-delete** (ADR 0002) : `Tenant.deleted_at` posé par
    `saas tenant delete` (CLI, confirmation par re-saisie du slug). Un tenant supprimé
    est invisible partout (HTTP 404, fan-out beat, webhooks, OAuth) mais ses données
    restent en base. Aucun autre chemin de suppression.
11. **Administration plateforme par CLI/SQL uniquement** (ADR 0003) : pas de back-office,
    pas de rôle plateforme, aucune route `/api/v1/admin/*`. La CLI `saas` (tenants,
    invitations, migrations) exige un accès shell machine.
12. **Aucun token de connecteur en clair, nulle part** : chiffré `KeyProvider` en base
    (`app.connectors.tenant_models`), jamais dans les logs (même tronqué), jamais dans
    une réponse API (la SPA ne voit que statuts et labels). Le reste du code ne consomme
    QUE les capabilities (`app.connectors.capabilities`) — tout accès direct aux APIs
    Google/Graph hors `app/connectors/providers/` est une violation. Tout appel provider
    passe par l'enveloppe throttle/backoff (`app.connectors.throttle`), aucun appel lourd
    dans le cycle requête/réponse HTTP. Cycle de vie audité (`connector.*`).
13. **Tout appel IA passe par `AIGateway`** (`app.ai.gateway`) : aucun import de `litellm`
    ni d'un SDK provider hors de `app/ai/` ; jamais d'appel IA sans contexte tenant.
    **Jamais de contenu de prompt ni de complétion** dans les logs ni dans
    `ai_usage_events` — uniquement des métriques (tokens, latence, coût, statut). Chaque
    appel produit exactement un événement d'usage (succès comme échec). La **politique
    zéro-rétention** est infranchissable par configuration (liste ZDR en code). Clés
    provider (plateforme via env, BYOK chiffré) jamais en clair en base ni dans les logs.
14. **Ajouter un module ne modifie JAMAIS le cœur** (`app/automation/`) : uniquement
    `app/modules/<name>/` + une ligne au registre (`app.automation.registry`) + une
    révision Alembic (`make revision`) — garanti par `test_module_isolation`. Un module
    ne consomme QUE les briques socle (capabilities, `AIGateway`, session tenant,
    `record_audit_event`) et **jamais un autre module**. Toute route de module porte
    `require_permission("<name>.…")` (vérifié au démarrage) **et** exige le module actif
    (`require_module_enabled`). Permissions/tâches/actions namespacées `<name>.*` (jamais
    `core.*`). Activation par tenant (`tenant_modules`) contrôlée par les
    `required_capabilities` ; désactivation conserve les données. Tâches périodiques :
    beat statique + fan-out sur les tenants actifs, contexte posé, verrou
    anti-chevauchement, échec isolé par tenant. Metering IA ventilé `module=<name>`.

## Conventions

- Python 3.12, Node 22 LTS (pnpm via corepack), PostgreSQL 17 — versions figées, jamais implicites.
- Backend : un package Python par module métier sous `apps/api/app/` (voir `apps/api/CLAUDE.md`).
- Toutes les routes API sous `/api/v1`.
- Frontend : TanStack Router/Query, Tailwind, react-hook-form + zod pour les formulaires ;
  les composants réutilisables vont dans `packages/ui` ; un seul client TS généré.
- Commits : messages impératifs courts ; une préoccupation par commit.

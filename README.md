# Socle SaaS B2B multi-tenant

Socle commun réutilisable (auth multi-tenant, connecteurs externes, couche IA
multi-fournisseurs) pour des modules d'automatisation métier.

Architecture **simplifiée MVP** (juillet 2026) : base unique + `tenant_id`, pas de
back-office ni de stack d'observabilité — décisions et procédures de réintroduction
dans [`docs/adr/`](docs/adr/), état complet d'avant (plans de phase inclus) sous le
tag git `archive/pre-mvp-simplification`. Documentation d'architecture :
[`docs/`](docs/README.md).

## Stack

Python 3.12 · FastAPI · Celery (broker Valkey) · PostgreSQL 17 ·
React/Vite/TypeScript · TanStack Router/Query · Tailwind · react-hook-form + zod ·
Docker Compose · Caddy.

## Démarrage local

Prérequis : [uv](https://docs.astral.sh/uv/), Node 22 (`corepack enable`), Docker.

```bash
cp .env.example .env
make install     # dépendances Python (uv) + Node (pnpm)
make dev         # infra Docker (Postgres, Valkey)
make migrate     # alembic upgrade head
# puis, dans des terminaux séparés :
make api         # API sur http://localhost:8000 (docs : /api/v1/docs)
make worker      # worker Celery
make web         # SPA client sur http://localhost:5173 (proxy /api -> :8000)
```

Environnement complet conteneurisé (SPA servie par Caddy sur http://localhost:8080) :
`docker compose up -d --build` — 6 services : postgres, valkey, api, worker, beat, caddy.

## Commandes

| Commande | Effet |
|---|---|
| `make lint` / `make format` | ruff + eslint |
| `make typecheck` | pyright strict + tsc |
| `make test` | pytest + vitest |
| `make generate-client` | régénère `packages/api-client` depuis l'OpenAPI |
| `make migrate` | `alembic upgrade head` (base unique) |
| `make revision m="..."` | nouvelle révision Alembic (autogenerate) |
| `make build` | build des images Docker |
| `make smoke` | vérifie le health à travers Caddy |

## Carte du repo

```
apps/api/            # Backend FastAPI + worker/beat Celery (même image Docker)
apps/web/            # SPA client React
packages/api-client/ # Client TS généré depuis l'OpenAPI — ne pas éditer
packages/ui/         # Composants React partagés
infra/               # Caddy (vhost public), unité systemd du déploiement staging
scripts/             # export OpenAPI, smoke test, déploiement staging
.github/workflows/   # CI bloquante + publication des images staging
docs/                # ADR, plans d'architecture et de phase
```

Les invariants du projet (multi-tenant, sécurité, logs) sont dans [CLAUDE.md](CLAUDE.md).

## Multi-tenant (ADR 0001)

**Base PostgreSQL unique** : toute table métier porte un `tenant_id` (FK indexée,
CASCADE) via le mixin `TenantScoped`. L'isolation est garantie par construction : des
garde-fous au niveau session SQLAlchemy injectent le filtre `tenant_id = contexte
courant` sur chaque requête, estampillent les insertions et refusent toute requête
métier sans contexte tenant résolu (`TenantContextError`). Le test
`apps/api/tests/test_tenant_isolation_db.py` en est le gardien.

Administration via le CLI `saas` (en conteneur : `docker compose run --rm api saas …`) :

```bash
uv run saas tenant create acme --name "ACME Corp"   # insert au catalogue, actif d'emblée
uv run saas tenant list                             # slug, état, soft-delete éventuel
uv run saas tenant delete acme                      # soft-delete (ADR 0002, re-saisie du slug)
uv run saas db upgrade                              # alembic upgrade head
```

La suppression est un **soft-delete** : le tenant devient invisible partout (HTTP 404,
tâches beat, webhooks) mais ses données restent en base — restauration par SQL
(`UPDATE tenants SET deleted_at = NULL`).

**Prérequis pour `make test` en local** : un Postgres joignable (celui de `make infra`
suffit) — les tests DB créent des bases éphémères `test_*` et les droppent en teardown.

## Auth + annuaire (Phase 2)

Auth interne construite sur des briques mûres : argon2id (mots de passe), sessions
serveur (cookie httpOnly/SameSite=Lax, révocables), TOTP pyotp (secrets chiffrés
AES-256-GCM), OAuth login Google/Microsoft (OIDC, Authlib). **Inscription publique
désactivée** : tout compte naît d'une invitation.

```bash
uv run saas tenant create acme --owner-email alice@example.com  # → URL d'invitation affichée
uv run saas invitation create acme bob@example.com --role member
# POST /api/v1/auth/invitations/accept {token, password}   → compte créé + membership
# POST /api/v1/auth/login {email, password}                → cookie de session
# GET  /api/v1/directory/members (Host: acme.<domaine>)    → annuaire du tenant
```

L'URL d'acceptation est **toujours retournée à l'appelant** ; l'envoi d'email est
optionnel (`SMTP_*`). RBAC : rôles `owner`/`admin`/`member`, permissions `core.*`
vérifiées par la dépendance unique `require_permission` ; toute route métier exige
sous-domaine tenant + session + membership.

Variables clés (voir `.env.example`) : `AUTH_MASTER_KEY` (obligatoire hors dev —
`openssl rand -base64 32`), `SESSION_COOKIE_DOMAIN`, `PUBLIC_BASE_URL`,
`GOOGLE_CLIENT_ID/SECRET` et `MICROSOFT_CLIENT_ID/SECRET`.

## SPA client

`apps/web` : login (mot de passe + TOTP, ou OAuth), acceptation d'invitation, annuaire
(membres, invitations, équipes), sécurité du compte (TOTP), journal d'audit
(owner/admin), connecteurs, pages de modules. L'état d'auth vient exclusivement de
`GET /api/v1/auth/me` ; un 401 redirige vers `/login`, un 403 affiche « accès refusé ».

## Audit

Chaque action métier significative écrit un événement dans `audit_events` (scopée
tenant, **append-only par design** — aucune route de modification/suppression), dans la
même transaction que l'action quand c'est possible. Consultable dans la SPA
(`core.audit.read`, owner/admin). Jamais dupliqué en clair dans les logs techniques.

L'administration plateforme (consultation transverse, politiques IA, restauration d'un
tenant soft-deleted) se fait par CLI/SQL — pas de back-office (ADR 0003).

## Connecteurs externes (Phase 5)

Framework de connexion aux comptes Google Workspace et Microsoft 365, exposant des
**capabilities normalisées** (`Mail`, `Calendar`) que les modules consomment sans jamais
toucher les APIs propriétaires. Tokens **chiffrés au repos** (AES-256-GCM), jamais en
clair nulle part ; refresh proactif (beat 5 min, verrou par connexion) ; révocation
provider → `needs_reconsent` + re-consentement guidé dans la SPA ; webhooks entrants
authentifiés avant tout traitement (`POST /api/v1/webhooks/{provider}/{route_key}` —
seule surface entrante depuis Internet avec le navigateur). Cycle de vie audité
(`connector.*`).

Apps OAuth **distinctes du login** :

- **Google Cloud Console** : redirect URI
  `<PUBLIC_BASE_URL>/api/v1/connectors/google/callback`, scopes `openid email`,
  `gmail.readonly`, `gmail.send`, `calendar`.
- **Azure AD (Entra ID)** : redirect URI
  `<PUBLIC_BASE_URL>/api/v1/connectors/microsoft/callback`, permissions déléguées
  `Mail.Read`, `Mail.Send`, `Calendars.ReadWrite`, `offline_access`.

Variables : `GOOGLE_CONNECTOR_CLIENT_ID/SECRET`, `MICROSOFT_CONNECTOR_CLIENT_ID/SECRET`,
`CONNECTOR_REFRESH_LEAD_MINUTES`, `CONNECTOR_WEBHOOK_BASE_URL`.

## Gateway IA (Phase 6)

**Interface interne unique** pour tout appel LLM — chat, streaming, tool-calling,
embeddings — bâtie sur LiteLLM en mode bibliothèque, isolé derrière des types Pydantic
maison : **aucun import de `litellm` hors de `app/ai/`**. Providers : Mistral (défaut,
ZDR), Anthropic, OpenAI — une clé plateforme par provider, optionnelle.

Gouvernance par tenant (`tenant_ai_policies`, gérée en SQL) : provider/modèle par
défaut, providers autorisés, **zéro-rétention infranchissable par configuration**
(liste ZDR en code, refus explicite hors liste), quota mensuel soft (l'appel passe,
l'alerte est auditée une fois par jour), fallback optionnel validé par la même règle.

Metering : chaque appel produit exactement un `ai_usage_events` (métriques seulement,
jamais de contenu), prix en code versionnés, agrégat quotidien `ai_usage_daily`
(beat), ventilation par module.

Variables : `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `MISTRAL_API_KEY`,
`AI_DEFAULT_PROVIDER`, `AI_DEFAULT_MODEL`, `AI_REQUEST_TIMEOUT_SECONDS`,
`AI_QUOTA_DEFAULT_MONTHLY_TOKENS`, `AI_USAGE_RAW_RETENTION_DAYS`.

## Runtime d'automatisation & modules (Phase 7)

Chaque fonctionnalité produit vit dans son package `app/modules/<name>/`, s'ajoute
**sans jamais modifier le cœur** (vérifié par `tests/test_module_isolation.py`) et ne
consomme que les briques socle. Checklist complète :

1. Créer `app/modules/<name>/` : `tenant_models.py` (tables `(Base, TenantScoped)`
   préfixées `<name>_`), `router.py` (chaque route porte
   `require_permission("<name>.…")`), `service.py` (tâches
   `async (tenant_id) -> None`, handlers), `manifest.py` (le `ModuleManifest`).
2. Ajouter **une ligne** dans `app/automation/registry.py`.
3. `make revision m="<name> tables"` pour les tables du module.
4. `make generate-client` ; si le module a une page, la coder dans `apps/web`.

Le montage expose les routes sous `/api/v1/modules/{name}/…` avec
`require_module_enabled` (403 si inactif pour le tenant). Le scheduler génère une
entrée beat par tâche ; à chaque tick, fan-out d'une tâche unitaire par tenant actif
(contexte posé, verrou anti-chevauchement, échec isolé). Activation par tenant
(`tenant_modules`) : refusée tant que les capabilities requises ne sont pas
satisfaites ; la désactivation conserve les données.

Module d'exemple : `sample_digest` (résumé IA quotidien des emails — traverse tout le
contrat, à copier comme squelette).

## Déploiement staging (modèle pull)

Chaque push sur `main` : la CI passe, puis `staging-images.yml` publie les images vers
GHCR (`:sha` + `:latest`). La machine de staging **tire elle-même** les nouveautés :
un timer systemd exécute `scripts/deploy-pull.sh` toutes les 5 minutes, qui redéploie
uniquement si une image a changé, applique `saas db upgrade`, puis lance le smoke test
HTTPS. Aucun accès entrant, aucun runner.

Mise en place initiale (une seule fois) :

1. Installer Docker, puis cloner ce repo dans `/srv/saas/app`.
2. `docker login ghcr.io` avec un PAT fine-grained **lecture seule** (`packages:read`).
3. Créer `/srv/saas/.env` à partir de `.env.example` avec au minimum :
   `APP_ENV=staging`, `SITE_ADDRESS=staging.<domaine>` (DNS pointé sur la machine,
   wildcard `*.staging.<domaine>` pour les sous-domaines tenant),
   `API_IMAGE=ghcr.io/<owner>/<repo>-api:latest`,
   `WEB_IMAGE=ghcr.io/<owner>/<repo>-web:latest`, `POSTGRES_PASSWORD` robuste.
4. Installer le timer :
   `sudo cp infra/systemd/saas-deploy.* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now saas-deploy.timer`.
   Suivi : `journalctl -u saas-deploy.service`.
5. Les ports 80/443 doivent être joignables depuis Internet (TLS automatique Caddy).

Observabilité MVP (ADR 0004) : `docker compose logs -f <service>` (logs JSON corrélés
par `request_id`) + `make smoke`.

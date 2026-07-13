# Socle SaaS B2B multi-tenant

Socle commun réutilisable (auth multi-tenant, connecteurs externes, couche IA
multi-fournisseurs, logs, RGPD) pour des modules d'automatisation métier.
Plans : [architecture globale](docs/architecture-plan.md) ·
[Phase 3 — Frontends + back-office](docs/phase-3-frontends-backoffice-plan.md) ·
[Phase 4 — Audit + socle RGPD](docs/phase-4-audit-rgpd-plan.md) ·
[Phase 5 — Connecteurs Google/Microsoft](docs/phase-5-connecteurs-plan.md) ·
[Phase 6 — Gateway IA](docs/phase-6-ai-gateway-plan.md) ·
[Phase 7 — Runtime d'automatisation](docs/phase-7-automation-runtime-plan.md).

## Stack

Python 3.12 · FastAPI · Celery (broker Valkey) · PostgreSQL 17 ·
React/Vite/TypeScript · TanStack Router/Query · Tailwind · react-hook-form + zod ·
Docker Compose · Caddy · Loki + Grafana + Alloy · Uptime Kuma.

## Démarrage local

Prérequis : [uv](https://docs.astral.sh/uv/), Node 22 (`corepack enable`), Docker.

```bash
cp .env.example .env
make install     # dépendances Python (uv) + Node (pnpm)
make dev         # infra Docker (Postgres, Valkey, Loki, Grafana, Alloy, Uptime Kuma)
# puis, dans des terminaux séparés :
make api         # API sur http://localhost:8000 (docs : /api/v1/docs)
make worker      # worker Celery (requis pour les migrations déclenchées depuis le back-office)
make web         # SPA client sur http://localhost:5173 (proxy /api -> :8000)
make admin       # SPA back-office sur http://localhost:5174 (jamais exposée publiquement)
```

Environnement complet conteneurisé (SPA client servie par Caddy sur http://localhost:8080,
back-office sur http://localhost:8081) : `docker compose up -d --build`.

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
apps/admin/          # SPA back-office React — jamais exposée publiquement
packages/api-client/ # Client TS généré depuis l'OpenAPI — ne pas éditer
packages/ui/         # Composants React partagés (SPA client + back-office)
infra/               # Caddy (public + back-office), Loki, Grafana, Alloy
scripts/             # export OpenAPI, smoke test, déploiement staging
.github/workflows/   # CI bloquante + déploiement continu staging
```

Les invariants du projet (multi-tenant, sécurité, logs) sont dans [CLAUDE.md](CLAUDE.md).

## Multi-tenant (Phase 1)

Une base PostgreSQL par tenant, un control-plane pour le catalogue et les identités.
Administration via le CLI `saas` (en conteneur : `docker compose run --rm api saas …`) :

```bash
uv run saas tenant create acme --name "ACME Corp"   # catalogue + CREATE DATABASE + migrations + seed
uv run saas tenant list                             # états + version de schéma par base
uv run saas tenant retry-provision acme             # rejoue un provisioning en échec
uv run saas db upgrade                              # migre control-plane + toutes les bases tenant
```

`saas db upgrade` rapporte base par base et sort en erreur au moindre échec (une base
en échec ne bloque pas les autres). Les migrations tournent automatiquement à chaque
déploiement staging (`scripts/deploy-pull.sh`). Nouvelles révisions :
`make revision-controlplane m="..."` / `make revision-tenant m="..."`.

**Prérequis pour `make test` en local** : un Postgres joignable (celui de `make infra`
suffit) — les tests DB créent des bases éphémères `test_*` et les droppent en teardown.

## Auth + annuaire (Phase 2)

Auth interne construite sur des briques mûres : argon2id (mots de passe), sessions
serveur en DB control-plane (cookie httpOnly/SameSite=Lax, révocables), TOTP pyotp
(secrets chiffrés AES-256-GCM), OAuth login Google/Microsoft (OIDC, Authlib).
**Inscription publique désactivée** : tout compte naît d'une invitation.

Flux type (au curl ou via `/api/v1/docs` — voir aussi la SPA, section suivante) :

```bash
uv run saas tenant create acme --owner-email alice@example.com  # → URL d'invitation affichée
uv run saas invitation create acme bob@example.com --role member
# POST /api/v1/auth/invitations/accept {token, password}   → compte créé + membership
# POST /api/v1/auth/login {email, password}                → cookie de session
#   (réponse totp_required + challenge si TOTP activé → POST /api/v1/auth/login/totp)
# GET  /api/v1/directory/members (Host: acme.<domaine>)    → annuaire du tenant
```

L'URL d'acceptation est **toujours retournée à l'appelant** (CLI ou API) ; l'envoi
d'email est optionnel et s'active en configurant `SMTP_*` (relais recommandé : §8.4
du plan global). RBAC : rôles `owner`/`admin`/`member`, permissions `core.*` vérifiées
par la dépendance unique `require_permission` ; toute route métier exige sous-domaine
tenant + session + membership.

Variables d'environnement clés (voir `.env.example`) : `AUTH_MASTER_KEY` (obligatoire
hors dev — `openssl rand -base64 32`), `SESSION_COOKIE_DOMAIN` (`.staging.<domaine>`
en staging : un login vaut pour tous les tenants de l'utilisateur), `PUBLIC_BASE_URL`,
`GOOGLE_CLIENT_ID/SECRET` et `MICROSOFT_CLIENT_ID/SECRET` (apps OAuth avec redirect URI
`<PUBLIC_BASE_URL>/api/v1/auth/oauth/{provider}/callback`, scopes `openid email profile`).

## Frontends + back-office (Phase 3)

**SPA client** (`apps/web`) : login (mot de passe + TOTP, ou OAuth Google/Microsoft),
acceptation d'invitation, annuaire (membres, invitations en attente, équipes), sécurité
du compte (activation/désactivation TOTP, QR code généré côté client, codes de
récupération affichés une seule fois). L'état d'auth vient exclusivement de
`GET /api/v1/auth/me` (pas de store parallèle) ; un 401 redirige vers `/login`, un 403
affiche une page « accès refusé » — dans les deux cas le serveur reste la seule autorité.

**Back-office** (`apps/admin`) : réservé aux `platform_admin`, **jamais exposé
publiquement** — en dev sur `http://localhost:5174`, en Compose/staging derrière un
vhost Caddy dédié (`infra/caddy/Caddyfile.admin`) lié à `127.0.0.1`/WireGuard
uniquement (le vhost public renvoie 403 sur `/api/v1/admin/*` en défense en
profondeur). Provisionne des tenants (URL d'invitation owner affichée) et supervise
les migrations (déclenchement Celery + rapport persisté, relu par polling). Seul moyen
de poser le rôle plateforme :

```bash
uv run saas admin grant alice@example.com   # is_platform_admin = true (jamais via l'API)
uv run saas admin revoke alice@example.com
```

## Audit + RGPD (Phase 4)

**Audit** : chaque action métier significative du socle (invitations, rôles, retraits,
équipes) écrit un événement dans `audit_events`, une table **DB tenant** (donnée du
client, jamais dans les logs techniques), dans la **même transaction** que l'action
auditée quand c'est architecturalement possible (équipes, tenant-only) — au pire un
événement orphelin plutôt qu'une action sans trace, pour les actions control-plane
(invitations, rôles). Consultable dans la SPA client (page « Journal d'audit »,
pagination par curseur, filtres) par `owner`/`admin` uniquement — `core.audit.read`,
403 pour `member`. Append-only : aucune route ni fonction de modification/suppression
en dehors de la politique de rétention.

**Export RGPD** (opérateur uniquement, pas de self-service tenant) :

```bash
uv run saas tenant export acme   # pg_dump -Fc + extrait control-plane + manifeste,
                                  # archive chiffrée (KeyProvider), TTL 7 j par défaut
```

Équivalent back-office : `POST /api/v1/admin/tenants/{slug}/export` (dispatch Celery,
ne bloque jamais la requête HTTP) puis `GET .../exports` pour le lien de téléchargement.

**Effacement RGPD**, en deux temps (délai de grâce, irréversible passé ce délai) :

```bash
uv run saas tenant delete acme          # re-saisie du slug exigée ; inaccessible immédiatement
uv run saas tenant cancel-delete acme   # annulation pendant le délai de grâce
```

Après `GDPR_ERASURE_GRACE_DAYS` (déf. 7 j), la tâche beat horaire
`core.gdpr.execute_pending_erasures` droppe la base, purge le catalogue (memberships,
invitations, users devenus orphelins) et écrit une trace minimale dans `erasure_log`
(control-plane, sans donnée métier). Équivalents back-office :
`POST /api/v1/admin/tenants/{slug}/request-erasure` / `.../cancel-erasure`.

**Rétention** : registre de politiques par type de donnée (`app.gdpr.retention`),
appliqué quotidiennement par tenant (`core.gdpr.apply_retention_policies`, purge par
lots) ; surchargeable par tenant via `tenant_settings` (clé `retention.<type>`, en
jours). Variables clés (voir `.env.example`) : `AUDIT_RETENTION_DAYS` (déf. 365),
`GDPR_EXPORT_DIR`/`GDPR_EXPORT_TTL_DAYS`, `GDPR_ERASURE_GRACE_DAYS`. Procédure
opérateur complète (dont la purge manuelle des backups pgBackRest, assumée jusqu'à la
Phase 8) : [`docs/runbook-gdpr.md`](docs/runbook-gdpr.md). Trames RGPD non techniques
(registre des traitements, sous-traitants, notification de violation) :
[`docs/rgpd/`](docs/rgpd/).

## Connecteurs externes (Phase 5)

Framework de connexion aux comptes Google Workspace et Microsoft 365, exposant des
**capabilities normalisées** (`Mail`, `Calendar`) que les modules consomment sans jamais
toucher les APIs propriétaires (§5 du plan global). Deux providers très différents —
Gmail/Calendar via `google-api-python-client`, Microsoft Graph via `httpx` — derrière le
même contrat (`app/connectors/capabilities.py`).

**Cycle de vie d'une connexion** (SPA client, page « Connecteurs », `core.connectors.*`) :
un `owner`/`admin` lance « Connecter Google/Microsoft » → flux OAuth tiers (distinct du
login) → la connexion apparaît `active`. Les tokens sont **chiffrés au repos**
(`KeyProvider` AES-256-GCM) en **DB tenant** — jamais en clair nulle part (base, logs,
réponse API) ; l'export/effacement RGPD (Phase 4) les couvre gratuitement. Le refresh
proactif (beat 5 min, verrou Valkey par connexion) renouvelle les access tokens avant
expiration ; une révocation côté provider bascule la connexion en `needs_reconsent` et
la SPA propose le re-consentement guidé. Tout le cycle est audité (`connector.*`).

**Webhooks entrants** : subscriptions Microsoft Graph (~3 j, renouvelées) et channels
Google Calendar (~7 j, recréés) ; chaque notification est authentifiée (echo
`validationToken` + `clientState` haché chez Microsoft, en-têtes de channel chez Google)
avant tout traitement, puis normalisée et livrée à un registre interne
(`on_connector_event`) — premier client réel en Phase 7. Le endpoint
`POST /api/v1/webhooks/{provider}/{route_key}` est **la première surface entrante depuis
Internet** (jusqu'ici seul le navigateur entrait) : il doit être joignable en HTTPS
public (vérifier Caddy). Gmail ne pousse que via Cloud Pub/Sub : sa capability mail reste
sans webhook dans cette phase (delta/historyId laissés aux consommateurs, Phase 7).

**Création des apps OAuth connecteurs** (distinctes des apps de login, décision D3 —
les scopes sensibles ne doivent pas mêler les deux cycles de vie) :

- **Google Cloud Console** : app OAuth « connecteurs », redirect URI
  `<PUBLIC_BASE_URL>/api/v1/connectors/google/callback`, scopes `openid email`,
  `gmail.readonly`, `gmail.send`, `calendar`. La vérification Google (scopes sensibles,
  lancée en Phase 3 T9) plafonne l'app à 100 utilisateurs de test tant qu'elle n'est pas
  validée — suffisant pour la démo, bloquant pour de vrais clients.
- **Azure AD (Entra ID)** : app « connecteurs », redirect URI
  `<PUBLIC_BASE_URL>/api/v1/connectors/microsoft/callback`, permissions déléguées
  `Mail.Read`, `Mail.Send`, `Calendars.ReadWrite`, `offline_access`.

Variables d'environnement (voir `.env.example`) : `GOOGLE_CONNECTOR_CLIENT_ID/SECRET`,
`MICROSOFT_CONNECTOR_CLIENT_ID/SECRET`, `CONNECTOR_REFRESH_LEAD_MINUTES` (déf. 10),
`CONNECTOR_WEBHOOK_BASE_URL` (déf. = `PUBLIC_BASE_URL` ; à surcharger si les webhooks
entrent par un autre nom d'hôte que l'apex public).

## Gateway IA (Phase 6)

**Interface interne unique** pour tout appel à un modèle de langage — chat, streaming,
tool-calling, embeddings (§6 du plan global). Bâtie sur **LiteLLM en mode bibliothèque**
(décision D1) : aucun service supplémentaire à opérer, le routing/les politiques/le
metering restent dans notre code typé. Tout le code (backend et modules Phase 7) appelle
`app.ai.gateway.AIGateway` — **aucun import de `litellm` ni d'un SDK provider hors de
`app/ai/`** (invariant de phase n°1) ; LiteLLM est un détail d'implémentation isolé
derrière des types Pydantic maison (décision D2), et sa version est **pinnée exactement**
(D8).

**Providers supportés** : Mistral (France, ZDR — provider par défaut), Anthropic et
OpenAI (hors UE, DPA + SCC — voir `docs/rgpd/sous-traitants.md`). Une clé plateforme par
provider, **optionnelle** : un provider sans clé est indisponible.

**Gouvernance par tenant** (`tenant_ai_policies`, gérée au **back-office** — page
« Consommation IA ») : provider/modèle par défaut, providers autorisés, **zéro-rétention**,
quota mensuel, fallback optionnel. La **politique zéro-rétention est infranchissable par
configuration** (invariant n°5, décision D5) : sous `zero_retention`, seuls les providers
d'une **liste ZDR en code** (Mistral d'abord) sont acceptés — un appel demandant
explicitement un provider hors liste est **refusé** (jamais dégradé silencieusement). Le
**fallback** de provider (D6) est optionnel, désactivé par défaut, et sa cible est validée
par la même règle ZDR. Le champ **BYOK** (clé du client, chiffrée `KeyProvider`) est
**préparé mais jamais exposé** (D7) : la plomberie est prouvée par un test.

**Metering dès le premier appel** (invariant n°4) : chaque appel — succès, erreur ou
timeout — produit **exactement un** `ai_usage_events` (control-plane), porteur **de seules
métriques** (tokens, latence, coût estimé, statut) — **jamais de prompt ni de complétion**
(invariant n°3). Les prix vivent **en code, versionnés** (`app/ai/pricing.py`,
`price_version` estampillé sur chaque événement, décision D4). Un beat quotidien agrège la
veille dans `ai_usage_daily` (rejouable) et purge les événements bruts au-delà de
`AI_USAGE_RAW_RETENTION_DAYS` (déf. 90) — les agrégats, eux, sont conservés (fondation
facturation).

**Quotas soft** (`app/ai/quota.py`) : compteur mensuel sur Valkey, recalé chaque jour par
l'agrégat SQL. Au-delà du quota **l'appel passe** (soft limit) mais une alerte est
**auditée + loggée une fois par jour** (`core.ai.quota_exceeded`) et affichée au
back-office (`over_quota`). Le hard limit est prévu mais non exposé.

Surfaces : `POST /api/v1/ai/chat` (`core.ai.use`, owner/admin — écho de démo et smoke
test, **pas d'UI de chat** : les usages viennent des modules Phase 7) ; back-office
`GET /api/v1/admin/ai/usage` et `GET/PUT /api/v1/admin/tenants/{slug}/ai-policy` (chaque
changement audité `core.ai.policy_changed`).

Variables d'environnement (voir `.env.example`) : `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`MISTRAL_API_KEY` (clés plateforme, chacune optionnelle), `AI_DEFAULT_PROVIDER`
(déf. `mistral`), `AI_DEFAULT_MODEL` (déf. `mistral-small-latest`),
`AI_REQUEST_TIMEOUT_SECONDS` (déf. 120), `AI_QUOTA_DEFAULT_MONTHLY_TOKENS` (déf. généreux,
soft), `AI_USAGE_RAW_RETENTION_DAYS` (déf. 90). **Aucun test ne consomme de clé réelle**
(LiteLLM est doublé, les clés de test sont factices).

## Runtime d'automatisation & modules (Phase 7)

La **coquille** qui accueille les modules métier : chaque fonctionnalité produit vit
dans son propre package `app/modules/<name>/`, s'ajoute **sans jamais modifier le
cœur**, et ne consomme que les briques socle (capabilities Phase 5, `AIGateway`
Phase 6, `record_audit_event` Phase 4, `get_tenant_session` Phase 1). Cette promesse
est **vérifiée mécaniquement** par `tests/test_module_isolation.py` (le cœur n'importe
aucun module hors `app/automation/registry.py` ; un module n'en importe jamais un
autre).

**Écrire un module** — la checklist complète (aucun autre fichier du cœur à toucher) :

1. Créer `app/modules/<name>/` avec :
   - `tenant_models.py` : les tables du module en **DB tenant**, préfixées `<name>_`
     (elles rejoignent l'arbre Alembic tenant unique — décision D5).
   - `router.py` : un `APIRouter` dont **chaque route** porte
     `require_permission("<name>.…")` (vérifié au démarrage — invariant n°2).
   - `service.py` : la logique (tâches, handlers d'événements). Signature imposée
     d'une tâche périodique : `async (tenant_id) -> None`.
   - `manifest.py` : le `ModuleManifest` (`app/automation/contract.py`) déclarant
     `permissions` (namespacées `<name>.`, rattachées aux rôles intégrés),
     `periodic_tasks`, `connector_events`, `required_capabilities`, `audit_actions`.
2. Ajouter **une ligne** dans `app/automation/registry.py` (`MODULES`).
3. Ajouter **une migration tenant** (`make revision-tenant m="<name> tables"`) pour les
   tables du module.
4. `make generate-client` (le contrat OpenAPI couvre les routes du module) ; si le
   module a une page, la coder dans `apps/web` (front hard-codé, assumé — risque n°3).

Le montage (`app/automation/mounting.py`, appelé une fois par `main.py`/`worker.py`)
expose les routes sous `/api/v1/modules/{name}/…` avec une dépendance
`require_module_enabled(name)` (403 si le module n'est pas activé pour le tenant),
rattache les permissions aux rôles, enregistre les actions d'audit et les handlers
d'événements connecteurs. Le **scheduler** (`app/automation/scheduler.py`, décision D4)
génère une entrée Celery beat statique par tâche périodique qui, à chaque tick,
publie une tâche unitaire par tenant où le module est actif — contexte tenant posé,
verrou Valkey anti-chevauchement, isolation des échecs (un tenant en échec ne bloque
pas les autres).

**Activation par tenant** (`tenant_modules`, control-plane — gouvernance, décision D3) :
au back-office (page « Modules »), un `platform_admin` active un module pour un tenant.
L'activation **échoue tant que les capabilities requises ne sont pas satisfaites** par
une connexion active (message explicite listant ce qui manque) ; la désactivation coupe
routes et tâches mais **conserve les données** du module en DB tenant (décision D6).

**Module d'exemple `sample_digest`** : trivial mais traversant tout le contrat. Sa tâche
quotidienne liste les emails des dernières 24 h (`MailCapability`), les résume via
`AIGateway.chat` (metering ventilé `module=sample_digest`), stocke le digest en DB
tenant (`sample_digest_digests`) et audite `sample_digest.digest_generated`. Côté SPA
client : page « Digest » (liste + « générer maintenant »), qui s'efface sur un 403
(l'API pilote l'affichage).

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
   machine, wildcard `*.staging.<domaine>` recommandé pour la suite), `ADMIN_SITE_ADDRESS`
   (hôte interne du back-office, jamais l'apex public),
   `API_IMAGE=ghcr.io/<owner>/<repo>-api:latest`, `WEB_IMAGE=ghcr.io/<owner>/<repo>-web:latest`,
   `ADMIN_IMAGE=ghcr.io/<owner>/<repo>-admin:latest`,
   `POSTGRES_PASSWORD` et `GRAFANA_ADMIN_PASSWORD` robustes.
4. Installer le timer :
   `sudo cp infra/systemd/saas-deploy.* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now saas-deploy.timer`.
   Suivi : `journalctl -u saas-deploy.service`.
5. Les ports 80/443 doivent être joignables depuis Internet (TLS automatique Caddy).
   Le back-office (:8081), Grafana (:3000) et Uptime Kuma (:3001) restent liés à
   127.0.0.1 : accès via WireGuard/tunnel SSH uniquement.

## Critère de démo — Phase 0

Un `git push` sur `main` déclenche CI verte + publication GHCR, puis la machine
staging se met à jour d'elle-même (≤ 5 minutes) ;
`https://staging.<domaine>` sert la SPA qui affiche le statut de
`GET /api/v1/health` via le client TS généré ; la requête est visible dans
Grafana/Loki corrélée par `request_id` ; le worker Celery tourne ;
Uptime Kuma surveille le health.

## Critère de démo — Phase 3

Sur staging : Alice ouvre `acme.<domaine>`, se connecte (mot de passe puis TOTP), invite
carol@example.com en `member` et lui transmet l'URL affichée ; Carol accepte, se connecte
et voit les équipes mais pas le bouton d'invitation (un appel direct à l'API lui rend
403). Alice active le TOTP depuis la page sécurité. Pendant ce temps, l'opérateur
connecté au WireGuard ouvre le back-office, se connecte avec son compte
`platform_admin` (posé par `saas admin grant`), crée le tenant `globex` (l'URL
d'invitation owner s'affiche), déclenche le runner de migrations et lit le rapport base
par base. Depuis Internet, le back-office et `/api/v1/admin/*` sont injoignables.
Détail complet : section E de
[`docs/phase-3-frontends-backoffice-plan.md`](docs/phase-3-frontends-backoffice-plan.md).

## Critère de démo — Phase 4

Sur staging : Alice (owner d'`acme`) invite un membre, change son rôle, crée une
équipe — la page « Journal d'audit » montre les événements horodatés avec acteur ;
Bob (`member`) reçoit 403 sur cette page. L'opérateur lance `saas tenant export acme` :
l'archive chiffrée apparaît, se déchiffre et se restaure dans une base jetable. Il
exécute ensuite `saas tenant delete globex` (re-saisie du slug) : `globex.<domaine>`
répond 403 immédiatement ; `cancel-delete` le ranime ; re-demande puis passage du délai
de grâce (raccourci en staging) : la DB n'existe plus, le catalogue est purgé,
`erasure_log` en garde la trace minimale, et les users uniquement membres de `globex`
ont disparu du control-plane. La purge de rétention quotidienne tourne et se voit dans
Loki (rapport par tenant, sans PII). Détail complet : section E de
[`docs/phase-4-audit-rgpd-plan.md`](docs/phase-4-audit-rgpd-plan.md).

## Critère de démo — Phase 6

Sur staging, avec de vraies clés plateforme : `POST /api/v1/ai/chat` sur `acme` répond
via le provider par défaut de la politique (Mistral) ; `ai_usage_events` contient
l'événement (tokens réels, coût estimé, `price_version`). L'opérateur passe `acme` en
zéro-rétention au back-office (page « Consommation IA ») : un appel demandant explicitement
un provider hors liste ZDR est refusé avec une erreur claire, l'appel par défaut part chez
Mistral. Un quota volontairement bas est posé : l'appel suivant passe mais l'alerte
apparaît (audit `core.ai.quota_exceeded` + page back-office `over_quota`). La page
consommation montre les agrégats par tenant après le passage du beat quotidien. Un provider
est coupé (clé retirée) : avec fallback activé, l'appel bascule et le metering attribue au
provider réel. Dans Loki : latences et statuts corrélés par `request_id`, **aucun fragment
de prompt ni de réponse**. Détail complet : section E de
[`docs/phase-6-ai-gateway-plan.md`](docs/phase-6-ai-gateway-plan.md).

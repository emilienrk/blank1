# Backend — les briques du socle, près du code

> Chaque package de `apps/api/app/` : son rôle, son mécanisme, ses points d'entrée.
> Vue d'ensemble et flux : [`architecture.md`](architecture.md). Les invariants
> non négociables restent dans les `CLAUDE.md` — ce document explique *comment ça
> marche*, pas *ce qui est interdit*.

## Patterns transverses

**Anatomie d'un package.** Chaque brique expose au besoin `router.py` (routes
FastAPI), `service.py` (logique), `models.py` (tables non scopées),
`tenant_models.py` (tables scopées), `tasks.py` (tâches Celery). Les schémas
d'entrée/sortie sont des Pydantic `BaseModel` du package concerné.

**Singletons paresseux `get_*()`.** Les objets coûteux ou à état sont des
singletons de process, créés au premier appel et jamais à l'import :

| Accesseur | Objet | Où |
|---|---|---|
| `get_settings()` | `Settings` (pydantic-settings, `@lru_cache`) | `core/config.py` |
| `get_control_engine()` / `get_control_sessionmaker()` | engine/sessionmaker async uniques | `core/db.py` |
| `get_key_provider()` | chiffrement AES-256-GCM | `core/crypto.py` |
| `get_gateway()` | `AIGateway` | `ai/gateway.py` |
| `get_valkey_client()` | client Valkey (throttle, verrous, quotas) | `connectors/throttle.py` |

Pourquoi ce patron : l'initialisation vient *après* la lecture de la config (pas
d'effet de bord à l'import), une instance par process suffit (pools de connexions),
et chaque accesseur a son `reset_*`/`dispose_*` pour les tests. Les tâches Celery
appellent `dispose_control_engine()` en fin d'exécution : les pools asyncpg sont
liés à l'event loop de la tâche, les garder tuerait la suivante.

**Frontières remplaçables en test.** Ce qui touche le réseau est isolé derrière
une variable de module qu'un test remplace : `set_completion_fn` du gateway IA,
`_sleep` du throttle, `enqueue_fanout`/`enqueue_unit` du scheduler. La CI ne fait
jamais de vrai appel réseau ni de vrai sleep.

## core — fondations techniques

- **`config.py`** : `Settings` lit exclusivement l'environnement (`.env` en dev).
  L'URL PostgreSQL n'est jamais stockée telle quelle : composée depuis
  `POSTGRES_*`. `master_key_bytes()` refuse de démarrer sans `AUTH_MASTER_KEY`
  hors dev.
- **`db.py`** : `Base` déclarative unique (toutes les tables), engine et
  sessionmaker uniques. Les noms `get_control_*` désignent l'accès aux tables
  *non scopées* (catalogue, users, memberships) — le nom vient de l'époque
  base-par-tenant, conservé pour limiter le churn (ADR 0001).
- **`crypto.py`** : interface `KeyProvider` (Protocol `encrypt`/`decrypt`) ;
  l'implémentation actuelle chiffre AES-256-GCM avec la clé maître d'environnement,
  nonce aléatoire préfixé. Clés par tenant, OpenBao ou KMS se brancheront derrière
  la même interface sans toucher les appelants (TOTP, tokens de connecteurs, BYOK).
- **`logging.py`** : structlog JSON sur stdout ; `RequestIdMiddleware` génère le
  `request_id` et le lie aux contextvars structlog — tout log d'une requête est
  corrélé sans le passer explicitement.
- **`csrf.py`** : vérification d'Origin sur les méthodes mutantes (les sessions
  sont en cookie).
- **`mailer.py`** : envoi SMTP optionnel — `SMTP_HOST` vide = aucun envoi, les
  URLs d'invitation sont de toute façon retournées à l'appelant.
- **`migrations.py`** : exécution programmatique d'Alembic (CLI `saas db upgrade`).

## tenancy — l'isolation par construction

Quatre fichiers qui forment le mécanisme central du projet :

- **`context.py`** — le contexte courant est une **contextvar** portant un
  `TenantContext` figé (`tenant_id`, `slug`, `role`). `current_tenant()` lève
  `TenantContextError` si rien n'est posé. Deux façons de poser le contexte :
  la dépendance HTTP `resolve_tenant`, ou le context manager `tenant_context(ctx)`
  (CLI, tâches, tests). Les contextvars suivent naturellement chaque requête
  asyncio sans fuite entre requêtes concurrentes.
- **`tenant_base.py`** — le mixin `TenantScoped` (colonne `tenant_id`, FK indexée,
  CASCADE) et les **garde-fous installés une fois sur la classe `Session`**, donc
  actifs sur toute session du process :
  - hook `do_orm_execute` : tout SELECT/UPDATE/DELETE touchant un mapper scopé
    exige un contexte et reçoit `with_loader_criteria(tenant_id == contexte)` —
    propagé aux alias et aux lazy loads, `Session.get` inclus ;
  - une requête qui référence une *table* scopée sans son *entité* ORM (ex.
    `select(func.count()).select_from(Model)`) est **refusée**, car le critère ne
    peut pas s'y appliquer — écrire `func.count(Model.id)` ;
  - hook `before_flush` : estampille `tenant_id` sur les nouveaux objets, refuse
    tout `tenant_id` incohérent avec le contexte (insert, update ou delete).
  Le SQL textuel (`text(...)`) contourne l'ORM : réservé aux tests et migrations.
- **`session.py`** — `get_tenant_session()` (dépendance HTTP) et
  `tenant_session()` (context manager hors HTTP) : le SEUL chemin vers les tables
  métier. Ils rendent une session ordinaire du sessionmaker unique — tout le
  scoping vient des garde-fous ; leur seul travail est d'exiger le contexte.
- **`deps.py`** — `resolve_tenant` : slug ← premier label du header Host →
  tenant du catalogue (404 si inconnu **ou soft-deleted** — indistinguable, 403 si
  suspendu) → session utilisateur (401) → membership (403) → contexte posé pour la
  durée de la requête, slug lié aux logs.

`provisioning.py` crée un tenant (INSERT catalogue + audit `core.tenant.provisioned`
dans la même transaction) ; le garde-fou permanent de toute la mécanique est
`tests/test_tenant_isolation_db.py`.

## auth — sessions, TOTP, OAuth, RBAC

- **Sessions serveur** : cookie httpOnly/SameSite=Lax portant un token dont seul
  le **hash sha256** est en base (`tokens.py`) — un vol de base ne donne aucune
  session. TTL 7 j, révocables.
- **Mots de passe** argon2id ; **TOTP** (pyotp) dont le secret est chiffré via
  `get_key_provider()` ; **OAuth login** Google/Microsoft (Authlib, OIDC) qui ne
  crée jamais de compte — liaison par email vérifié puis (provider, subject).
  Tout compte naît d'une **invitation** (token haché, TTL).
- **`permissions.py`** : RBAC entièrement en code. Trois rôles (`owner`/`admin`/
  `member`) avec leurs ensembles de permissions `core.*` figés ; les owners n'ont
  pas de permissions supplémentaires — les règles owner (promotion, dernier owner
  intouchable) sont des règles métier dans `directory/service.py`.
  `require_permission(perm)` est une **fabrique de dépendance** : elle compose
  `resolve_tenant` puis vérifie le rôle, et attache `required_permission` à la
  dépendance — c'est cette introspection qui permet au registre des modules de
  vérifier au démarrage que chaque route est protégée. Les modules ajoutent leurs
  permissions par `register_module_permission` (namespace `<module>.*` imposé,
  rattachement additif aux rôles).
- **`rate_limit.py`** : compteur fenêtre fixe Valkey sur les endpoints d'auth ;
  **`tasks.py`** : purge horaire des sessions/challenges/invitations expirés.

## directory — identités et annuaire

`users` est global (une identité = un email, potentiellement membre de plusieurs
tenants) ; `memberships` porte le rôle par tenant. Le package gère invitations,
équipes et les règles owner. C'est lui que `resolve_tenant` interroge pour croiser
utilisateur et tenant.

## audit — la trace de chaque action

`record_audit_event(session, action=…, …)` écrit dans `audit_events` (table scopée,
estampillée automatiquement) **sur la session de l'appelant** : même transaction,
donc jamais d'action sans trace ni de trace sans action (un rollback emporte les
deux). `record_audit_event_for_tenant(tenant, …)` couvre les cas sans contexte posé
(route publique d'acceptation, tâches beat) : il pose le contexte, ouvre sa propre
session et commit seul — au pire un événement orphelin, jamais une action muette.
Actions namespacées `core.*` / `connector.*` / `<module>.*`.

## connectors — comptes tiers et capabilities

Le framework qui relie un tenant à ses comptes Google Workspace / Microsoft 365 :

- **Modèle** : `ConnectorConnection` (scopée tenant) porte statut, scopes accordés
  et tokens **chiffrés** (`KeyProvider`) ; `webhook_routes` (non scopée) ne porte
  que le routage entrant.
- **`capabilities.py`** — le contrat consommé par le reste du code : des
  `Protocol` typés (`MailCapability`, `CalendarCapability`) et des modèles
  normalisés (champs communs + `provider_raw_id`). `get_capability(session,
  connection, MailCapability)` rend l'implémentation du provider de la connexion,
  ou une erreur si la capability n'est pas consentie. Les implémentations
  propriétaires vivent sous `providers/` et nulle part ailleurs.
- **`throttle.py`** — l'enveloppe commune de tout appel provider : compteur
  fenêtre fixe Valkey par (provider, connexion), respect de `Retry-After`, backoff
  exponentiel avec jitter sur 429/5xx, `ProviderUnavailable` au plafond. Le même
  client Valkey fournit les **verrous** (`SET NX` + TTL) qui sérialisent les
  refreshs et protègent les tâches périodiques du chevauchement.
- **Cycle de vie** : OAuth dédié (apps distinctes du login), refresh proactif
  (beat 5 min, verrou par connexion), `invalid_grant` → statut `needs_reconsent`
  + audit (jamais une exception qui casse le beat), webhooks entrants authentifiés
  avant tout traitement puis normalisés en `ConnectorEvent` dispatché aux handlers
  abonnés (`on_connector_event` — c'est par là que les modules réagissent).

## ai — le gateway unique

`AIGateway` (`get_gateway()`) est l'unique porte vers les LLM : `chat`,
`chat_stream`, `embed`. Enchaînement d'un appel : contexte tenant obligatoire →
politique du tenant (`policy.py`) → résolution provider/modèle validée contre
`allowed_providers` et la **liste ZDR en code** (un tenant `zero_retention` ne peut
JAMAIS atteindre un provider hors liste, le fallback repasse par la même règle) →
clés (plateforme via env, ou BYOK déchiffré) → appel LiteLLM sous timeout →
metering.

- **LiteLLM est un détail d'implémentation** : les types aux frontières sont des
  Pydantic maison (`ChatRequest`, `ChatResult`…), l'appel réel passe par
  `set_completion_fn`/`set_embedding_fn` (doublés en test).
- **`metering.py`** : chaque appel produit exactement un `ai_usage_events` —
  succès, échec ou timeout — avec uniquement des métriques (tokens, latence, coût
  estimé via `pricing.py` versionné, statut), jamais de contenu. `module=` ventile
  l'usage par module appelant.
- **`quota.py`** : quota mensuel *soft* compté dans Valkey — au-delà, l'appel
  passe et l'alerte est auditée une fois par jour. `tasks.py` : rollup quotidien
  idempotent (`ai_usage_daily`), recalage des compteurs, purge des bruts.

## automation — le runtime des modules

- **`contract.py`** : `ModuleManifest`, le contrat figé et versionné — nom, router,
  permissions (avec rattachement aux rôles), tâches périodiques
  (`async (tenant_id) -> None`), abonnements aux événements connecteurs,
  `required_capabilities`, actions d'audit.
- **`registry.py`** : `MODULES`, liste explicite en code — l'UNIQUE point de
  couture cœur ↔ modules ; ajouter un module = une ligne. À l'import (donc au boot
  de l'API et du worker), validation fail-fast : noms uniques, namespaces
  `<name>.*` respectés, chaque route porte un `require_permission` (retrouvé par
  introspection de l'arbre de dépendances FastAPI). Un oubli est une erreur de
  boot en CI, pas une faille en prod.
- **`mounting.py`** : `register_runtime()` (idempotent, API et worker) rattache
  permissions et handlers ; `mount_modules(app)` monte chaque router sous
  `/api/v1/modules/{name}/` avec `require_module_enabled(name)` sur tout le router.
- **`scheduler.py`** : le fan-out décrit dans [`architecture.md`](architecture.md)
  — `beat_entries()` génère une entrée beat statique par tâche déclarée ;
  `run_periodic_unit` verrouille, pose le contexte, exécute, capture l'échec.
- **`service.py`** : activation par tenant (`tenant_modules`) — `enable_module`
  refuse tant que les `required_capabilities` ne sont pas satisfaites par une
  connexion active ; `disable_module` conserve les données.

## Processus et couture Celery

`worker.py` construit l'app Celery (broker Valkey), déclare le **beat statique du
socle** (purge auth, refresh connecteurs, renouvellement subscriptions, rollup IA)
et y ajoute `beat_entries()` des modules. Les tâches se rattachent par import.

Piège documenté : `create_app()` (`main.py`) **importe `app.worker`** — sans cet
import, les `@shared_task` résolvent l'app Celery par défaut (non configurée) dans
le process API et les `.delay()` partent dans le vide.

`cli.py` : la CLI `saas` (Typer) — tenants (create/list/delete avec confirmation
par re-saisie du slug), invitations, migrations. Elle pose le contexte via
`tenant_context()` quand elle touche aux données métier.

# Backend `apps/api` — conventions du module

Voir le `CLAUDE.md` racine pour les invariants globaux. Spécificités backend :

## Structure

Un package Python par module métier sous `app/` :

```
app/core/      # config, logging, db control-plane, crypto (KeyProvider), csrf, mailer
app/health/    # sonde de vie
app/tenancy/   # catalogue, contexte tenant, engine manager, migrations runner, provisioning
app/auth/      # sessions, mots de passe, TOTP, OAuth login, RBAC, rate limiting, purges
app/directory/ # identités globales, memberships, invitations, annuaire, équipes (DB tenant)
app/admin/     # API back-office (hors contexte tenant) : tenants, migrations, lookup users
app/audit/     # journal d'audit applicatif (DB tenant, append-only)
app/gdpr/      # export, effacement (délai de grâce), rétention/purge
app/connectors/ # connexions OAuth tierces (Google/Microsoft), capabilities Mail/Calendar,
#                refresh proactif, webhooks entrants, throttle (Phase 5)
app/ai/        # gateway IA unique (chat/stream/embeddings via LiteLLM), politiques par
#                tenant, metering, quotas, pricing versionné (Phase 6)
app/automation/ # runtime de modules (Phase 7) : contrat ModuleManifest, registre,
#                montage, activation par tenant (control-plane), scheduler fan-out
app/modules/   # modules métier (Phase 7) : un package par module ; voir app/modules/CLAUDE.md
app/cli.py     # CLI `saas` (tenant/invitation/db/export/delete + admin grant/revoke)
```

Chaque module expose au besoin : `router.py` (routes FastAPI), `service.py` (logique),
`models.py` (SQLAlchemy), `tasks.py` (tâches Celery).

## Multi-tenant — règles non négociables

- **Accès aux DB tenant UNIQUEMENT via `app.tenancy.session.get_tenant_session()`** :
  elle exige un contexte tenant posé (dépendance `resolve_tenant` en HTTP,
  `tenant_context()` en CLI/tâches). Jamais de `create_async_engine` tenant à la main.
- **Deux MetaData séparées** : tables control-plane sur `app.core.db.ControlPlaneBase`
  (catalogue, users, memberships — jamais de données métier) ; tables métier sur
  `app.tenancy.tenant_base.TenantBase` (une DB par tenant).
- **Deux arbres Alembic** : `migrations/controlplane/` et `migrations/tenant/`
  (`make revision-controlplane m="..."` / `make revision-tenant m="..."`).
  Toute évolution de schéma passe par Alembic — jamais de `create_all` hors tests.
- **Aucun secret ni URL en base** : le catalogue stocke `db_name` + alias `db_host` ;
  les URL sont composées dans `Settings` uniquement.
- **Identifiants SQL** : seuls CREATE/DROP DATABASE (provisioning) interpolent un
  identifiant, toujours dérivé d'un slug validé (`validate_slug`) puis quoté.

## Auth — règles non négociables (Phase 2)

- **Aucune route métier sans `require_permission("core.x.y")`** (`app.auth.permissions`),
  qui compose `resolve_tenant` (sous-domaine x session x membership). Les seules routes
  anonymes sont une liste fermée : health, login (+TOTP), OAuth start/callback,
  acceptation d'invitation.
- **Aucun secret en clair en base** : mots de passe argon2id ; tokens de session,
  d'invitation et de challenge **hachés** (sha256, `app.auth.tokens`) ; secrets TOTP
  **chiffrés** via `app.core.crypto.get_key_provider()`. Un token en clair n'apparaît
  qu'une fois, dans la réponse à son créateur.
- **Inscription publique désactivée** : tout compte naît d'une invitation ; l'OAuth
  login ne crée jamais de compte (liaison par email vérifié, puis (provider, subject)).
- Jamais d'email ni de credential dans les logs — référencer `user_id`/`tenant`.
- Rôles en code (`owner`/`admin`/`member`, décision D6) ; règles owner (promotion,
  dernier owner intouchable) dans `app.directory.service`, pas dans le RBAC.

## Back-office — règles non négociables (Phase 3)

- **Toute route `/api/v1/admin/*` exige `require_platform_admin`** (`app.auth.deps`),
  hors contexte tenant (pas de `resolve_tenant`/sous-domaine) — vérifié par
  `test_admin_routes.py` (401/403/200 systématiques). Défense en profondeur réseau :
  le vhost public (`infra/caddy/Caddyfile`) renvoie 403 sur ces chemins avant même
  d'atteindre l'API ; seul le vhost interne (`Caddyfile.admin`, WireGuard) les sert.
- **`is_platform_admin` ne se pose JAMAIS via l'API** : uniquement `saas admin
  grant/revoke` (décision D5 Phase 3, même logique que le provisioning CLI).
- **Migrations déclenchées depuis le back-office = tâche Celery + rapport persisté**
  (`app.admin.models.MigrationReportRecord`, décision D6) : la route HTTP ne fait que
  créer le rapport (`running`) et dispatcher, jamais bloquer sur la durée du runner.
  Le dispatch (`app.admin.tasks.enqueue_migration_run`) est le point à monkeypatcher
  en test pour simuler le worker sans broker.
- **`app/main.py` importe `app.worker`** (dans `create_app()`) : nécessaire pour que
  `@shared_task` résolve la bonne app Celery (broker configuré) dans le process API
  qui dispatche via `.delay()` — sans cet import, les tâches partent sur l'app Celery
  par défaut (non configurée). Piège à ne pas re-payer sur une future tâche dispatchée
  depuis l'API.

## Audit + RGPD — règles non négociables (Phase 4)

- **Toute action métier significative écrit son audit dans la même transaction que
  l'action** (`app.audit.service.record_audit_event`, décision D1) : la table
  `audit_events` vit en **DB tenant** (donnée du client), jamais en control-plane, et
  n'est jamais dupliquée en clair dans les logs techniques (invariant racine n°4).
  Pour les actions purement tenant (équipes), l'atomicité est stricte : même session,
  rollback commun. Pour les actions control-plane (invitations, rôles), l'audit
  commit avant l'action (au pire un événement orphelin, jamais une action sans trace)
  — `record_audit_event_for_tenant` (`app.audit.service`) couvre les écritures hors
  contexte HTTP tenant (route publique d'acceptation, provisioning, tâches beat).
- **`audit_events` est append-only** : aucune route ni fonction de modification/suppression
  en dehors de la politique de rétention (`app.gdpr.retention`) — pas même pour un
  `platform_admin`. Registre typé des actions connues (`app.audit.service.ACTIONS`,
  namespaces `core.*` maintenant, `connector.*`/`module_x.*` réservés aux phases suivantes).
- **L'effacement RGPD passe toujours par le délai de grâce** (`app.gdpr.erasure`) :
  `request_erasure` bascule l'état catalogue en `pending_deletion` (refusé par
  `resolve_tenant` comme `suspended`) ; seule la tâche beat `execute_pending_erasures`
  exécute le `DROP DATABASE` après `gdpr_erasure_grace_days`, en réutilisant
  `drop_database_if_exists` du provisioning (invariant I6 Phase 1 : identifiant
  toujours dérivé d'un slug validé). Aucun autre chemin de drop.
- **Les exports RGPD** (`app.gdpr.export`) sont chiffrés au repos (`KeyProvider`
  existant) et à durée de vie bornée (`gdpr_export_ttl_days`, purge beat) ; jamais
  servis par la surface publique — back-office (`require_platform_admin`) ou CLI
  uniquement (décision D5).
- **Les tâches beat qui itèrent les tenants posent le contexte explicitement**
  (`app.tenancy.context.tenant_context` + `TenantEngineManager.session`) — voir
  `app.gdpr.tasks` pour le pattern (rétention, effacements arrivés à échéance).

## Connecteurs — règles non négociables (Phase 5)

- **Aucun token de connecteur en clair, nulle part** : chiffré `KeyProvider`
  (`app.core.crypto`) en **DB tenant** (`app.connectors.tenant_models`), via
  `app.connectors.service.encrypt_token`/`decrypt_token` uniquement. Le control-plane ne
  porte que le routage (`webhook_routes`, aucune donnée métier ni token, décision D6).
  Jamais dans les logs ni dans une réponse API (`ConnectionOut` = statuts et labels).
- **Le reste du code ne consomme QUE les capabilities** (`app.connectors.capabilities` :
  `get_capability(session, connection, MailCapability)`), jamais les APIs Google/Graph
  directement. Toute implémentation propriétaire vit sous `app/connectors/providers/` et
  nulle part ailleurs. Les modèles normalisés (`EmailMessage`, `CalendarEvent`) ne portent
  que les champs communs + `provider_raw_id`.
- **Tout appel provider passe par l'enveloppe throttle/backoff**
  (`app.connectors.throttle.run_with_backoff` + `client_base`) : compteur Valkey par
  (provider, connexion), respect de `Retry-After`, `ProviderUnavailable` au plafond. Les
  SDK synchrones (`google-api-python-client`) s'exécutent via `anyio.to_thread`
  (`client_base.run_sync_call`), jamais dans l'event loop. Aucun appel lourd (listing
  volumineux) dans une requête HTTP — Celery.
- **Refresh des tokens sérialisé par verrou Valkey par connexion** (décision D5,
  `throttle.acquire_lock`/`wait_for_lock`) : le refresh périodique (beat 5 min) et le
  refresh à la volée (`client_base.fresh_access_token`) ne se marchent jamais dessus.
  `invalid_grant` → `needs_reconsent` + audit, jamais une exception qui casse le beat.
- **Toute réception webhook est authentifiée avant toute action** (echo `validationToken`
  + `clientState` haché chez Microsoft, en-têtes de channel chez Google) ; un webhook
  invalide répond 2xx neutre sans traitement ni log verbeux (pas d'oracle). Le
  `client_state` est stocké haché (`hash_token`), comme tout token du socle.
- **Cycle de vie audité** (`connector.connected`, `connector.reconsent_required`,
  `connector.revoked`, `connector.subscription_renewal_failed`, `connector.event_received`)
  via `record_audit_event` (règle Phase 4 appliquée aux connecteurs).
- **Écart au plan (D2) assumé** : le plan prévoyait `msal` pour l'acquisition des tokens
  Microsoft. À l'implémentation, l'échange/refresh OAuth des DEUX providers passe
  finalement par `httpx` contre les endpoints du manifest (`app.connectors.service`) —
  même mécanique que l'OIDC manuel de la Phase 2, testable avec un faux provider local.
  Le cache de tokens en mémoire de `msal` entrerait en conflit avec notre store chiffré en
  DB et le verrou de refresh par connexion. `msal` n'est donc PAS une dépendance du projet.
  Les appels Graph métier restent en `httpx` REST direct (pas de `msgraph-sdk`), conformes
  au plan.

## Gateway IA — règles non négociables (Phase 6)

- **Tout appel à un provider IA passe par `AIGateway`** (`app.ai.gateway.get_gateway()`) :
  `chat`/`chat_stream`/`embed` avec des types Pydantic maison. **Aucun import de `litellm`
  ni d'un SDK provider hors de `app/ai/`** (décision D1/D2) — LiteLLM est isolé derrière
  des frontières remplaçables (`set_completion_fn`/`set_embedding_fn`, doublées en test) et
  sa version est **pinnée exactement** (D8). Les modules (Phase 7) programment contre notre
  contrat stable, jamais contre la lib tierce.
- **Aucun appel IA sans contexte tenant** : le gateway lit `current_tenant()` (extension de
  l'invariant racine n°1) ; chaque appel est rattaché à un tenant, un user éventuel et un
  module. **Jamais de contenu de prompt ni de complétion** dans les logs ni dans
  `ai_usage_events` — uniquement des métriques (tokens, latence, coût, statut).
- **Chaque appel produit exactement un `ai_usage_events`** (control-plane), succès comme
  échec/timeout (metering best-effort, décision D3 : l'échec d'insertion est loggé, jamais
  levé — il ne casse pas la réponse). Prix **en code, versionnés** (`app/ai/pricing.py`,
  `price_version` estampillé, D4). Beat quotidien (`core.ai.daily_usage_rollup`) : agrégat
  `ai_usage_daily` idempotent + recalage quotas Valkey + purge des bruts (agrégats conservés).
- **Politique zéro-rétention infranchissable par configuration** (`app.ai.policy`, décision
  D5) : liste ZDR **en code** (`ZERO_RETENTION_PROVIDERS`, Mistral d'abord) ; sous
  `zero_retention`, un provider hors liste demandé explicitement est **refusé** (`PolicyError`),
  jamais dégradé. Le **fallback** (D6) est optionnel/désactivé par défaut et sa cible passe
  par la même `select` (donc la même règle ZDR).
- **Quotas soft** (`app.ai.quota`, Valkey) : au-delà du quota l'appel passe, mais l'alerte
  est **auditée une fois par jour** (`core.ai.quota_exceeded`) + affichée au back-office.
- **Clés provider jamais en clair** : plateforme via `Settings` (env, invariant n°3) ; BYOK
  chiffré `KeyProvider` en `tenant_ai_policies.byok_keys_enc` — **préparé, jamais exposé par
  l'API** (D7, `byok_configured` booléen seulement). Politique gérée au back-office
  (`/api/v1/admin/ai/*`), changements audités `core.ai.policy_changed`.

## Runtime de modules — règles non négociables (Phase 7)

- **Le cœur n'importe jamais `app/modules/*`**, sauf l'unique ligne de
  `app/automation/registry.py` (`MODULES`) ; un module n'importe jamais un autre module
  (décision D8). Les deux sont vérifiés par `tests/test_module_isolation.py` (analyse
  statique) — le gardien permanent de la promesse « ajouter un module ne touche pas au
  cœur ».
- **Un module = `app/modules/<name>/` + une ligne au registre + une migration tenant.**
  Rien d'autre. Contrat figé : `ModuleManifest` (`app/automation/contract.py`), versionné
  (`version` obligatoire) — toute extension du contrat est une PR du cœur.
- **Toute route de module** porte `require_permission("<name>.…")` (introspection
  fail-fast au démarrage, décision D2) ; le montage y ajoute `require_module_enabled(name)`
  sur tout le router (403 si inactif). Permissions/tâches/`audit_actions` namespacées
  `<name>.*` — `core.*` interdit aux modules (registre + audit le refusent).
- **Un module ne consomme QUE les briques socle** : `get_capability` (jamais les APIs
  providers), `AIGateway` (jamais LiteLLM), `get_tenant_session`/engine manager,
  `record_audit_event`. Ses tables vivent en DB tenant, préfixées `<name>_`, dans l'arbre
  Alembic tenant unique (décision D5).
- **Tâches périodiques** : signature `async (tenant_id) -> None`. Le scheduler
  (`app.automation.scheduler`) génère une entrée beat statique par tâche ; le fan-out
  publie une tâche unitaire par tenant actif (contexte posé, verrou Valkey par
  (module, tâche, tenant), échec isolé). Le dispatch (`enqueue_fanout`/`enqueue_unit`) est
  le point à monkeypatcher en test.
- **Activation par tenant** en control-plane (`tenant_modules`, `app.automation.service`) :
  `enable_module` refuse tant que les `required_capabilities` ne sont pas satisfaites ;
  `disable_module` conserve les données (D6). Audit `core.module.enabled`/`disabled`.

## Règles

- Toutes les routes sous `/api/v1`, montées dans `app/main.py` ; chaque route a un
  `operation_id` explicite (noms propres dans le client TS généré).
- Schémas d'E/S : Pydantic `BaseModel` dans le module concerné — jamais de dict brut.
- Config : uniquement via `app.core.config.Settings` ; jamais `os.environ` en direct.
- Logs : `structlog.get_logger()` ; jamais de PII/contenu métier ; le `request_id` est
  injecté automatiquement par le middleware.
- Tâches Celery : nom explicite namespacé (`core.ping`), déclarées dans le module concerné
  et importées par `app/worker.py`.
- pyright strict : les exceptions (libs non typées comme Celery) se gèrent par un commentaire
  `# pyright:` ciblé en tête de fichier, jamais en désactivant une règle globalement.
- Tests dans `apps/api/tests/`, un fichier par sujet, exécutés par `make test`.

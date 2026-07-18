# Backend `apps/api` — conventions du module

Voir le `CLAUDE.md` racine pour les invariants globaux. Spécificités backend :

## Structure

Un package Python par module métier sous `app/` :

```
app/core/      # config, logging, db (Base unique + sessions), migrations, crypto, csrf, mailer
app/health/    # sonde de vie
app/tenancy/   # catalogue, contexte tenant, mixin TenantScoped + garde-fous, provisioning
app/auth/      # sessions, mots de passe, TOTP, OAuth login, RBAC, rate limiting, purges
app/directory/ # identités globales, memberships, invitations, annuaire, équipes
app/audit/     # journal d'audit applicatif (scopé tenant, append-only par design)
app/connectors/ # connexions OAuth tierces (Google/Microsoft), capabilities Mail/Calendar,
#                refresh proactif, webhooks entrants, throttle (Phase 5)
app/ai/        # gateway IA unique (chat/stream/embeddings via LiteLLM), politiques par
#                tenant, metering, quotas, pricing versionné (Phase 6)
app/automation/ # runtime de modules (Phase 7) : contrat ModuleManifest, registre,
#                montage, activation par tenant, scheduler fan-out
app/modules/   # modules métier : un package par module ; voir app/modules/CLAUDE.md
app/cli.py     # CLI `saas` (tenant create/list/delete, invitation, db upgrade)
```

Chaque module expose au besoin : `router.py` (routes FastAPI), `service.py` (logique),
`models.py` (SQLAlchemy), `tasks.py` (tâches Celery).

## Multi-tenant — règles non négociables (ADR 0001)

- **Base unique + `tenant_id`.** Toute table métier hérite de
  `(Base, TenantScoped)` (`app.core.db.Base` + `app.tenancy.tenant_base.TenantScoped`) :
  colonne `tenant_id` FK indexée `ON DELETE CASCADE`. Les tables non scopées (catalogue
  `tenants`, `users`, `memberships`, `webhook_routes`, metering IA…) héritent de `Base`
  seule.
- **Accès aux données métier UNIQUEMENT via `app.tenancy.session`** :
  `get_tenant_session()` (dépendance HTTP, composée par `resolve_tenant`) ou
  `tenant_session()` (context manager CLI/tâches, sous `tenant_context(...)`). Les
  garde-fous installés sur la classe `Session` (`app.tenancy.tenant_base`) font le reste :
  filtre `tenant_id` injecté sur SELECT/UPDATE/DELETE (`with_loader_criteria`, aliases et
  lazy loads inclus, `Session.get` couvert), estampillage des INSERT, refus d'un
  `tenant_id` incohérent, `TenantContextError` sans contexte.
- **Requêtes non scopables refusées** : une table scopée référencée sans son entité ORM
  (ex. `select(func.count()).select_from(Model)`) lève — écrire `func.count(Model.id)`.
  Le SQL textuel (`text(...)`) contourne l'ORM : réservé aux tests et migrations.
- **Un seul arbre Alembic** (`apps/api/migrations/`, `make revision m="..."`). Toute
  évolution de schéma passe par Alembic — jamais de `create_all` hors tests. L'env.py
  importe `app.automation.registry` : les tables des modules rejoignent la MetaData sans
  toucher l'env.
- **Garde-fou permanent** : `tests/test_tenant_isolation_db.py` (isolation lecture/écriture,
  estampillage, unicité par tenant) — à faire évoluer avec tout changement du mécanisme.

## Auth — règles non négociables (Phase 2)

- **Aucune route métier sans `require_permission("core.x.y")`** (`app.auth.permissions`),
  qui compose `resolve_tenant` (sous-domaine x session x membership). Les seules routes
  anonymes sont une liste fermée : health, login (+TOTP), OAuth start/callback,
  acceptation d'invitation, callback connecteurs, webhooks providers.
- **Aucun secret en clair en base** : mots de passe argon2id ; tokens de session,
  d'invitation et de challenge **hachés** (sha256, `app.auth.tokens`) ; secrets TOTP
  **chiffrés** via `app.core.crypto.get_key_provider()`. Un token en clair n'apparaît
  qu'une fois, dans la réponse à son créateur.
- **Inscription publique désactivée** : tout compte naît d'une invitation ; l'OAuth
  login ne crée jamais de compte (liaison par email vérifié, puis (provider, subject)).
- Jamais d'email ni de credential dans les logs — référencer `user_id`/`tenant`.
- Rôles en code (`owner`/`admin`/`member`, décision D6) ; règles owner (promotion,
  dernier owner intouchable) dans `app.directory.service`, pas dans le RBAC.
- **`app/main.py` importe `app.worker`** (dans `create_app()`) : nécessaire pour que
  `@shared_task` résolve la bonne app Celery (broker configuré) dans le process API qui
  dispatche via `.delay()`. Piège à ne pas re-payer.

## Audit — règles non négociables

- **Toute action métier significative écrit son audit** via
  `app.audit.service.record_audit_event` (même session, même transaction quand l'action
  est en base — ex. provisioning) ou `record_audit_event_for_tenant` (hors contexte
  posé : route publique d'acceptation, tâches beat — l'audit commit avant l'action, au
  pire un événement orphelin, jamais une action sans trace).
- **`audit_events` est append-only par design** : aucune route ni fonction de
  modification/suppression. Jamais dupliqué en clair dans les logs techniques.
- Actions namespacées par convention : `core.*` (socle), `connector.*` (connecteurs),
  `<module>.*` (modules — namespace validé au démarrage par le registre).

## Tenants — cycle de vie (ADR 0002)

- **Créer** : `saas tenant create` → `provision_tenant` = INSERT au catalogue (état
  `active` d'emblée) + audit `core.tenant.provisioned` dans la même transaction.
- **Supprimer** : `saas tenant delete` = soft-delete (`deleted_at`). Le tenant devient
  invisible partout — `resolve_tenant` (404), `_active_tenants` des tâches beat,
  webhooks, callbacks OAuth. Les données restent ; restauration par SQL
  (`deleted_at = NULL`). Tout nouveau chemin qui itère ou résout des tenants DOIT
  exclure `deleted_at IS NOT NULL`.

## Connecteurs — règles non négociables (Phase 5)

- **Aucun token de connecteur en clair, nulle part** : chiffré `KeyProvider`
  (`app.core.crypto`) dans `app.connectors.tenant_models`, via
  `app.connectors.service.encrypt_token`/`decrypt_token` uniquement. Jamais dans les
  logs ni dans une réponse API (`ConnectionOut` = statuts et labels). `webhook_routes`
  (non scopée) ne porte que le routage.
- **Le reste du code ne consomme QUE les capabilities** (`app.connectors.capabilities` :
  `get_capability(session, connection, MailCapability)`), jamais les APIs Google/Graph
  directement. Toute implémentation propriétaire vit sous `app/connectors/providers/` et
  nulle part ailleurs.
- **Tout appel provider passe par l'enveloppe throttle/backoff**
  (`app.connectors.throttle.run_with_backoff` + `client_base`) : compteur Valkey par
  (provider, connexion), respect de `Retry-After`, `ProviderUnavailable` au plafond. Les
  SDK synchrones s'exécutent via `anyio.to_thread`, jamais dans l'event loop. Aucun appel
  lourd dans une requête HTTP — Celery.
- **Refresh des tokens sérialisé par verrou Valkey par connexion** (décision D5) ;
  `invalid_grant` → `needs_reconsent` + audit, jamais une exception qui casse le beat.
- **Toute réception webhook est authentifiée avant toute action** (echo `validationToken`
  + `clientState` haché chez Microsoft, en-têtes de channel chez Google) ; un webhook
  invalide répond 2xx neutre sans traitement ni log verbeux (pas d'oracle).
- **Cycle de vie audité** (`connector.connected`, `connector.reconsent_required`,
  `connector.revoked`, `connector.subscription_renewal_failed`,
  `connector.event_received`).
- **Écart au plan (D2) assumé** : l'échange/refresh OAuth des DEUX providers passe par
  `httpx` contre les endpoints du manifest — `msal` n'est PAS une dépendance du projet.

## Gateway IA — règles non négociables (Phase 6)

- **Tout appel à un provider IA passe par `AIGateway`** (`app.ai.gateway.get_gateway()`).
  **Aucun import de `litellm` ni d'un SDK provider hors de `app/ai/`** — LiteLLM est
  isolé derrière `set_completion_fn`/`set_embedding_fn` (doublées en test), version
  pinnée exactement.
- **Aucun appel IA sans contexte tenant** : le gateway lit `current_tenant()`. **Jamais
  de contenu de prompt ni de complétion** dans les logs ni dans `ai_usage_events` —
  uniquement des métriques.
- **Chaque appel produit exactement un `ai_usage_events`** (succès comme échec/timeout,
  metering best-effort). Prix en code, versionnés (`app/ai/pricing.py`). Beat quotidien
  (`core.ai.daily_usage_rollup`) : agrégat idempotent + recalage quotas + purge des bruts.
- **Politique zéro-rétention infranchissable par configuration** (`app.ai.policy`) :
  liste ZDR en code ; sous `zero_retention`, un provider hors liste est refusé
  (`PolicyError`), jamais dégradé. Le fallback passe par la même règle.
- **Quotas soft** (`app.ai.quota`, Valkey) : au-delà du quota l'appel passe, l'alerte est
  auditée une fois par jour (`core.ai.quota_exceeded`).
- **Clés provider jamais en clair** : plateforme via `Settings` (env) ; BYOK chiffré
  (`tenant_ai_policies.byok_keys_enc`), jamais exposé par l'API. Les politiques par
  tenant se gèrent en SQL (ADR 0003).

## Runtime de modules — règles non négociables (Phase 7)

- **Le cœur n'importe jamais `app/modules/*`**, sauf l'unique ligne de
  `app/automation/registry.py` (`MODULES`) ; un module n'importe jamais un autre module.
  Vérifié par `tests/test_module_isolation.py` — le gardien permanent.
- **Un module = `app/modules/<name>/` + une ligne au registre + une révision Alembic.**
  Rien d'autre. Contrat figé : `ModuleManifest` (`app/automation/contract.py`), versionné.
- **Toute route de module** porte `require_permission("<name>.…")` (introspection
  fail-fast au démarrage) ; le montage ajoute `require_module_enabled(name)` sur tout le
  router (403 si inactif). Permissions/tâches/`audit_actions` namespacées `<name>.*` —
  `core.*` interdit aux modules (validé au démarrage).
- **Un module ne consomme QUE les briques socle** : `get_capability`, `AIGateway`,
  session tenant (`get_tenant_session`/`tenant_session`), `record_audit_event`. Ses
  tables héritent de `(Base, TenantScoped)`, préfixées `<name>_`, dans l'arbre Alembic
  unique.
- **Tâches périodiques** : signature `async (tenant_id) -> None`. Beat statique par
  tâche ; fan-out = une tâche unitaire par tenant actif (contexte posé, verrou Valkey par
  (module, tâche, tenant), échec isolé). Le dispatch (`enqueue_fanout`/`enqueue_unit`)
  est le point à monkeypatcher en test.
- **Activation par tenant** (`tenant_modules`, `app.automation.service`) :
  `enable_module` refuse tant que les `required_capabilities` ne sont pas satisfaites ;
  `disable_module` conserve les données. Audit `core.module.enabled`/`disabled`.

## Règles

- Toutes les routes sous `/api/v1`, montées dans `app/main.py` ; chaque route a un
  `operation_id` explicite (noms propres dans le client TS généré).
- Schémas d'E/S : Pydantic `BaseModel` dans le module concerné — jamais de dict brut.
- Config : uniquement via `app.core.config.Settings` ; jamais `os.environ` en direct.
- Logs : `structlog.get_logger()` ; jamais de PII/contenu métier ; le `request_id` est
  injecté automatiquement par le middleware.
- Tâches Celery : nom explicite namespacé (`core.ping`), déclarées dans le module concerné
  et importées par `app/worker.py`.
- pyright strict : les exceptions (libs non typées comme Celery) se gèrent par un
  commentaire `# pyright:` ciblé en tête de fichier, jamais globalement.
- Tests dans `apps/api/tests/`, un fichier par sujet, exécutés par `make test`.

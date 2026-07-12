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
app/cli.py     # CLI `saas` (tenant/invitation/db/export/delete + admin grant/revoke)
# Phase 5+ : app/connectors/, ...
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

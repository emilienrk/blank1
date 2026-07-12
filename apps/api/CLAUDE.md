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
app/cli.py     # CLI `saas` (tenant/invitation/db + admin grant/revoke)
# Phase 4+ : app/audit/, app/gdpr/, ...
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

# Backend `apps/api` — conventions du module

Voir le `CLAUDE.md` racine pour les invariants globaux. Spécificités backend :

## Structure

Un package Python par module métier sous `app/` :

```
app/core/      # config (pydantic-settings), logging (structlog + request_id), db control-plane
app/health/    # sonde de vie
app/tenancy/   # catalogue, contexte tenant, engine manager, migrations runner, provisioning
app/directory/ # identités globales (users) + memberships — l'auth arrive en Phase 2
app/cli.py     # CLI d'administration `saas` (tenant create/list/retry-provision, db upgrade)
# Phase 2+ : app/auth/, ...
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

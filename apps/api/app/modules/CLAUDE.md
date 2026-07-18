# Modules métier `app/modules/` — conventions (Phase 7)

Voir les `CLAUDE.md` racine et `apps/api/CLAUDE.md` (section « Runtime de modules ») pour
les invariants. Spécificités des modules :

## Anatomie d'un module `app/modules/<name>/`

```
tenant_models.py   # tables (Base, TenantScoped), TOUJOURS préfixées `<name>_`
router.py          # APIRouter ; CHAQUE route porte require_permission("<name>.…")
service.py         # logique : tâches périodiques (async (tenant_id) -> None), handlers
manifest.py        # le ModuleManifest (permissions, tasks, connector_events,
#                    required_capabilities, audit_actions) — importé par le registre
```

`<name>` : slug `^[a-z][a-z0-9_]{1,30}$`, unique au registre.

## Règles absolues

- **Ne jamais toucher au cœur.** Ajouter un module = créer ce package + une ligne dans
  `app/automation/registry.py` + une révision Alembic (`make revision m="..."`). Toute
  autre modification du cœur est un signe qu'une brique socle manque (à discuter, pas à
  contourner).
- **Ne jamais importer un autre module** (décision D8) : tout partage passe par le socle.
  Vérifié par `tests/test_module_isolation.py`.
- **Ne consommer que les briques socle** :
  - Connecteurs : `app.connectors.capabilities.get_capability(...)` (Mail/Calendar) —
    jamais `app/connectors/providers/*` ni une API Google/Graph directement.
  - IA : `app.ai.gateway.get_gateway().chat/embed(...)` avec `module="<name>"` (metering
    ventilé) — jamais `litellm` ni un SDK provider.
  - Données : via le contexte posé (`get_tenant_session` en HTTP ; `tenant_session()`
    dans une tâche, le scheduler pose le contexte) — jamais d'engine ni de session hors
    de ces chemins. Le filtre `tenant_id` est automatique (ADR 0001).
  - Audit : `record_audit_event(...)` avec des actions déclarées dans `audit_actions`.
- **Namespaces `<name>.*`** pour permissions, tâches et actions d'audit. `core.*` interdit.
- **Tables héritant de `(Base, TenantScoped)`, préfixées `<name>_`**, dans l'arbre
  Alembic unique. Un module désactivé garde ses tables et ses données (D6).
- **Pas d'appel lourd dans une requête HTTP** : le travail (listings, IA) va dans une
  tâche périodique ou un handler, dispatché en Celery.

## Le module de référence

`sample_digest` (T6) traverse tout le contrat de bout en bout : à copier comme squelette.

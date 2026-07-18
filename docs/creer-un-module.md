# Créer un module métier — guide pas à pas

> La phase actuelle du projet : le socle est terminé, la valeur se construit dans
> les modules. Ce guide suit le module de référence `sample_digest`
> (`apps/api/app/modules/sample_digest/`) — le copier comme squelette est la
> bonne façon de démarrer. Contexte : [`architecture.md` § frontière
> cœur/modules](architecture.md#la-frontière-cœur--modules).

## Ce qu'est un module

Un package `app/modules/<name>/` qui déclare un `ModuleManifest` et ne consomme
que quatre briques socle :

| Besoin | Brique | Jamais |
|---|---|---|
| Données | `get_tenant_session` (HTTP) / `tenant_session()` (tâche) | engine ou session à la main |
| Emails/agenda du tenant | `get_capability(session, connection, MailCapability)` | APIs Google/Graph directes |
| IA | `get_gateway().chat(...)` avec `module="<name>"` | `litellm` ou un SDK provider |
| Traçabilité | `record_audit_event(...)` | écrire dans les logs |

Ajouter un module ne modifie **jamais** le cœur : uniquement le package, une
ligne au registre, une révision Alembic. `tests/test_module_isolation.py` le
vérifie ; le registre valide le reste au démarrage (une erreur de namespace ou
une route non protégée = boot qui échoue en CI).

## 1. Le squelette

```
app/modules/<name>/
├── __init__.py
├── tenant_models.py   # tables (Base, TenantScoped), préfixées <name>_
├── service.py         # logique : tâches périodiques, handlers
├── router.py          # routes, chacune avec require_permission("<name>.…")
└── manifest.py        # le ModuleManifest — seul fichier lu par le registre
```

`<name>` : slug `^[a-z][a-z0-9_]{1,30}$`, unique au registre. Il préfixe tout :
tables, permissions, tâches, actions d'audit.

## 2. Les tables (`tenant_models.py`)

Hériter de `(Base, TenantScoped)` donne la colonne `tenant_id` et l'isolation
automatique — ne jamais poser `tenant_id` soi-même, les garde-fous estampillent.

```python
class SampleDigestDigest(Base, TenantScoped):
    __tablename__ = "sample_digest_digests"        # préfixe <name>_ obligatoire
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    summary: Mapped[str] = mapped_column(Text())
```

## 3. La logique (`service.py`)

Une **tâche périodique** a la signature `async (tenant_id) -> None`. Quand le
scheduler l'appelle, le contexte tenant est déjà posé et le verrou
anti-chevauchement pris — le module ouvre juste sa session et travaille :

```python
async def generate_digest_task(tenant_id: uuid.UUID) -> None:
    current_tenant()                      # fail-fast si le contexte manque
    async with tenant_session() as session:
        await generate_digest(session)    # capabilities + gateway + audit
        await session.commit()
```

Dans `generate_digest` : `get_capability(...)` pour lire les emails,
`get_gateway().chat(ChatRequest(..., module=MODULE_NAME))` pour le résumé (le
metering IA est ventilé par module grâce à ce paramètre), `record_audit_event`
sur la même session que l'écriture (action et trace commit ensemble).

Un **handler d'événement connecteur** (`async (ConnectorEvent) -> None`) est
appelé, contexte posé, à chaque webhook normalisé de la capability à laquelle le
module s'abonne.

## 4. Les routes (`router.py`)

Chaque route porte `require_permission("<name>.…")` — vérifié au démarrage par
introspection. Le montage ajoute le préfixe `/api/v1/modules/<name>/` et le 403
si le module n'est pas actif pour le tenant : le router n'a pas à s'en occuper.
Donner un `operation_id` explicite à chaque route (il nomme la fonction dans le
client TS généré).

Règle d'or : **pas d'appel lourd dans une requête HTTP**. Un déclenchement manuel
dispatch la même tâche unitaire que le scheduler :

```python
@router.post("/run", operation_id="sampleDigestRun", status_code=202)
async def run_now(ctx: Annotated[TenantContext, Depends(require_permission(PERM_MANAGE))]) -> RunResponse:
    await scheduler.enqueue_unit(MODULE_NAME, TASK_NAME, ctx.tenant_id)
    return RunResponse()
```

## 5. Le manifeste (`manifest.py`)

Tout ce que le module expose, en un objet figé :

```python
manifest = ModuleManifest(
    name="sample_digest",
    version="1.0.0",
    title="Digest d'exemple",
    description="Résumé quotidien des emails reçus…",
    router=router,
    permissions=(ModulePermission(name=PERM_READ, roles=(ROLE_MEMBER, ROLE_ADMIN, ROLE_OWNER)), …),
    periodic_tasks=(PeriodicTaskSpec(name=TASK_NAME, schedule_seconds=86_400.0, fn=generate_digest_task),),
    connector_events=(ConnectorEventSpec(capability=CAPABILITY_MAIL, handler=on_mail_event),),
    required_capabilities=(MailCapability,),   # contrôlées à l'activation par tenant
    audit_actions=(DIGEST_ACTION,),
)
```

## 6. Brancher, migrer, générer

1. **Registre** — la seule ligne de « couture » :
   ```python
   # app/automation/registry.py
   from app.modules.<name>.manifest import manifest as <name>_manifest
   MODULES = [..., <name>_manifest]
   ```
2. **Migration** — `make revision m="<name> tables"` puis `make migrate` (l'env
   Alembic importe le registre : les tables du module sont vues automatiquement).
3. **Client TS** — `make generate-client` si le module a des routes.
4. **Page SPA** — si besoin, une page dans `apps/web/src/pages/` + sa route dans
   `router.tsx` (voir [`frontend.md`](frontend.md)) ; `useCurrentRole()` pour
   masquer les actions non permises (UX uniquement).

## 7. Activer et vérifier

L'activation est **par tenant** (`tenant_modules`, via `app.automation.service`
en CLI/SQL — ADR 0003) : `enable_module` refuse tant qu'aucune connexion active
ne satisfait les `required_capabilities` ; la désactivation conserve les données.

Vérifications avant PR :

- `make lint && make typecheck && make test` — le boot des tests échoue si un
  namespace est faux ou une route non protégée (fail-fast du registre) ;
- des tests du module dans `apps/api/tests/` (doubler le gateway via
  `set_completion_fn`, le dispatch via monkeypatch de `enqueue_*`) ;
- aucun import d'un autre module, aucun changement hors du package + la ligne du
  registre + la migration (sinon : une brique socle manque — à discuter, pas à
  contourner).

## Pièges connus

- Écrire `select(func.count()).select_from(Model)` → refusé par les garde-fous ;
  écrire `func.count(Model.id)`.
- Oublier `module="<name>"` dans les appels au gateway → l'usage IA est compté
  sur `core`.
- Faire le travail dans la route HTTP → passer par une tâche dispatché en Celery.
- Nommer une permission/tâche/action hors du namespace `<name>.*` → erreur de boot.

# 0001 — Base-par-tenant → base unique + `tenant_id`

- **Date** : 2026-07-18
- **Statut** : accepté

## Contexte

Le socle isolait chaque tenant dans sa propre base PostgreSQL : engine manager
LRU par tenant, provisioning par `CREATE DATABASE`, deux MetaData et deux arbres
Alembic, runner de migrations multi-bases sous verrou advisory, contexte tenant
portant `db_name`/`db_host`. Une isolation physique excellente — et un coût
opérationnel permanent (N bases à migrer, sauvegarder, surveiller) injustifiable
pour un MVP à quelques tenants portés par une équipe de 2.

## Décision

Une **base unique** ; toute table métier porte `tenant_id` (FK indexée vers
`tenants.id`, `ON DELETE CASCADE`) via le mixin `TenantScoped`
(`app.tenancy.tenant_base`). L'invariant racine n°1 — « jamais de requête métier
sans contexte tenant résolu » — reste **impossible à violer par construction**,
déplacé du niveau connexion au niveau session SQLAlchemy :

- **`do_orm_execute`** (classe `Session`, donc toute session du process) : tout
  SELECT/UPDATE/DELETE touchant un mapper `TenantScoped` exige `current_tenant()`
  (sinon `TenantContextError`) et reçoit
  `with_loader_criteria(tenant_id == contexte, include_aliases=True)` — propagé
  aux lazy loads, appliqué à `Session.get()`.
- Une requête référençant une table scopée **sans son entité ORM**
  (`select(func.count()).select_from(Model)`) est **refusée** plutôt que laissée
  passer non filtrée — écrire `func.count(Model.id)`.
- **`before_flush`** : les inserts scopés sont estampillés du tenant courant ;
  un `tenant_id` incohérent (insert/update/delete) est refusé.
- Hors périmètre : le SQL textuel (`text(...)`) — réservé aux tests/migrations.

Conséquences structurelles : `TenantContext` réduit à `(tenant_id, slug, role)` ;
`get_tenant_session()`/`tenant_session()` rendent une session ordinaire du
sessionmaker unique ; provisioning = un INSERT (audit dans la même transaction) ;
un seul arbre Alembic (`apps/api/migrations/`, baseline `0001_baseline`,
`make revision`) ; `TenantState` réduit à `active`/`suspended` ; unicités
re-scopées par tenant (ex. `(tenant_id, name)` sur `teams`).

Le critère d'acceptation est `tests/test_tenant_isolation_db.py` : sans contexte
→ erreur ; sous le tenant A, les données de B sont invisibles en
select/get/update/delete ; estampillage automatique ; unicité par tenant.

## Conséquences

- Un seul `alembic upgrade head`, une seule base à sauvegarder/surveiller ;
  suppression de ~1 000 lignes de plomberie (engine manager, runner, provisioning
  multi-étapes, retry).
- L'isolation devient logique (filtre systématique) et non plus physique : le
  test garde-fou et la revue des rares `text(...)` sont la ligne de défense.
- Un pic de charge d'un tenant peut affecter les autres (pool partagé) —
  acceptable au volume MVP.
- L'audit du provisioning est désormais strictement atomique avec l'insert
  (bénéfice direct de la base unique).

## Procédure de réintroduction

Improbable avant une exigence client d'isolation physique. Le cas échéant :

1. `git show archive/pre-mvp-simplification` — récupérer
   `apps/api/app/tenancy/{engine_manager,migrations_runner}.py`, l'ancien
   `provisioning.py`, les deux arbres `apps/api/migrations/{controlplane,tenant}/`
   et les deux `.ini`.
2. Restaurer `db_name`/`db_host` sur `Tenant`, les settings `TENANT_*` et
   `control_plane_url`/`tenant_database_url` dans `Settings`.
3. Écrire la migration de données inverse (éclater la base par `tenant_id`) —
   n'existait pas à l'époque non plus, c'est le vrai coût.

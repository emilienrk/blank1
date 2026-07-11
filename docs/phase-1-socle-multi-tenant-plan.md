# Phase 1 — Socle multi-tenant : plan d'implémentation détaillé

> Référence : `docs/architecture-plan.md` (§3 « Multi-tenant et bases par tenant », §9
> « Phase 1 »). Cette phase couvre : control-plane (catalogue tenants, identités globales,
> memberships), `TenantEngineManager`, runner de migrations Alembic multi-bases,
> provisioning CLI. Rien de plus — l'auth, l'annuaire complet et la résolution par session
> arrivent en Phase 2 ; seules les fondations qu'il serait pénible de rétrofitter sont
> posées ici (chaque anticipation est signalée explicitement).

## État des lieux

Phase 0 fusionnée (PR #1) : monorepo, FastAPI `/api/v1/health`, worker Celery, SPA React,
client TS généré, CI 4 jobs, Compose complet, déploiement pull. **Aucune couche DB
n'existe encore** : ni SQLAlchemy, ni Alembic, ni accès Postgres depuis le code. Le
Compose fournit déjà Postgres 17 avec un rôle applicatif capable de `CREATE DATABASE`
(anticipation actée en Phase 0, T6).

---

## A. Tâches ordonnées

### T1 — Dépendances et configuration DB

Rôle : faire entrer SQLAlchemy 2.0 async + Alembic + Typer (prévu au plan global pour
cette phase) et étendre la config 12-factor.

| Fichier | Rôle |
|---|---|
| `apps/api/pyproject.toml` | Ajouts : `sqlalchemy[asyncio]`, `asyncpg` (décision D1), `alembic`, `typer`. |
| `apps/api/app/core/config.py` | Nouvelles settings : `postgres_host`, `postgres_port`, `postgres_user`, `postgres_password`, `postgres_db` (control-plane), `tenant_db_prefix` (déf. `tenant_`), `tenant_engine_cache_size` (déf. 20), `tenant_engine_pool_size` (déf. 2). Propriétés qui composent les URL asyncpg (control-plane et par-tenant) — **les URL ne sont jamais stockées, toujours composées** (décision D3). |
| `.env.example` | Variables ci-dessus avec valeurs factices. |

### T2 — Couche DB control-plane et catalogue

Rôle : le control-plane du §3 — catalogue tenants, identités globales, memberships.

| Fichier | Rôle |
|---|---|
| `apps/api/app/core/db.py` | Engine async control-plane (singleton), `async_sessionmaker`, `Base` déclarative **control-plane**. |
| `apps/api/app/tenancy/models.py` | Tables control-plane : `tenants` (id UUID, `slug` unique `^[a-z][a-z0-9-]{1,38}$`, `name`, `db_name`, `db_host` alias logique déf. `default` — le « serveur hôte » du §8.7, `state` enum `provisioning/active/suspended/failed`, `plan` déf. `standard` — préparation facturation §2, timestamps). |
| `apps/api/app/directory/models.py` | Identités globales minimales : `users` (id UUID, `email` unique casse-insensible, timestamps — **aucun credential**, l'auth est Phase 2) ; `memberships` (user × tenant × `role` texte, unicité (user, tenant)). Créées maintenant car le provisioning y touchera en Phase 2 et le schéma control-plane doit naître complet. |
| `apps/api/app/tenancy/tenant_base.py` | `Base` déclarative **tenant**, MetaData strictement séparée du control-plane (invariant I3). Première table métier factice : `tenant_settings` (clé/valeur) — juste assez pour prouver les migrations. |

### T3 — Migrations Alembic : deux arbres

Rôle : un arbre pour le control-plane, un pour le schéma tenant (§3), totalement
indépendants (décision D2).

| Fichier | Rôle |
|---|---|
| `apps/api/migrations/controlplane/` | `env.py` async (recette officielle `run_sync`), `versions/` avec la révision initiale (tables T2). |
| `apps/api/migrations/tenant/` | Idem pour le schéma tenant (révision initiale : `tenant_settings`). |
| `apps/api/alembic.controlplane.ini`, `apps/api/alembic.tenant.ini` | Deux configs explicites ; l'URL vient toujours de `Settings`, jamais de l'ini. |
| `Makefile` | Cibles : `migrate` (control-plane + toutes les bases tenant via le runner), `revision-controlplane`, `revision-tenant` (autogenerate). |

### T4 — `TenantEngineManager`

Rôle : le routage des connexions du §3.

| Fichier | Rôle |
|---|---|
| `apps/api/app/tenancy/engine_manager.py` | Un async engine SQLAlchemy par tenant : création **paresseuse**, cache **LRU** (`tenant_engine_cache_size`), `dispose()` à l'éviction, pool par engine petit (`tenant_engine_pool_size`) → plafond global = cache × pool, verrou asyncio contre la double création, `invalidate(tenant_id)` explicite (suspension/suppression). Clé de cache = id du tenant, URL composée depuis le catalogue (`db_name`, `db_host`) + credentials env. |

### T5 — Contexte tenant (fondation de l'invariant n°1)

Rôle : rendre exécutable « jamais de requête métier sans contexte tenant résolu ».
Anticipation minimale de la résolution HTTP du §3 (décision D5) : la dépendance existe et
est testée, mais **aucune route métier publique** tant que l'auth n'existe pas.

| Fichier | Rôle |
|---|---|
| `apps/api/app/tenancy/context.py` | `TenantContext` (id, slug, état) porté par un `contextvars.ContextVar` ; `current_tenant()` lève `TenantContextError` si absent. |
| `apps/api/app/tenancy/deps.py` | Dépendance FastAPI `resolve_tenant` : extrait le sous-domaine du `Host`, cherche le tenant actif au catalogue, pose le contexte (404 si inconnu, 403 si suspendu). Le croisement avec la session/membership est un TODO Phase 2 tracé dans le code. |
| `apps/api/app/tenancy/session.py` | `get_tenant_session()` : session async sur l'engine du tenant **courant** via le manager ; refuse de fonctionner sans contexte. C'est le seul chemin d'accès aux DB tenant. |

### T6 — Runner de migrations multi-bases

Rôle : le runner du §3 — verrou advisory, itération catalogue, rapport d'échecs partiels.

| Fichier | Rôle |
|---|---|
| `apps/api/app/tenancy/migrations_runner.py` | Séquence : (1) verrou advisory Postgres pris sur la DB control-plane (un seul runner à la fois, non bloquant → échec explicite si déjà pris) ; (2) upgrade control-plane ; (3) itération des tenants `active`/`provisioning`, `upgrade head` par base, **séquentiel** (décision D7) ; (4) par base : succès/échec capturé (l'échec d'une base ne bloque pas les suivantes), version atteinte relue dans `alembic_version` ; (5) `MigrationReport` structuré (par base : statut, version, erreur résumée) loggé en JSON et retourné ; **au moindre échec, code de sortie ≠ 0**. |

### T7 — Provisioning

Rôle : la commande de création de tenant du §3 (version CLI ; le back-office est Phase 3).

| Fichier | Rôle |
|---|---|
| `apps/api/app/tenancy/provisioning.py` | Séquence : valider le slug (regex T2 — le nom de DB est dérivé, jamais interpolé sans validation, invariant I6) → insérer au catalogue en `provisioning` → `CREATE DATABASE` (connexion autocommit à la DB d'admin) → migrations tenant sur la nouvelle base → seed minimal (`tenant_settings`) → état `active`. Sur échec : état `failed` + erreur loggée ; commande de reprise `tenant retry-provision <slug>` (droppe la DB orpheline si présente et rejoue). L'invitation du premier owner est un TODO Phase 2. |

### T8 — CLI Typer

Rôle : le point d'entrée admin de la phase (le back-office viendra en Phase 3).

| Fichier | Rôle |
|---|---|
| `apps/api/app/cli.py` | App Typer, console script `saas` (déclaré dans `pyproject.toml`). Commandes : `tenant create <slug> --name`, `tenant list` (états + version de schéma par base), `tenant retry-provision <slug>`, `db upgrade [--only-controlplane]` (runner T6). S'exécute dans le conteneur : `docker compose run --rm api saas …`. |

### T9 — CI et environnement d'exécution

| Fichier | Rôle |
|---|---|
| `.github/workflows/ci.yml` | Job back : ajouter un **service `postgres:17`** (les tests de cette phase exigent un vrai Postgres, décision D6) + variables d'env. |
| `scripts/deploy-pull.sh` | Après redéploiement : `docker compose run --rm api saas db upgrade` (décision D8) ; échec → visible dans journalctl, smoke non lancé. |
| `README.md` | Section multi-tenant : créer un tenant, lancer les migrations, prérequis Postgres pour `make test` en local. |

### T10 — Clôture

- `CLAUDE.md` racine et `apps/api/CLAUDE.md` mis à jour (modules `tenancy`/`directory`,
  règle « accès DB tenant uniquement via `get_tenant_session()` »).
- `make generate-client` (aucune route publique ajoutée → client inchangé, vérifié par la CI).
- Critère de démo (section E) déroulé et vérifié.

---

## B. Points de conception — décisions et recommandations

| # | Question | Recommandation | Justification |
|---|---|---|---|
| D1 | Driver async : asyncpg ou psycopg 3 ? | **asyncpg** | Le duo SQLAlchemy async + asyncpg est de loin le plus documenté (critère IA-friendly du plan global) ; performances éprouvées. psycopg 3 reste substituable (l'URL est composée à un seul endroit). |
| D2 | Un arbre Alembic à branches ou deux arbres séparés ? | **Deux répertoires + deux ini** | Isolation totale entre schéma control-plane et schéma tenant : impossible de mélanger les révisions, chaque `env.py` connaît une seule MetaData. Les branches Alembic sont la source d'erreurs classique. |
| D3 | Que stocke le catalogue pour la connexion ? | **`db_name` + alias `db_host`, jamais d'URL ni de credentials** | Aucun secret en DB (invariant racine n°3) ; les credentials du rôle applicatif viennent de l'env ; l'alias hôte prépare la répartition multi-serveurs (§8.7) sans refonte. |
| D4 | PgBouncer maintenant ? | **Non — différé** | Mentionné au §3 pour la cible, mais inutile à < ~50 tenants : le manager plafonne déjà les connexions (LRU × pool). asyncpg + pooling transactionnel exige des précautions (prepared statements) qu'on ne veut pas payer avant d'en avoir besoin. Le champ `db_host` laisse la porte ouverte. |
| D5 | Résolution HTTP du tenant dans cette phase ? | **Dépendance + contextvars oui, route métier non** | L'invariant n°1 doit devenir du code exécutable maintenant (le rétrofitter est le pire chantier, §9) ; mais sans auth, exposer une route résolvant les tenants révélerait leur existence. Testée par pytest uniquement ; Phase 2 y branchera session + membership. |
| D6 | Tests DB : vrai Postgres, SQLite ou testcontainers ? | **Vrai Postgres** (service CI + Compose en local) | `CREATE DATABASE`, verrous advisory et `alembic_version` ne se testent que sur Postgres ; SQLite dévierait du dialecte réel ; testcontainers ajoute du docker-in-docker fragile. Bases éphémères préfixées `test_`, droppées en teardown. |
| D7 | Migrations des N bases : séquentiel ou parallèle ? | **Séquentiel** | Rapport lisible, charge maîtrisée sur le cluster, code simple. Le parallélisme est une optimisation prématurée tant que N est petit ; l'interface du runner (liste → rapport) n'aura pas à changer. |
| D8 | Qui lance les migrations au déploiement ? | **`deploy-pull.sh`, étape dédiée après redéploiement** | Le plan global exige « exécuté au déploiement + à la demande ». Le modèle pull impose que ce soit la machine qui le fasse ; le verrou advisory protège du chevauchement avec un lancement manuel. Résultat visible dans journalctl comme le reste du déploiement. |

---

## C. Invariants et règles absolues de la phase

1. **Tout accès à une DB tenant passe par `get_tenant_session()`**, qui exige un contexte
   tenant posé — l'invariant racine n°1 devient exécutable et testé.
2. **Aucun secret ni URL de connexion en base** : le catalogue ne stocke que `db_name` +
   alias `db_host` ; credentials via l'environnement uniquement.
3. **Deux MetaData strictement séparées** (control-plane / tenant) ; aucune table métier
   en control-plane, aucune table de catalogue en DB tenant.
4. **Toute évolution de schéma passe par Alembic** — jamais de `create_all` hors
   fixtures de test.
5. **Le runner ne s'arrête jamais à la première base en échec** : il termine l'itération,
   rapporte base par base, et sort en erreur si au moins une a échoué.
6. **Aucune interpolation SQL non validée** : les noms de DB dérivent d'un slug validé par
   regex stricte ; `CREATE DATABASE`/`DROP DATABASE` sont les seuls endroits où un
   identifiant est interpolé, après validation + quoting.
7. Les invariants Phase 0 restent en vigueur (image unique api/worker, config par env,
   logs JSON corrélés — le runner et le provisioning loggent en structlog, pyright strict,
   client TS généré).

---

## D. Tests à écrire

**Backend (pytest, Postgres réel — `apps/api/tests/`)**
- `test_engine_manager.py` : création paresseuse ; réutilisation du même engine ; éviction
  LRU → `dispose()` appelé ; `invalidate()` ; deux tenants n'obtiennent jamais le même engine.
- `test_tenant_context.py` : `current_tenant()` sans contexte → `TenantContextError` ;
  `get_tenant_session()` sans contexte → refus ; la dépendance `resolve_tenant` pose le
  contexte depuis le sous-domaine (app de test) ; tenant inconnu → 404 ; suspendu → 403.
- `test_provisioning.py` : `create` → DB créée, `alembic_version` à head, seed présent,
  catalogue `active` ; slug invalide → rejet sans toucher à la DB ; slug en doublon → rejet ;
  échec injecté à mi-parcours → état `failed`, puis `retry-provision` aboutit.
- `test_migrations_runner.py` : N bases saines → toutes à head, rapport OK ; panne injectée
  sur une base (droit révoqué ou `alembic_version` corrompue) → cette base en échec au
  rapport, **les autres migrées quand même**, code retour ≠ 0 ; verrou advisory déjà pris →
  échec explicite immédiat.
- `test_cli.py` : smoke des commandes via `CliRunner` (create/list/upgrade sur base de test).
- `test_config.py` (extension) : nouvelles settings + composition d'URL.

**CI** : le job back embarque le service Postgres ; les jobs front/contrat/images sont
inchangés (aucune route publique nouvelle → client TS identique, la CI de dérive le prouve).

---

## E. Critère de démo de fin de phase

> Sur staging (ou en local via Compose) : `saas tenant create acme` puis `saas tenant
> create globex` créent deux bases migrées et enregistrées au catalogue. On ajoute une
> révision tenant factice, on sabote volontairement la base `globex` (droit révoqué), puis
> `saas db upgrade` : le rapport montre `acme` migrée à head et `globex` en échec avec
> l'erreur, le code de sortie est non nul, et `saas tenant list` reflète versions et états.
> Après réparation, `saas db upgrade` remet tout à head. Toutes les opérations sont
> visibles dans Loki en JSON corrélé.

C'est le critère du plan global (§9) : « créer un tenant en CLI, migrations appliquées sur
N bases, échec partiel correctement rapporté ».

---

## F. Dépendances manquantes et risques propres à la phase

1. **La branche `main` n'existe toujours pas** : `staging-images.yml` cible `main` ; tant
   qu'elle n'est pas créée (action admin GitHub : créer `main` depuis la branche par défaut
   actuelle et la définir par défaut), rien ne se déploie. À régler avant la démo staging.
2. **`docker compose up` complet jamais exécuté** (hérité de la Phase 0, pas de daemon
   Docker en sandbox) : la démo de cette phase l'exige — premier point à valider sur la
   machine de staging.
3. **Durée de CI** : le service Postgres et les tests DB allongent le job back ; acceptable,
   mais surveiller (< 5 min visé).
4. **`make test` local exige désormais un Postgres joignable** (celui du Compose suffit) —
   documenté au README ; c'est le prix de tests fidèles (D6).
5. Aucune dépendance vers les phases ultérieures : l'auth (Phase 2) consommera
   `resolve_tenant`, `users`/`memberships` et le provisioning tels que définis ici.

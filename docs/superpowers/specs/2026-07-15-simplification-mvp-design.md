# Simplification MVP — design validé

Date : 2026-07-15
Statut : validé (design) — prêt pour rédaction du plan d'implémentation.
Contexte : équipe de 2 développeurs, objectif MVP au contact des utilisateurs le plus vite
possible. La base de code actuelle (~25 000 lignes, 11 services Docker, 7 phases
d'infrastructure) est sur-dimensionnée pour un MVP : elle est entièrement de la plomberie
plateforme (aucune feature produit réelle — seul `sample_digest`, un module d'exemple).

## Produit

Cœur produit confirmé : **automatisations sur comptes connectés** (connecter Google/Microsoft
et lancer des automatisations dessus). Conséquence directe : connecteurs, runtime de modules,
worker/beat et gateway IA ne sont **pas** de la plomberie mais le cœur produit. L'IA est
**pivot** dans le projet.

## Objectif

Réduire drastiquement la surface opérationnelle et cognitive **sans jeter le cœur produit**,
en convertissant le modèle de tenancy le plus lourd (base-par-tenant) vers un modèle standard
MVP (base unique + colonne `tenant_id`), et en archivant proprement (récupérable) tout ce qui
relève d'une préoccupation de scale-up conforme prématurée.

Métrique de santé : le nombre de lignes de code **produit** (pas de plomberie) écrites par
semaine. Aujourd'hui : zéro.

## Décisions

### Briques conservées
- **Auth** (`app/auth/`) : argon2id, TOTP, OAuth login, invitations, RBAC. Solide, coûteux à
  réécrire.
- **Connecteurs** (`app/connectors/`) : **Google ET Microsoft** conservés. Cœur produit.
- **Gateway IA** (`app/ai/`) : conservée quasi entière (l'IA est pivot ; le code est propre,
  LiteLLM isolé derrière des frontières remplaçables, ne dépend pas de la base-par-tenant).
  **Seule exception retirée** : `app/ai/admin_service.py` + routes `/api/v1/admin/ai/*` (couplées
  au back-office archivé).
- **Runtime de modules** (`app/automation/`) : conservé entier. C'est le backbone du produit
  (scheduler fan-out multi-tenant + contrat de module). Code stable, versionné, documenté,
  gardé par le test d'isolation statique. Seul ajustement : `TenantContext` se simplifie (voir
  conversion tenant_id).
- **Worker + beat + Celery** : conservés. Indispensables aux automatisations planifiées.

### Briques simplifiées
- **Audit** (`app/audit/`) : garder la table `events` + `record_audit_event`. Jeter
  l'append-only strict, le registre typé d'actions verrouillé et le couplage à la rétention RGPD.
- **Base-par-tenant → `tenant_id`** : voir section dédiée (le gros morceau structurel).

### Briques archivées (récupérables via tag git)
- **RGPD délai de grâce** (`app/gdpr/`) : effacement à délai de grâce, rétention, exports
  chiffrés. Remplacé par un simple `deleted_at` (soft-delete).
- **Back-office** : `apps/admin/` (SPA), routes `/api/v1/admin/*`, `require_platform_admin`,
  `infra/caddy/Caddyfile.admin`, `app/ai/admin_service.py`. Administration via CLI/SQL au MVP.
- **Observabilité lourde** : Loki, Alloy, Grafana, Uptime Kuma (services compose + `infra/`
  correspondants). Remplacé par `docker logs` + un uptime hébergé plus tard.

### Topologie repo
**Monorepo conservé** pour le MVP. Le client TS est généré depuis l'OpenAPI du back
(invariant n°5) ; scinder front/back en repos séparés casserait le flux atomique contrat→client
et imposerait une synchro cross-repo à 2 personnes. Tooling déjà câblé (Makefile, pnpm
workspace, CI). Chaque app reste isolée sous `apps/`, donc scinder plus tard reste trivial.

### Résultat
Docker : **11 → 6 services** (postgres, valkey, api, worker, beat, web/caddy).

## Conversion base-par-tenant → `tenant_id`

Le changement qui touche le plus de fichiers. Approche :

1. **Fusionner les deux `MetaData`.** Aujourd'hui : `ControlPlaneBase` (catalogue, users,
   memberships — control-plane) et `TenantBase` (données métier, une DB par tenant). Cible :
   une seule base ; toutes les tables métier (`TenantBase`) reçoivent une colonne `tenant_id`
   (FK vers `tenants`), indexée. Concerne aussi `audit_events`, les tokens de connecteurs
   (`connectors/tenant_models.py`) et les tables de modules (`<name>_*`).
2. **Remplacer `get_tenant_session()`.** Au lieu de router vers un engine par tenant
   (`engine_manager` LRU), une session unique sur la base partagée + un **filtre `tenant_id`
   systématique**. L'invariant n°1 est **préservé** : le contexte tenant reste obligatoire, mais
   il injecte un filtre au lieu de sélectionner une base. Sans contexte → toujours une erreur.
3. **Supprimer** : `app/tenancy/engine_manager.py`, le provisioning `CREATE DATABASE`
   (`provisioning.py`), `migrations_runner.py` (migrations par tenant), et **collapser les deux
   arbres Alembic (`migrations/controlplane/` + `migrations/tenant/`) en un seul**.
4. **Simplifier `TenantContext`** (`app/tenancy/context.py`) : retirer `db_name`/`db_host` ;
   garder `tenant_id`/`slug`/`state`/`role`. Répercuter partout où `TenantContext(...)` est
   construit (notamment `app/automation/scheduler.py`, `app/gdpr/tasks.py` si conservé).
5. **Garde-fou** : conserver/adapter un test qui échoue si une requête métier part sans
   `tenant_id` en scope — l'esprit de l'invariant n°1 transposé au single-DB.

Note provisioning : la création d'un tenant devient une simple insertion de ligne `tenants`
(plus de `CREATE DATABASE`). L'effacement devient un `deleted_at` (soft-delete), plus de
`DROP DATABASE`.

## Documentation & ADR

Arborescence cible :

```
docs/
  adr/
    README.md   # index + convention ADR + lien vers le tag archive
    0001-passage-base-par-tenant-vers-tenant-id.md
    0002-archivage-rgpd-delai-de-grace.md
    0003-archivage-back-office-admin.md
    0004-simplification-observabilite.md
    0005-monorepo-conserve-pour-mvp.md
  archive/
    README.md   # quoi a été retiré, où le retrouver (tag), comment réintroduire
```

Chaque ADR couvre : contexte, décision, conséquences, **procédure de réintroduction** (chemin
dans le tag `archive/pre-mvp-simplification`). Les plans de phase existants (`docs/phase-*.md`)
sont conservés : ils deviennent la doc de référence des briques archivées.

## Backup

- Tag git **`archive/pre-mvp-simplification`** sur l'état actuel, **poussé sur origin** —
  backup permanent indépendant du repo de travail (l'utilisateur envisage de recréer des repos).
- Les ADRs du repo simplifié pointent vers ce tag pour ré-extraire n'importe quelle brique.

## Ordre d'exécution

Chaque étape testée (`make test`/`lint`/`typecheck`) avant la suivante.

1. Tag + push de l'archive ; créer le squelette `docs/adr/` + `docs/archive/`.
2. Archiver l'observabilité (compose + `infra/`) et le back-office (`apps/admin`, routes admin,
   `require_platform_admin`, Caddyfile.admin, `ai/admin_service.py`).
3. Archiver le RGPD à délai de grâce → soft-delete `deleted_at` ; simplifier l'audit.
4. **Conversion `tenant_id`** (étape isolée, la plus lourde).
5. Nettoyage IA (retrait `admin_service`) ; vérifier connecteurs + runtime + IA en single-DB.
6. Réécrire `CLAUDE.md` (invariants mis à jour) + README ; `make test`/`lint`/`typecheck` verts.

## Hors périmètre

- Écrire la première vraie feature produit (viendra après la simplification, dans un cycle
  spec → plan → implémentation dédié).
- Scinder le monorepo en plusieurs repos (explicitement reporté).
- Réintroduire une des briques archivées (chacune fera son propre cycle si un client réel
  l'exige).

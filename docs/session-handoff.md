# Passation de session — état du projet et reprise

> Document destiné à une nouvelle session (Claude/Fable) pour continuer le travail
> sans re-dériver le contexte. Dernière mise à jour : 2026-07-11 (session « plan Phase 1 »).

## Le projet en bref

Socle SaaS B2B multi-tenant auto-hébergé en France : auth interne, une DB PostgreSQL
par tenant, connecteurs externes (Google/Microsoft), couche IA multi-providers,
observabilité allégée, RGPD. Équipe très réduite, développement fortement assisté par
IA → stack volontairement mûre et sur-documentée (FastAPI, SQLAlchemy, Celery, React).

Documents de référence, à lire dans cet ordre :
1. `docs/architecture-plan.md` — plan global (stack, architecture, 9 phases). **Toutes
   les décisions d'architecture y sont actées : ne pas les rouvrir sans demande explicite.**
2. `docs/phase-1-socle-multi-tenant-plan.md` — plan détaillé de la phase courante
   (tâches T1-T10, décisions D1-D8, invariants, tests, critère de démo). **C'est le
   document de travail actuel.**
3. `docs/phase-0-fondations-plan.md` — plan de la phase précédente (implémentée, fusionnée).
4. `CLAUDE.md` (racine) et `apps/api/CLAUDE.md` — conventions et invariants opérationnels.

## État actuel

### Phase 0 : fusionnée ✅

PR #1 fusionnée dans la branche par défaut du repo, qui est **encore
`claude/saas-architecture-planning-8k9dpc`** (pas de `main` — voir « Hors repo » ci-dessous).
Contenu livré : monorepo uv+pnpm, FastAPI `/api/v1/health` + worker Celery (même image),
SPA React/Vite + client TS généré, CI 4 jobs verte, Compose complet (Postgres 17, Valkey,
Caddy, Loki/Grafana/Alloy, Uptime Kuma), déploiement staging en modèle pull
(`scripts/deploy-pull.sh` + timer systemd). Détail complet : `docs/phase-0-fondations-plan.md`
et le README.

### Phase 1 : IMPLÉMENTÉE ✅ (plan validé par l'utilisateur, décisions D1-D8 suivies)

- **Branche** : `claude/next-phase-detailed-plan-twlm2j` (PR associée).
- Livré, conformément au plan `docs/phase-1-socle-multi-tenant-plan.md` :
  - Control-plane : `tenants` (catalogue, D3 : jamais d'URL/credentials en base),
    `users` + `memberships` (identités globales minimales, zéro credential) ;
    engine/session singleton dans `app/core/db.py`.
  - `TenantEngineManager` (`app/tenancy/engine_manager.py`) : engines async paresseux,
    LRU + dispose, `invalidate()`, plafond = cache_size x pool_size.
  - Contexte tenant (`context.py`, contextvars) + `get_tenant_session()` (`session.py`,
    SEUL chemin vers les DB tenant, lève `TenantContextError` sans contexte) +
    dépendance `resolve_tenant` (`deps.py`, sous-domaine → 404/403, D5 : aucune route
    publique ne l'expose encore).
  - Deux arbres Alembic (`apps/api/migrations/{controlplane,tenant}`, env.py async,
    D2) + runner multi-bases (`migrations_runner.py` : verrou advisory 715001,
    séquentiel D7, rapport par base, échec partiel ne bloque pas les autres).
  - Provisioning (`provisioning.py` : validate slug → catalogue → CREATE DATABASE →
    migrations → seed → active ; `retry_provision` droppe l'orpheline) + CLI Typer
    `saas` (`app/cli.py`, console script) : tenant create/list/retry-provision,
    db upgrade (exit 1 si échec partiel, 2 si verrou occupé).
  - CI : service postgres:17 sur le job backend ; `deploy-pull.sh` lance
    `saas db upgrade` après redéploiement (D8), échec → déploiement en erreur.
  - 35 tests pytest (Postgres réel, D6 — bases éphémères `test_*` droppées) :
    engine manager (LRU/dispose/invalidate), contexte + resolver (404/403),
    provisioning (bout en bout, slug invalide/dupliqué/réservé, échec→retry),
    runner (échec partiel, verrou advisory), CLI, composition d'URL.
- **Pièges appris (à ne pas re-payer)** : les pools asyncpg sont liés à leur event
  loop — le CLI enveloppe chaque commande dans `run_async()` qui dispose les engines
  dans la même boucle ; TestClient a sa propre boucle → `dispose_control_engine()`
  avant de l'instancier dans un test qui a déjà touché la DB ; typeshed récent exige
  `Generator`/`AsyncGenerator` (pas `Iterator`) comme retour des fonctions décorées
  `@contextmanager`/`@asynccontextmanager`.

### Décisions d'implémentation héritées de la Phase 0 (toujours en vigueur)

- **TypeScript figé en 5.x** : TS 7 incompatible avec openapi-typescript (peer `^5.x`).
- **Client API** : `fetch` résolu à chaque appel (stub testable) ; `baseUrl =
  window.location.origin` (jsdom/undici refusent les URL relatives).
- **Pyright strict** : libs non typées (Celery, TestClient) → commentaire `# pyright:`
  ciblé en tête de fichier, jamais de règle globale.
- **Versions pinnées** : uv 0.8.17, pnpm 10.33.0, Node 22, Python 3.12, Postgres 17,
  grafana/loki:3.4.1, grafana/alloy:v1.7.5, grafana/grafana:11.5.2.

### Vérifié / non vérifié

- ✅ Chaîne qualité locale + CI GitHub (Phase 0) ; runtime réel de l'API (uvicorn + curl).
- ⚠️ **Jamais exécuté** : `docker compose up` complet (pas de daemon Docker dans les
  sandboxes des sessions). Au premier déploiement, valider : Caddy sert la SPA et route
  `/api`, Alloy pousse les logs vers Loki, le worker consomme Valkey.

## Reste à faire hors repo (bloquants pour la démo staging)

1. **Créer la branche `main`** depuis la branche par défaut actuelle et la définir comme
   branche par défaut (action admin GitHub) — `staging-images.yml` cible `main`, rien ne
   se déploie sans elle.
2. **Domaine + DNS** avec wildcard `*.staging.<domaine>` (tenancy par sous-domaine).
3. **Machine de staging** : Docker + `/srv/saas/.env` + PAT `packages:read` + timer
   systemd (procédure complète dans le README, section « Déploiement staging »).

## Prochaine étape (pour la session suivante)

1. Faire fusionner la PR Phase 1 ; dérouler le critère de démo staging (section E du
   plan Phase 1) dès que la machine existe.
2. Ensuite : **Phase 2 — Auth + annuaire** (sessions, argon2, TOTP, OAuth login
   Google/Microsoft via Authlib, orgs/équipes/rôles/permissions, invitations).
   Même méthode : plan détaillé d'abord (`docs/phase-2-...-plan.md`), validation
   utilisateur, puis implémentation. La Phase 2 branche session + membership dans
   `resolve_tenant` (TODO tracé dans `app/tenancy/deps.py`), consomme
   `users`/`memberships` et ajoute l'invitation du premier owner au provisioning
   (TODO tracé dans `app/tenancy/provisioning.py`).

## Commandes utiles

```bash
make install && make dev        # démarrage local (voir README)
make lint typecheck test        # chaîne qualité complète
make generate-client            # après toute modif des routes API
docker compose config --quiet   # valider les fichiers compose
```

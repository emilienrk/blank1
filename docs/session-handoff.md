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

### Phase 1 : plan détaillé rédigé, EN ATTENTE DE VALIDATION ⏳

- **Branche** : `claude/project-handoff-review-xb5o01` (PR draft associée).
- Livrable de cette session : `docs/phase-1-socle-multi-tenant-plan.md` — control-plane
  (catalogue `tenants`, `users` identités globales, `memberships`), `TenantEngineManager`
  (async engines paresseux, LRU), deux arbres Alembic (control-plane / tenant), runner de
  migrations multi-bases (verrou advisory, rapport d'échecs partiels, séquentiel), 
  provisioning + CLI Typer `saas`, Postgres réel dans la CI.
- **Méthode convenue avec l'utilisateur : ne PAS implémenter tant que ce plan n'est pas
  validé.** À la validation (éventuellement amendée), implémenter tâche par tâche (T1→T10).
- Décisions proposées à valider en particulier : D4 (PgBouncer différé), D5 (résolution
  HTTP du tenant limitée à une dépendance testée, pas de route publique), D8 (migrations
  lancées par `deploy-pull.sh`).

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

1. Recueillir la validation de l'utilisateur sur `docs/phase-1-socle-multi-tenant-plan.md`
   (amender si retours, notamment D4/D5/D8).
2. Implémenter la Phase 1 **tâche par tâche dans l'ordre T1→T10** du plan, en gardant la
   CI verte à chaque étape ; les tests DB exigent un Postgres réel (service CI + Compose
   en local, décision D6).
3. Dérouler le critère de démo (section E du plan) et mettre à jour ce handoff.
4. Ensuite : Phase 2 — Auth + annuaire (même méthode : plan détaillé d'abord, validation,
   puis implémentation).

## Commandes utiles

```bash
make install && make dev        # démarrage local (voir README)
make lint typecheck test        # chaîne qualité complète
make generate-client            # après toute modif des routes API
docker compose config --quiet   # valider les fichiers compose
```

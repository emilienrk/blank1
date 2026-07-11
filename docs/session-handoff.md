# Passation de session — état du projet et reprise

> Document destiné à une nouvelle session (Claude/Fable) pour continuer le travail
> sans re-dériver le contexte. Dernière mise à jour : 2026-07-11.

## Le projet en bref

Socle SaaS B2B multi-tenant auto-hébergé en France : auth interne, une DB PostgreSQL
par tenant, connecteurs externes (Google/Microsoft), couche IA multi-providers,
observabilité allégée, RGPD. Équipe très réduite, développement fortement assisté par
IA → stack volontairement mûre et sur-documentée (FastAPI, SQLAlchemy, Celery, React).

Documents de référence, à lire dans cet ordre :
1. `docs/architecture-plan.md` — plan global (stack, architecture, 9 phases). **Toutes
   les décisions d'architecture y sont actées : ne pas les rouvrir sans demande explicite.**
2. `docs/phase-0-fondations-plan.md` — plan détaillé de la Phase 0 (tâches T1-T10,
   décisions D1-D6, invariants, tests, critère de démo).
3. `CLAUDE.md` (racine) et `apps/api/CLAUDE.md` — conventions et invariants opérationnels.

## État actuel : Phase 0 implémentée ✅

- **Branche** : `claude/next-phase-detailed-plan-twlm2j` — **PR #1** (draft) :
  https://github.com/emilienrk/blank1/pull/1
- **CI verte 4/4** sur le dernier commit : backend (ruff, pyright strict 0 erreur,
  7 tests pytest), frontend (eslint, tsc, 2 tests vitest, build), dérive du client TS,
  build des deux images Docker.
- La branche par défaut du repo est encore `claude/saas-architecture-planning-8k9dpc`
  (elle ne contient que le plan global). **À la fusion de la PR #1, créer/définir `main`
  comme branche par défaut** — le workflow de déploiement cible `main`.

### Ce qui existe et fonctionne

| Zone | Contenu |
|---|---|
| Monorepo | Workspaces uv (`apps/api`) + pnpm (`apps/web`, `packages/*`) ; `Makefile` point d'entrée unique ; `.env.example` |
| Backend | FastAPI sous `/api/v1` (`GET /api/v1/health`), config pydantic-settings, structlog JSON + middleware `X-Request-ID`, worker Celery (`core.ping`, broker Valkey) — même code, même image Docker |
| Frontend | SPA React/Vite/TS strict, TanStack Router (code-based) + Query, Tailwind 4 ; la page d'accueil affiche le health via le client généré ; `packages/ui` (StatusBadge) |
| Contrat | `scripts/export_openapi.py` → `packages/api-client` généré (openapi-typescript + openapi-fetch), committé, dérive bloquée en CI |
| Infra | `docker-compose.yml` + `docker-compose.staging.yml` : Postgres 17, Valkey, api, worker, Caddy (statiques + proxy `/api` + TLS auto), Loki (rétention 30 j), Grafana provisionné, Alloy, Uptime Kuma ; ports admin liés à 127.0.0.1 |
| CI/CD | `.github/workflows/ci.yml` (bloquante) et `deploy-staging.yml` (GHCR + runner self-hosted + smoke) |

### Décisions prises pendant l'implémentation (en plus de D1-D6 du plan de phase)

- **TypeScript figé en 5.x** : TS 7 (installé par défaut par pnpm en 2026) est
  incompatible avec openapi-typescript (peer `^5.x`). Ne pas monter en TS 7 sans
  vérifier cette compatibilité.
- **Client API** : `fetch` résolu à chaque appel (et non figé à la création du client)
  pour permettre le stub de `globalThis.fetch` dans les tests ; `baseUrl =
  window.location.origin` car `new Request()` hors navigateur (jsdom/undici) refuse
  les URL relatives.
- **Pyright strict** : les libs non typées (Celery, TestClient) se gèrent par un
  commentaire `# pyright:` ciblé en tête de fichier, jamais par une règle globale.
- **Versions pinnées** : uv 0.8.17 (Dockerfile + CI), pnpm 10.33.0, Node 22, Python 3.12,
  grafana/loki:3.4.1, grafana/alloy:v1.7.5, grafana/grafana:11.5.2 — tags vérifiés
  existants dans les registries.

### Vérifié / non vérifié

- ✅ Toute la chaîne qualité locale + CI GitHub (y compris build des images).
- ✅ Runtime réel de l'API (uvicorn + curl : payload correct, `X-Request-ID` propagé).
- ⚠️ **Jamais exécuté** : `docker compose up` complet (pas de daemon Docker dans la
  sandbox de la session précédente). Au premier déploiement, valider : Caddy sert la
  SPA et route `/api`, Alloy pousse bien les logs vers Loki, le worker consomme Valkey.

## Reste à faire hors repo (bloquants pour le critère de démo Phase 0)

1. **Domaine + DNS** avec wildcard `*.staging.<domaine>` (tenancy par sous-domaine en Phase 1).
2. **Machine de staging** : Docker + fichier `/srv/saas/.env` (voir README).
3. **Mécanisme de déploiement** — décision ouverte, voir ci-dessous.
4. Créer la branche `main` (cible du workflow de déploiement).

## Déploiement staging : modèle pull (décidé et implémenté)

L'utilisateur a choisi le **modèle pull** (au lieu du runner GitHub self-hosted,
initialement retenu en D4 du plan de phase — décision révisée) :

- `.github/workflows/staging-images.yml` : sur push `main`, build + push des images
  vers GHCR (`:sha` + `:latest`). C'est tout ce que fait GitHub.
- `scripts/deploy-pull.sh` (exécuté PAR la machine staging via
  `infra/systemd/saas-deploy.timer`, tick 5 min + `OnBootSec`) : `git fetch` best
  effort des fichiers compose, `compose pull`, redéploiement **uniquement si l'ID
  d'image api ou web a changé**, puis smoke test HTTPS avec retries et
  `docker image prune`. Verrou `flock` anti-chevauchement.
- Justification : zéro accès entrant et zéro service GitHub sur la machine
  (IP résidentielle, §8.6 du plan global) ; rattrapage automatique au boot.
  Coût accepté : latence ≤ 5 min, résultat du déploiement visible dans
  `journalctl -u saas-deploy.service` (pas dans GitHub Actions), PAT fine-grained
  lecture seule (`packages:read`) stocké sur la machine.
- Mise en place initiale documentée dans le README (section « Déploiement staging »).

## Prochaine étape : Phase 1 — Socle multi-tenant

Méthode de travail convenue avec l'utilisateur, à respecter :
1. **D'abord** produire un plan détaillé de la phase (comme `docs/phase-0-fondations-plan.md` :
   tâches ordonnées avec fichiers/rôles, décisions avec recommandations, invariants,
   tests, critère de démo, risques) — **sans anticiper les phases suivantes** au-delà
   du nécessaire. Le soumettre à validation.
2. **Ensuite seulement** implémenter, tâche par tâche.

Périmètre de la Phase 1 (plan global §3 et §9) : control-plane (catalogue tenants,
identités globales, memberships), `TenantEngineManager` (engines async par tenant, LRU),
runner de migrations Alembic multi-bases (verrou advisory, rapport d'échecs partiels),
provisioning CLI (Typer). SQLAlchemy 2.0 async + Alembic entrent dans les dépendances
à ce moment-là. Critère de démo du plan global : « créer un tenant en CLI, migrations
appliquées sur N bases, échec partiel correctement rapporté ».

## Commandes utiles

```bash
make install && make dev        # démarrage local (voir README)
make lint typecheck test        # chaîne qualité complète
make generate-client            # après toute modif des routes API
docker compose config --quiet   # valider les fichiers compose
```

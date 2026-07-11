# Phase 0 — Fondations : plan d'implémentation détaillé

> Référence : `docs/architecture-plan.md` (plan global v2). Cette phase couvre le point §9 « Phase 0 — Fondations » : monorepo, CI, Docker Compose, squelettes api/worker/web, génération du client TS depuis l'OpenAPI, déploiement continu vers staging. Rien de plus — les phases 1+ ne sont anticipées que là où ne pas le faire nous bloquerait (voir chaque mention explicite).

## État des lieux

Le repo ne contient que le plan global. Aucun code, aucune CI, aucune infra. Tout ce qui suit part de zéro.

---

## A. Tâches ordonnées

### T1 — Racine du monorepo

Rôle : câbler l'espace de travail Python + TypeScript et les commandes communes. Tout le reste s'appuie dessus.

| Fichier | Rôle |
|---|---|
| `pyproject.toml` (racine) | Workspace **uv** (`[tool.uv.workspace]`, membre `apps/api`) ; config partagée `ruff`, `pyright`, `pytest` (un seul endroit pour les règles de qualité). |
| `pnpm-workspace.yaml` | Workspace pnpm : `apps/web`, `packages/*`. `apps/admin` **non créé** (Phase 3, décision D5) — seul le chemin est réservé par convention. |
| `Makefile` | Cibles : `dev`, `lint`, `typecheck`, `test`, `openapi`, `generate-client`, `build`, `smoke`. Point d'entrée unique humain + CI + IA. |
| `.gitignore`, `.editorconfig` | Hygiène de base (`.env`, `node_modules`, `.venv`, `dist`, `__pycache__`…). |
| `.env.example` | Toutes les variables d'environnement avec valeurs factices. Committé ; `.env` ne l'est jamais. |
| `CLAUDE.md` (racine) | Conventions du monorepo + **invariants transverses**, dont « jamais de requête métier sans contexte tenant » — acté dès maintenant bien que la tenancy arrive en Phase 1, pour que tout code généré ensuite naisse tenant-aware (§2 du plan global). |
| `README.md` | Démarrage local en 3 commandes, carte du repo. |

### T2 — Squelette backend `apps/api` (api + worker, même code)

Rôle : le monolithe modulaire FastAPI/Celery du plan global (§2), réduit à sa plus simple expression fonctionnelle.

| Fichier | Rôle |
|---|---|
| `apps/api/pyproject.toml` | Dépendances : `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `structlog`, `celery[redis]`, `httpx` ; dev : `pytest`, `pytest-asyncio`, `ruff`, `pyright`. **Pas de SQLAlchemy/Alembic** : la couche DB arrive en Phase 1 avec le TenantEngineManager. |
| `apps/api/app/main.py` | Factory `create_app()` ; montage des routers sous `/api/v1` (décision D6) ; branchement logging + middleware. |
| `apps/api/app/core/config.py` | `Settings` pydantic-settings : config **exclusivement par variables d'env** (12-factor). `APP_ENV`, `APP_VERSION`, `LOG_LEVEL`, `VALKEY_URL`, `POSTGRES_*`… |
| `apps/api/app/core/logging.py` | structlog JSON sur stdout ; middleware `request_id` : lit ou génère `X-Request-ID`, le propage en réponse et l'injecte dans le contexte de log (contextvars). Fondation de la corrélation exigée au §7. |
| `apps/api/app/health/router.py` | `GET /api/v1/health` → `{status, version, env}`. **Aucun accès DB en Phase 0.** |
| `apps/api/app/worker.py` | App Celery (broker Valkey), config issue de `core/config.py` ; tâche `ping` de démonstration. Même code que l'api — c'est le Dockerfile qui distingue les deux processus (invariant I1). |
| `apps/api/CLAUDE.md` | Conventions du backend : structure de module (`router.py`, `service.py`, `models.py` à venir), règles de typage, interdits. |

### T3 — Qualité Python

Rôle : le garde-fou du code généré par IA (§1 « Qualité / IA-friendly »), en place avant la première vraie fonctionnalité.

- `ruff` : lint + format, config racine.
- `pyright` : mode **strict** dès la première ligne — rétrofitter le strict sur un codebase existant est un chantier pénible, l'inverse est gratuit.
- `pytest` + `pytest-asyncio` configurés ; premiers tests (section D).

### T4 — Squelette frontend `apps/web` + `packages/ui`

Rôle : la SPA client du plan global (§1 Frontend), réduite à une page qui prouve la chaîne complète front → client généré → API.

| Fichier/dossier | Rôle |
|---|---|
| `apps/web/` | Vite + React + TypeScript **strict**. TanStack Router (route `/` unique) + TanStack Query. Tailwind + init shadcn/ui. |
| `apps/web/src/routes/index.tsx` | Page d'accueil : appelle `GET /api/v1/health` via `packages/api-client` et affiche statut + version. C'est la preuve vivante du contrat OpenAPI bout en bout. |
| `packages/ui/` | Package React partagé minimal (ex. un composant `StatusBadge` utilisé par la page) — valide le câblage workspace qui servira à `apps/admin` en Phase 3. |
| ESLint, `tsc --noEmit`, Vitest | Miroir front du trio ruff/pyright/pytest. |

### T5 — Client TS généré `packages/api-client`

Rôle : matérialiser l'invariant « le contrat OpenAPI est la source de vérité front/back » (§1, §2).

| Fichier | Rôle |
|---|---|
| `scripts/export_openapi.py` | Exporte `openapi.json` depuis `create_app()` **sans lancer de serveur** (import direct de l'app). |
| `packages/api-client/` | Types générés par **openapi-typescript** + client runtime **openapi-fetch** (décision D2). **Jamais édité à la main.** |
| Cible `make generate-client` | Chaîne export → génération. La CI régénère et échoue si `git diff` n'est pas vide (détection de dérive, T8). |

### T6 — Docker : images et Compose

Rôle : l'environnement d'exécution complet du §1 (Infra) et §7 (observabilité allégée), identique en dev et staging à un override près.

| Fichier | Rôle |
|---|---|
| `apps/api/Dockerfile` | Multi-stage (uv) → **une seule image** pour `api` (uvicorn) et `worker` (celery), commandes différentes au runtime (invariant I1). |
| `apps/web/Dockerfile` | Build Vite → statiques servis par Caddy. |
| `docker-compose.yml` | Services dev : `postgres:17`, `valkey`, `api`, `worker`, `web`, `caddy`, `loki`, `grafana`, `alloy`, `uptime-kuma`. Postgres configuré avec un rôle applicatif capable de `CREATE DATABASE` — seule anticipation Phase 1 (provisioning §3), coût nul maintenant, migration pénible plus tard. |
| `docker-compose.staging.yml` | Override staging : TLS auto Caddy, `restart: unless-stopped`, **ports admin (Grafana, Uptime Kuma) non exposés publiquement** (invariant I6). |
| `infra/caddy/Caddyfile` | `staging.<domaine>` → SPA statique + reverse-proxy `/api/*` vers `api`. Entrée DNS **wildcard `*.staging.<domaine>`** prévue dès maintenant : la résolution du tenant par sous-domaine (§3) en dépendra. |

### T7 — Logs centralisés (observabilité allégée §7)

| Fichier | Rôle |
|---|---|
| `infra/alloy/config.alloy` | Collecte des sorties Docker → Loki (**Alloy**, décision D3). |
| `infra/grafana/provisioning/` | Datasource Loki provisionnée automatiquement (zéro clic). |
| `infra/loki/loki-config.yaml` | Rétention **30 jours** (§7). |

Règle actée ici et pour toujours : logs techniques JSON, corrélés `request_id`, **jamais de contenu métier ni de PII**.

### T8 — CI GitHub Actions

Rôle : la CI bloquante du §1, exécutée à chaque commit.

| Fichier | Rôle |
|---|---|
| `.github/workflows/ci.yml` | Jobs parallèles : **back** (ruff, pyright, pytest), **front** (eslint, tsc, vitest, build), **contrat** (régénération du client TS + `git diff --exit-code`), **images** (build Docker api + web, sans push). Requis pour tout merge. |

### T9 — Déploiement continu staging

Rôle : « déploiement continu vers staging dès le premier jour » (§9 Phase 0).

| Fichier | Rôle |
|---|---|
| `.github/workflows/deploy-staging.yml` | Sur push `main` : build + push des images vers **GHCR**, puis job de déploiement exécuté par un **runner GitHub self-hosted sur la machine staging** (décision D4) : `docker compose pull && docker compose up -d`, puis `make smoke`. |
| `make smoke` | `curl` de `https://staging.<domaine>/api/v1/health` (via Caddy, donc TLS + reverse-proxy validés) + vérification du payload. |

### T10 — Clôture

- `README.md` finalisé (dev local : `cp .env.example .env && make dev`), `CLAUDE.md` relus.
- Critère de démo (section E) déroulé et vérifié de bout en bout.

---

## B. Points de conception — décisions et recommandations

| # | Question | Recommandation | Justification |
|---|---|---|---|
| D1 | Makefile ou justfile ? | **Makefile** | Critère IA-friendly du plan global : l'outil le plus massivement représenté dans les données d'entraînement ; installé partout, y compris sur les runners CI. |
| D2 | openapi-typescript + openapi-fetch, ou orval ? | **openapi-typescript + openapi-fetch** | Plus léger (types purs + fetch typé, quasi zéro code généré à maintenir), très stable. Orval (hooks TanStack générés) reste substituable plus tard sans toucher au contrat OpenAPI. |
| D3 | Promtail ou Alloy pour la collecte ? | **Alloy** | Promtail est en fin de vie (EOL mars 2026) ; Alloy est le successeur officiel Grafana, et le cas d'usage (logs Docker → Loki) tient en une config courte. |
| D4 | Comment déployer sur staging (IP résidentielle) ? | **Runner GitHub self-hosted sur la machine staging**, qui tire les images depuis GHCR | Aucune exposition SSH entrante — cohérent avec la sécurité périmétrique §8.6 (admin derrière WireGuard). Alternative documentée si le runner déplaît : SSH sortant via WireGuard depuis un runner cloud. |
| D5 | Créer `apps/admin` maintenant ? | **Non — Phase 3** | Le plan global (§9) ne demande que les squelettes api/worker/web en Phase 0 ; `packages/ui` suffit à garantir que le câblage multi-app fonctionne. |
| D6 | Préfixe `/api/v1` dès le premier endpoint ? | **Oui** | Versionner après coup casse le client généré et tous ses consommateurs ; le coût est nul maintenant. |

---

## C. Invariants et règles absolues de la phase

1. **Une seule image Docker** pour `api` et `worker` — seule la commande de démarrage diffère (§2).
2. **Config par variables d'environnement uniquement** (pydantic-settings). Aucun secret dans le repo ; `.env.example` committé, `.env` ignoré.
3. **Logs : JSON sur stdout uniquement**, `request_id` systématique, jamais de PII ni de contenu métier (§7). Pas de fichiers de log.
4. **Le client TS est toujours généré**, jamais édité à la main ; la dérive contrat/client casse la CI.
5. **CI bloquante** : aucun merge sans ruff + pyright strict + pytest + eslint + tsc + vitest verts.
6. **Surfaces admin jamais exposées publiquement** : Grafana, Uptime Kuma (et plus tard le back-office) accessibles uniquement en réseau local/WireGuard, même sur staging (§8.6).
7. **Préparation multi-tenant sans l'implémenter** : aucun code de Phase 0 ne doit supposer « une seule base de données » ou « un seul client » ; l'invariant « jamais de requête métier sans contexte tenant » est inscrit dans `CLAUDE.md` dès cette phase ; DNS wildcard réservé ; rôle Postgres capable de `CREATE DATABASE`.
8. **Typage strict dès la première ligne** : pyright strict côté Python, `strict: true` côté TypeScript.

---

## D. Tests à écrire

**Backend (pytest, `apps/api/tests/`)**
- `test_health.py` : `GET /api/v1/health` → 200, payload `{status: "ok", version, env}`.
- `test_config.py` : `Settings` se charge depuis l'environnement ; valeur manquante obligatoire → erreur explicite.
- `test_request_id.py` : header `X-Request-ID` absent → généré et renvoyé ; présent → propagé tel quel ; le `request_id` figure dans la ligne de log JSON émise (capture de stdout).
- `test_worker.py` : la tâche `ping` s'exécute en mode eager (`task_always_eager`) et renvoie la valeur attendue.

**Frontend (Vitest, `apps/web/`)**
- Rendu smoke de la route `/`.
- Affichage du statut de santé avec le client api mocké (états succès et erreur).

**CI (les tests « de structure »)**
- Job de dérive : régénération du client TS puis `git diff --exit-code`.
- Build des deux images Docker (sans push) — garantit que les Dockerfiles ne pourrissent pas.

**Staging**
- `make smoke` post-déploiement : health en HTTPS à travers Caddy, payload vérifié.

---

## E. Critère de démo de fin de phase

> Un `git push` sur `main` déclenche la CI (verte : lint, types, tests back et front, dérive du client, build des images), puis le déploiement automatique vers staging. Ensuite : `https://staging.<domaine>` sert la SPA ; celle-ci appelle `GET /api/v1/health` **via le client TS généré** et affiche statut + version ; la requête est visible dans Grafana/Loki en JSON, corrélée par son `request_id` ; le worker Celery a exécuté la tâche `ping` (visible dans les logs Loki) ; Uptime Kuma surveille le health ; Grafana et Uptime Kuma ne sont pas joignables depuis Internet.

Si ce paragraphe est vrai de bout en bout, la Phase 0 est terminée et la Phase 1 (socle multi-tenant) peut démarrer sur des rails.

---

## F. Dépendances manquantes et risques propres à la phase

1. **Nom de domaine + DNS** : requis avant T6/T9 (TLS Caddy, critère de démo), avec **wildcard `*.staging.<domaine>`** pour ne pas bloquer la résolution tenant par sous-domaine en Phase 1. → À provisionner en amont.
2. **Machine de staging** : le plan global exige le staging dès le premier jour (§8.5). Si elle n'existe pas encore, décision à acter : soit on la provisionne avant T9, soit le critère de démo est temporairement réduit à un « staging local » (Compose sur la machine de dev) — à dire explicitement, pas par défaut.
3. **Secrets et enregistrement manuels** : token GHCR (packages write), enregistrement du runner self-hosted, premier `.env` staging — actions manuelles hors CI, à documenter dans le README.
4. **Versions à figer explicitement** : Python 3.12, Node 22 LTS (pnpm via corepack), Postgres 17 — dans `pyproject.toml`, `.nvmrc`/`package.json#engines` et le Compose, jamais implicites.

Aucune dépendance vers les phases ultérieures n'est requise pour démarrer : la Phase 0 est autoporteuse une fois les points 1–3 réglés.

# Passation de session — état du projet et reprise

> Document destiné à une nouvelle session (Claude/Fable) pour continuer le travail
> sans re-dériver le contexte. Dernière mise à jour : 2026-07-12 (session
> « implémentation Phase 3 »).

## Le projet en bref

Socle SaaS B2B multi-tenant auto-hébergé en France : auth interne, une DB PostgreSQL
par tenant, connecteurs externes (Google/Microsoft), couche IA multi-providers,
observabilité allégée, RGPD. Équipe très réduite, développement fortement assisté par
IA → stack volontairement mûre et sur-documentée (FastAPI, SQLAlchemy, Celery, React).

Documents de référence, à lire dans cet ordre :
1. `docs/architecture-plan.md` — plan global (stack, architecture, 9 phases). **Toutes
   les décisions d'architecture y sont actées : ne pas les rouvrir sans demande explicite.**
2. `docs/phase-3-frontends-backoffice-plan.md` — plan détaillé de la phase courante
   (tâches T1-T10, décisions D1-D8, invariants, tests, critère de démo).
   **Plan suivi et implémenté** (écarts documentés dans « État actuel » ci-dessous).
3. `docs/phase-2-auth-annuaire-plan.md`, `docs/phase-1-socle-multi-tenant-plan.md` et
   `docs/phase-0-fondations-plan.md` — plans des phases précédentes (implémentées,
   fusionnées).
4. `docs/phase-4-audit-rgpd-plan.md` … `docs/phase-8-durcissement-plan.md` —
   plans détaillés des phases à venir (rédigés en avance, session « plans phases
   3-8 » du 2026-07-11). **À re-valider avec l'utilisateur au démarrage de chaque
   phase** : l'« état des lieux » de chaque plan est une hypothèse à confronter au
   réel, et les décisions D restent des recommandations tant que la phase n'a pas
   démarré.
5. `CLAUDE.md` (racine) et `apps/api/CLAUDE.md` — conventions et invariants opérationnels.

## État actuel

### Phase 0 : fusionnée ✅

PR #1 fusionnée dans la branche par défaut du repo, qui est **encore
`claude/saas-architecture-planning-8k9dpc`** (pas de `main` — voir « Hors repo » ci-dessous).
Contenu livré : monorepo uv+pnpm, FastAPI `/api/v1/health` + worker Celery (même image),
SPA React/Vite + client TS généré, CI 4 jobs verte, Compose complet (Postgres 17, Valkey,
Caddy, Loki/Grafana/Alloy, Uptime Kuma), déploiement staging en modèle pull
(`scripts/deploy-pull.sh` + timer systemd). Détail complet : `docs/phase-0-fondations-plan.md`
et le README.

### Phase 1 : FUSIONNÉE ✅ (PR #3, plan validé par l'utilisateur, décisions D1-D8 suivies)

- **Branche** : `claude/next-phase-detailed-plan-twlm2j` (PR #3, fusionnée).
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

### Phase 2 : IMPLÉMENTÉE ✅ (plan validé par l'utilisateur, décisions D1-D9 suivies)

- **Branche** : `claude/phase-2-handoff-review-yc44nn` (PR associée, plan fusionné via PR #4).
- Livré, conformément au plan `docs/phase-2-auth-annuaire-plan.md` :
  - `app/core/crypto.py` : `KeyProvider` AES-256-GCM (clé maître env, D4) —
    réutilisable par les connecteurs Phase 5 ; `app/core/mailer.py` (SMTP optionnel,
    D8) ; `app/core/csrf.py` (contrôle Origin sur mutations, D7).
  - `app/auth/` : modèles (credentials argon2id, sessions token-haché D1,
    oauth_identities, invitations, login_challenges, recovery codes), service
    (login indistinct + re-hash D3, TOTP pyotp anti-rejeu par compteur, codes de
    récupération à usage unique), router (login en 2 temps, totp setup/activate/
    disable, me, logout, invitations/accept, oauth start/callback), `permissions.py`
    (RBAC D6 : owner/admin/member en code, `require_permission` dépendance unique),
    `rate_limit.py` (fenêtre fixe Valkey D9), `tasks.py` (purge beat horaire),
    `oauth.py` (OIDC manuel + JOSE Authlib — PAS l'intégration Starlette : state
    signé auto-porteur au lieu d'une session serveur ; testé par faux provider local).
  - `resolve_tenant` croise désormais session x membership (TODO Phase 1 levé) :
    401 non authentifié, 403 non membre ; contexte enrichi du rôle.
  - `app/directory/` : invitations (token haché, usage unique, dernier owner
    intouchable), annuaire (members list/patch/delete), équipes en DB TENANT
    (`tenant_models.py`, migration tenant 0002) — premières routes traversant
    `get_tenant_session()` en HTTP réel.
  - CLI : `tenant create --owner-email` (invitation owner en fin de provisioning),
    `invitation create` ; l'URL d'acceptation est toujours affichée (D8).
  - Migrations : controlplane 0002 (auth), tenant 0002 (teams).
  - 88 tests pytest verts (Postgres réel + fakeredis pour le rate limiting — la CI
    n'a pas besoin de Redis) ; pyright strict 0 erreur ; client TS régénéré
    (premières vraies routes du contrat).
- **Pièges appris (en plus de ceux de la Phase 1)** : penser `reset_db_engines()`
  à CHAQUE bascule pytest ↔ TestClient (helpers `tests/helpers.py`) ; l'intégration
  Starlette d'Authlib exige SessionMiddleware → OIDC manuel avec state signé HMAC ;
  anti-rejeu TOTP = compteur strictement croissant (les tests utilisent le pas de
  temps suivant pour éviter la collision avec le code d'activation) ;
  `TENANT_HEAD_REVISION` dans `tests/conftest.py` à bumper à chaque révision tenant.

### Phase 3 : IMPLÉMENTÉE ✅ (plan `docs/phase-3-frontends-backoffice-plan.md` suivi,
écarts documentés ci-dessous)

- Livré, conformément au plan (T1-T8, T10 — **T9 reste hors repo, voir plus bas**) :
  - `apps/web` : socle applicatif (`lib/auth.ts` sur `GET /auth/me` seul, décision D1 ;
    `lib/api.ts` intercepte 401→redirection dure vers `/login`, 403→événement
    `api:forbidden` écouté par `AppLayout` qui affiche « accès refusé » ; router
    TanStack avec garde `beforeLoad` unique sur les routes protégées) ; écrans login
    (mot de passe + TOTP en 2 temps, jeton de challenge en mémoire de page uniquement,
    liens OAuth), acceptation d'invitation, sécurité du compte (TOTP : QR client via
    `qrcode.react`, codes de récupération affichés une fois) ; annuaire (membres,
    invitations en attente, équipes + composition) avec actions masquées selon le rôle
    (UX seulement, le serveur reste la seule autorité).
  - `apps/admin` : nouvelle SPA calquée sur `apps/web` (même stack de session, pas de
    notion de tenant/sous-domaine), pages `tenants.tsx` (catalogue, création avec URL
    d'invitation owner affichée, retry-provision) et `migrations.tsx` (déclenchement +
    rapport polls jusqu'à complétion).
  - `packages/ui` : `Button`, `Input`, `Label`, `FormField`, `Table`, `Badge`,
    `Dialog` (léger, sans Radix), `ToastProvider`/`useToast` — uniquement ce que les
    pages consomment.
  - `app/admin/` (backend) : router `/api/v1/admin/*` (tenants list/create/
    retry-provision, users lookup, migrations run/last-report) entièrement derrière
    `require_platform_admin` ; service réutilisant `provisioning.py`/
    `migrations_runner.py` tels quels ; tâche Celery `core.admin.run_migrations` +
    table `migration_reports` (control-plane 0003) pour le polling (décision D6) ;
    CLI `saas admin grant/revoke` (seul moyen de poser `is_platform_admin`, décision D5).
  - `infra/caddy/` : `Caddyfile` (public) bloque désormais `/api/v1/admin/*` (403) en
    plus de servir la SPA client ; nouveau `Caddyfile.admin` sert le back-office,
    lié à `127.0.0.1`/WireGuard uniquement en Compose (`docker-compose.yml` : service
    `admin`, même schéma que `caddy` — toujours une seule image api/worker).
  - CI (`ci.yml`, `staging-images.yml`) étendue à `apps/admin` (lint/tsc/vitest/build +
    image Docker) ; `scripts/deploy-pull.sh` suit désormais aussi `ADMIN_IMAGE`.
  - 9 nouveaux tests pytest (`test_admin_routes.py`, extension `test_cli.py`,
    `test_invitations.py`, `test_teams.py`) + 26 tests vitest (5 fichiers `apps/web`,
    3 fichiers `apps/admin`) ; **97 tests pytest et 26 tests vitest verts au total** ;
    pyright strict 0 erreur ; ruff/eslint 0 erreur ; client TS régénéré.

- **Écarts assumés par rapport au plan initial** (l'« état des lieux » d'un plan
  écrit en avance est une hypothèse à confronter au réel — ici, aux endpoints
  effectivement livrés par la Phase 2) :
  - Le plan supposait un endpoint de liste des invitations en attente et de composition
    d'équipe ; la Phase 2 ne les avait pas exposés (seuls create/revoke existaient).
    Ajoutés en Phase 3, cohérents avec les conventions existantes : `GET
    /directory/invitations` (jamais le token, juste les métadonnées — invariant n°5
    intact) et `GET /directory/teams/{id}/members` (jointure applicative control-plane
    × tenant, même pattern que `team_member_add`). Testés dans
    `test_invitations.py`/`test_teams.py`.
- **Pièges appris (à ne pas re-payer)** :
  - **Vite dev proxy et CSRF** : le shorthand `"/api": "http://localhost:8000"`
    réécrit le header `Host` vers la cible — casse le contrôle CSRF Origin-vs-Host
    (Phase 2) dès qu'on teste un sous-domaine tenant en local (`acme.localhost:5173`).
    Fix : `{ target, changeOrigin: false }` dans `vite.config.ts` (les deux apps) —
    Caddy en staging ne réécrit déjà pas le Host, donc ce n'était invisible qu'en dev.
  - **`@shared_task` et l'app Celery « courante »** : un `.delay()` appelé depuis le
    process API échouait (`Connection refused`) parce que `app/worker.py` (qui
    construit `Celery("worker", broker=...)`) n'était importé que par le process
    worker. Toute tâche déclenchée depuis l'API doit d'abord garantir cet import —
    fait dans `create_app()` (`app/main.py`). Premier cas réel où l'API dispatche un
    Celery task (Phase 2 n'avait que du beat, jamais déclenché depuis une requête HTTP).
  - **Formulaires react-hook-form + champ masqué conditionnellement** : un champ zod
    `.optional()` avec valeur par défaut `""` (pas `undefined`) échoue quand même sa
    contrainte `.min(n)` — piège sur `accept-invitation.tsx` (mot de passe masqué pour
    un compte existant). Pattern : `.optional().or(z.literal(""))`.
  - Vérifié en conditions quasi réelles (Postgres 16 local, Valkey/redis local, worker
    Celery local, deux serveurs Vite, navigateur Chromium piloté) : login mot de passe
    + TOTP, acceptation d'invitation, invite/revoke/changement de rôle/retrait membre,
    CRUD équipes + composition, activation/désactivation TOTP, 403→« accès refusé »,
    provisioning + invitation owner + retry + runner de migrations depuis le
    back-office. **Jamais testé** : `docker compose up` réel (pas de daemon Docker
    dans les sandboxes de session, comme documenté depuis la Phase 0).

## Reste à faire hors repo (bloquants pour la démo staging, Phase 3)

1. **T9 — Vérifications d'apps OAuth Google/Microsoft** (scopes connecteurs Gmail/
   Agenda/Graph) : **non lancées** — aucune action possible depuis une session Claude
   (comptes développeur externes). À faire manuellement dès que possible : voir
   plan Phase 3 §A/T9 pour le périmètre de scopes exact. Conditionne le démarrage
   réel de la Phase 5 avec de vrais clients.
2. Tout ce qui était déjà listé en Phase 0/1 (branche `main`, DNS wildcard, machine de
   staging) reste valable et bloque le déroulé du critère de démo Phase 3 en conditions
   réelles.

## Prochaine étape (pour la session suivante)

1. Faire fusionner la PR Phase 3 ; dérouler le critère de démo (section E du plan
   Phase 3 / README) dès que la machine de staging existe.
2. Lancer T9 (vérifications OAuth) dès que possible — hors repo, voir ci-dessus.
3. Ensuite : **Phase 4 — Audit + socle RGPD** (`docs/phase-4-audit-rgpd-plan.md`,
   déjà rédigé) — le faire valider par l'utilisateur (état des lieux à reconfronter
   au réel, Phase 3 vient de changer la surface front), puis implémenter. Les plans
   des phases 5 à 8 sont également rédigés, même méthode à chaque phase.
4. Toujours en attente côté staging : dérouler les critères de démo Phases 0-3 dès que
   la machine existe.

## Commandes utiles

```bash
make install && make dev        # démarrage local (voir README)
make lint typecheck test        # chaîne qualité complète
make generate-client            # après toute modif des routes API
docker compose config --quiet   # valider les fichiers compose
```

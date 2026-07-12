# Passation de session — état du projet et reprise

> Document destiné à une nouvelle session (Claude/Fable) pour continuer le travail
> sans re-dériver le contexte. Dernière mise à jour : 2026-07-12 (session
> « implémentation Phase 5 »).

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

### Phase 4 : IMPLÉMENTÉE ✅ (plan `docs/phase-4-audit-rgpd-plan.md` suivi, T1-T10)

- Livré, conformément au plan :
  - `app/audit/` : table tenant `audit_events` (append-only, migration tenant 0003) ;
    `record_audit_event`/`record_audit_event_for_tenant` (registre typé `ACTIONS`,
    décision D1 — même transaction quand l'action est tenant-only comme les équipes ;
    au pire un événement orphelin, jamais une action sans trace, pour les actions
    control-plane comme invitations/rôles, qui restent cross-database) ; instrumentation
    de `app/directory/router.py` (invitations créée/révoquée/acceptée, rôle modifié,
    membre retiré, équipe créée/supprimée, membre d'équipe ajouté/retiré) et de
    `app/tenancy/provisioning.py` (`core.tenant.provisioned`, acteur `cli`) ; route
    `GET /api/v1/audit/events` (curseur `(occurred_at, id)`, filtres, permission
    `core.audit.read` owner/admin only) ; page SPA `apps/web/src/pages/audit.tsx`
    (pagination infinie, filtre par action, détail du payload).
  - `app/gdpr/` : `export.py` (`pg_dump -Fc` + extrait control-plane JSON + manifeste,
    archive tar chiffrée `KeyProvider`, TTL `gdpr_export_ttl_days`) ; `erasure.py`
    (machine à deux temps D2 : `TenantState.PENDING_DELETION` + `deletion_requested_at`
    — `resolve_tenant` refuse comme `suspended` — puis `execute_pending_erasures` beat
    horaire : `DROP DATABASE` via `provisioning.drop_database_if_exists`, purge
    memberships/invitations, users orphelins supprimés (décision D6), `erasure_log`
    control-plane, `TenantEngineManager.invalidate`) ; `retention.py` (registre de
    politiques, `audit_events` en premier, surcharge `tenant_settings` clé
    `retention.<type>`, purge par lots de 5000 décision D7) ; `tasks.py` (dispatch
    export + 3 tâches beat : rétention quotidienne, effacements horaire, purge
    exports horaire — ajoutées à `app/worker.py` beat_schedule).
  - CLI (`app/cli.py`) : `tenant export`, `tenant delete` (confirmation par re-saisie
    du slug), `tenant cancel-delete`. Back-office (`app/admin/router.py`) : export
    (dispatch Celery + liste + téléchargement), request/cancel-erasure — SPA
    `apps/admin/src/pages/tenants.tsx` étendue (boutons RGPD, section exports).
  - Migrations : tenant 0003 (`audit_events`), controlplane 0004
    (`tenants.deletion_requested_at` + table `erasure_log`).
  - Docs non techniques versionnées : `docs/rgpd/{registre-traitements,
    sous-traitants,notification-violation}.md`, `docs/runbook-gdpr.md`.
  - 25 nouveaux tests pytest (`test_audit_events.py`, `test_gdpr_export.py` — dump
    réellement restauré via `pg_restore` dans une base jetable —, `test_gdpr_erasure.py`,
    `test_retention.py`, extension `test_cli.py`/`test_admin_routes.py`) ; **122 tests
    pytest** au total ; 4 nouveaux tests vitest (`audit.test.tsx`) ; pyright strict et
    ruff 0 erreur ; client TS régénéré.
- **Écart assumé** : deux tests pré-existants (`test_invitations.py`,
  `test_permissions.py::test_permission_matrix_over_http`) utilisaient
  `add_catalog_tenant` (tenant au catalogue sans base réelle provisionnée, raccourci
  pour des tests HTTP purs) sur des routes qui n'écrivaient jusqu'ici que dans le
  control-plane. Ces routes touchent désormais aussi la DB tenant (audit) : basculés
  sur `provision_tenant` (base réelle). Les tests qui ne franchissent jamais le point
  d'écriture de l'audit (règles métier refusées en amont, ex. dernier owner
  intouchable) continuent de fonctionner avec `add_catalog_tenant` — la session tenant
  SQLAlchemy est paresseuse (pas de connexion tant qu'aucune requête n'est exécutée).
- **Piège appris** : basculer entre deux `TestClient(create_app())` successifs (ou
  entre un `TestClient` et un appel direct dans la boucle pytest) exige
  `await reset_db_engines()` à CHAQUE bascule, y compris entre deux `with TestClient`
  consécutifs dans le même test (pas seulement pytest ↔ TestClient comme documenté
  jusqu'ici) — sinon `RuntimeError: ... attached to a different loop` sur les pools
  asyncpg.
- **Gap préexistant comblé** : aucun service `celery beat` n'existait dans
  `docker-compose.yml` depuis la Phase 2 (le `beat_schedule` était déclaré mais rien ne
  le lançait — la purge auth horaire ne tournait donc jamais en pratique). Corrigé ici
  avec un nouveau service `beat` (même image `api`/`worker`, commande `celery beat`,
  process séparé du `worker` par recommandation Celery) — nécessaire pour que les
  tâches de rétention/effacement/purge exports de cette phase tournent réellement.
- **Non vérifié** (comme toutes les phases précédentes, faute de daemon Docker en
  session) : `docker compose up` réel — en particulier le volume `gdpr_exports`, le
  nouveau service `beat`, et l'image `api`/`worker` avec `postgresql-client-17`
  (ajouté au `Dockerfile`, jamais testé en conteneur).

### Phase 5 : IMPLÉMENTÉE ✅ (plan `docs/phase-5-connecteurs-plan.md` suivi, T1-T10)

- Livré, conformément au plan :
  - `app/connectors/` : modèles tenant `connector_connections`/`connector_subscriptions`
    (tokens chiffrés `KeyProvider`, migration tenant 0004) + control-plane `webhook_routes`
    (routage seul, migration control-plane 0005, décision D6) ; registre de providers
    (`registry.py`, `ProviderManifest` figé) + manifests Google/Microsoft ; flux OAuth
    tiers (`oauth.py` : start sous tenant `core.connectors.manage`, callback anonyme sur
    l'apex reposant le contexte depuis le state signé, création `webhook_routes`, audit
    `connector.connected`) ; router de gestion (`router.py` : list `core.connectors.read`,
    revoke/reconsent `core.connectors.manage`) ; capabilities normalisées
    (`capabilities.py` : `MailCapability`/`CalendarCapability`, `EmailMessage`/
    `CalendarEvent`, `get_capability`) + `client_base.py` (refresh à la volée sous verrou,
    `anyio.to_thread` pour les SDK sync, enveloppe throttle) ; implémentations Google
    (`google-api-python-client`) et Microsoft (Graph REST via `httpx`) sous
    `providers/{google,microsoft}/{mail,calendar}.py` ; refresh proactif + renouvellement
    subscriptions + événements webhook (`tasks.py`, beats à 5 min et horaire dans
    `worker.py`) ; throttle/backoff + verrous Valkey (`throttle.py`) ; webhooks entrants
    (`webhooks.py` : validation providers, registre interne `on_connector_event` — D7).
  - Permissions `core.connectors.read` (tous rôles) / `core.connectors.manage`
    (owner/admin) ajoutées à `app/auth/permissions.py` ; actions d'audit `connector.*`
    ajoutées au registre `app/audit/service.py`. Deux routes anonymes ajoutées à la liste
    fermée (invariant n°9) : `connectors/{provider}/callback`, `webhooks/{provider}/{route_key}`.
  - `apps/web` : page `connectors.tsx` (liste, statuts/santé, connecter Google/Microsoft,
    re-consentement conditionnel, révocation avec confirmation) + route + entrée de nav ;
    5 tests vitest `connectors.test.tsx`. Client TS régénéré.
  - **33 nouveaux tests pytest** (`test_connector_throttle.py`, `test_connector_oauth.py`,
    `test_connector_capabilities.py`, `test_connector_refresh.py`, `test_connector_webhooks.py`,
    `test_connector_routes.py`, + helpers `tests/connector_helpers.py`) — **155 tests pytest
    au total** ; côté front 26 vitest web (dont 5 `connectors.test.tsx`) + 9 admin ;
    pyright strict et ruff 0 erreur. Faux providers locaux
    (`httpx.MockTransport` + manifests surchargés via `registry.override_provider`),
    fakeredis pour throttle/verrous — aucun test ne touche un vrai provider.
- **Écart au plan assumé (D2)** : `msal` n'a PAS été ajouté. L'échange/refresh OAuth des
  deux providers passe par `httpx` contre les endpoints du manifest (même mécanique que
  l'OIDC manuel Phase 2) — le cache de tokens mémoire de `msal` entrerait en conflit avec
  le store chiffré en DB et le verrou de refresh par connexion. Les appels Graph métier
  restent en `httpx` REST direct (pas de `msgraph-sdk`), conformes au plan. Dépendances
  ajoutées : `google-api-python-client`, `google-auth`, `anyio`.
- **Écart au plan assumé (Gmail webhooks)** : Gmail ne pousse que via Cloud Pub/Sub (pas
  de webhook HTTP direct) — seul le channel Google Calendar est implémenté côté Google.
  La capability mail Google fonctionne (list/get/send) mais sans notification entrante
  dans cette phase (documenté au README, risque n°4 du plan). Microsoft a ses deux
  subscriptions (Mail + Calendar) Graph.
- **Pièges re-payés** (déjà au handoff) : `await reset_db_engines()` à CHAQUE bascule
  boucle pytest ↔ TestClient, y compris avant de re-toucher la DB après un bloc
  `with TestClient` (sinon `RuntimeError: attached to a different loop`) ;
  `get_settings.cache_clear()` après tout `monkeypatch.setenv` d'une variable lue par un
  handler ; `TENANT_HEAD_REVISION` dans `conftest.py` bumpé à `0004_tenant_connectors`.
- **Piège nouveau** : pyright `reportUnusedFunction` flague les fixtures autouse au niveau
  module dont le nom commence par `_` (considérées privées) — les nommer sans underscore
  de tête (les fixtures de `conftest.py` échappent au flag pour cette raison).
- **Non vérifié** (comme toutes les phases, faute de daemon Docker en session et de vrais
  comptes providers) : `docker compose up` réel ; le critère de démo E (comptes de test
  Google Workspace + Microsoft 365, consentement réel, webhook déclenché depuis Internet).

## Prochaine étape (pour la session suivante)

1. Faire fusionner la PR Phase 5 ; dérouler le critère de démo (section E du plan Phase 5)
   dès que la machine de staging + les comptes de test providers existent — c'est la
   première surface entrante depuis Internet (webhooks) : vérifier Caddy et le HTTPS public.
2. **T9 Phase 3 — vérifications d'apps OAuth Google/Microsoft** (scopes connecteurs
   Gmail/Calendar/Graph) : toujours en attente, hors repo, désormais BLOQUANT pour de
   vrais clients (l'app Google est plafonnée à 100 utilisateurs de test sans validation).
   Créer aussi les apps OAuth connecteurs dédiées (distinctes du login, voir README).
3. Provisionner les comptes de test (hors repo) : un tenant Google Workspace + un tenant
   Microsoft 365 de dev, indispensables pour la démo E (les tests auto n'en dépendent pas).
4. Ensuite : **Phase 6 — AI Gateway multi-providers** (`docs/phase-6-ai-gateway-plan.md`,
   déjà rédigé) — le faire valider par l'utilisateur (état des lieux à reconfronter au
   réel), puis implémenter. Plans 7-8 également rédigés, même méthode.
5. Toujours en attente côté staging : dérouler les critères de démo Phases 0-5 dès que la
   machine existe.

## Commandes utiles

```bash
make install && make dev        # démarrage local (voir README)
make lint typecheck test        # chaîne qualité complète
make generate-client            # après toute modif des routes API
docker compose config --quiet   # valider les fichiers compose
```

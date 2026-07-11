# Phase 2 — Auth + annuaire : plan d'implémentation détaillé

> Référence : `docs/architecture-plan.md` (§4 « Authentification auto-hébergée et rôles »,
> §9 « Phase 2 »). Cette phase couvre : sessions serveur, mots de passe argon2, TOTP,
> OAuth login Google/Microsoft (Authlib), rôles/permissions, invitations, et le
> branchement session × membership dans `resolve_tenant` (TODO tracé en Phase 1).
> Rien de plus — le SSO entreprise par organisation (SAML/OIDC configurable) est
> explicitement reporté (§9, pilote en Phase 8) ; les écrans de login et la gestion
> d'équipe côté SPA arrivent en Phase 3 ; les connecteurs OAuth tiers en Phase 5.
> Chaque anticipation est signalée explicitement.

## État des lieux

Phase 1 fusionnée (PR #3) : control-plane opérationnel (`tenants`, `users` sans
credential, `memberships` à rôle texte libre), `TenantEngineManager`,
`get_tenant_session()` (seul chemin vers les DB tenant), deux arbres Alembic + runner
multi-bases, provisioning CLI. Deux TODO Phase 2 sont tracés dans le code :
`resolve_tenant` ne croise pas encore session × membership (`app/tenancy/deps.py`), et
le provisioning n'invite pas le premier owner (`app/tenancy/provisioning.py`).
**Aucun code d'auth n'existe** : pas de credential, pas de session, pas de route
publique hors `/api/v1/health`. Le client TS généré est donc quasi vide — cette phase
introduit les premières vraies routes.

---

## A. Tâches ordonnées

### T1 — Dépendances et configuration

Rôle : faire entrer les briques d'auth actées au plan global (§1, §4) et étendre la
config 12-factor.

| Fichier | Rôle |
|---|---|
| `apps/api/pyproject.toml` | Ajouts : `authlib`, `argon2-cffi`, `pyotp`, `cryptography` (KeyProvider, T2). `httpx` est déjà là (client OIDC async d'Authlib). |
| `apps/api/app/core/config.py` | Nouvelles settings : `session_ttl_hours` (déf. 168), `session_cookie_name` (déf. `saas_session`), `session_cookie_domain` (déf. vide = host-only en dev ; `.staging.<domaine>` en staging, décision D2), `auth_master_key` (clé AES-256 base64 — obligatoire hors dev, défaut factice en dev), `public_base_url` (URL apex publique, callbacks OAuth), `google_client_id`/`google_client_secret`, `microsoft_client_id`/`microsoft_client_secret`, `invitation_ttl_hours` (déf. 168), `smtp_host/port/user/password/sender` (optionnels, D8), `auth_rate_limit_attempts` (déf. 10) / `auth_rate_limit_window_seconds` (déf. 300). |
| `.env.example` | Variables ci-dessus avec valeurs factices. |

### T2 — Crypto minimale : `KeyProvider`

Rôle : anticipation actée du §1 (« AES-256-GCM en enveloppe derrière une interface
`KeyProvider` »). Nécessaire dès maintenant : les secrets TOTP ne peuvent pas être
stockés en clair (décision D4). Les connecteurs (Phase 5) réutiliseront cette interface.

| Fichier | Rôle |
|---|---|
| `apps/api/app/core/crypto.py` | Interface `KeyProvider` + implémentation `EnvKeyProvider` (clé maître via `auth_master_key`) ; `encrypt(bytes) -> bytes` / `decrypt(bytes) -> bytes` en AES-256-GCM (nonce aléatoire préfixé). Rien de plus : les clés par tenant et OpenBao/KMS restent branchables plus tard derrière la même interface. |

### T3 — Modèles auth control-plane + migration

Rôle : les données d'auth/sessions du control-plane (§3), complétant `users`/`memberships`
nés en Phase 1.

| Fichier | Rôle |
|---|---|
| `apps/api/app/auth/models.py` | Tables control-plane : `user_credentials` (user_id PK/FK, `password_hash` nullable — null si compte OAuth-only, `totp_secret` chiffré nullable, `totp_enabled` bool, `totp_last_counter` anti-rejeu, `recovery_codes` hachés) ; `sessions` (id UUID, user_id, `token_hash` sha256 unique — jamais le token en clair, `created_at`, `expires_at`, `last_seen_at`, `revoked_at`) ; `oauth_identities` (provider enum `google/microsoft`, `subject`, user_id, unicité (provider, subject)) ; `invitations` (id UUID, `email`, tenant_id, `role`, `token_hash` unique, `expires_at`, `accepted_at`, `invited_by_user_id` nullable). |
| `apps/api/app/directory/models.py` | `users` : ajout `display_name` nullable et `is_platform_admin` bool déf. false (rôle plateforme §4 — aucun usage public avant le back-office Phase 3). |
| `apps/api/migrations/controlplane/versions/…` | Révision 0002 (autogenerate) pour l'ensemble. |

### T4 — Sessions + login mot de passe (module `app/auth/`)

Rôle : le cœur du §4 — sessions serveur en DB control-plane, cookie httpOnly, révocables.

| Fichier | Rôle |
|---|---|
| `apps/api/app/auth/service.py` | argon2id via `argon2-cffi` (défauts de la lib, re-hash transparent si paramètres obsolètes) ; création de session : token opaque 256 bits aléatoire, **seul le hash sha256 en DB** ; validation (expiration absolue `session_ttl_hours`, `last_seen_at` rafraîchi) ; révocation unitaire (logout) et globale (tous les appareils). |
| `apps/api/app/auth/deps.py` | `current_user` : cookie → session valide → user (401 sinon) ; variante optionnelle. |
| `apps/api/app/auth/router.py` | `POST /api/v1/auth/login` (email+password ; si TOTP activé → réponse `totp_required` + jeton de login partiel court (5 min, même mécanique hachée) à échanger avec le code), `POST /api/v1/auth/login/totp`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`. Réponses d'échec indistinctes (pas d'oracle « email inconnu » vs « mauvais mot de passe »). |
| `apps/api/app/core/logging.py` ou middleware dédié | Contrôle CSRF : sur toute méthode mutante, l'`Origin` (ou `Referer`) doit appartenir au domaine de l'app (décision D7). |

Cookie : `httpOnly`, `Secure` (hors dev), `SameSite=Lax`, domaine `session_cookie_domain`
(D2). Nouveau token à chaque login (anti-fixation).

### T5 — TOTP + codes de récupération

| Fichier | Rôle |
|---|---|
| `apps/api/app/auth/router.py` (suite) | `POST /api/v1/auth/totp/setup` (génère le secret — chiffré via KeyProvider — et retourne l'URI `otpauth://`), `POST /api/v1/auth/totp/activate` (vérifie un premier code, active, génère 8 codes de récupération retournés **une seule fois** et stockés hachés), `POST /api/v1/auth/totp/disable` (mot de passe requis). Vérification pyotp fenêtre ±1, anti-rejeu par mémorisation du dernier compteur accepté ; un code de récupération consommé est invalidé. |

### T6 — OAuth login Google & Microsoft (Authlib)

Rôle : le login social du §4 — **login uniquement**, distinct des connecteurs Phase 5.

| Fichier | Rôle |
|---|---|
| `apps/api/app/auth/oauth.py` | Registre Authlib : deux clients OIDC déclarés par `server_metadata_url` (découverte Google / Microsoft `common`), scopes `openid email profile` uniquement. |
| `apps/api/app/auth/router.py` (suite) | `GET /api/v1/auth/oauth/{provider}/start` et `GET /api/v1/auth/oauth/{provider}/callback`, servis sur le domaine apex (`public_base_url` — un seul redirect URI par provider) ; le `state` signé transporte le sous-domaine de retour. À la callback : `id_token` vérifié → `email_verified` exigé → **l'utilisateur doit déjà exister** (inscription publique désactivée, décision D5) → liaison `oauth_identities` créée au premier login (correspondance par email vérifié), puis lookup par (provider, subject) → session posée → redirect vers le sous-domaine d'origine. Aucune création de compte à la volée. |

### T7 — RBAC et branchement tenant

Rôle : le RBAC du §4 et la levée du TODO Phase 1 dans `resolve_tenant`.

| Fichier | Rôle |
|---|---|
| `apps/api/app/auth/permissions.py` | Registre des permissions namespacées `core.*` (ex. `core.members.read`, `core.members.manage`, `core.teams.manage`, `core.tenant.settings`) ; rôles intégrés `owner`/`admin`/`member` → ensembles de permissions **définis en code** (décision D6 ; la colonne texte de `memberships` garde la porte ouverte aux rôles custom). `require_permission("core.x.y")` : LA dépendance unique du §4, composée de `resolve_tenant` + `current_user` + membership. |
| `apps/api/app/tenancy/deps.py` | `resolve_tenant` croise désormais session × membership (TODO levé) : non authentifié → 401 ; authentifié non membre → 403 (le 404 « tenant inconnu » reste inchangé) ; le contexte s'enrichit du rôle. `memberships.role` est validé contre les rôles connus. |
| `apps/api/app/auth/deps.py` (suite) | `require_platform_admin` : existe et est testée, mais **aucune route publique ne l'expose** (back-office Phase 3) — même logique que D5 Phase 1. |

### T8 — Invitations + annuaire + équipes

Rôle : « uniquement invitations » (§4) + les premières vraies routes métier, dont la
première route qui traverse `get_tenant_session()` en HTTP réel.

| Fichier | Rôle |
|---|---|
| `apps/api/app/directory/service.py` | Invitations : token opaque haché, TTL `invitation_ttl_hours`, usage unique ; création (email + rôle), renvoi, révocation. Règles de rôle : seul un `owner` promeut/rétrograde un `owner` ; le dernier `owner` d'un tenant est intouchable (ni rétrogradé ni retiré). Retrait de membre = suppression du membership (la session globale survit, D2). |
| `apps/api/app/directory/router.py` | Sous tenant (permissions entre parenthèses) : `GET /api/v1/directory/members` (`core.members.read`), `POST /api/v1/directory/invitations` (`core.members.manage`), `DELETE /api/v1/directory/invitations/{id}`, `PATCH /api/v1/directory/members/{user_id}` (rôle), `DELETE /api/v1/directory/members/{user_id}`. Hors tenant : `POST /api/v1/auth/invitations/accept` (token → crée le user s'il n'existe pas + mot de passe initial, ou rattache l'existant ; crée le membership ; invalide l'invitation). La réponse de création d'invitation contient **toujours** l'URL d'acceptation (D8). |
| `apps/api/app/directory/tenant_models.py` + révision tenant 0002 | Équipes en **DB tenant** (données du client, §3) : `teams` (id, name, description), `team_members` (team_id, user_id — référence l'UUID control-plane sans FK inter-bases, unicité (team, user)). CRUD minimal dans `router.py` (`core.teams.manage`) — c'est la preuve vivante de la pile Phase 1 en HTTP. |
| `apps/api/app/core/mailer.py` | Interface `Mailer` + backend SMTP (si `smtp_host` configuré) + backend no-op en dev. Jamais d'adresse email dans les logs (invariant racine n°4) — l'observabilité de l'envoi passe par un événement `invitation_sent` avec user_id/tenant. |
| `apps/api/app/tenancy/provisioning.py` + `app/cli.py` | TODO Phase 1 levé : `saas tenant create <slug> --name … --owner-email …` crée l'invitation `owner` en fin de provisioning et **affiche l'URL d'acceptation en sortie CLI**. Nouvelle commande `saas invitation create <slug> <email> --role`. |

### T9 — Rate limiting + tâches périodiques

| Fichier | Rôle |
|---|---|
| `apps/api/app/auth/rate_limit.py` | Limiteur fenêtre fixe sur Valkey (déjà dans la stack), par IP **et** par email cible : login, login/totp, invitations/accept → 429 au-delà de `auth_rate_limit_attempts` (décision D9). |
| `apps/api/app/auth/tasks.py` | Tâches Celery beat : purge des sessions expirées/révoquées et des invitations expirées (namespace `core.auth.*`, importées par `app/worker.py`). |

### T10 — Contrat, CI et clôture

- `make generate-client` : premières vraies routes → le client TS change réellement pour
  la première fois ; `operation_id` explicites partout (convention `apps/api/CLAUDE.md`).
- `README.md` : section auth (flux invitation → login → TOTP, création des apps OAuth
  Google/Microsoft, variables d'env) ; `.env.example` complet.
- `CLAUDE.md` racine + `apps/api/CLAUDE.md` : module `auth`, règle « **aucune route métier
  sans `require_permission`** » (les seules routes anonymes sont listées : health, login,
  oauth start/callback, invitations/accept), phase courante mise à jour.
- CI inchangée dans sa structure (le service Postgres existe depuis la Phase 1) ; le job
  contrat validera le nouveau client TS.
- Critère de démo (section E) déroulé et vérifié.

---

## B. Points de conception — décisions et recommandations

| # | Question | Recommandation | Justification |
|---|---|---|---|
| D1 | Sessions : DB control-plane, Valkey ou JWT ? | **DB control-plane, token opaque haché** | C'est ce qu'acte le §4 (« sessions serveur en DB, révocables ») ; une seule source de vérité, révocation triviale, pas d'état signé impossible à invalider (JWT). Valkey en cache de lecture = optimisation ultérieure sans changer le contrat. |
| D2 | Périmètre du cookie de session ? | **Domaine parent (`.staging.<domaine>`)** | Les identités sont globales (§3) : un login vaut pour tous les tenants dont on est membre, `resolve_tenant` filtre par membership. Un cookie par sous-domaine imposerait un re-login par tenant et compliquerait la callback OAuth. Coût assumé : logout global, et tous les sous-domaines sont de confiance (ils le sont : même app). |
| D3 | Hachage des mots de passe ? | **argon2id, défauts `argon2-cffi`, re-hash transparent** | Choix acté au plan global (§1) ; les défauts de la lib suivent les recommandations courantes ; le re-hash au login absorbe les évolutions de paramètres sans migration. |
| D4 | Secrets TOTP : clair, ou chiffrés dès maintenant ? | **Chiffrés — introduire `KeyProvider` (T2) maintenant** | Le §1 acte l'enveloppe AES-256-GCM derrière une interface ; un secret TOTP en clair en DB contredirait l'invariant « aucun secret en base ». Coût : ~60 lignes ; bénéfice : l'interface existe pour les tokens connecteurs (Phase 5). |
| D5 | OAuth login : créer les comptes à la volée ? | **Non — invitation obligatoire, liaison par email vérifié** | « Inscription publique désactivée : uniquement invitations » (§4). La callback lie l'identité OAuth à un user existant (email vérifié identique), puis les logins suivants passent par (provider, subject) — insensible aux changements d'email chez le provider. |
| D6 | Rôles : table de rôles custom ou rôles en code ? | **`owner`/`admin`/`member` définis en code** | Le §4 prévoit les rôles custom mais rien ne les consomme avant les modules métier (Phase 7). Les ensembles de permissions en code sont typés, testables et lisibles ; `memberships.role` (texte, Phase 1) permettra d'ajouter une table de rôles custom sans migration destructive. |
| D7 | Protection CSRF ? | **SameSite=Lax + vérification `Origin` sur les mutations** | SPA même site + cookie SameSite=Lax couvrent l'essentiel ; le contrôle d'Origin ferme le reste. Un token synchronizer ajouterait de l'état et du code front pour un gain nul dans cette topologie. |
| D8 | Envoi des invitations : email obligatoire ? | **L'URL d'acceptation est toujours retournée à l'appelant autorisé ; SMTP optionnel** | La délivrabilité email est le risque §8.4 (relais français à provisionner, hors repo). En attendant : l'admin qui invite transmet le lien lui-même — zéro dépendance bloquante, onboarding manuel assumé. Le backend SMTP s'active par simple configuration. |
| D9 | Rate limiting : lib dédiée ou compteurs Valkey ? | **Compteurs fenêtre fixe sur Valkey, maison** | Une dizaine de lignes, Valkey est déjà là, et seuls 3 endpoints sensibles en ont besoin. Le rate limiting global reste en Phase 8 comme prévu (§9). |

---

## C. Invariants et règles absolues de la phase

1. **Aucune route métier sans `require_permission`** (donc sans auth + membership) ; les
   routes anonymes sont une liste fermée et documentée : health, login (+TOTP), OAuth
   start/callback, acceptation d'invitation.
2. **Aucun secret en clair en base** : mots de passe en argon2id ; tokens de session,
   d'invitation et de login partiel **hachés** (sha256) ; secrets TOTP et à venir
   **chiffrés** via `KeyProvider`. Un token n'apparaît qu'une fois, dans la réponse à
   son créateur.
3. **Jamais d'email ni de credential dans les logs** (l'invariant racine n°4 devient
   critique : l'auth manipule des PII) — les logs d'auth référencent `user_id`/`tenant`.
4. **Cookies de session httpOnly + Secure + SameSite=Lax** ; aucun token d'auth en
   localStorage, en query string ou dans le client TS (le token d'invitation en URL est
   l'unique exception : usage unique, haché, expirant).
5. **Inscription publique désactivée** : tout compte naît d'une invitation ; l'OAuth
   login ne crée jamais de compte.
6. **`resolve_tenant` exige un membership actif** — l'invariant racine n°1 est désormais
   complet : contexte tenant = sous-domaine × session × membership.
7. Les invariants Phases 0/1 restent en vigueur (image unique, config par env, deux
   MetaData, Alembic seul, `get_tenant_session()` seul chemin tenant, client TS généré).

---

## D. Tests à écrire

**Backend (pytest, Postgres réel — `apps/api/tests/`)**
- `test_crypto.py` : chiffrer/déchiffrer ; clé invalide → erreur explicite ; deux
  chiffrements du même clair diffèrent (nonce).
- `test_auth_sessions.py` : login ok → cookie posé, session en DB (hash ≠ token) ;
  mauvais mot de passe / email inconnu → même réponse 401 ; expiration → 401 ;
  logout → session révoquée ; re-hash transparent d'un hash à paramètres affaiblis.
- `test_totp.py` : setup → activate (mauvais code refusé) → login en deux temps ;
  anti-rejeu (même code deux fois → refus) ; code de récupération à usage unique ;
  disable exige le mot de passe.
- `test_oauth_login.py` : callback avec id_token forgé pour les tests (métadonnées OIDC
  et JWKS servis par un faux provider local) → session posée pour un user invité ;
  email non vérifié → refus ; email inconnu du système → refus sans création ;
  `state` altéré → 400 ; deuxième login retrouve l'identité par (provider, subject).
- `test_permissions.py` : matrice rôle × permission (owner/admin/member) ; non
  authentifié → 401 ; membre d'un autre tenant → 403 ; dernier owner intouchable ;
  `require_platform_admin` testée sans route publique.
- `test_resolve_tenant.py` (extension Phase 1) : membership branchée — authentifié
  non membre → 403 ; membre → contexte posé avec rôle.
- `test_invitations.py` : cycle complet (create → accept → membership) ; token expiré,
  déjà consommé, inconnu → refus ; acceptation par user existant vs nouveau ;
  invitation owner générée par le provisioning (`--owner-email`).
- `test_teams.py` : CRUD via HTTP sur la DB tenant (preuve `get_tenant_session` bout en
  bout) ; sans permission → 403.
- `test_rate_limit.py` : dépassement du seuil → 429, reset après la fenêtre (Valkey
  fake ou instance de test).
- `test_cli.py` (extension) : `tenant create --owner-email` affiche l'URL ;
  `invitation create`.

**CI** : structure inchangée ; le job contrat valide le nouveau client TS (premières
routes réelles), les jobs front/images sont inchangés.

---

## E. Critère de démo de fin de phase

> En local (Compose) ou staging, au curl + docs FastAPI (la SPA arrive en Phase 3) :
> `saas tenant create acme --owner-email alice@example.com` affiche une URL
> d'invitation ; Alice l'accepte (mot de passe), se connecte sur `acme.<domaine>`,
> active le TOTP et doit ensuite fournir son code au login. Elle invite
> bob@example.com en `member` ; Bob accepte, voit `GET /api/v1/directory/members`
> mais reçoit 403 sur la création d'invitation. Bob se connecte aussi via
> « Se connecter avec Google » (compte lié à son email invité). Alice crée une équipe
> (donnée écrite en DB tenant via `get_tenant_session`). Sur `globex.<domaine>`
> (dont ils ne sont pas membres), Alice et Bob reçoivent 403. Un logout révoque la
> session en DB. Dans Loki : tout le flux corrélé par `request_id`, **aucun email ni
> token en clair**.

C'est la traduction exécutable du §4 : auth complète, invitations seules portes
d'entrée, RBAC vérifié par une dépendance unique, contexte tenant complet.

---

## F. Dépendances manquantes et risques propres à la phase

1. **Pas de SPA avant la Phase 3** : la démo se fait au curl/docs FastAPI — assumé, la
   Phase 3 consommera le client TS généré ici (raison de plus pour soigner les
   `operation_id` et les schémas Pydantic).
2. **Apps OAuth Google/Microsoft à créer** (hors repo) : client_id/secret + redirect
   URI en HTTPS sur le domaine réel. Les scopes de login (`openid email profile`) ne
   déclenchent pas les vérifications lourdes (§8.3 — celles-ci concernent les scopes
   sensibles des connecteurs, à lancer en Phase 3 comme prévu) ; en dev, les tests
   passent par un faux provider OIDC local, sans compte réel.
3. **Cookie parent-domain et OAuth exigent le domaine + wildcard DNS** — déjà bloquant
   listé au handoff (hors repo), rien de nouveau mais la Phase 2 en dépend pour sa
   démo staging.
4. **Délivrabilité email non résolue** (§8.4) : neutralisée par D8 (URL toujours
   retournée) ; le relais SMTP français reste à provisionner avant d'activer l'envoi
   réel.
5. **La branche `main` n'existe toujours pas** (hérité Phases 0/1) : rien ne se déploie
   sur staging tant que l'action admin GitHub n'est pas faite.
6. **Logout global** (conséquence D2) : une session unique pour tous les tenants d'un
   user — comportement à documenter côté produit, pas un défaut technique.

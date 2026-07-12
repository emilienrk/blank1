# Phase 5 — Framework de connecteurs externes : plan d'implémentation détaillé

> Référence : `docs/architecture-plan.md` (§5 « Framework de connecteurs externes »,
> §9 « Phase 5 » — « le plus gros morceau »). Cette phase couvre : l'abstraction
> Provider / Connection / Capability, le token store chiffré en DB tenant, les flux
> OAuth tiers (distincts du login), le refresh proactif Celery, les webhooks entrants
> et le renouvellement des subscriptions, le rate limiting par provider, et les deux
> premières implémentations — **Google Workspace** et **Microsoft 365**, capabilities
> **Mail** et **Calendar**. Rien de plus — Contacts/Files sont des capabilities
> ultérieures (à la demande des modules), la consommation des capabilities par un
> module métier est la démo de la Phase 7. Chaque anticipation est signalée
> explicitement.

## État des lieux (attendu en entrée de phase)

Hypothèses : Phases 0-4 fusionnées. Points d'appui directs : `KeyProvider`
(AES-256-GCM, prévu pour ces tokens dès la Phase 2), `record_audit_event()` (Phase 4 —
l'audit existe avant que des données clients ne circulent, comme voulu), OAuth login
Authlib en OIDC manuel avec state signé (mécanique réutilisable), Celery beat en place
(purges), SPA + `packages/ui` (Phase 3). Les **vérifications d'apps OAuth** pour les
scopes sensibles ont été lancées en Phase 3 (T9) — leur statut conditionne l'ouverture
à de vrais comptes, pas le développement (comptes de test). `app/connectors/` n'existe
pas.

---

## A. Tâches ordonnées

### T1 — Dépendances, configuration, modèles

| Fichier | Rôle |
|---|---|
| `apps/api/pyproject.toml` | Ajouts : `google-api-python-client` + `google-auth`, `msal` (acquisition de tokens Microsoft). Décision D2 : **pas** de `msgraph-sdk` — appels Graph via `httpx` déjà présent. |
| `apps/api/app/core/config.py` | Extensions : `google_connector_client_id/secret`, `microsoft_connector_client_id/secret` (apps OAuth **distinctes** du login, décision D3), `connector_refresh_lead_minutes` (déf. 10), `connector_webhook_base_url` (déf. = `public_base_url`). |
| `apps/api/app/connectors/tenant_models.py` | DB **tenant** (§3 : « connexions OAuth tierces en DB tenant ») : `connector_connections` (id UUID, `provider` enum `google/microsoft`, `kind` enum `tenant/user`, `user_id` UUID nullable — propriétaire si `kind=user`, `account_label` — adresse du compte connecté, affichage, `scopes` liste, `access_token_enc`/`refresh_token_enc` chiffrés KeyProvider, `access_token_expires_at`, `status` enum `active/needs_reconsent/revoked/error`, `last_error` résumé technique, `health_checked_at`, timestamps) ; `connector_subscriptions` (id, connection_id FK, `capability`, `provider_subscription_id`, `resource`, `expires_at`, `client_state` secret aléatoire haché). |
| `apps/api/app/connectors/models.py` | DB **control-plane**, routage uniquement (décision D6) : `webhook_routes` (`route_key` opaque unique — figure dans l'URL de webhook, tenant_id, connection_id ; **aucune donnée métier ni token**). |
| Migrations | Révision tenant 000N (`connector_connections`, `connector_subscriptions`), control-plane 000N (`webhook_routes`). |

### T2 — Registre de providers et manifests

| Fichier | Rôle |
|---|---|
| `apps/api/app/connectors/registry.py` | `ProviderManifest` (nom, scopes par capability, endpoints OAuth, capabilities supportées) + registre en code (dict figé — pas de plugin dynamique). `get_provider(name)` typé. |
| `apps/api/app/connectors/providers/google/manifest.py`, `…/microsoft/manifest.py` | Les deux manifests : scopes mail/calendar (Gmail : `gmail.readonly` + `gmail.send` ; Calendar : `calendar` ; Graph : `Mail.Read`, `Mail.Send`, `Calendars.ReadWrite`, `offline_access`), URLs d'autorisation/token, spécificités webhooks. |

### T3 — Flux OAuth tiers (connexion d'un compte)

Rôle : « flux OAuth tiers distincts du login » (§5) — même mécanique de state signé
que la Phase 2, apps et scopes différents.

| Fichier | Rôle |
|---|---|
| `apps/api/app/connectors/oauth.py` | `GET /api/v1/connectors/{provider}/start` (`core.connectors.manage`, sous tenant) : state signé HMAC portant tenant, user, kind, capabilities demandées → URL d'autorisation (avec `access_type=offline`/`prompt=consent` chez Google pour garantir le refresh token) ; `GET /api/v1/connectors/{provider}/callback` (route anonyme sur l'apex — liste fermée mise à jour, invariant n°9) : échange code → tokens, chiffrement KeyProvider, création/mise à jour de `connector_connections` en DB tenant (contexte reposé depuis le state), création du `webhook_routes` control-plane, audit `connector.connected`, redirect vers la page connecteurs du sous-domaine. |
| `apps/api/app/connectors/router.py` | Gestion : `GET /api/v1/connectors` (`core.connectors.read` — liste, statuts, santé), `DELETE /api/v1/connectors/{id}` (`core.connectors.manage` — révocation : appel best-effort de révocation chez le provider, suppression des tokens, statut `revoked`, audit), `POST /api/v1/connectors/{id}/reconsent` (relance un flux OAuth sur une connexion `needs_reconsent`). Permissions ajoutées à `permissions.py` (`core.connectors.read` → tous rôles, `manage` → owner/admin). |

### T4 — Capabilities : interfaces et modèles normalisés

Rôle : « les modules consomment les capabilities, jamais les APIs propriétaires » (§5).

| Fichier | Rôle |
|---|---|
| `apps/api/app/connectors/capabilities.py` | Protocols Python typés : `MailCapability` (`list_messages(since, folder)`, `get_message(id)`, `send_message(draft)`), `CalendarCapability` (`list_events(window)`, `create_event(event)`) ; modèles Pydantic normalisés `EmailMessage`, `EmailDraft`, `CalendarEvent` (champs communs uniquement + `provider_raw_id`). `get_capability(connection, MailCapability)` → implémentation du provider ou erreur explicite si non supportée. |
| `apps/api/app/connectors/client_base.py` | Mécanique commune aux implémentations : construction du client authentifié depuis une connexion (déchiffrement, refresh à la volée si expiré — verrou D5), enveloppe rate-limit/backoff (T7), exécution des SDK synchrones hors event loop (`anyio.to_thread`, décision D2). |

### T5 — Implémentations Google Workspace et Microsoft 365

| Fichier | Rôle |
|---|---|
| `apps/api/app/connectors/providers/google/{mail,calendar}.py` | `MailCapability`/`CalendarCapability` sur `google-api-python-client` (Gmail API, Calendar API) ; mapping vers les modèles normalisés ; pagination absorbée. |
| `apps/api/app/connectors/providers/microsoft/{mail,calendar}.py` | Idem sur Microsoft Graph via `httpx` (+ `msal` pour les tokens) ; deux APIs très différentes = validation de l'abstraction (§5). |

### T6 — Refresh proactif des tokens (Celery)

| Fichier | Rôle |
|---|---|
| `apps/api/app/connectors/tasks.py` | Beat toutes les 5 min : itère les tenants `active`, pose le contexte, sélectionne les connexions expirant sous `connector_refresh_lead_minutes` → tâche de refresh unitaire avec **verrou Valkey par connexion** (D5). Refresh réussi → tokens re-chiffrés, santé OK ; échec récupérable → retry backoff ; `invalid_grant`/révocation → statut `needs_reconsent` + audit `connector.reconsent_required` (le « re-consentement guidé » du §5 : la SPA affiche l'action). |

### T7 — Rate limiting par provider + backoff

| Fichier | Rôle |
|---|---|
| `apps/api/app/connectors/throttle.py` | Compteurs Valkey par (provider, connexion) alignés sur les quotas publics (réutilise la brique fenêtre fixe de la Phase 2, généralisée) ; enveloppe commune : respect de `Retry-After`, backoff exponentiel + jitter sur 429/5xx, plafond de tentatives → erreur typée `ProviderUnavailable`. Appels lourds (listings volumineux) exécutés via Celery, jamais dans une requête HTTP (§5). |

### T8 — Webhooks entrants + renouvellement des subscriptions

| Fichier | Rôle |
|---|---|
| `apps/api/app/connectors/webhooks.py` | `POST /api/v1/webhooks/{provider}/{route_key}` (route anonyme, liste fermée) : résolution `route_key` → `webhook_routes` (control-plane, D6) ; validation d'origine (Microsoft : echo `validationToken` + `clientState` comparé au hash ; Google : en-têtes de channel comparés) ; **traitement minimal** : accusé immédiat, publication d'une tâche Celery `connector_event_received` qui pose le contexte tenant et normalise l'événement (décision D7 : dans cette phase, l'événement normalisé est journalisé + audité — les consommateurs réels arrivent en Phase 7 via un hook d'abonnement interne minimal). |
| `apps/api/app/connectors/tasks.py` (suite) | Beat de renouvellement : subscriptions Microsoft Graph (~3 j) et channels Google (~7 j) renouvelés avant `expires_at` (même pattern verrou + rapport que le refresh) ; échec définitif → santé dégradée + audit. |

### T9 — Page SPA connecteurs

| Fichier | Rôle |
|---|---|
| `apps/web/src/pages/connectors.tsx` | Liste des connexions (provider, compte, statut santé, dernières erreurs résumées) ; « Connecter Google / Microsoft » (start OAuth) ; bouton re-consentement quand `needs_reconsent` ; révocation avec confirmation. |

### T10 — Contrat, CI et clôture

- `make generate-client` ; `operation_id` explicites.
- `README.md` : création des apps OAuth connecteurs (redirect URIs, scopes, état des
  vérifications), variables d'env, exposition publique du endpoint webhooks.
- `CLAUDE.md` racine + `apps/api/CLAUDE.md` : module `connectors`, règles « aucun token
  provider en clair nulle part » et « les modules ne touchent jamais les APIs
  propriétaires », phase courante mise à jour.
- Critère de démo (section E) déroulé et vérifié.

---

## B. Points de conception — décisions et recommandations

| # | Question | Recommandation | Justification |
|---|---|---|---|
| D1 | Où vivent les tokens ? | **DB tenant, chiffrés KeyProvider (acté §3/§5)** | Donnée du client : export/effacement RGPD (Phase 4) les couvrent gratuitement. Le KeyProvider existe pour ça depuis la Phase 2. Le control-plane ne voit jamais un token. |
| D2 | SDK Microsoft : `msgraph-sdk` ou `httpx` + `msal` ? | **`httpx` + `msal`, appels Graph REST directs** | Le `msgraph-sdk` Python est récent, verbeux et mal connu des IA (critère §1) ; l'API REST Graph est massivement documentée et on ne consomme que quelques endpoints normalisés par capability. `msal` (mûr) gère l'acquisition/refresh des tokens. Côté Google, `google-api-python-client` est acté au plan global (§5). |
| D3 | Apps OAuth : réutiliser celles du login ? | **Non — apps dédiées connecteurs** | Les scopes sensibles (Gmail, Calendars) déclencheraient les vérifications lourdes sur l'app de login et mêleraient deux cycles de vie (un retrait de consentement connecteur ne doit pas toucher le login). Les vérifications lancées en Phase 3 portent sur ces apps dédiées. |
| D4 | Exécution des SDK synchrones dans une app async ? | **`anyio.to_thread` pour les appels courts, Celery pour les lourds** | `google-api-python-client` est synchrone : l'appeler dans l'event loop bloquerait tout uvicorn. Le pattern threadpool est standard et invisible derrière `client_base` ; les listings volumineux passent en tâche (§5 « appels lourds via Celery »). |
| D5 | Concurrence sur le refresh d'un token ? | **Verrou Valkey par connexion (SET NX + TTL)** | Google invalide les anciens refresh tokens dans certains flux et Microsoft peut faire tourner le refresh token à chaque échange : deux refresh concurrents peuvent se voler la validité. Le verrou par connexion (acté §5) sérialise refresh périodique et refresh à la volée. |
| D6 | Résolution du tenant à la réception d'un webhook ? | **Table de routage control-plane `webhook_routes` (route_key opaque en URL)** | Le webhook arrive sur l'apex sans sous-domaine ni session : il faut retrouver le tenant **avant** de pouvoir ouvrir sa DB — impossible si le mapping est en DB tenant. La table ne contient que du routage (ids), aucune donnée métier : compatible §3. Le `route_key` opaque évite d'exposer des ids internes ; le `client_state` haché authentifie le contenu. |
| D7 | Que fait-on des événements webhook dans cette phase ? | **Normalisation + journal + audit, consommateurs en Phase 7** | Le framework doit prouver la chaîne complète (réception validée → tâche → contexte tenant) sans inventer un bus d'événements spéculatif. Un registre interne minimal `on_connector_event(capability, handler)` suffit — le runtime d'automatisation (Phase 7) sera son premier client réel. |
| D8 | Santé des connexions : polling dédié ? | **Pas de health-check actif — la santé dérive des opérations réelles** | Refresh périodique + appels réels + renouvellements de subscriptions touchent déjà chaque connexion régulièrement : leurs succès/échecs alimentent `status`/`health_checked_at`/`last_error`. Un ping dédié consommerait du quota provider pour une information déjà disponible. |
| D9 | Révocation côté provider lors du DELETE ? | **Best-effort, jamais bloquant** | L'utilisateur peut avoir déjà révoqué côté Google/Microsoft (endpoint en erreur) : la suppression locale des tokens doit aboutir quoi qu'il arrive — c'est elle qui protège. L'échec de révocation distante est loggé, pas propagé. |

---

## C. Invariants et règles absolues de la phase

1. **Aucun token provider en clair, nulle part** : chiffré KeyProvider en DB tenant,
   jamais en control-plane, jamais dans les logs (même tronqué), jamais dans une
   réponse API (la SPA ne voit que statuts et labels).
2. **Les modules et le reste du code ne consomment que les capabilities** — tout accès
   direct aux APIs Google/Graph hors `app/connectors/providers/` est une violation.
3. **Toute réception webhook est authentifiée** (validation provider + `client_state`
   haché) avant toute action ; un webhook invalide répond 2xx neutre sans traitement
   ni log verbeux (pas d'oracle sur l'existence des routes).
4. **Les routes anonymes restent une liste fermée** : + `connectors/{provider}/callback`
   et `webhooks/{provider}/{route_key}` — documentées dans `CLAUDE.md` (invariant n°9).
5. **Tout appel provider passe par l'enveloppe throttle/backoff** ; aucun appel lourd
   dans le cycle requête/réponse HTTP.
6. **Cycle de vie audité** : connexion, re-consentement requis, révocation, échec de
   renouvellement → `record_audit_event` (règle Phase 4 appliquée aux connecteurs).
7. Les invariants Phases 0-4 restent en vigueur (contexte tenant obligatoire — les
   tâches le posent explicitement, `require_permission`, client TS généré, image
   unique).

---

## D. Tests à écrire

**Backend (pytest, Postgres réel + `respx`/serveurs de test locaux pour les providers —
`apps/api/tests/`)**
- `test_connector_oauth.py` : start → URL avec scopes du manifest et state signé ;
  callback → tokens chiffrés en DB tenant (valeur en base ≠ token), `webhook_routes`
  créé, audit émis ; state altéré → 400 ; callback sans refresh token (Google sans
  `prompt=consent`) → erreur explicite.
- `test_capabilities.py` : `get_capability` retourne l'implémentation du bon provider ;
  capability non supportée → erreur typée ; mapping Gmail → `EmailMessage` et Graph →
  `EmailMessage` normalisés identiquement (mêmes champs, fixtures réelles anonymisées).
- `test_token_refresh.py` : connexion proche d'expirer → refresh, tokens re-chiffrés ;
  verrou pris → seconde tâche s'abstient ; `invalid_grant` → `needs_reconsent` +
  audit ; refresh à la volée dans `client_base` sérialisé avec le périodique.
- `test_throttle.py` : 429 avec `Retry-After` respecté ; backoff croissant ; plafond →
  `ProviderUnavailable` ; compteurs par connexion indépendants.
- `test_webhooks.py` : validation Microsoft (echo `validationToken`) ; notification
  avec bon `clientState` → tâche publiée avec le bon tenant ; `clientState` faux ou
  `route_key` inconnu → réponse neutre, aucune tâche ; renouvellement des
  subscriptions avant expiration ; échec définitif → santé dégradée.
- `test_connector_routes.py` : matrice permissions (read/manage) ; DELETE → tokens
  effacés même si la révocation distante échoue (D9) ; reconsent sur connexion saine
  → 409.

**Frontend (vitest)** : `connectors.test.tsx` (statuts, bouton re-consentement
conditionnel, confirmation de révocation).

**CI** : structure inchangée — aucun test ne touche un vrai provider (fixtures +
serveurs locaux), comme le faux provider OIDC de la Phase 2.

---

## E. Critère de démo de fin de phase

> Sur staging, avec des comptes de test Google Workspace et Microsoft 365 : Alice
> connecte le compte Google du tenant (consentement réel), la connexion apparaît
> `active` ; un script de démo (shell + capability Mail) liste ses derniers emails et
> en envoie un — puis la même chose sur le compte Microsoft **sans changer une ligne
> du script** (preuve de l'abstraction). Un email reçu sur le compte déclenche le
> webhook : la tâche tracée dans Loki montre le bon tenant et l'événement normalisé.
> On force l'expiration de l'access token : le refresh proactif le renouvelle sans
> intervention. On révoque le consentement côté Google : la connexion passe
> `needs_reconsent`, la SPA propose le re-consentement guidé, qui la ranime. Le
> journal d'audit du tenant montre tout le cycle de vie. Dans la DB tenant, les
> colonnes de tokens sont illisibles ; dans Loki, aucun token n'apparaît.

C'est la traduction exécutable du §5 : deux providers très différents derrière les
mêmes capabilities, tokens chiffrés, refresh et webhooks industrialisés.

---

## F. Dépendances manquantes et risques propres à la phase

1. **Vérifications d'apps OAuth en cours** (lancées Phase 3) : sans validation Google,
   l'app connecteurs est plafonnée à 100 utilisateurs de test et affiche un écran
   « non vérifiée » — suffisant pour la démo, bloquant pour de vrais clients. Suivre
   au handoff.
2. **Comptes de test à provisionner** (hors repo) : un tenant Google Workspace et un
   tenant Microsoft 365 de développement — indispensables pour la démo E et les
   validations manuelles (les tests automatisés, eux, n'en dépendent pas).
3. **Webhooks exigent une URL publique en HTTPS** : la machine de staging doit être
   joignable depuis Google/Microsoft — première dépendance entrante depuis Internet
   (jusqu'ici seul le navigateur entrait). Vérifier Caddy et les éventuels filtrages.
4. **Deltas non traités dans cette phase** : les webhooks signalent « du nouveau »
   sans le contenu (Graph) ou par channel (Google) — la récupération incrémentale
   fine (delta queries, historyId) est laissée aux consommateurs réels (Phase 7),
   le framework livrant l'événement normalisé minimal. Assumé.
5. **Quotas providers en développement** : les quotas par défaut (Gmail API
   notamment) suffisent pour la démo ; les demandes d'augmentation sont un sujet
   d'exploitation ultérieur.
6. **Volume de code le plus gros du projet** (§9) : prévoir de découper la PR
   (framework T1-T4, puis providers T5, puis refresh/webhooks T6-T8) si la revue
   devient ingérable.

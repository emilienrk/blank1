# Phase 8 — Durcissement : plan d'implémentation détaillé

> Référence : `docs/architecture-plan.md` (§4 « SSO entreprise », §7 « reporté au
> durcissement », §8 « Besoins et risques », §9 « Phase 8 »). Cette phase couvre :
> le SSO entreprise entrant (SAML via python3-saml + OIDC via Authlib, configurable
> par organisation, **activé pour un client pilote**), le rate limiting global, les
> métriques Prometheus + le suivi d'erreurs GlitchTip, la revue sécurité, le runbook
> d'exploitation, et les **backups pgBackRest offsite avec restauration testée**.
> C'est la phase qui transforme un socle démontrable en plateforme opérable devant de
> vrais clients. Rien de plus — SCIM et les exigences IdP lourdes restent le plan B
> documenté du §4 (bascule Zitadel/Keycloak isolée dans le module `auth`) ; le
> multi-serveur DB (§8.7) reste dormant derrière `db_host`. Chaque anticipation est
> signalée explicitement.

## État des lieux (attendu en entrée de phase)

Hypothèses : Phases 0-7 fusionnées — le socle complet fonctionne sur staging avec de
vrais comptes de test. Points d'appui : auth interne complète (Phase 2 — le SSO s'y
insère), rate limiting ciblé Valkey (Phase 2, D9 : « le global reste en Phase 8 »),
Loki/Grafana/Uptime Kuma (Phase 0 — Prometheus s'y ajoute), procédure de purge backups
en attente réelle (Phase 4, T5), table de prix IA à entretenir (Phase 6). Rien de
cette phase n'existe : pas de SAML, pas de `/metrics`, pas de Sentry SDK, pas de
pgBackRest configuré, pas de runbook.

---

## A. Tâches ordonnées

### T1 — SSO entreprise : configuration par organisation

Rôle : le « SSO entreprise entrant » du §4 — le client garde ses comptes chez
Azure AD / Google, le produit est Service Provider.

| Fichier | Rôle |
|---|---|
| `apps/api/pyproject.toml` + `Dockerfile` | Ajout `python3-saml` (+ dépendance système `xmlsec1` dans l'image — seule brique à dépendance native du projet, décision D2). |
| `apps/api/app/auth/models.py` (extension) | Control-plane : `sso_configurations` (id, tenant_id unique, `protocol` enum `saml/oidc`, `idp_metadata` — XML SAML ou URL de découverte OIDC, `idp_client_id`/`idp_client_secret_enc` chiffré (OIDC), `sp_certificate`/`sp_private_key_enc` chiffré (SAML), `enforce_sso` bool déf. false, `enabled` bool). Migration control-plane 000N. |
| `apps/api/app/auth/sso_saml.py` | SP SAML sur python3-saml : `GET /api/v1/auth/sso/{slug}/login` (AuthnRequest → redirect IdP), `POST /api/v1/auth/sso/{slug}/acs` (assertion validée : signature, audience, horodatage → email extrait), `GET /api/v1/auth/sso/{slug}/metadata` (XML SP à fournir à l'IdP client). Routes anonymes (liste fermée étendue). |
| `apps/api/app/auth/sso_oidc.py` | Variante OIDC : mêmes étapes sur la mécanique Authlib manuelle de la Phase 2 (state signé), métadonnées IdP par organisation. |
| `apps/api/app/auth/service.py` (extension) | Règle commune aux deux protocoles (décision D3) : l'email asserté doit correspondre à un **user existant membre du tenant** — sinon refus explicite (« demandez une invitation ») ; **jamais de création de compte à la volée** (prolonge D5 Phase 2). Session posée = session standard. `enforce_sso` : le login mot de passe/OAuth social est refusé pour les membres de ce tenant (sauf `recovery_admins` : les owners gardent le mot de passe en secours anti-lockout, décision D4). |
| `app/admin/router.py` + `apps/admin` (extensions) | CRUD `sso_configurations` au back-office (platform_admin, onboarding manuel du pilote) ; test de configuration (« SP metadata à télécharger », statut du dernier login SSO). |
| `apps/web/src/pages/login.tsx` (extension) | Détection : le tenant a un SSO actif → bouton « Se connecter via l'entreprise » (et masquage du formulaire si `enforce_sso`). |

### T2 — Rate limiting global

Rôle : le « rate limiting global » du §9, au-delà des 3 endpoints sensibles de la
Phase 2.

| Fichier | Rôle |
|---|---|
| `apps/api/app/core/rate_limit.py` | Généralisation de la brique Valkey Phase 2 en middleware : budget par fenêtre glissante approchée (deux fenêtres fixes pondérées — simple, documenté) avec **deux clés** : par session (user) et par IP pour l'anonyme ; classes de routes configurables (`api_default` déf. 300/min, `auth` (reprend les seuils Phase 2), `webhooks` par provider, `admin` exempt sur WireGuard) ; réponse 429 + `Retry-After` ; en-têtes `X-RateLimit-*`. Valkey indisponible → **fail-open loggé** (décision D5). |
| `apps/api/app/core/config.py` | Seuils par classe, exemptions. |

### T3 — Métriques Prometheus + dashboards

Rôle : la levée du report du §7 (« Prometheus/métriques fines »).

| Fichier | Rôle |
|---|---|
| `apps/api/app/core/metrics.py` | Métriques via le client `prometheus_client` officiel : HTTP (latence/statut/route — labels **sans tenant**, décision D6), tâches Celery (durée/échecs par nom), pool d'engines tenants (taille du cache LRU, évictions — instrumentation du manager Phase 1), rate limit (429 par classe), IA (appels/latence/tokens par provider — sans tenant), migrations (dernier statut du runner). Endpoint `/metrics` **sur un port séparé** (non exposé par Caddy public — surface admin, invariant n°7). |
| `infra/prometheus/` + `compose.yaml` | Conteneur Prometheus (scrape api, worker via même mécanique, node_exporter machine), rétention 30 j ; alerting par **règles Grafana** (déjà là) vers email/ntfy — pas d'Alertmanager séparé (décision D7). Alertes initiales : taux de 5xx, latence p99, échec du runner de migrations, échec de refresh connecteurs répété, disque. |
| `infra/grafana/dashboards/` | Dashboards provisionnés en fichiers : « API », « Worker/Celery », « Connecteurs », « IA », « Système ». |

### T4 — Suivi d'erreurs : GlitchTip

| Fichier | Rôle |
|---|---|
| `compose.yaml` + `infra/` | GlitchTip self-hosted (compatible SDK Sentry, self-host léger — décision D8), sur la surface admin (WireGuard). |
| `apps/api/…` + `apps/web`/`apps/admin` | `sentry-sdk` (backend, DSN par env, `send_default_pii=False`, scrubbing : jamais d'email/token — cohérent invariant n°4) + `@sentry/react` (fronts). `request_id` en tag → corrélation avec Loki. |

### T5 — Backups pgBackRest offsite + restauration testée

Rôle : le risque n°1 du §8 (« backups offsite + tests de restauration réguliers ») —
et le bouclage de la purge backups promise en Phase 4.

| Fichier | Rôle |
|---|---|
| `infra/pgbackrest/` + `compose.yaml` | pgBackRest : full hebdo + incrémental quotidien + WAL archivé en continu, **chiffré**, vers object storage français (Scaleway/OVH — §8.1), rétention configurée (ex. 30 j) ; timer systemd sur la machine (comme `deploy-pull`). |
| `scripts/restore-drill.sh` | Exercice de restauration **scripté et reproductible** : restaure le dernier backup dans un Postgres jetable (conteneur dédié), vérifie `alembic_version` du control-plane et d'un échantillon de bases tenant, compte des lignes témoins, imprime un rapport. À exécuter à cadence fixe (note runbook) — « un backup non testé n'existe pas ». |
| `docs/runbook-gdpr.md` (mise à jour) | La purge backups post-effacement (Phase 4) devient concrète : expiration naturelle par la rétention pgBackRest + procédure de purge anticipée si exigée. |

### T6 — Revue sécurité

| Fichier | Rôle |
|---|---|
| `infra/caddy/Caddyfile` (extension) | En-têtes : HSTS, `X-Content-Type-Options`, `Referrer-Policy`, CSP pour les deux SPA (stricte : `default-src 'self'` + ajustements Vite mesurés). |
| `.github/workflows/ci.yml` (extension) | Jobs de scan **bloquants** : `pip-audit` (uv) + `pnpm audit` (seuil high), scan de secrets (gitleaks). |
| `docs/security-review.md` | Revue structurée type ASVS-allégé, menée sur le code réel : checklist auth/session (fixation, oracle, expiration), tenancy (tentatives de traversée de tenant — tests dédiés ci-dessous), injection (l'interpolation DDL du provisioning re-auditée), SSRF (métadonnées IdP fournies par un client = URL sortante → allowlist de schémas/résolution, garde anti-réseau interne), dépendances, images Docker (user non-root — vérifié Phase 0), conclusions et actions. Le pentest externe est budgété hors repo (recommandation, pas un livrable de code). |

### T7 — Runbook d'exploitation

| Fichier | Rôle |
|---|---|
| `docs/runbook.md` | Le document d'astreinte, procédures pas-à-pas : incident type (API down, DB down, disque plein, certificat), restauration (renvoi vers `restore-drill.sh` en mode réel), rotation des secrets (clé maître KeyProvider → procédure de ré-encryption des colonnes chiffrées, script `saas crypto rotate` livré ici — décision D9 ; clés OAuth ; clés IA), gestion d'un tenant compromis (suspension, révocation des sessions), montée de version (LiteLLM/prix — note Phase 6), checklist de mise en production d'un nouveau client (DNS, SSO éventuel, quotas, DPA). |

### T8 — Contrat, CI et clôture

- `make generate-client` (routes SSO + admin SSO).
- `README.md` : sections SSO entreprise (parcours d'intégration côté client IdP),
  supervision (Prometheus/GlitchTip), backups.
- `CLAUDE.md` racine + `apps/api/CLAUDE.md` : modules/étendues de cette phase, liste
  fermée des routes anonymes mise à jour (SSO), phase courante mise à jour.
- Critère de démo (section E) déroulé et vérifié — **avec le client pilote**.

---

## B. Points de conception — décisions et recommandations

| # | Question | Recommandation | Justification |
|---|---|---|---|
| D1 | SSO : SAML seul ou SAML + OIDC ? | **Les deux (acté §4), OIDC recommandé par défaut aux clients** | Azure AD et Google supportent OIDC, plus simple et déjà outillé chez nous (Phase 2) ; SAML reste indispensable pour les IdP d'entreprise historiques — c'est lui que le §9 nomme pour le pilote. Une seule table de config, deux exécutions. |
| D2 | python3-saml et sa dépendance native `xmlsec1` ? | **Assumée dans l'image Docker (unique dépendance native)** | Acté §1/§4 : lib SAML Python de référence. L'alternative pysaml2 est plus lourde encore ; signer/valider du XML-DSig à la main est exclu. Coût : quelques Mo d'image, pinnés. |
| D3 | Provisioning des comptes via SSO (JIT) ? | **Non — l'assertion doit matcher un membre invité existant** | « Inscription publique désactivée » (§4) reste la règle : le SSO **authentifie**, l'invitation **autorise**. Le JIT ferait de l'IdP client la source d'autorisation dans notre système. SCIM (provisioning piloté par l'IdP) est explicitement le plan B §4 si un client l'exige. |
| D4 | `enforce_sso` : verrouiller tout le monde ? | **Owners exemptés (mot de passe en secours)** | Un IdP client mal configuré ou résilié ne doit pas rendre le tenant définitivement inaccessible (lockout total, support impuissant). L'exemption est visible dans la config et auditée à chaque usage. |
| D5 | Rate limiting : fail-open ou fail-closed si Valkey tombe ? | **Fail-open + log + alerte** | Le rate limiting protège de l'abus ; il ne doit pas transformer une panne du cache en panne totale du produit. L'alerte (T3) rend l'état dégradé visible immédiatement. Les endpoints d'auth Phase 2 gardent leur logique propre (plus stricte). |
| D6 | Cardinalité Prometheus : label `tenant` ? | **Non — jamais de label tenant sur les métriques** | La cardinalité par tenant explose le stockage et les requêtes (anti-pattern documenté) ; la vue par tenant existe déjà là où elle a du sens (usage IA en SQL, logs Loki filtrables). Prometheus observe le **système**, pas les clients. |
| D7 | Alertmanager dédié ? | **Non — alerting Grafana** | Grafana (déjà en place, Phase 0) alerte nativement sur Prometheus et Loki : un conteneur et une config de moins pour une équipe très réduite. Alertmanager redevient pertinent avec des routes d'astreinte complexes — pas le cas. |
| D8 | Suivi d'erreurs : GlitchTip ou Sentry SaaS ? | **GlitchTip self-hosted (acté §7)** | Cohérent avec l'auto-hébergement France et le RGPD (les stack traces peuvent contenir des données) ; API/SDK compatibles Sentry (critère IA-friendly : le SDK Sentry est ultra-documenté) ; bascule Sentry SaaS triviale si le self-host pèse. |
| D9 | Rotation de la clé maître KeyProvider ? | **Procédure + commande `saas crypto rotate` livrées, exécution à la demande** | La Phase 2 a différé la rotation ; avant de vrais clients il faut pouvoir la faire sans inventer la procédure en crise : déchiffrer/re-chiffrer colonne par colonne (TOTP, tokens connecteurs, BYOK, clés SSO) sous verrou, ancienne clé acceptée en lecture le temps de la migration (`key_version` préfixé au ciphertext — le format Phase 2 le permet). |

---

## C. Invariants et règles absolues de la phase

1. **Le SSO ne crée jamais de compte ni de membership** (D3) — l'invitation reste
   l'unique porte d'entrée (invariant Phase 2 étendu au SSO).
2. **Les surfaces d'observabilité (Prometheus, GlitchTip, Grafana) ne sont jamais
   exposées publiquement** (invariant racine n°7) et **ne reçoivent jamais de PII**
   (scrubbing Sentry, labels sans tenant, logs déjà propres).
3. **Aucun secret SSO en clair** : secrets OIDC et clés privées SAML chiffrés
   KeyProvider (invariant racine n°3 étendu).
4. **Toute réponse de limitation est honnête** : 429 + `Retry-After` ; jamais de
   bannissement silencieux.
5. **Un backup n'est réputé exister qu'après un drill de restauration réussi** —
   le script T5 est la définition opérationnelle du mot « backup ».
6. **La CI casse sur vulnérabilité high/critical ou secret détecté** — au même titre
   que lint/types/tests (invariant racine n°6 étendu).
7. Les invariants Phases 0-7 restent en vigueur.

---

## D. Tests à écrire

**Backend (pytest, Postgres réel ; IdP SAML/OIDC simulés localement — même approche
que le faux provider OIDC Phase 2 — `apps/api/tests/`)**
- `test_sso_saml.py` : metadata SP générées ; assertion signée valide d'un membre
  invité → session ; assertion pour un email inconnu ou non-membre → refus sans
  création ; signature invalide, audience/horodatage faux, rejeu → refus ;
  `enforce_sso` → login mot de passe refusé pour un membre, accepté pour un owner
  (D4, audité).
- `test_sso_oidc.py` : mêmes cas côté OIDC (IdP par organisation).
- `test_rate_limit_global.py` : budgets par classe ; clé session vs IP ; 429 +
  `Retry-After` ; exemption admin ; Valkey coupé → fail-open + log (D5).
- `test_metrics.py` : `/metrics` expose les familles attendues ; absence de label
  tenant (test qui échoue si un label interdit apparaît — gardien D6) ; port séparé
  non servi par l'app principale.
- `test_crypto_rotate.py` : rotation sur données réelles (TOTP + tokens + SSO) →
  tout redéchiffrable avec la nouvelle clé, l'ancienne refusée à l'écriture ;
  interruption à mi-parcours → reprise idempotente.
- `test_tenant_isolation.py` (revue sécurité T6, automatisée) : batterie de
  tentatives de traversée — session tenant A sur sous-domaine B, ids d'objets d'un
  autre tenant dans chaque route métier, webhook `route_key` d'un autre tenant →
  tout refusé. Devient un gardien permanent.

**Hors pytest** : `restore-drill.sh` exécuté en CI hebdomadaire planifiée (workflow
`schedule`) contre le backup de staging — l'échec ouvre une alerte.

**Frontend (vitest)** : login avec SSO (bouton conditionnel, masquage `enforce_sso`) ;
back-office config SSO.

---

## E. Critère de démo de fin de phase

> Sur staging puis avec le client pilote : l'IdP du pilote (Azure AD) est configuré
> au back-office à partir des metadata SP téléchargées ; un employé invité du pilote
> se connecte via « Se connecter via l'entreprise » sans mot de passe chez nous ; un
> employé non invité est refusé avec le message attendu. `enforce_sso` activé : le
> mot de passe est refusé aux membres, l'owner de secours passe. Un tir de charge
> modéré déclenche des 429 propres sans affecter les autres tenants. Grafana montre
> les dashboards alimentés ; une exception forcée apparaît dans GlitchTip avec son
> `request_id`, retrouvable dans Loki. `restore-drill.sh` restaure le backup de la
> veille dans un Postgres jetable et son rapport est vert ; la rotation de clé
> maître est exécutée puis un login TOTP et un appel connecteur prouvent que tout
> se déchiffre. La CI échoue volontairement sur une dépendance vulnérable épinglée
> en test. Le runbook a été déroulé une fois « à blanc » par quelqu'un d'autre que
> son auteur.

C'est la traduction exécutable du §9 Phase 8 : un pilote authentifié par son IdP,
une plateforme observée, limitée, sauvegardée, restaurable — et des procédures qui
survivent à leur auteur.

---

## F. Dépendances manquantes et risques propres à la phase

1. **Un client pilote SSO réel** (§9) : sans lui, tout se valide sur un IdP de test
   (Azure AD dev tenant) — suffisant techniquement, mais la phase n'est « finie »
   qu'avec le pilote en production.
2. **Object storage français à provisionner** (hors repo, §8.1) : bucket
   Scaleway/OVH + credentials pour pgBackRest — bloquant pour T5.
3. **Le pentest externe est recommandé, pas livré** : la revue T6 est interne ;
   budgéter un audit tiers avant les premiers clients sensibles.
4. **Charge de la machine unique** : Prometheus + GlitchTip + pgBackRest s'ajoutent
   sur le même hôte (§8.2) — surveiller RAM/disque via les métriques fraîchement
   posées ; c'est aussi le moment de re-évaluer la promesse de SLA.
5. **SCIM et exigences IdP lourdes** : premier client qui l'exige → déclencher le
   plan B §4 (Zitadel/Keycloak), isolé dans `auth` — décision consciente, pas une
   extension de cette phase.
6. **Fin du plan global** : après cette phase, le socle est complet (§9) — la suite
   est produit (vrais modules métier, facturation sur le metering §6, multi-serveur
   §8.7 le jour venu), à re-planifier avec l'utilisateur.

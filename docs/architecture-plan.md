# Plan global d'architecture — Socle SaaS B2B multi-tenant (v2, backend Python)

## Contexte

Création d'un SaaS B2B généraliste : un socle commun réutilisable (auth multi-tenant, connecteurs externes, couche IA multi-fournisseurs, logs, RGPD) sur lequel viendront s'appuyer des modules d'automatisation métier. Contraintes actées : monorepo, backend monolithique modulaire, une base de données par tenant, auto-hébergement en France, auth gérée en interne, onboarding manuel, facturation reportée. Équipe très réduite.

**Révisions v2 (retour utilisateur)** : backend **Python** ; observabilité **allégée** ; critère transverse : **développement fortement assisté par IA générative** → ne retenir que des langages/frameworks massivement représentés dans les données d'entraînement des modèles, matures et stables (éviter les bibliothèques trop récentes que les IA connaissent mal).

---

## 1. Stack technique recommandée

| Domaine | Choix | Justification courte |
|---|---|---|
| Backend | **Python 3.12 + FastAPI + Pydantic v2** | FastAPI est probablement le framework backend le mieux connu des IA génératives (documentation énorme, patterns ultra-standardisés) ; async natif ; OpenAPI généré automatiquement. Python est aussi le langage de référence de tout l'écosystème IA (SDK officiels Anthropic/OpenAI/Mistral d'abord en Python). |
| ORM / migrations | **SQLAlchemy 2.0 (async) + Alembic** | Le duo le plus documenté de l'écosystème ; engines dynamiques par tenant natifs ; Alembic s'exécute programmatiquement sur N bases (recettes multi-tenant éprouvées). |
| Base de données | **PostgreSQL 17** (1 cluster : 1 DB control-plane + 1 DB/tenant) | Standard, fiable, self-host trivial, backups mûrs (pgBackRest). |
| Jobs / scheduler | **Celery + Celery Beat, broker Valkey** (Redis open source) | La solution de jobs Python la plus connue des IA, très mûre ; couvre refresh de tokens, webhooks, syncs, et le futur runtime d'automatisation (chains/groups). Valkey sert aussi de cache/rate-limiter. |
| Authentification | **Construite dans le backend** sur libs mûres : Authlib (OAuth/OIDC), python3-saml (SAML SP), argon2 (mots de passe), pyotp (TOTP 2FA) | Voir §4. Pas de service supplémentaire à opérer ; briques anciennes, stables, massivement documentées — idéal pour du code généré par IA. |
| Couche IA | **LiteLLM (bibliothèque Python)** encapsulée dans un gateway maison | Abstraction multi-providers de facto en Python (Anthropic, OpenAI, Mistral, self-hosted), très connue des IA ; utilisée en mode librairie (pas de proxy séparé → un seul backend). On n'écrit que routing, politiques et metering. |
| Frontend | **React + Vite + TypeScript**, TanStack Router/Query, Tailwind + shadcn/ui | Stack front la plus représentée dans l'entraînement des IA ; SPA suffit pour une app B2B authentifiée (pas de SSR à opérer). |
| Contrat API | **OpenAPI natif FastAPI → client TS généré** (openapi-typescript / orval) | Contrat typé bout en bout sans écrire le schéma à la main ; pattern archi-connu. |
| Monorepo | **uv (Python) + pnpm (front)**, Makefile/justfile racine | Léger ; pas besoin de Turborepo avec un seul backend Python. |
| Qualité / IA-friendly | **ruff + pyright (strict) + pytest** ; ESLint + tsc côté front | Le garde-fou indispensable du code généré par IA : typage strict + lint + tests exécutés en CI à chaque commit. |
| Logs (allégé) | **structlog (JSON) → Loki + Grafana** (2 conteneurs) | Voir §7 — centralisé et consultable, sans l'usine à gaz. Prometheus/traces reportés à la phase durcissement. |
| Infra | **Docker Compose + Caddy (TLS auto) + GitHub Actions** | Le plus simple à opérer seul sur son matériel ; images 12-factor → k3s possible plus tard. |
| Secrets / chiffrement | **AES-256-GCM en enveloppe** (lib `cryptography`) derrière une interface `KeyProvider` | Natif au départ (clé maître + clés par tenant) ; OpenBao/KMS branchable plus tard. |

**Écartés suite à la v2** : NestJS/Drizzle/better-auth (TS, dont deux libs trop récentes pour être bien connues des IA), Vercel AI SDK (remplacé par LiteLLM), stack observabilité complète OTel/Prometheus/Tempo (reportée).

---

## 2. Architecture d'ensemble et découpage en composants

**Monolithe modulaire, deux processus issus du même code** : `api` (FastAPI/uvicorn) et `worker` (Celery) — même image Docker, entrypoints différents.

```
apps/
  api/            # backend FastAPI — packages Python = modules
    app/core/     # config, crypto (KeyProvider), routage DB, logging
    app/tenancy/  # catalogue tenants, provisioning, runner migrations
    app/auth/     # sessions, OAuth login, SSO entreprise, 2FA
    app/directory/# orgs, équipes, users, rôles, permissions, invitations
    app/connectors/  # framework + google_workspace/ + microsoft_365/
    app/ai/       # AI Gateway : LiteLLM, routing, metering
    app/audit/    # journal d'audit applicatif par tenant
    app/gdpr/     # export, effacement, rétention
    app/admin/    # API back-office (hors contexte tenant)
    app/automation/  # (coquille, phase finale) contrat des modules métier
  web/            # SPA client (React/TS)
  admin/          # SPA back-office interne (React/TS, partage packages/ui)
packages/
  api-client/     # client TS généré depuis l'OpenAPI
  ui/             # composants React partagés
```

**Pratiques pour le développement assisté par IA** (transverse) : fichier `CLAUDE.md` racine + par module (conventions, invariants comme « jamais de requête sans contexte tenant ») ; typage strict partout ; modules petits et réguliers ; tests pytest par module ; le contrat OpenAPI comme source de vérité front/back. Un codebase régulier et fortement typé est ce qui rend l'assistance IA fiable.

**Préparation facturation (sans la construire)** : événements d'usage IA (§6) + champ `plan` sur le tenant. Rien d'autre.

---

## 3. Multi-tenant et bases par tenant

- **DB control-plane** : catalogue tenants (URL de connexion, état, serveur hôte), identités globales (un email peut appartenir à plusieurs tenants), memberships (user × tenant × rôles), données d'auth/sessions, événements d'usage IA, config plateforme.
- **DB tenant** : toutes les données métier + audit log + connexions OAuth tierces → export/effacement RGPD triviaux.

**Résolution du tenant** : sous-domaine (`client.app.tld`) croisé avec la session ; dépendance FastAPI qui vérifie le membership et attache le contexte tenant (contextvars). Invariant : aucune requête métier sans contexte tenant résolu.

**Routage des connexions** : `TenantEngineManager` — async engine SQLAlchemy par tenant, création paresseuse, LRU + fermeture des engines inactifs, plafond global. **PgBouncer** (transaction mode) devant le cluster.

**Migrations** : un seul arbre de migrations Alembic pour le schéma tenant (+ un pour le control-plane). Runner dédié : verrou advisory, itération sur le catalogue, version journalisée par base (table alembic_version), **rapport d'échecs partiels** (une base en échec ne bloque pas les autres mais bloque le déploiement tant que non résolue). Exécuté au déploiement + à la demande via back-office.

**Provisioning** : commande admin (CLI Typer + back-office) : CREATE DATABASE depuis template → migrations → seed → catalogue → invitation du premier owner.

---

## 4. Authentification auto-hébergée et rôles

**Recommandation : auth construite dans le backend** sur des briques Python anciennes et éprouvées, plutôt qu'un IdP dédié (Keycloak/Zitadel) :
- Mots de passe : **argon2** ; 2FA : **pyotp** (TOTP).
- Sessions serveur en DB control-plane, cookie httpOnly/SameSite, révocables.
- **OAuth login Google & Microsoft** : **Authlib** (référence Python OAuth/OIDC).
- **SSO entreprise entrant** : le client garde ses comptes chez Azure AD/Google et ton produit est Service Provider — OIDC via Authlib, **SAML via python3-saml**, configurable par organisation.
- Inscription publique désactivée : uniquement invitations.

C'est plus d'assemblage qu'une solution clé en main, mais chaque brique est mûre, stable et massivement documentée — exactement le profil de code qu'une IA générative produit de façon fiable, et zéro service supplémentaire à opérer. *Plan B documenté* : si SCIM ou des exigences IdP lourdes s'imposent, basculer vers Zitadel/Keycloak self-hosted ; le module `auth` isole ce changement.

**RBAC** : rôles plateforme (`platform_admin`, hors tenant, back-office) ; rôles tenant (`owner`, `admin`, `member` + rôles custom) ; permissions granulaires `ressource.action` vérifiées par une dépendance FastAPI unique, **namespacées** (`core.*`, futur `module_x.*`) pour les modules métier.

---

## 5. Framework de connecteurs externes

- **Provider** : Google, Microsoft… (manifest : scopes OAuth, capabilities, endpoints webhooks).
- **Connection** : instance liée à un tenant ou un utilisateur, tokens **chiffrés en enveloppe** en DB tenant, statut de santé, re-consentement guidé si un refresh échoue définitivement.
- **Capability** : interfaces normalisées (`MailCapability`, `CalendarCapability`, `ContactsCapability`, `FilesCapability`…). Les modules métier consomment les capabilities, jamais les APIs propriétaires.

Mécanique commune (écrite une fois) : flux OAuth tiers distincts du login ; **refresh proactif des tokens** par tâche Celery avec verrou par connexion ; **webhooks entrants** `/webhooks/:provider` + jobs de renouvellement des subscriptions (Microsoft Graph ~3 jours, Google Push ~7) ; rate limiting par provider + backoff ; appels lourds via Celery.

Premières implémentations : **Google Workspace** (google-api-python-client) et **Microsoft 365** (msgraph-sdk + msal) — deux APIs très différentes qui valident l'abstraction.

---

## 6. Couche IA et mesure de consommation

**AI Gateway** (module `ai`) : interface interne unique — chat, streaming, tool-calling, embeddings — au-dessus de **LiteLLM en mode bibliothèque** (pas de proxy séparé).

- **Routing par politique de tenant** : provider/modèle par défaut, providers autorisés, option **« zéro rétention »** (Mistral France, endpoints Zero-Data-Retention contractuels ; extensible vers vLLM self-hosted).
- **Providers initiaux** : Anthropic (Claude), OpenAI (GPT), Mistral. Clés plateforme, champ prévu pour BYOK.
- **Metering dès le premier appel** : événement d'usage par requête (tenant, user, module, provider, modèle, tokens in/out/cachés, latence, coût estimé via table de prix versionnée) → `ai_usage_events` en control-plane + agrégats journaliers. Fondation directe de la facturation à l'usage.
- Garde-fous : quotas par tenant (soft limit + alerte), timeout, fallback de provider optionnel.

**RGPD** : Anthropic/OpenAI = transfert hors UE (DPA + SCC à référencer dans ta propre DPA) ; Mistral = France. La politique « zéro rétention » par tenant neutralise le sujet pour les clients sensibles.

---

## 7. Logs, observabilité (allégée) et RGPD

**Version minimale assumée, extensible plus tard** :
- **Logs techniques** : structlog JSON, corrélés (`request_id`, `tenant_id`, user pseudonymisé), **redaction des PII** (règle : jamais de contenu métier dans les logs techniques) → **Loki + Grafana** (2 conteneurs, collecte Promtail/Alloy des sorties Docker). Rétention 30 jours. C'est tout pour démarrer : centralisé, requêtable, self-hosted.
- **Supervision minimale** : endpoints `/health`, **Uptime Kuma** (1 conteneur) pour l'alerte de disponibilité (email/ntfy).
- **Reporté au durcissement (phase 8)** : Prometheus/métriques fines, traces, GlitchTip/Sentry. On l'ajoute quand il y a de vrais clients et de vraies charges.

**Audit log applicatif** (distinct des logs techniques) : qui a fait quoi, quand, sur quoi — écrit dans la **DB du tenant** (donnée du client), consultable dans l'app par les admins du tenant, exportable. Émis par le socle (auth, rôles, connecteurs) puis par les modules.

**RGPD** :
- DB-par-tenant + France = minimisation structurelle. **Export** = dump base tenant + données control-plane associées ; **effacement** = drop + purge catalogue + purge backups à échéance.
- Chiffrement : disques LUKS, TLS partout, secrets/tokens chiffrés applicativement.
- Jobs de **rétention/purge** configurables par type de donnée.
- Livrables non techniques : registre des traitements, DPA type (tu es sous-traitant de tes clients), liste des sous-traitants ultérieurs (providers IA), procédure de notification de violation.

---

## 8. Besoins et risques non mentionnés dans le brief

1. **Backups offsite** : pgBackRest chiffré vers object storage français (Scaleway/OVH) + tests de restauration réguliers. Risque n°1 de l'auto-hébergement à domicile.
2. **Disponibilité domestique** (courant/fibre, IP résidentielle) : SLA réaliste à cadrer avec les clients ; page de statut hébergée ailleurs.
3. **Vérification des apps OAuth** : validation Google (scopes sensibles Gmail/Agenda) et Microsoft publisher verification = **des semaines** → à lancer dès la phase 3.
4. **Emails transactionnels** : délivrabilité quasi nulle depuis une IP résidentielle → relais SMTP français (Scaleway TEM / OVH).
5. **Staging** dès le départ (le runner de migrations multi-bases se teste ailleurs qu'en prod).
6. **Sécurité périmétrique** : admin (SSH, Grafana, back-office) derrière WireGuard ; mises à jour auto ; fail2ban ; scan de dépendances en CI.
7. **Scalabilité DB-par-tenant** : OK jusqu'à quelques centaines de tenants avec PgBouncer ; champ « serveur hôte » au catalogue pour répartir plus tard sans refonte.

---

## 9. Ordre de construction

- **Phase 0 — Fondations** : monorepo, CI (ruff, pyright, pytest, tsc), Docker Compose (Postgres, Valkey, Caddy, Loki+Grafana, Uptime Kuma), squelettes api/worker/web, génération du client TS depuis l'OpenAPI, déploiement continu vers staging dès le premier jour. *Tout le reste se livre dessus.*
- **Phase 1 — Socle multi-tenant** : control-plane, catalogue, TenantEngineManager, runner de migrations Alembic multi-bases, provisioning CLI. *La décision la plus structurante : tout code écrit ensuite naît tenant-aware — rétrofitter le multi-tenant est le pire chantier possible.*
- **Phase 2 — Auth + annuaire** : sessions, argon2, TOTP, OAuth login Google/Microsoft (Authlib), orgs/équipes/rôles/permissions, invitations.
- **Phase 3 — Frontends + back-office** : SPA client (login, gestion d'équipe), back-office (provisioning, supervision migrations). **Lancer ici les vérifications d'apps OAuth Google/Microsoft.**
- **Phase 4 — Audit + socle RGPD** : audit log, export/effacement, purges. *Avant les connecteurs : dès que des données clients circulent, l'audit doit exister.*
- **Phase 5 — Framework connecteurs** : abstraction + Google Workspace + Microsoft 365 (mail/agenda), token store chiffré, refresh Celery, webhooks. *Le plus gros morceau.*
- **Phase 6 — AI Gateway** : LiteLLM, politiques par tenant, metering, quotas.
- **Phase 7 — Runtime d'automatisation (coquille)** : contrat de module métier (manifest : routes, tâches Celery, permissions, capabilities requises), scheduler, un module d'exemple bout en bout. *Prouve qu'un module s'ajoute sans toucher au cœur.*
- **Phase 8 — Durcissement** : SAML activé pour un client pilote, rate limiting global, métriques Prometheus + GlitchTip, revue sécurité, runbook, restauration testée.

Chaque phase se termine par un critère de démo concret (ex. Phase 1 : « créer un tenant en CLI, migrations appliquées sur N bases, échec partiel correctement rapporté »).

---

## Vérification (à l'échelle de ce plan)

Validation attendue sur : (1) stack Python/FastAPI/SQLAlchemy/Celery + front React/TS, (2) auth assemblée sur Authlib/python3-saml plutôt qu'IdP dédié, (3) identités globales en control-plane + données métier en DB tenant, (4) observabilité minimale Loki+Grafana+Uptime Kuma, (5) ordre des phases. Ensuite, détail implémentable phase par phase.

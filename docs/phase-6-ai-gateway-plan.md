# Phase 6 — AI Gateway : plan d'implémentation détaillé

> Référence : `docs/architecture-plan.md` (§6 « Couche IA et mesure de consommation »,
> §9 « Phase 6 »). Cette phase couvre : le gateway interne unique (chat, streaming,
> tool-calling, embeddings) sur **LiteLLM en mode bibliothèque**, les politiques par
> tenant (provider/modèle par défaut, providers autorisés, zéro-rétention), le
> metering dès le premier appel (`ai_usage_events` + agrégats journaliers + table de
> prix versionnée), les quotas soft avec alerte, timeout et fallback optionnel.
> Rien de plus — le BYOK n'est que préparé (champ chiffré, aucune UI), la facturation
> reste hors périmètre (le metering en est la fondation, §2), et le premier
> consommateur réel du gateway est le module d'exemple de la Phase 7. Chaque
> anticipation est signalée explicitement.

## État des lieux (attendu en entrée de phase)

Hypothèses : Phases 0-5 fusionnées. Points d'appui : `KeyProvider` (champ BYOK),
compteurs Valkey (brique rate-limit Phase 2, généralisée Phase 5 — réutilisée pour les
quotas), Celery beat (agrégats), back-office (vue consommation), `record_audit_event`
(changements de politique). `app/ai/` n'existe pas. Aucun SDK IA n'est encore dans les
dépendances.

---

## A. Tâches ordonnées

### T1 — Dépendances, configuration, politique par tenant

| Fichier | Rôle |
|---|---|
| `apps/api/pyproject.toml` | Ajout : `litellm` (version pinnée — lib à évolution rapide, décision D8). |
| `apps/api/app/core/config.py` | Extensions : `anthropic_api_key`, `openai_api_key`, `mistral_api_key` (clés plateforme, optionnelles individuellement — un provider sans clé est indisponible), `ai_default_provider` (déf. `mistral`), `ai_default_model`, `ai_request_timeout_seconds` (déf. 120), `ai_quota_default_monthly_tokens` (déf. généreux, soft). |
| `apps/api/app/ai/models.py` | Control-plane (§6 : événements d'usage en control-plane) : `tenant_ai_policies` (tenant_id PK/FK, `default_provider`, `default_model`, `allowed_providers` liste, `zero_retention` bool déf. false, `monthly_token_quota` nullable — null = défaut plateforme, `byok_keys_enc` JSONB chiffré nullable — **préparé, jamais exposé**, décision D7) ; `ai_usage_events` (id, `occurred_at`, tenant_id, user_id nullable, `module` — `core` ou nom de module Phase 7, `provider`, `model`, `input_tokens`, `output_tokens`, `cached_tokens`, `latency_ms`, `estimated_cost_microeur`, `price_version`, `status` enum `ok/error/timeout`, `error_kind` nullable) ; `ai_usage_daily` (agrégat (jour, tenant, provider, model) : compteurs sommés). |
| Migrations | Révision control-plane 000N. |

### T2 — Table de prix versionnée

| Fichier | Rôle |
|---|---|
| `apps/api/app/ai/pricing.py` | Table de prix **en code, versionnée par le repo** (décision D4) : `PRICE_VERSION` (date), dict (provider, model) → prix par million de tokens in/out/cachés, en micro-euros. `estimate_cost(provider, model, usage)` ; modèle inconnu → coût 0 + warning loggé (jamais bloquant : le metering ne doit pas casser un appel). |

### T3 — Le gateway : interface interne unique

Rôle : « interface interne unique — chat, streaming, tool-calling, embeddings » (§6).
C'est du code appelé par le backend (et les modules Phase 7), pas une API publique.

| Fichier | Rôle |
|---|---|
| `apps/api/app/ai/gateway.py` | `AIGateway` : `chat(request) -> ChatResult`, `chat_stream(request) -> AsyncIterator[Chunk]`, `embed(request) -> EmbedResult`. Enchaînement par appel : (1) tenant courant obligatoire (contexte Phase 1) ; (2) chargement de la politique (cache mémoire court) ; (3) résolution provider/modèle (demande explicite du code appelant validée contre `allowed_providers`, sinon défauts de la politique) ; (4) contrôle de quota (T5) ; (5) appel LiteLLM (`litellm.acompletion`/`aembedding`) avec timeout et clés plateforme (ou BYOK si présent) ; (6) fallback optionnel de la politique sur erreur provider (D6) ; (7) metering (T4) — **y compris en erreur/timeout** (tokens à 0, statut renseigné). Types Pydantic propres (`ChatRequest`, `Message`, `ToolDef`…) : LiteLLM reste un détail d'implémentation invisible des appelants (décision D2). |
| `apps/api/app/ai/policy.py` | Lecture/écriture des politiques : `get_policy(tenant_id)` (défauts plateforme si absente) ; enforcement zéro-rétention (D5) : si `zero_retention`, l'ensemble providers/modèles est restreint à la liste ZDR en code (Mistral d'abord) — un appel explicite hors liste est **refusé**, pas dégradé silencieusement. |

### T4 — Metering

| Fichier | Rôle |
|---|---|
| `apps/api/app/ai/metering.py` | `record_usage(event)` : insertion directe en control-plane, **best-effort** (décision D3) : un échec d'insertion logge en erreur mais ne fait pas échouer la réponse IA. En streaming, l'événement est écrit à la fin du flux (usage du dernier chunk LiteLLM) ; flux interrompu → événement `status=error` avec les tokens connus. |
| `apps/api/app/ai/tasks.py` | Beat quotidien : agrégation de la veille dans `ai_usage_daily` (upsert idempotent — rejouable) ; purge des événements bruts au-delà de `ai_usage_raw_retention_days` (déf. 90 — les agrégats, eux, sont conservés : fondation facturation §2). |

### T5 — Quotas soft + alerte

| Fichier | Rôle |
|---|---|
| `apps/api/app/ai/quota.py` | Compteur mensuel par tenant sur Valkey (clé `ai:quota:<tenant>:<AAAA-MM>`, incrément post-appel, TTL 62 j) — rapide, sans requête SQL par appel ; recalé par l'agrégat quotidien (dérive bornée à la journée). Au-delà du quota : **l'appel passe** (soft limit, acté §6) mais un événement d'alerte est loggé + audité (`core.ai.quota_exceeded`, une fois par jour par tenant) et le back-office l'affiche. Le hard limit est un simple booléen de politique prévu mais non exposé (défaut soft). |

### T6 — Surfaces : back-office et route de test

| Fichier | Rôle |
|---|---|
| `apps/api/app/admin/router.py` (extension) | `GET /api/v1/admin/ai/usage` (agrégats par tenant/mois, dépassements de quota), `GET/PUT /api/v1/admin/tenants/{slug}/ai-policy` (politique : defaults, allowed, zero_retention, quota — chaque changement audité). La politique se gère **au back-office** (onboarding manuel assumé) — pas d'UI tenant dans cette phase. |
| `apps/admin/src/pages/ai-usage.tsx` | Consommation par tenant (mois courant + historique), politiques, alertes de quota. |
| `apps/api/app/ai/router.py` | Une seule route tenant, minimale : `POST /api/v1/ai/chat` (`core.ai.use`, owner/admin par défaut) — l'écho du gateway pour la démo et le smoke test staging. **Pas d'UI de chat** : le socle fournit l'infrastructure, les usages viennent des modules (Phase 7). |

### T7 — Contrat, CI et clôture

- `make generate-client` (routes admin AI + `ai/chat`).
- `README.md` : providers supportés, variables d'env, politique zéro-rétention
  (providers ZDR), quotas.
- `docs/rgpd/sous-traitants.md` (Phase 4) mis à jour : Anthropic/OpenAI (hors UE,
  DPA + SCC), Mistral (France) — le renvoi prévu depuis la Phase 4 devient réel.
- `CLAUDE.md` racine + `apps/api/CLAUDE.md` : module `ai`, règles « tout appel IA passe
  par `AIGateway` » et « jamais de contenu de prompt/complétion dans les logs ni dans
  `ai_usage_events` », phase courante mise à jour.
- Critère de démo (section E) déroulé et vérifié.

---

## B. Points de conception — décisions et recommandations

| # | Question | Recommandation | Justification |
|---|---|---|---|
| D1 | LiteLLM bibliothèque ou proxy ? | **Bibliothèque (acté §1/§6)** | Pas de service supplémentaire à opérer ; le routing/politiques/metering sont notre valeur ajoutée et restent dans notre code typé. |
| D2 | Les appelants voient-ils les types LiteLLM ? | **Non — types Pydantic maison aux frontières du gateway** | LiteLLM évolue vite (D8) : l'isoler dans `gateway.py` borne le rayon d'une mise à jour. Les modules (Phase 7) programment contre notre contrat stable, pas contre une lib tierce. |
| D3 | Metering : insertion directe, Celery, ou batch ? | **Insertion directe best-effort** | Un event par appel IA : volumétrie faible devant le coût de l'appel lui-même (secondes). Celery ajouterait un délai et un risque de perte (broker) pour rien ; le batch est une optimisation prématurée. « Best-effort » car le metering ne doit jamais casser une réponse — l'échec est loggé et visible. |
| D4 | Table de prix : en base ou en code ? | **En code, versionnée (`PRICE_VERSION` sur chaque événement)** | Les prix changent par release des providers, pas par action utilisateur : un fichier Python typé, revu en PR, suffit et reste auditable. `price_version` sur l'événement permet de recalculer/contester a posteriori. Une table éditable en base = du back-office et de la validation pour zéro gain à ce stade. |
| D5 | Zéro-rétention : garantie comment ? | **Liste fermée de providers/modèles ZDR en code ; violation = refus explicite** | C'est une promesse contractuelle (§6) : elle ne peut pas dépendre d'une configuration libre. Mistral (France) d'abord ; les endpoints ZDR contractuels d'autres providers s'ajoutent à la liste par PR. Un fallback silencieux vers un provider non-ZDR serait une faute — d'où le refus. |
| D6 | Fallback de provider : automatique ? | **Optionnel, par politique, désactivé par défaut** | Acté §6 (« fallback de provider optionnel »). Un fallback silencieux change le modèle qui répond (qualité, coût, juridiction) : décision de politique par tenant, jamais un défaut global. Incompatible avec `zero_retention` sauf si la cible est aussi ZDR (vérifié par code). |
| D7 | BYOK maintenant ? | **Champ chiffré préparé, aucune UI ni doc client** | Acté §6 (« champ prévu pour BYOK »). Le schéma est le coût irréversible (migration) ; l'UI et le support opérationnel sont le vrai chantier — reporté à la demande réelle. Le gateway lit déjà la clé si présente : la plomberie est prouvée par un test. |
| D8 | Version de LiteLLM ? | **Pinnée exactement, mise à jour manuelle consciente** | La lib bouge vite (nouveaux providers/modèles chaque semaine) — l'inverse du critère « briques stables » (§1), assumé car son rôle est justement d'absorber la volatilité des APIs providers. Le pin + l'isolation D2 + les tests du gateway rendent chaque montée de version maîtrisée. |
| D9 | Les prompts/réponses sont-ils persistés quelque part ? | **Non — nulle part dans le socle** | `ai_usage_events` ne porte que des métriques (invariant n°3 ci-dessous). Si un module métier (Phase 7) veut conserver des conversations, c'est **sa** donnée, dans la DB tenant, sous ses permissions — pas un service du gateway. Minimisation RGPD par défaut. |

---

## C. Invariants et règles absolues de la phase

1. **Tout appel à un provider IA passe par `AIGateway`** — aucun import de `litellm`
   (ni SDK provider) hors de `app/ai/`.
2. **Aucun appel IA sans contexte tenant** : le gateway refuse sans tenant courant
   (extension naturelle de l'invariant racine n°1) ; chaque appel est rattaché à un
   tenant, un user éventuel et un module.
3. **Jamais de contenu de prompt ni de complétion dans les logs techniques ni dans
   `ai_usage_events`** — uniquement des métriques (tokens, latence, coût, statut).
4. **Chaque appel produit exactement un événement d'usage**, succès comme échec —
   le metering n'est pas optionnel (fondation facturation, §2/§6).
5. **La politique zéro-rétention est infranchissable par configuration** : liste ZDR
   en code, refus explicite hors liste (D5).
6. **Clés provider (plateforme et BYOK) jamais en clair en base ni dans les logs** —
   env pour les clés plateforme (invariant racine n°3), KeyProvider pour BYOK.
7. Les invariants Phases 0-5 restent en vigueur.

---

## D. Tests à écrire

**Backend (pytest, Postgres réel, LiteLLM doublé par mock/`respx` — aucun appel réseau
réel — `apps/api/tests/`)**
- `test_gateway.py` : appel chat sans contexte tenant → refus ; politique par défaut
  appliquée ; provider explicite hors `allowed_providers` → refus ; timeout →
  `status=timeout` metered ; réponse ok → `ChatResult` normalisé + événement complet
  (tokens, coût, `price_version`) ; streaming → chunks puis événement final ; flux
  interrompu → événement `error`.
- `test_policy.py` : zero_retention → provider non-ZDR refusé même explicitement ;
  fallback désactivé par défaut ; fallback activé → bascule sur erreur provider,
  metering du provider réel ; fallback non-ZDR sous zero_retention → refusé.
- `test_pricing.py` : coûts calculés sur cas connus ; modèle inconnu → 0 + warning,
  appel non bloqué.
- `test_metering.py` : insertion échouée (DB indisponible simulée) → réponse IA
  intacte, erreur loggée ; agrégat quotidien idempotent (rejouable) ; purge des bruts
  au-delà de la rétention, agrégats conservés.
- `test_quota.py` : compteur Valkey incrémenté ; dépassement → appel passe + alerte
  auditée une fois par jour ; recalage par l'agrégat ; BYOK présent → clé du tenant
  utilisée (plomberie D7 prouvée).
- `test_ai_routes.py` : `ai/chat` → matrice permissions ; routes admin (usage,
  politique — modification auditée).

**Frontend (vitest)** : `ai-usage.test.tsx` (agrégats rendus, édition de politique,
alerte quota visible).

**CI** : structure inchangée ; aucun test ne consomme de clé réelle (les clés d'env de
test sont factices et le mock intercepte tout).

---

## E. Critère de démo de fin de phase

> Sur staging, avec de vraies clés plateforme : `POST /api/v1/ai/chat` sur `acme`
> répond via le provider par défaut de la politique ; `ai_usage_events` contient
> l'événement (tokens réels, coût estimé, version de prix). L'opérateur passe `acme`
> en zéro-rétention au back-office : un appel demandant explicitement un provider
> hors liste ZDR est refusé avec une erreur claire ; l'appel par défaut part chez
> Mistral. Un quota volontairement bas est posé : l'appel suivant passe mais
> l'alerte apparaît (audit + page back-office). La page consommation montre les
> agrégats par tenant après le passage du beat. Un provider est coupé (clé retirée) :
> avec fallback activé, l'appel bascule et le metering attribue au provider réel.
> Dans Loki : latences et statuts corrélés par `request_id`, **aucun fragment de
> prompt ni de réponse**.

C'est la traduction exécutable du §6 : un point d'entrée IA unique, gouverné par
tenant, mesuré dès le premier appel.

---

## F. Dépendances manquantes et risques propres à la phase

1. **Clés API réelles à provisionner** (hors repo) : comptes Anthropic, OpenAI,
   Mistral — avec les DPA/SCC à référencer (mise à jour `docs/rgpd/sous-traitants.md`,
   T7). Sans clés, tout fonctionne en staging sauf la démo E.
2. **Pas de consommateur réel avant la Phase 7** : le gateway est démontré par la
   route de test — assumé, c'est exactement la situation de la Phase 1 (socle prouvé
   par tests avant usage réel).
3. **Dérive des prix** : la table D4 vieillit silencieusement — se doter d'un
   réflexe de mise à jour à chaque changement de tarif provider (note au runbook
   Phase 8) ; `price_version` borne le dégât.
4. **LiteLLM pinnée** (D8) : les nouveaux modèles exigent une montée de version
   consciente — coût récurrent accepté en échange de la stabilité.
5. **Le compteur Valkey de quota est approximatif** (perte possible au restart avant
   recalage quotidien) : acceptable pour un soft limit ; un hard limit facturable
   exigerait la source SQL — prévu, non construit.
6. **Streaming et proxys** : vérifier bout en bout (uvicorn → Caddy → SPA plus tard)
   que le flux SSE/chunked n'est pas bufferisé — point de validation staging.

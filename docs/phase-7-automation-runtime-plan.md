# Phase 7 — Runtime d'automatisation (coquille) : plan d'implémentation détaillé

> Référence : `docs/architecture-plan.md` (§2 `app/automation/`, §9 « Phase 7 »).
> Cette phase couvre : le **contrat de module métier** (manifest : routes, tâches
> Celery, permissions, capabilities requises), le registre et le montage des modules,
> l'activation par tenant, le scheduler des tâches périodiques de modules, et **un
> module d'exemple bout en bout** qui prouve qu'un module s'ajoute **sans toucher au
> cœur**. Rien de plus — aucun vrai module métier n'est écrit ici (ils sont la raison
> d'être du produit, pas du socle) ; le module d'exemple reste volontairement trivial.
> Chaque anticipation est signalée explicitement.

## État des lieux (attendu en entrée de phase)

Hypothèses : Phases 0-6 fusionnées. Tout ce qu'un module consomme existe :
`require_permission` (permissions namespacées prévues pour `module_x.*` dès la
Phase 2), capabilities connecteurs + hook `on_connector_event` (Phase 5, D7 — le
runtime en est le premier client réel annoncé), `AIGateway` (Phase 6),
`record_audit_event` avec namespace `module_x.*` (réservé depuis la Phase 4),
`get_tenant_session()` et les migrations tenant (Phase 1). `app/automation/` n'existe
pas ; rien dans le code ne connaît la notion de « module ».

---

## A. Tâches ordonnées

### T1 — Le contrat : `ModuleManifest`

Rôle : la définition formelle de ce qu'est un module (§9 : « manifest : routes, tâches
Celery, permissions, capabilities requises »).

| Fichier | Rôle |
|---|---|
| `apps/api/app/automation/contract.py` | `ModuleManifest` (Pydantic, figé) : `name` (slug `^[a-z][a-z0-9_]{1,30}$`, unique), `version`, `title`/`description` (affichage), `router` (APIRouter aux routes **toutes** protégées par `require_permission("<name>.…")` — vérifié au montage, T2), `permissions` (déclaration des permissions du module et de leur rattachement aux rôles intégrés), `periodic_tasks` (liste de `PeriodicTaskSpec` : nom, cadence, fonction — signature imposée `(tenant_id) -> None`), `connector_events` (abonnements `(capability, handler)` au hook Phase 5), `required_capabilities` (ex. `MailCapability` — contrôlées à l'activation, T3). |

### T2 — Registre et montage

Rôle : l'unique point de couture entre le cœur et les modules — écrit une fois,
plus jamais modifié par l'ajout d'un module.

| Fichier | Rôle |
|---|---|
| `apps/api/app/automation/registry.py` | `MODULES: list[ModuleManifest]` — **liste explicite en code** (décision D1), remplie par import des manifests depuis `app/modules/…`. Validations au démarrage (fail-fast) : noms uniques, permissions du module toutes préfixées `<name>.`, chaque route du router porte une dépendance `require_permission` (introspection — décision D2), tâches nommées `<name>.…`. |
| `apps/api/app/automation/mounting.py` | Montage : routes sous `/api/v1/modules/{name}/…` avec une dépendance additionnelle `require_module_enabled(name)` injectée sur tout le router (403 explicite si le module n'est pas actif pour le tenant courant) ; enregistrement des permissions déclarées dans le registre central (`permissions.py` accepte désormais les namespaces de modules) ; enregistrement des handlers `connector_events` ; déclaration des tâches périodiques auprès du scheduler (T4). Appelé une fois dans `main.py`/`worker.py` — **dernière modification du cœur** que les modules exigeront. |

### T3 — Activation par tenant

| Fichier | Rôle |
|---|---|
| `apps/api/app/automation/models.py` | Control-plane (donnée de gouvernance, pas donnée métier — décision D3) : `tenant_modules` (tenant_id, `module_name`, `enabled` bool, timestamps, unicité (tenant, module)). |
| `apps/api/app/automation/service.py` | `enable_module(tenant, name)` : vérifie que le module existe au registre et que ses `required_capabilities` sont satisfaites par au moins une connexion active du tenant (Phase 5) — sinon erreur explicite listant ce qui manque ; audit `core.module.enabled`. `disable_module` : coupe routes (via `require_module_enabled`) et tâches (le scheduler filtre, T4) ; les données du module en DB tenant **restent** (décision D6). `require_module_enabled(name)` : dépendance FastAPI (cache court par tenant). |
| `apps/api/app/admin/router.py` + `apps/admin/src/pages/…` (extensions) | Activation/désactivation par tenant au **back-office** (onboarding manuel assumé, cohérent D5 Phase 6) ; liste des modules disponibles (registre) et de leur état par tenant. |
| Migrations | Révision control-plane 000N (`tenant_modules`). |

### T4 — Scheduler des tâches de modules

Rôle : le « scheduler » du §9, en fan-out sur les tenants actifs.

| Fichier | Rôle |
|---|---|
| `apps/api/app/automation/scheduler.py` | Pour chaque `PeriodicTaskSpec` déclarée : une entrée Celery beat **statique** (générée au démarrage du worker depuis le registre) qui, à chaque tick, itère les tenants où le module est `enabled` et publie une tâche unitaire par tenant (décision D4). La tâche unitaire : pose le contexte tenant (helper commun aux tâches Phase 4/5, réutilisé), exécute la fonction du module, capture l'échec (un tenant en échec ne bloque pas les autres — philosophie du runner Phase 1), logge un rapport corrélé. Verrou Valkey par (module, tâche, tenant) contre les chevauchements si un tick dure plus que la cadence. |

### T5 — Migrations de schéma des modules

Rôle : décision structurante — un module peut avoir des tables en DB tenant.

| Fichier | Rôle |
|---|---|
| `apps/api/app/tenancy/tenant_base.py` (convention, pas de code nouveau) | Les tables d'un module vivent dans la MetaData **tenant** existante, préfixées `<name>_` (décision D5), et leurs migrations rejoignent **l'arbre tenant unique** (Phase 1) — le runner multi-bases les applique comme le reste. Un module désactivé garde ses tables (vides ou non) : le schéma tenant reste identique pour tous les tenants, seul le comportement diffère. |

### T6 — Le module d'exemple : `sample_digest`

Rôle : la preuve exigée par le §9 (« un module d'exemple bout en bout — prouve qu'un
module s'ajoute sans toucher au cœur »). Trivial mais traversant **tout** : routes,
permissions, tâche périodique, capability, AI Gateway, audit, table tenant.

| Fichier | Rôle |
|---|---|
| `apps/api/app/modules/sample_digest/manifest.py` | Le `ModuleManifest` complet : permission `sample_digest.read`/`sample_digest.manage`, capability requise `MailCapability`, tâche périodique quotidienne. |
| `apps/api/app/modules/sample_digest/service.py` + `tenant_models.py` | La tâche : via `MailCapability`, liste les emails des dernières 24 h ; via `AIGateway.chat`, produit un résumé (module=`sample_digest` dans le metering — la ventilation par module du §6 devient réelle) ; stocke le digest dans la table tenant `sample_digest_digests` ; audite `sample_digest.digest_generated`. |
| `apps/api/app/modules/sample_digest/router.py` | `GET /api/v1/modules/sample_digest/digests` (`sample_digest.read`), `POST …/run` (`sample_digest.manage` — déclenchement manuel de la tâche). |
| `apps/web/src/pages/module-sample-digest.tsx` | Page minimale : liste des digests, bouton « générer maintenant » — visible seulement si le module est actif (l'API 403 pilote l'affichage). |
| Migration tenant 000N | Table `sample_digest_digests`. |

### T7 — Contrat, CI et clôture

- `make generate-client` (routes du module d'exemple incluses — le contrat OpenAPI
  couvre les modules comme le reste).
- **Vérification « zéro toucher au cœur »** : un test CI (décision D7) échoue si
  l'ajout d'un module exige autre chose que `app/modules/<name>/` + une ligne au
  registre + une migration tenant.
- `README.md` : « écrire un module » (le contrat, les briques disponibles, la
  checklist) — première version du guide développeur de modules.
- `CLAUDE.md` racine + `apps/api/CLAUDE.md` + `apps/api/app/modules/CLAUDE.md` :
  conventions modules (préfixes, permissions, interdiction d'importer un autre module,
  briques socle autorisées), phase courante mise à jour.
- Critère de démo (section E) déroulé et vérifié.

---

## B. Points de conception — décisions et recommandations

| # | Question | Recommandation | Justification |
|---|---|---|---|
| D1 | Découverte des modules : entry-points/plugins ou liste en code ? | **Liste explicite dans `registry.py`** | Les modules sont développés par la même équipe dans le même repo (monolithe modulaire, §2) : la découverte dynamique (entry-points, scan de dossiers) ajoute de la magie, casse pyright et l'analyse IA, pour un besoin de tiers qui n'existe pas. Ajouter un module = une ligne, revue en PR. |
| D2 | Comment garantir qu'aucune route de module n'échappe aux permissions ? | **Validation par introspection au montage, fail-fast au démarrage** | L'invariant racine n°9 doit être structurel, pas conventionnel : le montage inspecte chaque route du router de module et refuse de démarrer si une dépendance `require_permission` manque. Un oubli devient une erreur de boot en CI, pas une faille en prod. |
| D3 | Activation par tenant : control-plane ou DB tenant ? | **Control-plane (`tenant_modules`)** | C'est de la gouvernance de plateforme (qui a souscrit quoi — future facturation §2), lue par le scheduler **avant** de poser un contexte tenant et par le montage à chaque requête. En DB tenant, le scheduler devrait ouvrir N bases juste pour savoir quoi faire. Aucune donnée métier : compatible §3. |
| D4 | Scheduler : entrées beat dynamiques par tenant ou fan-out ? | **Beat statique par tâche de module + fan-out sur les tenants actifs** | Le beat dynamique par tenant (N × M entrées) exige un scheduler custom ou redbeat — fragile et peu documenté. Le fan-out reprend le pattern déjà éprouvé des tâches multi-tenants (Phases 4/5/6) : une entrée par tâche, itération filtrée, isolation des échecs. |
| D5 | Tables des modules : arbre Alembic par module ou arbre tenant unique ? | **Arbre tenant unique, tables préfixées `<name>_`** | Le runner multi-bases (Phase 1) reste l'unique machinerie de migration ; des arbres par module recréeraient le problème des branches Alembic écarté en Phase 1 (D2). Schéma identique pour tous les tenants = un seul chemin testé ; l'activation est comportementale, pas structurelle. |
| D6 | Désactivation : que deviennent les données du module ? | **Conservées ; suppression = décision explicite séparée** | Une désactivation peut être temporaire (impayé, essai) : détruire des données métier en effet de bord serait irrattrapable. La purge s'appuie sur le cadre de rétention Phase 4 (le module enregistre ses types purgeables) le jour où le besoin est réel. |
| D7 | Comment prouver « zéro toucher au cœur » durablement ? | **Test CI structurel** | Un test qui liste les fichiers hors `app/modules/` + `registry.py` référencés par le module d'exemple (imports inversés interdits : le cœur n'importe jamais `app/modules/*` sauf le registre). La promesse du §9 devient vérifiable à chaque PR, pas une déclaration d'intention. |
| D8 | Un module peut-il importer un autre module ? | **Non — interdit (vérifié par le test D7)** | Les dépendances inter-modules recréeraient un monolithe enchevêtré sous un autre nom. Tout partage passe par le socle (capabilities, gateway, audit) ; si deux modules doivent partager du code métier, c'est un signe qu'une brique socle manque — décision consciente à ce moment-là. |

---

## C. Invariants et règles absolues de la phase

1. **Ajouter un module ne modifie jamais le cœur** : uniquement `app/modules/<name>/`,
   une ligne de registre, une migration tenant — garanti par le test structurel D7.
2. **Toute route de module porte `require_permission("<name>.…")` + le module actif
   pour le tenant** — vérifié au démarrage (D2), pas seulement en revue.
3. **Un module ne consomme que les briques socle** : capabilities (jamais les APIs
   providers — invariant Phase 5), `AIGateway` (jamais LiteLLM — invariant Phase 6),
   `get_tenant_session()`, `record_audit_event`. Jamais un autre module (D8).
4. **Toute tâche de module s'exécute dans un contexte tenant posé**, sous verrou
   anti-chevauchement, et son échec sur un tenant n'affecte pas les autres.
5. **Les permissions de modules sont namespacées `<name>.*`** — le namespace `core.*`
   leur est interdit (registre le refuse).
6. **Le metering IA ventile par module** (`module=<name>`) — aucune consommation IA
   anonyme.
7. Les invariants Phases 0-6 restent en vigueur.

---

## D. Tests à écrire

**Backend (pytest, Postgres réel — `apps/api/tests/`)**
- `test_module_contract.py` : manifest valide accepté ; nom en collision, permission
  hors namespace, route sans `require_permission`, tâche mal nommée → refus au
  montage (fail-fast prouvé).
- `test_module_mounting.py` : routes montées sous `/api/v1/modules/<name>/` ; module
  non activé → 403 même avec la permission ; activé → matrice permissions du module ;
  handlers connecteurs enregistrés.
- `test_module_activation.py` : activation sans la capability requise → erreur
  explicite ; avec → activé + audit ; désactivation → routes 403, tâches non
  publiées, données tenant intactes (D6).
- `test_module_scheduler.py` : fan-out ne publie que pour les tenants actifs ; échec
  sur un tenant → les autres passent ; verrou anti-chevauchement ; contexte tenant
  posé dans la tâche (une requête DB du module le prouve).
- `test_sample_digest.py` : bout en bout avec capabilities et gateway mockés — la
  tâche lit les mails (mock), appelle l'IA (mock, metering `module=sample_digest`
  vérifié), écrit le digest en DB tenant, audite ; routes lecture/run.
- `test_module_isolation.py` (le test D7) : le cœur n'importe aucun module (hors
  registre) ; `sample_digest` n'importe rien hors socle autorisé + lui-même.

**Frontend (vitest)** : page digest (liste, bouton run, absence si 403) ; back-office
activation.

**CI** : structure inchangée ; le test D7 devient le gardien permanent de la promesse
de la phase.

---

## E. Critère de démo de fin de phase

> Sur staging : le back-office montre `sample_digest` disponible ; son activation sur
> `acme` échoue tant qu'aucune connexion Google/Microsoft n'est active (message
> explicite), puis réussit. Le lendemain (ou via « générer maintenant »), la page du
> module sur `acme.<domaine>` affiche un digest réel des emails du compte connecté,
> résumé par le provider IA de la politique du tenant ; le journal d'audit montre
> `sample_digest.digest_generated` ; la page consommation IA ventile `sample_digest`.
> Sur `globex` (module non activé), la page n'existe pas et l'API répond 403 à un
> membre pourtant `owner`. **La revue de la PR du module d'exemple montre zéro
> fichier du cœur modifié**, et le test d'isolation le garantit pour les suivants.

C'est la traduction exécutable du §9 : la coquille existe, un module la traverse de
bout en bout, et le coût marginal d'un module est prouvé minimal.

---

## F. Dépendances manquantes et risques propres à la phase

1. **La démo dépend des phases 5 et 6 opérationnelles sur staging** (connexion réelle
   + clés IA) : sans elles, tout se démontre en mocks — les tests suffisent au merge,
   pas à la démo.
2. **Le contrat de module va évoluer** avec les premiers vrais modules (webhooks
   propres aux modules ? UI déclarée ? paramètres par tenant ?) : la coquille assume
   d'être minimale ; toute extension du contrat est une PR du cœur, versionnée dans
   le manifest (`version` présent dès maintenant).
3. **Le front des modules n'est pas « pluggable »** : la page du module d'exemple est
   codée en dur dans `apps/web` — assumé (une SPA, un produit) ; un vrai système de
   front modulaire serait de la sur-ingénierie sans plusieurs vrais modules.
4. **Charge du fan-out** : cadence × modules × tenants tâches publiées — négligeable
   à l'échelle visée (§8.7), à revoir avec les métriques Phase 8 si N grossit.
5. **Frontière socle/module encore théorique** : la première tentation de faire
   communiquer deux modules (D8) sera le vrai test de la discipline — la règle et le
   test existent pour forcer la discussion au bon endroit.

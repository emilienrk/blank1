# Phase 4 — Audit + socle RGPD : plan d'implémentation détaillé

> Référence : `docs/architecture-plan.md` (§7 « Logs, observabilité (allégée) et
> RGPD », §9 « Phase 4 »). Cette phase couvre : le journal d'audit applicatif en DB
> tenant (émission + consultation), l'export RGPD d'un tenant, l'effacement d'un
> tenant, et le cadre de rétention/purge configurable. Elle précède volontairement les
> connecteurs : « dès que des données clients circulent, l'audit doit exister » (§9).
> Rien de plus — l'instrumentation d'audit des connecteurs arrive avec eux (Phase 5),
> celle des modules métier en Phase 7 ; la purge des backups reste une procédure
> opérateur documentée (pgBackRest, hors runtime) finalisée en Phase 8. Chaque
> anticipation est signalée explicitement.

## État des lieux (attendu en entrée de phase)

Hypothèses : Phases 0-3 fusionnées. Le socle émet déjà des actions auditables
(invitations, changements de rôle, retraits de membres, équipes) sans les journaliser ;
les logs techniques (structlog → Loki, 30 j) existent mais sont **explicitement** autre
chose que l'audit (§7 : l'audit est une donnée du client, en DB tenant, consultable
dans l'app). Le back-office (Phase 3) offre le canal opérateur pour l'export et
l'effacement. Le module `app/audit/` et `app/gdpr/` n'existent pas ; la CLI `saas` et
le runner de migrations sont les points d'appui.

---

## A. Tâches ordonnées

### T1 — Modèle d'audit en DB tenant + migration

Rôle : la table du §7, append-only, donnée du client.

| Fichier | Rôle |
|---|---|
| `apps/api/app/audit/tenant_models.py` | Table tenant `audit_events` : `id` UUID, `occurred_at` (UTC, indexé), `actor_user_id` UUID nullable (null = système/CLI), `actor_label` (affichage figé au moment du fait — l'email peut changer, l'audit non, décision D3), `action` texte namespacé (`core.member.invited`, `core.member.role_changed`, `core.team.created`…), `resource_type`, `resource_id`, `payload` JSONB (détail factuel : ancien/nouveau rôle, nom d'équipe…). Aucune colonne modifiable après insertion. |
| `apps/api/migrations/tenant/versions/…` | Révision tenant 0003 (`audit_events` + index `(occurred_at)`, `(action)`). |

### T2 — Émission : `record_audit_event()`

Rôle : l'API d'écriture unique, réutilisée par toutes les phases suivantes.

| Fichier | Rôle |
|---|---|
| `apps/api/app/audit/service.py` | `record_audit_event(session, *, action, resource_type, resource_id, payload)` : écrit via la session tenant **courante** (même transaction que l'action auditée, décision D1) ; l'acteur vient du contexte (user courant, ou `actor_user_id=None` + label `system`/`cli` hors requête). Registre typé des actions connues (enum extensible par namespace — `core.*` maintenant, `connector.*`/`module_x.*` plus tard). |
| `apps/api/app/directory/service.py` (extension) | Instrumentation du socle existant : invitation créée/révoquée/acceptée, rôle modifié, membre retiré, équipe créée/modifiée/supprimée, membre d'équipe ajouté/retiré. |
| `apps/api/app/tenancy/provisioning.py` (extension) | Premier événement de la vie du tenant : `core.tenant.provisioned` (écrit après le seed, acteur `cli`). |

### T3 — Consultation : API + page SPA

| Fichier | Rôle |
|---|---|
| `apps/api/app/audit/router.py` | `GET /api/v1/audit/events` (`core.audit.read`, accordée à `owner`/`admin` — mise à jour de `permissions.py`) : pagination par curseur `(occurred_at, id)` (décision D4), filtres `action`, `actor_user_id`, bornes de dates. Lecture seule — **aucune route d'écriture ou de suppression**. |
| `apps/web/src/pages/audit.tsx` | Page « Journal d'audit » : table paginée, filtres, détail du payload ; composants `packages/ui` de la Phase 3. |

### T4 — Export RGPD d'un tenant (`app/gdpr/`)

Rôle : « export = dump base tenant + données control-plane associées » (§7).

| Fichier | Rôle |
|---|---|
| `apps/api/app/gdpr/export.py` | Tâche Celery `export_tenant(tenant_id)` : (1) `pg_dump` de la DB tenant (format custom `-Fc`) ; (2) extraction JSON des données control-plane du tenant (ligne catalogue, users membres — id/email/display_name, memberships, invitations en cours) ; (3) manifeste JSON (horodatage, versions de schéma, contenu) ; (4) archive tar chiffrée via `KeyProvider` (réutilisé tel quel), déposée dans `gdpr_export_dir` (volume dédié), nom `export_<slug>_<horodatage>.tar.enc`, TTL de rétention `gdpr_export_ttl_days` (déf. 7, purge par tâche beat). |
| `apps/api/app/core/config.py` (extension) | `gdpr_export_dir`, `gdpr_export_ttl_days`, `gdpr_erasure_grace_days` (déf. 7). |
| `apps/api/app/cli.py` + `app/admin/router.py` (extensions) | `saas tenant export <slug>` (lance et attend la tâche, affiche le chemin) ; côté back-office : `POST /api/v1/admin/tenants/{slug}/export` + liste des exports disponibles avec téléchargement (platform_admin, via WireGuard — décision D5 : l'export est remis par l'opérateur, pas de self-service tenant dans cette phase). |

### T5 — Effacement RGPD d'un tenant

Rôle : « effacement = drop + purge catalogue + purge backups à échéance » (§7), avec
garde-fous — c'est l'opération la plus destructrice du système.

| Fichier | Rôle |
|---|---|
| `apps/api/app/gdpr/erasure.py` | Machine à deux temps (décision D2) : (1) `request_erasure(slug)` → état catalogue `pending_deletion` (nouvel état de l'enum `tenants.state`), `resolve_tenant` refuse désormais le tenant (403 comme `suspended`), horodatage de demande ; (2) après `gdpr_erasure_grace_days`, la tâche beat `execute_pending_erasures` exécute : `DROP DATABASE` (réutilise la mécanique validée du provisioning — identifiant dérivé du slug validé, invariant I6 Phase 1), suppression des memberships et invitations du tenant, suppression des users **devenus orphelins** (membres d'aucun autre tenant, décision D6), éviction de l'engine (`TenantEngineManager.invalidate`), puis suppression de la ligne catalogue avec écriture d'une trace minimale `erasure_log` control-plane (slug, horodatages demande/exécution — aucune donnée métier). `cancel_erasure(slug)` pendant le délai de grâce → retour à `active`. |
| `apps/api/migrations/controlplane/versions/…` | Révision : état `pending_deletion`, table `erasure_log`, horodatage de demande. |
| `apps/api/app/cli.py` + `app/admin/router.py` (extensions) | `saas tenant delete <slug>` (demande, **confirmation interactive par re-saisie du slug**), `saas tenant cancel-delete <slug>` ; équivalents back-office. |
| `docs/runbook-gdpr.md` | Procédure opérateur : purge des backups pgBackRest contenant le tenant effacé à l'échéance de rétention (le runtime ne pilote pas pgBackRest — assumé, finalisé en Phase 8 avec la config backups). |

### T6 — Cadre de rétention/purge configurable

Rôle : « jobs de rétention/purge configurables par type de donnée » (§7), en cadre
générique que les phases suivantes rempliront.

| Fichier | Rôle |
|---|---|
| `apps/api/app/gdpr/retention.py` | Registre de **politiques de rétention** : chaque type de donnée purgeable s'enregistre avec (clé, durée par défaut, fonction de purge prenant une session tenant et une date limite). Première politique enregistrée : `audit_events` (déf. `audit_retention_days` = 365). Surcharge par tenant via `tenant_settings` (table Phase 1, enfin utile : clé `retention.<type>` en jours). |
| `apps/api/app/gdpr/tasks.py` | Tâche beat quotidienne `apply_retention_policies` : itère les tenants `active`, pose le contexte, applique chaque politique (suppressions par lots, décision D7) ; rapport JSON par tenant/type dans les logs. |

### T7 — Livrables non techniques (hors code, versionnés dans `docs/`)

- `docs/rgpd/registre-traitements.md` : trame du registre des traitements (plateforme
  = sous-traitant).
- `docs/rgpd/sous-traitants.md` : liste des sous-traitants ultérieurs (hébergement,
  relais SMTP, providers IA avec renvoi vers la politique zéro-rétention Phase 6).
- `docs/rgpd/notification-violation.md` : procédure de notification de violation
  (72 h, contacts, modèle de message).
- Trames à compléter par l'utilisateur (juridique hors périmètre technique) —
  l'important est qu'elles existent et soient versionnées (§7).

### T8 — Contrat, CI et clôture

- `make generate-client` : routes `audit` + admin export/erasure.
- `README.md` : sections audit (qui voit quoi) et RGPD (export, effacement, délai de
  grâce, rétention).
- `CLAUDE.md` racine + `apps/api/CLAUDE.md` : modules `audit`/`gdpr`, règle « toute
  action métier significative appelle `record_audit_event` dans la même transaction »,
  phase courante mise à jour.
- Critère de démo (section E) déroulé et vérifié.

---

## B. Points de conception — décisions et recommandations

| # | Question | Recommandation | Justification |
|---|---|---|---|
| D1 | Écriture de l'audit : même transaction, after-commit, ou tâche Celery ? | **Même transaction que l'action auditée** | Atomicité parfaite : impossible d'avoir l'action sans sa trace ou l'inverse. Un envoi asynchrone peut perdre des événements (crash entre commit et publication) — inacceptable pour de l'audit. Coût : une insertion par action, négligeable. |
| D2 | Effacement : immédiat ou en deux temps ? | **Deux temps avec délai de grâce (`pending_deletion`, déf. 7 j)** | `DROP DATABASE` est irréversible ; une erreur d'opérateur (mauvais slug) doit être rattrapable. Le tenant devient inaccessible immédiatement (l'obligation d'arrêt de traitement est satisfaite), la destruction physique suit. Annulable pendant le délai. |
| D3 | L'audit référence-t-il l'acteur par id seul ? | **id + `actor_label` figé à l'écriture** | Les users vivent en control-plane et peuvent changer d'email ou disparaître (effacement d'un autre tenant, D6) ; un journal d'audit doit rester lisible tel qu'au moment du fait, sans jointure inter-bases (impossible proprement) ni fuite de PII d'un user devenu étranger au tenant. |
| D4 | Pagination de la consultation ? | **Curseur `(occurred_at, id)`** | Un journal grandit sans borne : `OFFSET` se dégrade linéairement et se décale quand des lignes s'insèrent. Le curseur composite est stable, indexé, et c'est le pattern le plus documenté. |
| D5 | Export : self-service pour l'owner du tenant ? | **Non — opérateur (CLI + back-office) dans cette phase** | L'onboarding est manuel et les demandes RGPD passent par le support (réalité B2B à ce stade). Un self-service exposerait un fichier lourd et sensible via la surface publique — à concevoir sérieusement (liens signés, quotas) le jour où la demande existe. L'archive chiffrée + WireGuard suffit au besoin légal. |
| D6 | Effacement : que deviennent les users control-plane du tenant ? | **Supprimés s'ils ne sont membres d'aucun autre tenant, conservés sinon** | L'identité globale est partagée entre tenants (§3) : la supprimer aveuglément casserait d'autres memberships. L'orphelin, lui, n'a plus aucune raison d'être conservé — le garder serait une violation de minimisation. |
| D7 | Purges : `DELETE` massif ou par lots ? | **Par lots (ex. 5 000 lignes) avec commit intermédiaire** | Un `DELETE` de millions de lignes tient des verrous longs et gonfle le WAL ; le lot borné est le pattern standard, trivial à écrire, et la tâche est quotidienne donc le rattrapage est naturel. |
| D8 | Chiffrement des exports : `KeyProvider` ou clé dédiée ? | **`KeyProvider` existant (clé maître)** | L'interface AES-256-GCM de la Phase 2 est faite pour ça ; une hiérarchie de clés dédiée à l'export serait de la sur-ingénierie tant que les exports restent sur le volume local à TTL court. La rotation de clés est un sujet Phase 8 (runbook). |

---

## C. Invariants et règles absolues de la phase

1. **L'audit est append-only** : aucune route ni fonction de modification/suppression
   d'`audit_events` en dehors de la politique de rétention (T6) — pas même pour un
   platform_admin.
2. **Audit ≠ logs techniques** : l'audit vit en DB tenant, peut contenir des données
   métier, et est montré au client ; les logs techniques restent JSON/Loki **sans PII**
   (invariant racine n°4). Aucun événement d'audit n'est dupliqué en clair dans Loki.
3. **Toute action métier significative du socle écrit son événement d'audit dans la
   même transaction** (D1) — règle qui s'imposera aux connecteurs (Phase 5) et aux
   modules (Phase 7).
4. **L'effacement passe toujours par le délai de grâce** — aucun chemin de drop direct
   hors `execute_pending_erasures` (et le `retry-provision` Phase 1, qui ne droppe que
   des bases jamais devenues `active`).
5. **Les exports sont chiffrés au repos et à durée de vie bornée** ; jamais servis par
   la surface publique.
6. Les invariants Phases 0-3 restent en vigueur (contexte tenant obligatoire — les
   tâches beat de cette phase le posent explicitement par tenant, `require_permission`
   partout, secrets chiffrés, client TS généré, admin derrière WireGuard).

---

## D. Tests à écrire

**Backend (pytest, Postgres réel — `apps/api/tests/`)**
- `test_audit_events.py` : chaque action instrumentée (invitation, rôle, retrait,
  équipe) écrit l'événement attendu ; rollback de l'action → pas d'événement (preuve
  D1) ; acteur système (provisioning) → `actor_user_id` null + label ; lecture
  paginée par curseur stable ; filtre par action ; `member` → 403 sur la route.
- `test_gdpr_export.py` : export d'un tenant peuplé → archive présente, déchiffrable
  (KeyProvider), manifeste cohérent, dump restaurable (`pg_restore` dans une base
  jetable → tables et lignes attendues) ; purge des exports au-delà du TTL.
- `test_gdpr_erasure.py` : demande → `pending_deletion`, `resolve_tenant` → 403,
  annulation → `active` ; exécution après grâce (horloge simulée) → DB droppée,
  catalogue purgé, user orphelin supprimé, user multi-tenant conservé, `erasure_log`
  écrit, engine invalidé ; slug inconnu / re-demande → erreurs propres.
- `test_retention.py` : politique `audit_events` purge au-delà de la durée, respecte
  la surcharge `tenant_settings`, procède par lots ; un tenant en échec n'empêche pas
  les autres (même philosophie que le runner Phase 1).
- `test_cli.py` (extension) : `tenant export`, `tenant delete` (confirmation par
  re-saisie), `cancel-delete`.

**Frontend (vitest)** : `audit.test.tsx` (rendu table, filtres, pagination).

**CI** : structure inchangée ; `pg_dump`/`pg_restore` requis dans l'image de test
(déjà présents via l'image Postgres client — à vérifier au premier commit de T4).

---

## E. Critère de démo de fin de phase

> Sur staging : Alice (owner d'`acme`) invite un membre, change son rôle, crée une
> équipe — la page « Journal d'audit » montre les événements horodatés avec acteur ;
> Bob (`member`) reçoit 403 sur cette page. L'opérateur lance `saas tenant export
> acme` : l'archive chiffrée apparaît, se déchiffre et se restaure dans une base
> jetable. Il exécute ensuite `saas tenant delete globex` (re-saisie du slug) :
> `globex.<domaine>` répond 403 immédiatement ; `cancel-delete` le ranime ; re-demande
> puis passage du délai de grâce (raccourci en staging) : la DB n'existe plus, le
> catalogue est purgé, `erasure_log` en garde la trace minimale, et les users
> uniquement membres de `globex` ont disparu du control-plane. La purge de rétention
> quotidienne tourne et se voit dans Loki (rapport par tenant, sans PII).

C'est la traduction exécutable du §7 : l'audit existe avant les connecteurs, et les
deux droits RGPD structurants (accès/portabilité, effacement) sont opérables.

---

## F. Dépendances manquantes et risques propres à la phase

1. **La purge des backups reste manuelle** (runbook T5) : pgBackRest n'est configuré
   qu'en Phase 8 — d'ici là, l'engagement d'effacement complet inclut une étape
   opérateur documentée. Assumé et tracé.
2. **`pg_dump`/`pg_restore` dans l'image applicative** : l'export les exécute en
   sous-processus — vérifier leur présence (paquet `postgresql-client` 17) dans
   l'image Docker, sinon l'ajouter (léger).
3. **Volume `gdpr_export_dir`** à déclarer dans le Compose (persistant, hors image) —
   petit changement d'infra à ne pas oublier.
4. **Croissance d'`audit_events`** : borné par la rétention T6 (365 j par défaut),
   mais un tenant très actif peut grossir — le curseur D4 et les index tiennent,
   surveiller en Phase 8 (métriques).
5. **Les trames juridiques (T7) ne valent pas conseil juridique** : à faire relire
   (DPA notamment) avant les premiers clients réels — hors périmètre technique.
6. Anticipation assumée : le registre d'actions d'audit prévoit les namespaces
   `connector.*` et `module_x.*` (simple convention de nommage, zéro code spéculatif).

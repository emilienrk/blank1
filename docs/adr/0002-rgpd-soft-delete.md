# 0002 — RGPD à délai de grâce archivé, soft-delete des tenants

- **Date** : 2026-07-18
- **Statut** : accepté

## Contexte

La Phase 4 avait livré un pipeline RGPD complet : effacement à délai de grâce
(`pending_deletion` puis `DROP DATABASE` par tâche beat), exports chiffrés à TTL
borné (`pg_dump` + archive), politique de rétention de l'audit par tenant. Trois
tâches beat, six modules Python, une table `erasure_log`, quatre settings. Sans
client en production, ce pipeline protège des données qui n'existent pas encore ;
l'obligation légale réelle du MVP (pouvoir supprimer un tenant) se satisfait plus
simplement.

## Décision

- **Suppression de tenant = soft-delete** : `Tenant.deleted_at` remplace l'état
  `pending_deletion` + `deletion_requested_at`. `saas tenant delete` (confirmation
  par re-saisie du slug) pose l'horodatage ; le tenant devient invisible partout
  (résolution HTTP → 404, fan-out beat, webhooks, callbacks OAuth) mais ses données
  restent en base. Restauration : `UPDATE tenants SET deleted_at = NULL`.
- **`app/gdpr/` archivé** entièrement (effacement, exports, rétention, tâches beat,
  CLI `tenant export`/`cancel-delete`, settings associés).
- **Audit simplifié** : `record_audit_event`/`record_audit_event_for_tenant` et la
  table `audit_events` sont conservés tels quels (même transaction que l'action,
  append-only par design). Le registre verrouillé `ACTIONS` et
  `register_module_actions` disparaissent — l'action est une string namespacée par
  convention (`core.*`, `connector.*`, `<module>.*`), le namespace des actions de
  modules restant validé au démarrage par le registre des modules.

## Conséquences

- Un effacement physique RGPD (demande réelle d'un client) redevient une opération
  manuelle : suppression SQL des données du tenant. Acceptable au volume MVP ;
  le pipeline complet est réintroduisible si le besoin devient récurrent.
- Plus d'export RGPD automatisé : `pg_dump` manuel au besoin.
- L'audit croît sans purge automatique (la rétention est partie avec `app/gdpr/`) ;
  à réévaluer quand le volume le justifiera.
- Une faute de frappe dans une action d'audit du socle n'est plus bloquée par un
  registre — la revue de code et les tests la couvrent.

## Procédure de réintroduction

1. `git checkout archive/pre-mvp-simplification -- apps/api/app/gdpr docs/runbook-gdpr.md`
2. Restaurer les 3 entrées beat `gdpr-*` et l'import `app.gdpr.tasks`
   (`app/worker.py`), les commandes CLI `tenant export`/`delete`/`cancel-delete`,
   les settings `AUDIT_RETENTION_DAYS`/`GDPR_*` (`app/core/config.py` +
   `.env.example`), le volume `gdpr_exports` du compose.
3. Adapter au modèle single-DB (ADR 0001) : l'effacement par `DROP DATABASE` devient
   un `DELETE ... WHERE tenant_id = …` (les FK `ON DELETE CASCADE` du schéma unique
   font l'essentiel), l'export par `pg_dump` devient un export filtré par tenant.

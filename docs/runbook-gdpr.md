# Runbook opérateur — RGPD (export, effacement, purge des backups)

> Procédures d'exploitation pour l'export et l'effacement RGPD (`app.gdpr`,
> voir `docs/phase-4-audit-rgpd-plan.md`). Réservé aux opérateurs (accès CLI
> machine ou back-office derrière WireGuard).

## Export d'un tenant

```bash
docker compose run --rm api saas tenant export <slug>
# ou depuis le back-office : POST /api/v1/admin/tenants/{slug}/export (asynchrone),
# puis GET /api/v1/admin/tenants/{slug}/exports pour le lien de téléchargement.
```

L'archive (`export_<slug>_<horodatage>.tar.enc`) est chiffrée avec la clé
maître (`KeyProvider`, AES-256-GCM) et déposée sur le volume dédié
(`GDPR_EXPORT_DIR`). Elle est purgée automatiquement après `GDPR_EXPORT_TTL_DAYS`
(déf. 7 j) par la tâche beat `core.gdpr.purge_expired_exports`. **Ne jamais**
transmettre l'archive telle quelle par un canal non sécurisé — la déchiffrer
localement (même mécanisme que `app.core.crypto.EnvKeyProvider`) avant remise
au client, ou transmettre via un canal déjà chiffré de bout en bout.

## Effacement d'un tenant

```bash
docker compose run --rm api saas tenant delete <slug>   # re-saisie du slug exigée
docker compose run --rm api saas tenant cancel-delete <slug>   # pendant le délai de grâce
```

Séquence : le tenant passe immédiatement en `pending_deletion` (inaccessible,
403 sur toute requête applicative) ; après `GDPR_ERASURE_GRACE_DAYS` (déf. 7 j),
la tâche beat `core.gdpr.execute_pending_erasures` (horaire) exécute le
`DROP DATABASE`, purge le catalogue (memberships, invitations, users devenus
orphelins) et écrit une trace minimale dans `erasure_log` (control-plane).
**Aucun autre chemin de suppression n'existe** — un tenant ne peut être effacé
que par cette procédure.

## Purge des backups pgBackRest (étape manuelle jusqu'à la Phase 8)

pgBackRest n'est configuré et automatisé qu'en Phase 8 (durcissement). D'ici
là, l'engagement d'effacement complet d'un tenant inclut une étape opérateur
manuelle une fois `execute_pending_erasures` passé :

1. Identifier les sauvegardes contenant encore la base droppée (répertoire
   pgBackRest, ou nom de base dans les archives WAL selon la config retenue).
2. Une fois la fenêtre de rétention des backups écoulée (à définir en Phase 8
   avec la politique de rétention pgBackRest), vérifier qu'aucune sauvegarde
   restaurable ne contient plus la base du tenant effacé.
3. Documenter la date de purge effective dans un registre opérateur (hors
   application — pas de donnée métier dans les logs techniques, invariant
   racine n°4).

Cette étape est **assumée manuelle** pour cette phase (voir plan Phase 4 §F1) ;
elle sera pilotée automatiquement quand la politique de rétention pgBackRest
sera actée en Phase 8.

## Rétention/purge courante

La tâche beat quotidienne `core.gdpr.apply_retention_policies` applique
chaque politique enregistrée (`app.gdpr.retention`) à tous les tenants actifs
— par lots (5 000 lignes, commit intermédiaire). Le rapport (nombre de lignes
purgées par tenant/type) est loggé en JSON sans PII (Loki). Une politique
peut être surchargée par tenant via `tenant_settings` (clé
`retention.<type>`, valeur en jours).

## Vérification après un effacement

```bash
docker compose run --rm api saas tenant list   # le tenant n'apparaît plus
```

`erasure_log` (control-plane) conserve `slug`, `requested_at`, `executed_at` —
seule trace persistante, sans donnée métier.

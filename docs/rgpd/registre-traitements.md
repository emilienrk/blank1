# Registre des traitements (trame)

> Trame technique à faire relire et compléter par un juriste avant les premiers
> clients réels (voir `docs/phase-4-audit-rgpd-plan.md` §F5) — hors périmètre
> technique de cette phase. La plateforme agit comme **sous-traitant** de ses
> clients (les tenants) au sens RGPD ; chaque tenant reste responsable de
> traitement pour ses propres données métier.

## Traitement 1 — Hébergement et exécution de la plateforme SaaS

- **Finalité** : fournir le service B2B souscrit par le client (tenant) —
  authentification, annuaire, et modules métier des phases suivantes.
- **Responsable de traitement** : le client (tenant).
- **Sous-traitant** : l'éditeur de la plateforme (auto-hébergement France, voir
  `docs/architecture-plan.md` §1).
- **Catégories de données** : identités (email, nom affiché), rôles et
  appartenances, journal d'audit applicatif (`app.audit`), contenus métier des
  modules activés par le tenant.
- **Base légale** : exécution du contrat liant l'éditeur et le tenant.
- **Durée de conservation** : durée du contrat + délai de grâce d'effacement
  (`gdpr_erasure_grace_days`, déf. 7 j, `app.gdpr.erasure`) ; journal d'audit
  purgé selon la politique de rétention (`audit_retention_days`, déf. 365 j,
  `app.gdpr.retention`), surchargeable par tenant.
- **Mesures de sécurité** : une base PostgreSQL par tenant (minimisation
  structurelle), secrets chiffrés en base (argon2id, AES-256-GCM), TLS,
  disques chiffrés (LUKS, engagement infra), accès back-office restreint
  (WireGuard + `require_platform_admin`).

## Traitement 2 — Authentification et gestion des comptes

- **Finalité** : identifier les utilisateurs, gérer les rôles et invitations.
- **Catégories de données** : email, mot de passe (haché argon2id), secret
  TOTP (chiffré), sessions (token haché), historique de connexion minimal.
- **Base légale** : exécution du contrat.
- **Durée de conservation** : purge horaire des sessions/jetons expirés
  (`app.auth.tasks`) ; suppression du compte à l'effacement du dernier tenant
  dont l'utilisateur est membre (`app.gdpr.erasure`, décision D6).

## Traitement 3 — Export et effacement RGPD (droits d'accès et à l'oubli)

- **Finalité** : répondre aux demandes d'accès/portabilité et d'effacement
  d'un tenant.
- **Catégories de données** : l'intégralité des données du tenant concerné
  (dump de sa base + extrait control-plane), le temps de la remise à
  l'opérateur.
- **Durée de conservation** : archive d'export chiffrée, TTL
  `gdpr_export_ttl_days` (déf. 7 j), jamais servie publiquement.

## À compléter (juridique)

- Identité légale de l'éditeur (raison sociale, DPO le cas échéant).
- Transferts hors UE le cas échéant (providers IA, Phase 6 — voir
  `docs/rgpd/sous-traitants.md`).
- Analyse d'impact (AIPD) si un traitement à risque est identifié plus tard.

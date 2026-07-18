# Registre des traitements (trame)

> Trame technique à faire relire et compléter par un juriste avant les premiers
> clients réels — hors périmètre technique du MVP. La plateforme agit comme **sous-traitant** de ses
> clients (les tenants) au sens RGPD ; chaque tenant reste responsable de
> traitement pour ses propres données métier.

## Traitement 1 — Hébergement et exécution de la plateforme SaaS

- **Finalité** : fournir le service B2B souscrit par le client (tenant) —
  authentification, annuaire, et modules métier des phases suivantes.
- **Responsable de traitement** : le client (tenant).
- **Sous-traitant** : l'éditeur de la plateforme (auto-hébergement France).
- **Catégories de données** : identités (email, nom affiché), rôles et
  appartenances, journal d'audit applicatif (`app.audit`), contenus métier des
  modules activés par le tenant.
- **Base légale** : exécution du contrat liant l'éditeur et le tenant.
- **Durée de conservation** : durée du contrat ; la résiliation déclenche un
  soft-delete du tenant (ADR 0002 : données conservées mais inaccessibles).
  L'effacement définitif et la rétention automatisée du journal d'audit sont
  archivés pour le MVP — à réintroduire avant les premiers clients réels
  (procédure dans l'ADR 0002).
- **Mesures de sécurité** : isolation par tenant garantie par construction
  (filtre `tenant_id` injecté sur chaque requête, ADR 0001), secrets chiffrés
  en base (argon2id, AES-256-GCM), TLS, disques chiffrés (LUKS, engagement
  infra), administration plateforme par CLI/SQL avec accès shell machine
  uniquement (ADR 0003).

## Traitement 2 — Authentification et gestion des comptes

- **Finalité** : identifier les utilisateurs, gérer les rôles et invitations.
- **Catégories de données** : email, mot de passe (haché argon2id), secret
  TOTP (chiffré), sessions (token haché), historique de connexion minimal.
- **Base légale** : exécution du contrat.
- **Durée de conservation** : purge horaire des sessions/jetons expirés
  (`app.auth.tasks`). La suppression de compte automatisée est archivée avec
  la brique d'effacement (ADR 0002) — traitement manuel (SQL) en attendant.

## Traitement 3 — Export et effacement RGPD (droits d'accès et à l'oubli)

- **État MVP** : la brique automatisée (export chiffré, effacement à délai de
  grâce) est **archivée** (ADR 0002, récupérable via le tag
  `archive/pre-mvp-simplification`). En attendant sa réintroduction, une
  demande d'accès ou d'effacement se traite manuellement par l'opérateur
  (requêtes SQL sur les données du tenant) — acceptable tant qu'il n'y a pas
  de clients réels.

## À compléter (juridique)

- Identité légale de l'éditeur (raison sociale, DPO le cas échéant).
- Transferts hors UE le cas échéant (providers IA — voir
  `docs/rgpd/sous-traitants.md`).
- Analyse d'impact (AIPD) si un traitement à risque est identifié plus tard.
- Réintroduire l'export/effacement automatisés (ADR 0002) avant les premiers
  clients réels.

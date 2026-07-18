# 0003 — Back-office archivé, administration via CLI/SQL

- **Date** : 2026-07-18
- **Statut** : accepté

## Contexte

La Phase 3 avait livré un back-office complet : SPA dédiée (`apps/admin`), 18 routes
`/api/v1/admin/*` protégées par `require_platform_admin`, rôle `is_platform_admin`
posé uniquement par CLI, vhost Caddy interne (`Caddyfile.admin`) et double barrière
réseau. Pour une équipe de 2 en phase MVP, cette surface (une SPA, une image Docker,
un vhost, des tests dédiés) coûte plus qu'elle ne rend : les opérations
d'administration sont rares et peuvent passer par la CLI `saas` ou un accès SQL direct.

## Décision

Archiver l'intégralité du back-office : `apps/admin/`, `apps/api/app/admin/`,
`app/ai/admin_service.py`, `require_platform_admin`, le champ `User.is_platform_admin`,
les commandes `saas admin grant/revoke`, `infra/caddy/Caddyfile.admin` et le bloc 403
`/api/v1/admin/*` du vhost public. L'administration plateforme se fait par la CLI
`saas` (tenants, invitations, migrations) et, au besoin, par SQL sur la machine.

## Conséquences

- Deux services Docker en moins (`admin` + son vhost), une SPA en moins à maintenir,
  plus de notion de rôle plateforme dans le modèle `users`.
- Les usages IA et politiques par tenant (ex-routes `/api/v1/admin/ai/*`) se
  consultent en SQL (`ai_usage_daily`, `tenant_ai_policies`) tant qu'aucune surface ne
  les réexpose.
- L'invariant « surfaces admin jamais exposées publiquement » devient sans objet côté
  applicatif : il n'y a plus de surface admin HTTP.
- Les rapports de migration persistés (`migration_reports`) disparaissent avec leur
  seul consommateur.

## Procédure de réintroduction

1. `git checkout archive/pre-mvp-simplification -- apps/admin apps/api/app/admin apps/api/app/ai/admin_service.py infra/caddy/Caddyfile.admin`
2. Restaurer `require_platform_admin` (`app/auth/deps.py`), `User.is_platform_admin`
   (+ migration), le groupe CLI `admin` (`app/cli.py`), le montage du router dans
   `app/main.py`, l'import `app.admin.tasks` dans `app/worker.py`.
3. Restaurer le service `admin` du `docker-compose.yml`, `apps/admin` dans
   `pnpm-workspace.yaml`, les filtres `admin` du Makefile et de la CI, l'image admin
   de `staging-images.yml` et `deploy-pull.sh`.
4. Adapter les routes admin au modèle single-DB si l'ADR 0001 est passé entre-temps.

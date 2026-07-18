# Briques archivées — simplification MVP

Tout ce qui a été retiré du socle lors de la simplification MVP (juillet 2026) reste
récupérable intégralement via le tag git **`archive/pre-mvp-simplification`** :

```bash
# Voir un fichier archivé
git show archive/pre-mvp-simplification:<chemin/du/fichier>

# Restaurer un répertoire entier dans le working tree
git checkout archive/pre-mvp-simplification -- <chemin/du/repertoire>

# Explorer l'arborescence complète à cet état
git ls-tree -r --name-only archive/pre-mvp-simplification
```

## Ce qui a été retiré

| Brique | Emplacement archivé | ADR |
|--------|--------------------|-----|
| Stack observabilité (Loki, Alloy, Grafana, Uptime Kuma) | `infra/loki/`, `infra/alloy/`, `infra/grafana/`, services `docker-compose.yml` | [0004](../adr/0004-observabilite-docker-logs.md) |
| Back-office (SPA admin + routes `/api/v1/admin/*`) | `apps/admin/`, `apps/api/app/admin/`, `apps/api/app/ai/admin_service.py`, `infra/caddy/Caddyfile.admin` | [0003](../adr/0003-back-office-archive.md) |
| RGPD à délai de grâce (effacement, exports chiffrés, rétention) | `apps/api/app/gdpr/`, `docs/runbook-gdpr.md` | [0002](../adr/0002-rgpd-soft-delete.md) |
| Base-par-tenant (engine manager, provisioning `CREATE DATABASE`, double arbre Alembic) | `apps/api/app/tenancy/engine_manager.py`, `apps/api/app/tenancy/migrations_runner.py`, `apps/api/migrations/{controlplane,tenant}/` | [0001](../adr/0001-base-unique-tenant-id.md) |

## Plans de phase historiques

Les plans de développement du socle (`docs/architecture-plan.md`, `docs/phase-0` à
`phase-8`, `docs/session-handoff.md`) ont été retirés du working tree une fois le socle
livré : ils documentaient la conception initiale (dont les briques archivées ci-dessus)
et restent consultables dans l'historique git :

```bash
git show archive/pre-mvp-simplification:docs/architecture-plan.md
git show archive/pre-mvp-simplification:docs/phase-5-connecteurs-plan.md
```

La documentation vivante de l'architecture actuelle : [`docs/`](../README.md).

# Architecture Decision Records (ADR)

Chaque décision structurante de la simplification MVP est consignée ici. Un ADR est
court, daté, et ne se réécrit pas : une décision qui change donne un nouvel ADR qui
supersède l'ancien.

## Format

```markdown
# NNNN — Titre

- **Date** : YYYY-MM-DD
- **Statut** : accepté | supersédé par NNNN

## Contexte
Pourquoi la question se posait.

## Décision
Ce qui a été décidé, en une phrase, puis les détails.

## Conséquences
Ce que ça change, ce qu'on perd, ce qu'on gagne.

## Procédure de réintroduction
Comment récupérer ce qui a été retiré, en partant du tag
`archive/pre-mvp-simplification` (`git show archive/pre-mvp-simplification:<chemin>`).
```

## Index

| ADR | Titre | Statut |
|-----|-------|--------|
| [0001](0001-base-unique-tenant-id.md) | Base-par-tenant → base unique + `tenant_id` | à venir (étape 3) |
| [0002](0002-rgpd-soft-delete.md) | RGPD à délai de grâce archivé → soft-delete | accepté |
| [0003](0003-back-office-archive.md) | Back-office archivé — admin via CLI/SQL | accepté |
| [0004](0004-observabilite-docker-logs.md) | Observabilité lourde archivée — `docker logs` | accepté |
| [0005](0005-monorepo-conserve-pour-mvp.md) | Monorepo conservé pour le MVP | accepté |

Le design global de la simplification : `docs/superpowers/specs/2026-07-15-simplification-mvp-design.md`.

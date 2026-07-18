# 0005 — Monorepo conservé pour le MVP

- **Date** : 2026-07-17
- **Statut** : accepté

## Contexte

La simplification MVP retire une part importante du socle (observabilité, back-office,
RGPD à délai de grâce, base-par-tenant). La question s'est posée de simplifier aussi la
structure du repo : éclater le monorepo, fusionner `packages/ui` dans `apps/web`, ou
abandonner le client TS généré.

## Décision

Le monorepo est conservé tel quel : `apps/api`, `apps/web`, `packages/api-client`
(généré), `packages/ui`, `infra/`, `scripts/`, `docs/`, orchestrés par le `Makefile` et
pnpm workspaces.

## Conséquences

- Aucun churn de structure : les chemins, la CI et les commandes `make` restent stables
  pendant que le contenu se simplifie — les diffs des étapes 1 à 4 restent lisibles.
- `packages/api-client` reste généré depuis l'OpenAPI (`make generate-client`), jamais
  édité à la main : la suppression de routes (ex. `/api/v1/admin/*`) se propage aux SPA
  par régénération, et la dérive contrat/client continue de casser la CI.
- `packages/ui` reste le foyer des composants partagés même si `apps/admin` disparaît :
  si un back-office revient un jour, il reconsomme le même socle UI.
- Le coût du monorepo (pnpm workspaces, filtres CI) est déjà payé et faible pour une
  équipe de 2 ; le supprimer serait du travail sans gain MVP.

## Procédure de réintroduction

Sans objet — rien n'est retiré par cet ADR. Il fige la décision de ne PAS restructurer
le repo pendant la simplification.

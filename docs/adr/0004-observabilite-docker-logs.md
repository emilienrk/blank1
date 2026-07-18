# 0004 — Observabilité lourde archivée, `docker logs` en MVP

- **Date** : 2026-07-18
- **Statut** : accepté

## Contexte

Le socle embarquait une stack d'observabilité complète : Loki (stockage de logs),
Alloy (collecte), Grafana (visualisation), Uptime Kuma (sondes). Quatre services
Docker, trois répertoires de configuration (`infra/loki`, `infra/alloy`,
`infra/grafana`), un mot de passe à gérer — pour un trafic MVP que deux personnes
peuvent surveiller directement.

## Décision

Archiver les quatre services et leurs configurations. L'observabilité MVP :
`docker compose logs -f <service>` (les logs restent du JSON structuré sur stdout,
corrélés par `request_id` — cet invariant ne bouge pas) et le smoke test
(`make smoke` / `scripts/smoke.sh`) pour la disponibilité.

## Conséquences

- 4 services et 4 volumes Docker en moins ; `make infra` = Postgres + Valkey.
- Pas de rétention de logs au-delà de celle de Docker (configurer `logging.driver`
  au besoin) ni d'alerting automatique : acceptable tant que le trafic est confidentiel.
- Le format de log JSON/stdout est conservé tel quel : rebrancher un collecteur ne
  demandera aucun changement applicatif.

## Procédure de réintroduction

1. `git checkout archive/pre-mvp-simplification -- infra/loki infra/alloy infra/grafana`
2. Restaurer les services `loki`, `alloy`, `grafana`, `uptime-kuma` et leurs volumes
   dans `docker-compose.yml` (+ blocs `restart` du staging), `GRAFANA_ADMIN_PASSWORD`
   dans `.env.example`, et la cible `make infra` étendue.

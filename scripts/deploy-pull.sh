#!/usr/bin/env bash
# Déploiement staging par pull (exécuté PAR la machine de staging, via systemd timer).
# Tire les images :latest depuis GHCR et ne redémarre les services que si une
# image a changé (comparaison des IDs d'image). Smoke test après tout redéploiement.
#
# Prérequis (une seule fois, voir README) :
#   - repo cloné dans REPO_DIR, `docker login ghcr.io` fait avec un PAT packages:read
#   - ENV_FILE avec APP_ENV=staging, SITE_ADDRESS, API_IMAGE/WEB_IMAGE (tags :latest GHCR)
#
# Variables surchargables : REPO_DIR (défaut /srv/saas/app), ENV_FILE (défaut /srv/saas/.env)
set -euo pipefail

REPO_DIR="${REPO_DIR:-/srv/saas/app}"
ENV_FILE="${ENV_FILE:-/srv/saas/.env}"
LOCK_FILE="${LOCK_FILE:-/var/lock/saas-deploy.lock}"

# Verrou anti-chevauchement (ceinture et bretelles : systemd sérialise déjà l'unité)
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "Un déploiement est déjà en cours, abandon de ce tick."
    exit 0
fi

cd "$REPO_DIR"

# Met à jour les fichiers compose/scripts depuis main (best effort : un échec réseau
# ne doit pas empêcher le déploiement des images déjà publiées)
if git fetch origin main --quiet 2>/dev/null; then
    git reset --hard origin/main --quiet
else
    echo "AVERTISSEMENT : git fetch impossible, on continue avec les fichiers locaux."
fi

compose() {
    docker compose --env-file "$ENV_FILE" \
        -f docker-compose.yml -f docker-compose.staging.yml "$@"
}

image_var() {
    grep "^${1}=" "$ENV_FILE" | cut -d= -f2-
}

API_IMAGE=$(image_var API_IMAGE)
WEB_IMAGE=$(image_var WEB_IMAGE)

running_image_id() {
    local container_id
    container_id=$(compose ps -q "$1" 2>/dev/null || true)
    if [ -n "$container_id" ]; then
        docker inspect --format '{{.Image}}' "$container_id" 2>/dev/null || echo "none"
    else
        echo "none"
    fi
}

pulled_image_id() {
    docker image inspect --format '{{.Id}}' "$1" 2>/dev/null || echo "missing"
}

compose pull --quiet

CHANGED=0
for pair in "api:${API_IMAGE}" "caddy:${WEB_IMAGE}"; do
    service="${pair%%:*}"
    image="${pair#*:}"
    if [ "$(running_image_id "$service")" != "$(pulled_image_id "$image")" ]; then
        echo "Service ${service} : nouvelle image détectée (${image})."
        CHANGED=1
    fi
done

if [ "$CHANGED" -eq 0 ]; then
    echo "Rien de nouveau, aucun redéploiement."
    exit 0
fi

echo "Redéploiement…"
compose up -d --remove-orphans

# Migrations : control-plane + toutes les bases tenant (décision D8 Phase 1).
# Une base en échec n'empêche pas les autres d'être migrées, mais fait échouer
# le déploiement (visible dans journalctl) ; le verrou advisory protège d'un
# chevauchement avec un lancement manuel.
echo "Migrations de schéma…"
if ! compose run --rm api saas db upgrade; then
    echo "ÉCHEC : migrations en erreur — voir le rapport ci-dessus." >&2
    exit 1
fi

SITE=$(image_var SITE_ADDRESS)
echo "Smoke test sur https://${SITE}"
# Laisse quelques secondes aux services pour démarrer avant la sonde
for attempt in 1 2 3 4 5 6; do
    if ./scripts/smoke.sh "https://${SITE}"; then
        echo "Déploiement OK."
        docker image prune -f > /dev/null
        exit 0
    fi
    echo "Tentative ${attempt}/6 échouée, nouvel essai dans 5 s…"
    sleep 5
done

echo "ÉCHEC : le smoke test ne passe pas après redéploiement." >&2
exit 1

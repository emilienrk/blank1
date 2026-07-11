#!/usr/bin/env bash
# Smoke test post-déploiement : le health doit répondre à travers Caddy.
# Usage : ./scripts/smoke.sh [URL de base, ex. https://staging.exemple.fr]
set -euo pipefail

BASE_URL="${1:-http://localhost:8080}"

echo "Smoke test sur ${BASE_URL}/api/v1/health"
BODY=$(curl --fail --silent --show-error --max-time 10 "${BASE_URL}/api/v1/health")
echo "Réponse : ${BODY}"

echo "${BODY}" | grep -q '"status":"ok"' || {
    echo "ÉCHEC : le payload ne contient pas \"status\":\"ok\"" >&2
    exit 1
}
echo "OK"

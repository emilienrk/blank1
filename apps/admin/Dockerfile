# Build de la SPA back-office puis service par Caddy (statiques + reverse-proxy /api + TLS auto).
# Contexte de build : la racine du repo (workspace pnpm). Jamais exposé publiquement
# (invariant racine n°7) : le vhost servi par cette image n'écoute qu'en interne/WireGuard.

FROM node:22-alpine AS build
RUN npm install -g pnpm@10.33.0
WORKDIR /repo

# Couche dépendances
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/admin/package.json apps/admin/
COPY packages/api-client/package.json packages/api-client/
COPY packages/ui/package.json packages/ui/
RUN pnpm install --frozen-lockfile

# Couche sources
COPY apps/admin apps/admin
COPY packages packages
RUN pnpm --filter admin build

FROM caddy:2-alpine AS runtime
COPY infra/caddy/Caddyfile.admin /etc/caddy/Caddyfile
COPY --from=build /repo/apps/admin/dist /srv/www

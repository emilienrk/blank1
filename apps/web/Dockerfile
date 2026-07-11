# Build de la SPA puis service par Caddy (statiques + reverse-proxy /api + TLS auto).
# Contexte de build : la racine du repo (workspace pnpm).

FROM node:22-alpine AS build
RUN npm install -g pnpm@10.33.0
WORKDIR /repo

# Couche dépendances
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/api-client/package.json packages/api-client/
COPY packages/ui/package.json packages/ui/
RUN pnpm install --frozen-lockfile

# Couche sources
COPY apps/web apps/web
COPY packages packages
RUN pnpm --filter web build

FROM caddy:2-alpine AS runtime
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /repo/apps/web/dist /srv/www

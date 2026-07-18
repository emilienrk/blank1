# Frontend — la SPA client

> `apps/web` : React + Vite + TypeScript strict, TanStack Router/Query, Tailwind.
> Vue d'ensemble : [`architecture.md`](architecture.md).

## Principe directeur

La SPA est un **relais, jamais une barrière** : toute la sécurité est côté API.
Le front masque des boutons selon le rôle pour l'UX, mais un utilisateur qui
forgerait la requête serait de toute façon refusé par `require_permission`.

## Structure

```
src/lib/       # api.ts (client + interceptions), auth.ts (état d'auth), tenant.ts, utils.ts
src/pages/     # une page = un fichier + son test à côté (home.tsx / home.test.tsx)
src/router.tsx # toutes les routes, en un seul endroit
src/layout.tsx # layout applicatif (navigation, bandeau « accès refusé »)
src/test/      # harnais de test (mock-fetch, render-route, setup)
packages/ui    # composants React réutilisables entre apps
```

## Le client API (`lib/api.ts`)

Un seul client, construit sur `@app/api-client` — le client **généré** depuis
l'OpenAPI (`make generate-client`, jamais édité à la main : la dérive
contrat/client casse la CI et le typage des pages suit automatiquement le
backend). Même origine partout : proxy Vite en dev, Caddy en staging/prod.

Deux interceptions transverses, pour ne jamais gérer 401/403 page par page :

- **401** (hors `/auth/me` et page de login) → redirection dure vers `/login`
  avec retour post-login. Redirection *dure* à dessein : on repart d'un état
  mémoire propre, le serveur reste la seule vérité.
- **403** → événement `api:forbidden`, écouté par le layout qui affiche
  « accès refusé ».

## État d'authentification (`lib/auth.ts`)

Le cookie de session est httpOnly — invisible au JS. La **seule** source de
vérité est `GET /api/v1/auth/me`, exposée en `meQueryOptions` (TanStack Query) :

- `null` = non authentifié (le 401 de `/auth/me` est une réponse attendue, pas
  une erreur) ;
- après login/logout/acceptation d'invitation, on **invalide la query**
  (`useInvalidateCurrentUser`) — jamais de mutation locale de l'état d'auth ;
- `useCurrentRole()` croise `me.memberships` avec le sous-domaine courant
  (`lib/tenant.ts`, miroir de l'`extract_slug` du backend) — usage UX uniquement.

## Routage (`router.tsx`)

Deux routes publiques seulement (`/login`, `/accept-invitation` — la liste fermée
du backend a son miroir ici). Tout le reste est enfant d'une route layout dont le
`beforeLoad` exige `me` (redirection `/login` sinon) : **un seul guard**, pas de
vérification par page. Les pages de modules sont codées en dur dans la SPA
(`/modules/sample-digest`) — un front « pluggable » serait de la sur-ingénierie
tant qu'il y a peu de modules.

## Conventions de page

- Données : TanStack Query par-dessus le client généré ; pas d'état serveur
  dupliqué dans du state local.
- Formulaires : react-hook-form + zod.
- Tests : vitest + jsdom, un `*.test.tsx` à côté de chaque page, avec le harnais
  `src/test/` (`mock-fetch.ts` intercepte le réseau, `render-route.tsx` monte une
  page dans un routeur réel). `make test` les exécute avec pytest.

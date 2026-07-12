# Phase 3 — Frontends + back-office : plan d'implémentation détaillé

> Référence : `docs/architecture-plan.md` (§1 « Frontend », §2 `apps/web` / `apps/admin`,
> §8.3 « Vérification des apps OAuth », §9 « Phase 3 »). Cette phase couvre : la SPA
> client (login complet, acceptation d'invitation, gestion des membres et des équipes,
> sécurité du compte), le back-office interne (`apps/admin` + module API `app/admin/`)
> pour le provisioning et la supervision des migrations, et le **lancement hors repo des
> vérifications d'apps OAuth** Google/Microsoft pour les scopes connecteurs (Phase 5).
> Rien de plus — l'audit log et sa page arrivent en Phase 4, les écrans connecteurs en
> Phase 5, la consommation IA en Phase 6, le SSO entreprise en Phase 8. Chaque
> anticipation est signalée explicitement.

## État des lieux

Phase 2 fusionnée (PR #5) : API d'auth complète (login 2 temps + TOTP, OAuth login,
invitations, RBAC `require_permission`), annuaire (membres, invitations, équipes en DB
tenant), client TS régénéré avec les premières vraies routes. Côté front, **tout reste à
faire** : `apps/web` est le squelette Phase 0 (une page d'accueil, TanStack Router/Query
installés, Tailwind), `packages/ui` ne contient qu'un `status-badge`, et `apps/admin`
**n'existe pas**. Côté back, `require_platform_admin` existe et est testée mais aucune
route ne l'expose (décision D5 Phase 1 / T7 Phase 2) ; `users.is_platform_admin` existe
en base sans aucun moyen de le poser.

---

## A. Tâches ordonnées

### T1 — Socle applicatif de la SPA client

Rôle : transformer le squelette `apps/web` en application authentifiée consommant le
client TS généré.

| Fichier | Rôle |
|---|---|
| `apps/web/src/lib/auth.ts` | État d'auth via TanStack Query sur `GET /api/v1/auth/me` (source de vérité unique, pas de store parallèle — décision D1) ; helpers `useCurrentUser()`, invalidation après login/logout. |
| `apps/web/src/lib/api.ts` (extension) | Interceptions transverses : 401 → redirection `/login` (avec retour post-login), 403 → page « accès refusé » ; le client généré reste intact (invariant n°5). |
| `apps/web/src/router.tsx` (refonte) | Arbre TanStack Router : routes publiques (`/login`, `/login/totp`, `/invitations/accept`), routes protégées derrière un guard `beforeLoad` qui exige `me` ; layout applicatif (nav, tenant courant affiché depuis le sous-domaine). |

### T2 — Écrans d'authentification

Rôle : la traduction UI des flux T4-T6 de la Phase 2, sans logique nouvelle côté back.

| Fichier | Rôle |
|---|---|
| `apps/web/src/pages/login.tsx` | Email + mot de passe ; sur réponse `totp_required` → étape code TOTP (jeton de login partiel porté en mémoire uniquement, jamais persisté) ; lien de secours « code de récupération ». Boutons « Se connecter avec Google / Microsoft » = simples liens vers `/api/v1/auth/oauth/{provider}/start`. Erreurs indistinctes affichées telles quelles (pas d'oracle côté front non plus). |
| `apps/web/src/pages/accept-invitation.tsx` | Route publique : lit le token en query string, formulaire mot de passe (nouveau compte) ou simple confirmation (compte existant), puis redirection vers `/login`. |
| `apps/web/src/pages/logout.ts` | Action logout → `POST /auth/logout` → invalidation du cache Query → `/login`. |

### T3 — Sécurité du compte (TOTP)

| Fichier | Rôle |
|---|---|
| `apps/web/src/pages/account-security.tsx` | Parcours TOTP : setup (QR code rendu **côté client** depuis l'URI `otpauth://`, décision D4) → activation par premier code → affichage **unique** des codes de récupération (avertissement explicite, aucun re-affichage possible) ; désactivation avec mot de passe. |

### T4 — Annuaire : membres, invitations, équipes

Rôle : la consommation UI des routes `directory` de la Phase 2, permissions reflétées
dans l'interface (mais **jamais** substituées au contrôle serveur).

| Fichier | Rôle |
|---|---|
| `apps/web/src/pages/members.tsx` | Liste des membres (rôle, statut), invitations en attente ; actions selon le rôle courant : inviter (email + rôle), renvoyer/révoquer une invitation (**l'URL d'acceptation retournée par l'API est affichée avec bouton copier** — c'est le canal principal tant que SMTP n'est pas branché, D8 Phase 2), changer un rôle, retirer un membre. Les règles « dernier owner intouchable » remontent du serveur ; le front se contente d'afficher l'erreur. |
| `apps/web/src/pages/teams.tsx` | CRUD équipes + composition (ajout/retrait de membres) — première UI qui matérialise des données en DB tenant. |

### T5 — Composants partagés (`packages/ui`)

Rôle : tout composant réutilisable entre `apps/web` et `apps/admin` vit ici (convention
racine), sur base shadcn/ui (acté §1).

| Fichier | Rôle |
|---|---|
| `packages/ui/src/…` | Primitives nécessaires aux écrans ci-dessus : `Button`, `Input`, `FormField` (react-hook-form + zod, décision D3), `Table`, `Dialog`, `Badge` (rôles/états), `Toast`. Uniquement ce que les pages consomment réellement — pas de bibliothèque exhaustive spéculative. |

### T6 — Module API back-office (`app/admin/`)

Rôle : l'API du back-office (§2), **hors contexte tenant**, entièrement derrière
`require_platform_admin` — première exposition de cette dépendance.

| Fichier | Rôle |
|---|---|
| `apps/api/app/admin/router.py` | Routes `/api/v1/admin/*` : `GET tenants` (catalogue + version de schéma + état), `POST tenants` (provisioning, mêmes règles que le CLI), `POST tenants/{slug}/retry-provision`, `POST migrations/run` (déclenche le runner, décision D6 : synchrone si possible, sinon tâche Celery + polling), `GET migrations/last-report` (dernier `MigrationReport`), `GET users/{email}` (lookup identité globale + memberships — diagnostic support). Toutes en `require_platform_admin`. |
| `apps/api/app/admin/service.py` | Orchestration fine : réutilise `provisioning.py` et `migrations_runner.py` **tels quels** (aucune duplication de logique CLI) ; persistance du dernier rapport de migration (table control-plane `migration_reports`, décision D6). |
| `apps/api/app/cli.py` (extension) | `saas admin grant <email>` / `saas admin revoke <email>` : seul moyen de poser `is_platform_admin` (décision D5 — jamais via l'API elle-même). |
| `apps/api/migrations/controlplane/versions/…` | Révision 0003 : `migration_reports`. |

### T7 — SPA back-office (`apps/admin`)

Rôle : le deuxième front du §2, jamais exposé publiquement (invariant racine n°7).

| Fichier | Rôle |
|---|---|
| `apps/admin/` | Nouvelle app React/Vite/TS calquée sur la config `apps/web` (tsconfig strict, ESLint, vitest), consommant `packages/ui` et `packages/api-client` (un seul client généré pour les deux SPA, décision D2). Login = même stack de session (un platform_admin est un user comme un autre). |
| `apps/admin/src/pages/tenants.tsx` | Liste du catalogue (état, plan, version de schéma), création de tenant (slug, nom, email owner — l'URL d'invitation retournée est affichée), retry-provision sur les `failed`. |
| `apps/admin/src/pages/migrations.tsx` | Déclenchement du runner + affichage du dernier rapport base par base (statut, version, erreur) — la supervision voulue par le §9. |

### T8 — Exposition : Caddy et Compose

| Fichier | Rôle |
|---|---|
| `infra/caddy/Caddyfile` | Deux surfaces distinctes : le vhost public (wildcard tenants) sert `apps/web` + `/api` **en excluant `/api/v1/admin/*`** (403 au niveau Caddy, défense en profondeur) ; un vhost `admin.<domaine interne>` **écoutant uniquement sur l'IP WireGuard** sert `apps/admin` + `/api` complet. |
| `compose.yaml` / build | `apps/admin` est buildé comme `apps/web` (statique servi par Caddy) — **pas de nouvelle image de service**, l'invariant « une seule image api/worker » est intouché. |

### T9 — Vérifications d'apps OAuth (hors repo — à lancer maintenant)

Rôle : le risque §8.3 — les validations Google (scopes sensibles Gmail/Agenda) et la
« publisher verification » Microsoft prennent **des semaines** et conditionnent la
Phase 5.

- Créer/compléter les apps OAuth Google et Microsoft avec les **scopes connecteurs**
  cibles (Gmail lecture/envoi, Calendar, Microsoft Graph Mail/Calendar — périmètre
  exact défini au §5) en plus des scopes de login déjà en service.
- Lancer les processus de vérification chez les deux fournisseurs ; documenter l'état
  d'avancement dans `docs/session-handoff.md` (section « hors repo »).
- Aucun code dans cette tâche — uniquement des actions console + doc.

### T10 — Contrat, CI et clôture

- `make generate-client` : nouvelles routes admin dans le contrat, `operation_id`
  explicites.
- CI : les jobs front (lint/tsc/vitest/build) couvrent désormais `apps/admin` ;
  structure des jobs back inchangée.
- `README.md` : sections « SPA client » et « Back-office » (accès WireGuard,
  `saas admin grant`), captures du flux de démo.
- `CLAUDE.md` racine + `apps/api/CLAUDE.md` : module `admin`, règle « toute route
  `/api/v1/admin/*` est en `require_platform_admin` », phase courante mise à jour.
- Critère de démo (section E) déroulé et vérifié.

---

## B. Points de conception — décisions et recommandations

| # | Question | Recommandation | Justification |
|---|---|---|---|
| D1 | État d'auth côté SPA : store dédié (Zustand/Redux) ou TanStack Query seul ? | **TanStack Query seul, `GET /auth/me` comme source de vérité** | Le cookie httpOnly est invisible au JS : le serveur est la seule vérité. Un store parallèle créerait une double source à synchroniser. Query gère cache, revalidation et invalidation post-login/logout — zéro dépendance nouvelle. |
| D2 | Un client TS par SPA ou un client commun ? | **Un seul client généré, partagé** | Un seul contrat OpenAPI existe (invariant n°5) ; les routes admin dans le spec public ne révèlent rien de sensible (le code est le contrat) et Caddy bloque leur accès public. Deux clients = deux générations à dériver, pour rien. |
| D3 | Formulaires : maison ou react-hook-form + zod ? | **react-hook-form + zod** | Les deux libs les plus documentées de l'écosystème (critère IA-friendly §1) ; zod donne la validation côté client alignée sur les schémas Pydantic sans duplication manuelle excessive. |
| D4 | QR code TOTP : généré par l'API ou côté client ? | **Côté client depuis l'URI `otpauth://`** | L'API retourne déjà l'URI (T5 Phase 2) ; générer l'image côté serveur ajouterait une dépendance image et ferait transiter le secret dans un format de plus. Une lib QR front minuscule suffit. |
| D5 | Comment nommer le premier `platform_admin` ? | **CLI uniquement (`saas admin grant`), jamais via l'API** | Une route « promouvoir platform_admin » serait une cible d'escalade ; le CLI exige un accès shell à la machine (déjà le canal du provisioning Phase 1). Cohérent avec l'onboarding manuel assumé. |
| D6 | Lancement du runner de migrations depuis le back-office : synchrone ou Celery ? | **Tâche Celery + rapport persisté (`migration_reports`) + polling** | Le runner peut durer (N bases) : au-delà du timeout HTTP c'est intenable en synchrone. Le verrou advisory (Phase 1) protège déjà des lancements concurrents ; persister le rapport donne l'historique de supervision voulu par le §9 sans rien inventer. |
| D7 | Back-office : app séparée ou routes cachées dans `apps/web` ? | **App séparée `apps/admin` (acté §2)** | Séparation nette des surfaces (invariant n°7) : le bundle public ne contient **aucun** code admin ; l'exposition est réglée au niveau réseau (Caddy/WireGuard), pas par de l'obscurité front. `packages/ui` évite la duplication. |
| D8 | Protection de `/api/v1/admin/*` : app FastAPI séparée ? | **Même app, double barrière `require_platform_admin` + blocage Caddy sur le vhost public** | Une deuxième app casserait l'image unique (invariant n°2). La dépendance serveur est la vraie barrière ; Caddy ajoute la défense en profondeur réseau exigée par l'invariant n°7. |

---

## C. Invariants et règles absolues de la phase

1. **Toute route `/api/v1/admin/*` exige `require_platform_admin`** — aucune exception,
   et le vhost public ne les sert jamais (double barrière D8).
2. **Le front n'est jamais une barrière de sécurité** : masquer un bouton selon le rôle
   est de l'UX ; l'autorisation reste 100 % serveur (`require_permission`).
3. **Aucun secret ni token en localStorage/sessionStorage** : la session vit dans le
   cookie httpOnly ; le jeton de login partiel TOTP ne vit qu'en mémoire de page ;
   le token d'invitation reste l'unique token en URL (règle Phase 2 inchangée).
4. **`apps/admin` n'est jamais exposé publiquement** (invariant racine n°7) : vhost
   WireGuard uniquement, y compris en staging.
5. **Un seul client TS généré** consommé par les deux SPA — jamais d'appel `fetch`
   artisanal vers l'API dans les pages.
6. Les invariants Phases 0-2 restent en vigueur (image unique api/worker, config par
   env, `require_permission` sur toute route métier, secrets hachés/chiffrés,
   logs JSON sans PII).

---

## D. Tests à écrire

**Backend (pytest, Postgres réel — `apps/api/tests/`)**
- `test_admin_routes.py` : chaque route admin → 401 sans session, 403 avec un user
  normal, 200 en platform_admin ; création de tenant via l'API = mêmes effets que le
  CLI (catalogue, DB, invitation owner) ; `retry-provision` sur un tenant `failed` ;
  lancement du runner → rapport persisté et relu via `last-report`.
- `test_cli.py` (extension) : `admin grant`/`revoke` (idempotence, email inconnu → erreur).

**Frontend (vitest — `apps/web`, `apps/admin`)**
- `login.test.tsx` : soumission → appel client mocké ; réponse `totp_required` → étape
  code ; erreur → message indistinct affiché ; redirection post-login.
- `accept-invitation.test.tsx` : token en query → formulaire adapté (nouveau vs
  existant) ; erreur token expiré affichée.
- `members.test.tsx` : rendu liste ; actions masquées pour un `member` ; l'URL
  d'invitation retournée est affichée après création.
- `account-security.test.tsx` : parcours setup → activate ; codes de récupération
  affichés une seule fois.
- `apps/admin` : `tenants.test.tsx` (création → URL d'invitation affichée),
  `migrations.test.tsx` (rapport rendu base par base, états d'échec visibles).

**CI** : jobs front étendus à `apps/admin` ; le job contrat valide le client régénéré
(routes admin) ; jobs back/images inchangés structurellement.

---

## E. Critère de démo de fin de phase

> Sur staging : dans un navigateur, Alice ouvre `acme.<domaine>`, est redirigée vers
> `/login`, se connecte (mot de passe puis code TOTP), voit la liste des membres,
> invite carol@example.com en `member` et lui transmet l'URL affichée ; Carol accepte
> dans son navigateur, définit son mot de passe, se connecte et voit les équipes mais
> pas le bouton d'invitation (et un appel direct à l'API lui rend bien 403). Alice
> active le TOTP depuis la page sécurité (QR scanné, codes de récupération affichés une
> fois). Pendant ce temps, l'opérateur connecté au WireGuard ouvre `admin.<domaine
> interne>`, se connecte avec son compte `platform_admin` (posé par `saas admin
> grant`), crée le tenant `globex` depuis le back-office (l'URL d'invitation owner
> s'affiche), déclenche le runner de migrations et lit le rapport base par base.
> Depuis Internet, `admin.<…>` et `/api/v1/admin/*` sont injoignables. En parallèle,
> les demandes de vérification d'apps OAuth Google et Microsoft sont soumises
> (statut documenté au handoff).

C'est la traduction exécutable du §9 Phase 3 : les deux fronts vivent, le back-office
provisionne et supervise, et le chronomètre des vérifications OAuth tourne.

---

## F. Dépendances manquantes et risques propres à la phase

1. **La démo exige la machine de staging + DNS wildcard + WireGuard** (hérités,
   hors repo) — le back-office est justement la surface qui ne doit exister que
   derrière WireGuard, on ne peut pas le « démontrer » sans lui.
2. **Vérifications OAuth = calendrier subi** : des semaines côté Google/Microsoft,
   d'où T9 lancé maintenant ; la Phase 5 peut démarrer en mode « test users »
   sans la validation finale, mais pas s'ouvrir à de vrais clients.
3. **`apps/admin` double la surface front en CI** (lint/tsc/vitest/build ×2) — durée à
   surveiller, mitigée par le cache pnpm.
4. **Premier vrai usage du cookie parent-domain en navigateur** (D2 Phase 2) : à
   valider sur staging (login sur `acme.…` puis navigation `globex.…` → 403 propre,
   pas de boucle de redirection).
5. **La branche `main` n'existe toujours pas** (hérité Phases 0-2) : à régler pour que
   staging reçoive ces livrables.
6. Aucune anticipation des phases 4+ : pas de page audit, pas d'écran connecteurs,
   pas de page consommation IA — les pages arriveront avec leurs phases.

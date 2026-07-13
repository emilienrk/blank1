import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";

import { AppLayout } from "@/layout";
import { meQueryOptions } from "@/lib/auth";
import { AcceptInvitationPage } from "@/pages/accept-invitation";
import { AccountSecurityPage } from "@/pages/account-security";
import { AuditPage } from "@/pages/audit";
import { ConnectorsPage } from "@/pages/connectors";
import { HomePage } from "@/pages/home";
import { LoginPage } from "@/pages/login";
import { MembersPage } from "@/pages/members";
import { ModuleSampleDigestPage } from "@/pages/module-sample-digest";
import { TeamsPage } from "@/pages/teams";

interface RouterContext {
  queryClient: QueryClient;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({});

// --- Routes publiques (invariant Phase 2 : liste fermée d'accès anonymes) ---

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: (search: Record<string, unknown>): { redirect?: string } => ({
    redirect: typeof search.redirect === "string" ? search.redirect : undefined,
  }),
  component: LoginPage,
});

const acceptInvitationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/accept-invitation",
  validateSearch: (search: Record<string, unknown>): { token?: string } => ({
    token: typeof search.token === "string" ? search.token : undefined,
  }),
  component: AcceptInvitationPage,
});

// --- Routes protégées : guard unique (beforeLoad exige `me`) + layout applicatif ---

const appLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app-layout",
  beforeLoad: async ({ context, location }) => {
    const me = await context.queryClient.ensureQueryData(meQueryOptions);
    if (me === null) {
      throw redirect({ to: "/login", search: { redirect: location.href } });
    }
  },
  component: AppLayout,
});

const indexRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/",
  component: HomePage,
});

const membersRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/members",
  component: MembersPage,
});

const teamsRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/teams",
  component: TeamsPage,
});

const accountSecurityRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/account-security",
  component: AccountSecurityPage,
});

const auditRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/audit",
  component: AuditPage,
});

const connectorsRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/connectors",
  component: ConnectorsPage,
});

// Module d'exemple (Phase 7) : page codée en dur dans la SPA (risque n°3 assumé —
// un front modulaire « pluggable » serait de la sur-ingénierie sans plusieurs modules).
const moduleSampleDigestRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/modules/sample-digest",
  component: ModuleSampleDigestPage,
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  acceptInvitationRoute,
  appLayoutRoute.addChildren([
    indexRoute,
    membersRoute,
    teamsRoute,
    accountSecurityRoute,
    auditRoute,
    connectorsRoute,
    moduleSampleDigestRoute,
  ]),
]);

export function createAppRouter(queryClient: QueryClient) {
  return createRouter({ routeTree, context: { queryClient } });
}

export type AppRouter = ReturnType<typeof createAppRouter>;

declare module "@tanstack/react-router" {
  interface Register {
    router: AppRouter;
  }
}

import { queryOptions, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { currentTenantSlug } from "@/lib/tenant";

import type { components } from "@app/api-client";

export type MeResponse = components["schemas"]["MeResponse"];

/** `GET /auth/me` = source de vérité unique de l'état d'auth (décision D1 Phase 3) :
 * le cookie de session est httpOnly, invisible au JS. `null` = non authentifié
 * (401 attendu, pas une erreur — voir l'exception dans `lib/api.ts`). */
async function fetchMe(): Promise<MeResponse | null> {
  const { data, error, response } = await api.GET("/api/v1/auth/me");
  if (response.status === 401) return null;
  if (error !== undefined || data === undefined) {
    throw new Error("Impossible de récupérer l'utilisateur courant.");
  }
  return data;
}

export const meQueryOptions = queryOptions({
  queryKey: ["auth", "me"] as const,
  queryFn: fetchMe,
  retry: false,
});

export function useCurrentUser() {
  return useQuery(meQueryOptions);
}

/** À appeler après login/logout/acceptation d'invitation : seule façon de faire
 * évoluer l'état d'auth affiché (pas de mutation locale, tout revient du serveur). */
export function useInvalidateCurrentUser() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: meQueryOptions.queryKey });
}

/** Rôle de l'utilisateur sur le tenant courant (sous-domaine) — UX uniquement,
 * masquer un bouton n'est jamais l'autorisation (invariant C2, contrôle 100% serveur). */
export function useCurrentRole(): string | null {
  const { data: me } = useCurrentUser();
  const tenant = currentTenantSlug();
  if (me === null || me === undefined || tenant === null) return null;
  return me.memberships.find((membership) => membership.tenant_slug === tenant)?.role ?? null;
}

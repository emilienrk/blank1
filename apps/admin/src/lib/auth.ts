import { queryOptions, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

import type { components } from "@app/api-client";

export type MeResponse = components["schemas"]["MeResponse"];

/** `GET /auth/me` = source de vérité unique de l'état d'auth (décision D1 Phase 3,
 * inchangée pour le back-office) : un platform_admin est un user comme un autre —
 * `is_platform_admin` n'est pas dans `MeResponse`, il se révèle au premier appel
 * `/admin/*` (403 sinon, écouté dans `lib/api.ts`). */
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

export function useInvalidateCurrentUser() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: meQueryOptions.queryKey });
}

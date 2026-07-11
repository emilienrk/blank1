import createClient from "openapi-fetch";

import type { paths } from "./schema";

export type { components, paths } from "./schema";

/** Client typé sur le contrat OpenAPI. baseUrl vide = même origine (Caddy/proxy Vite). */
export function createApiClient(baseUrl = "") {
  return createClient<paths>({
    baseUrl,
    // fetch résolu à chaque appel (et non figé à la création du client) :
    // permet aux tests de stubber globalThis.fetch après import du module.
    fetch: (request) => globalThis.fetch(request),
  });
}

export type ApiClient = ReturnType<typeof createApiClient>;

import { createApiClient } from "@app/api-client";

/** Même origine : proxy Vite en dev, Caddy en staging/prod.
 * URL absolue requise : fetch(Request) hors navigateur (tests jsdom) refuse les URL relatives. */
export const api = createApiClient(window.location.origin);

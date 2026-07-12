import { createApiClient } from "@app/api-client";

/** Même origine : proxy Vite en dev, Caddy (vhost interne WireGuard) en staging/prod.
 * URL absolue requise : fetch(Request) hors navigateur (tests jsdom) refuse les URL relatives. */
export const api = createApiClient(window.location.origin);

// Interceptions transverses (miroir de apps/web/src/lib/api.ts, T7).
api.use({
  onResponse({ request, response }) {
    const isMeCheck = request.url.endsWith("/api/v1/auth/me");
    const onLoginPage = window.location.pathname.startsWith("/login");
    if (response.status === 401 && !isMeCheck && !onLoginPage) {
      const redirectTo = window.location.pathname + window.location.search;
      window.location.assign(`/login?redirect=${encodeURIComponent(redirectTo)}`);
    }
    if (response.status === 403) {
      // Un platform_admin manquant (require_platform_admin) déclenche cet état.
      window.dispatchEvent(new CustomEvent("api:forbidden"));
    }
    return response;
  },
});

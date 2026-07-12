import { createApiClient } from "@app/api-client";

/** Même origine : proxy Vite en dev, Caddy en staging/prod.
 * URL absolue requise : fetch(Request) hors navigateur (tests jsdom) refuse les URL relatives. */
export const api = createApiClient(window.location.origin);

// Interceptions transverses (Phase 3 T1) : le client généré (`@app/api-client`)
// reste intact — invariant n°5, jamais édité à la main.
api.use({
  onResponse({ request, response }) {
    const isMeCheck = request.url.endsWith("/api/v1/auth/me");
    const onLoginPage = window.location.pathname.startsWith("/login");
    if (response.status === 401 && !isMeCheck && !onLoginPage) {
      // Session invalide/expirée : redirection dure (repart d'un état propre)
      // avec retour post-login (décision D1 — le serveur reste la seule vérité).
      const redirectTo = window.location.pathname + window.location.search;
      window.location.assign(`/login?redirect=${encodeURIComponent(redirectTo)}`);
    }
    if (response.status === 403) {
      // Écouté par le layout applicatif pour afficher « accès refusé » (T1) ;
      // le front n'est jamais une barrière de sécurité (invariant C2), juste un relais.
      window.dispatchEvent(new CustomEvent("api:forbidden"));
    }
    return response;
  },
});

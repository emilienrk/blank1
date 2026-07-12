import { vi } from "vitest";

export interface FetchRoute {
  method: string;
  path: string;
  status?: number;
  body?: unknown;
}

/** Stub `fetch` par méthode + pathname (le client généré résout `fetch` à chaque appel,
 * cf. `lib/api.ts`) — un tableau ordonné, la première route qui matche répond. */
export function stubFetch(routes: FetchRoute[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const route = routes.find(
        (candidate) => candidate.method === request.method && url.pathname === candidate.path,
      );
      if (route === undefined) {
        throw new Error(`Aucune route mockée pour ${request.method} ${url.pathname}`);
      }
      return new Response(route.body === undefined ? null : JSON.stringify(route.body), {
        status: route.status ?? 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

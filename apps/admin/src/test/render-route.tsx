import { ToastProvider } from "@app/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { act, render } from "@testing-library/react";
import type { ReactNode } from "react";

/** Rend une page qui utilise `useNavigate`/`useSearch`/`Link` sous un routeur minimal :
 * la page testée est montée à `path`, une route `/` factice sert de cible de redirection
 * observable ("Accueil (test)"). La résolution des routes est asynchrone (TanStack Router) :
 * `router.load()` avant le rendu évite un premier rendu vide en test. */
export async function renderPage(
  path: string,
  Component: () => ReactNode,
  options?: { search?: Record<string, unknown>; validateSearch?: (search: Record<string, unknown>) => object },
) {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const targetRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => <p>Accueil (test)</p>,
  });
  const pageRoute = createRoute({
    getParentRoute: () => rootRoute,
    path,
    validateSearch: options?.validateSearch,
    component: Component,
  });
  const routeTree = rootRoute.addChildren([targetRoute, pageRoute]);

  const search = options?.search ?? {};
  const searchString = new URLSearchParams(search as Record<string, string>).toString();
  const initialEntry = searchString.length > 0 ? `${path}?${searchString}` : path;

  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [initialEntry] }),
  });
  await router.load();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  let result!: ReturnType<typeof render>;
  await act(async () => {
    result = render(
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <RouterProvider router={router} />
        </ToastProvider>
      </QueryClientProvider>,
    );
  });

  return { router, ...result };
}

import { ToastProvider } from "@app/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModulesPage } from "@/pages/modules";
import { stubFetch } from "@/test/mock-fetch";

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ModulesPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const acmeTenant = {
  id: "t1",
  slug: "acme",
  name: "ACME",
  state: "active",
  plan: "standard",
  db_name: "acme_db",
  schema_revision: null,
  deletion_requested_at: null,
  erasure_due_at: null,
};

const tenantsRoute = { method: "GET", path: "/api/v1/admin/tenants", body: [acmeTenant] };

const moduleState = (overrides: Record<string, unknown> = {}) => ({
  name: "sample_digest",
  version: "1.0.0",
  title: "Digest d'exemple",
  description: "Résumé quotidien des emails.",
  enabled: false,
  missing_capabilities: [],
  ...overrides,
});

const modulesRoute = (items: unknown[]) => ({
  method: "GET",
  path: "/api/v1/admin/tenants/acme/modules",
  body: items,
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModulesPage", () => {
  it("désactive le bouton d'activation quand une capability manque", async () => {
    stubFetch([tenantsRoute, modulesRoute([moduleState({ missing_capabilities: ["mail"] })])]);
    renderPage();

    expect(await screen.findByText("Digest d'exemple")).toBeInTheDocument();
    expect(screen.getByText("mail")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activer" })).toBeDisabled();
  });

  it("active un module dont les capabilities sont satisfaites", async () => {
    stubFetch([
      tenantsRoute,
      modulesRoute([moduleState()]),
      {
        method: "POST",
        path: "/api/v1/admin/tenants/acme/modules/sample_digest/enable",
        body: { status: "ok" },
      },
    ]);
    renderPage();
    const user = userEvent.setup();

    const enableButton = await screen.findByRole("button", { name: "Activer" });
    expect(enableButton).not.toBeDisabled();
    await user.click(enableButton);
    expect(await screen.findByText("Module activé")).toBeInTheDocument();
  });

  it("propose la désactivation d'un module actif", async () => {
    stubFetch([tenantsRoute, modulesRoute([moduleState({ enabled: true })])]);
    renderPage();

    expect(await screen.findByText("Activé")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Désactiver" })).toBeInTheDocument();
  });
});

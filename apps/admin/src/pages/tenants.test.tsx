import { ToastProvider } from "@app/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TenantsPage } from "@/pages/tenants";
import { stubFetch } from "@/test/mock-fetch";

const tenantsList = {
  method: "GET",
  path: "/api/v1/admin/tenants",
  body: [
    {
      id: "t1",
      slug: "acme",
      name: "ACME",
      state: "active",
      plan: "standard",
      db_name: "tenant_acme",
      schema_revision: "0002_tenant_teams",
    },
  ],
};

function renderTenants() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <TenantsPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TenantsPage", () => {
  it("affiche le catalogue des tenants", async () => {
    stubFetch([tenantsList]);
    renderTenants();

    expect(await screen.findByText("acme")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByText("0002_tenant_teams")).toBeInTheDocument();
  });

  it("création : l'URL d'invitation owner retournée est affichée", async () => {
    stubFetch([
      tenantsList,
      {
        method: "POST",
        path: "/api/v1/admin/tenants",
        status: 201,
        body: {
          tenant: {
            id: "t2",
            slug: "globex",
            name: "globex",
            state: "active",
            plan: "standard",
            db_name: "tenant_globex",
            schema_revision: "0002_tenant_teams",
          },
          owner_invitation_accept_url: "http://localhost:8000/accept-invitation?token=xyz",
        },
      },
    ]);
    renderTenants();
    const user = userEvent.setup();

    await screen.findByText("acme");
    await user.click(screen.getByRole("button", { name: "Nouveau tenant" }));
    await user.type(screen.getByLabelText("Slug"), "globex");
    await user.type(screen.getByLabelText("Email du premier owner (facultatif)"), "owner@example.com");
    await user.click(screen.getByRole("button", { name: "Provisionner" }));

    expect(
      await screen.findByText("http://localhost:8000/accept-invitation?token=xyz"),
    ).toBeInTheDocument();
  });

  it("un tenant en échec propose de rejouer le provisioning", async () => {
    stubFetch([
      {
        method: "GET",
        path: "/api/v1/admin/tenants",
        body: [
          {
            id: "t3",
            slug: "broken",
            name: "broken",
            state: "failed",
            plan: "standard",
            db_name: "tenant_broken",
            schema_revision: null,
          },
        ],
      },
    ]);
    renderTenants();

    expect(await screen.findByRole("button", { name: "Rejouer le provisioning" })).toBeInTheDocument();
  });
});

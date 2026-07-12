import { ToastProvider } from "@app/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AIUsagePage } from "@/pages/ai-usage";
import { stubFetch } from "@/test/mock-fetch";

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AIUsagePage />
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

const acmeUsage = {
  tenant_id: "t1",
  slug: "acme",
  name: "ACME",
  input_tokens: 1000,
  output_tokens: 500,
  cached_tokens: 0,
  request_count: 3,
  error_count: 1,
  estimated_cost_microeur: 1_234_567,
  total_tokens: 1500,
  monthly_token_quota: 1000,
  over_quota: true,
};

const acmePolicy = {
  slug: "acme",
  default_provider: "mistral",
  default_model: "mistral-small-latest",
  allowed_providers: [],
  zero_retention: false,
  monthly_token_quota: null,
  hard_limit_enabled: false,
  fallback_provider: null,
  fallback_model: null,
  byok_configured: false,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AIUsagePage", () => {
  it("rend les agrégats et l'alerte de quota dépassé", async () => {
    stubFetch([
      { method: "GET", path: "/api/v1/admin/ai/usage", body: [acmeUsage] },
      { method: "GET", path: "/api/v1/admin/tenants", body: [acmeTenant] },
    ]);
    renderPage();

    expect(await screen.findByText("acme")).toBeInTheDocument();
    // Alerte de dépassement visible (quota soft, §6).
    expect(screen.getByText("Quota dépassé")).toBeInTheDocument();
    // Compteurs d'erreurs/requêtes rendus.
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("édite la politique d'un tenant (chargement puis PUT)", async () => {
    stubFetch([
      { method: "GET", path: "/api/v1/admin/ai/usage", body: [acmeUsage] },
      { method: "GET", path: "/api/v1/admin/tenants", body: [acmeTenant] },
      { method: "GET", path: "/api/v1/admin/tenants/acme/ai-policy", body: acmePolicy },
      {
        method: "PUT",
        path: "/api/v1/admin/tenants/acme/ai-policy",
        body: { ...acmePolicy, zero_retention: true },
      },
    ]);
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Politique" }));
    // Le formulaire est pré-rempli depuis la politique existante.
    expect(await screen.findByDisplayValue("mistral-small-latest")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    expect(await screen.findByText("Politique enregistrée")).toBeInTheDocument();
  });
});

import { ToastProvider } from "@app/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConnectorsPage } from "@/pages/connectors";
import { stubFetch } from "@/test/mock-fetch";

vi.mock("@/lib/tenant", () => ({ currentTenantSlug: () => "acme" }));

const meWithRole = (role: string) => ({
  method: "GET",
  path: "/api/v1/auth/me",
  body: {
    id: "u1",
    email: "me@example.com",
    display_name: null,
    totp_enabled: false,
    memberships: [{ tenant_slug: "acme", role }],
  },
});

const connection = (overrides: Record<string, unknown> = {}) => ({
  id: "c1",
  provider: "google",
  kind: "tenant",
  account_label: "contact@acme.fr",
  scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
  status: "active",
  last_error: null,
  health_checked_at: "2026-07-12T10:00:00Z",
  access_token_expires_at: "2026-07-12T11:00:00Z",
  created_at: "2026-07-01T09:00:00Z",
  ...overrides,
});

const connectorsList = (items: unknown[]) => ({
  method: "GET",
  path: "/api/v1/connectors",
  body: items,
});

function renderConnectors() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ConnectorsPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ConnectorsPage", () => {
  it("affiche les connexions avec leur statut", async () => {
    stubFetch([
      meWithRole("admin"),
      connectorsList([
        connection(),
        connection({ id: "c2", provider: "microsoft", account_label: "alice@acme.fr", status: "error", last_error: "Refresh échoué : ProviderUnavailable" }),
      ]),
    ]);
    renderConnectors();

    expect(await screen.findByText("contact@acme.fr")).toBeInTheDocument();
    expect(screen.getByText("Google Workspace")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("En erreur")).toBeInTheDocument();
    expect(screen.getByText("Refresh échoué : ProviderUnavailable")).toBeInTheDocument();
  });

  it("un member ne voit ni connexion ni actions de gestion", async () => {
    stubFetch([meWithRole("member"), connectorsList([connection()])]);
    renderConnectors();

    await screen.findByText("contact@acme.fr");
    expect(screen.queryByRole("button", { name: "Connecter Google" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Révoquer" })).not.toBeInTheDocument();
  });

  it("n'affiche « Se reconnecter » que sur une connexion needs_reconsent", async () => {
    stubFetch([
      meWithRole("owner"),
      connectorsList([
        connection(),
        connection({ id: "c2", account_label: "b@acme.fr", status: "needs_reconsent" }),
      ]),
    ]);
    renderConnectors();

    await screen.findByText("b@acme.fr");
    expect(screen.getAllByRole("button", { name: "Se reconnecter" })).toHaveLength(1);
    expect(screen.getByText("Re-consentement requis")).toBeInTheDocument();
  });

  it("demande confirmation avant de révoquer", async () => {
    const deleteCall = vi.fn();
    stubFetch([meWithRole("admin"), connectorsList([connection()])]);
    renderConnectors();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Révoquer" }));
    // La confirmation s'affiche, aucun DELETE n'est parti.
    expect(await screen.findByText("Révoquer la connexion ?")).toBeInTheDocument();
    expect(deleteCall).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Annuler" }));
    expect(screen.queryByText("Révoquer la connexion ?")).not.toBeInTheDocument();
  });

  it("révoque après confirmation", async () => {
    stubFetch([
      meWithRole("admin"),
      connectorsList([connection()]),
      { method: "DELETE", path: "/api/v1/connectors/c1", body: { status: "ok" } },
    ]);
    renderConnectors();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Révoquer" }));
    const dialogButtons = screen.getAllByRole("button", { name: "Révoquer" });
    const confirmButton = dialogButtons[dialogButtons.length - 1];
    if (confirmButton === undefined) throw new Error("Bouton de confirmation introuvable");
    await user.click(confirmButton);

    expect(await screen.findByText("Connexion révoquée")).toBeInTheDocument();
  });
});

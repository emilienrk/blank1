import { ToastProvider } from "@app/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModuleSampleDigestPage } from "@/pages/module-sample-digest";
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

const digest = (overrides: Record<string, unknown> = {}) => ({
  id: "d1",
  generated_at: "2026-07-13T08:00:00Z",
  message_count: 3,
  summary: "• 3 sujets clés",
  ...overrides,
});

const digestsList = (items: unknown[], status = 200) => ({
  method: "GET",
  path: "/api/v1/modules/sample_digest/digests",
  status,
  body: items,
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ModuleSampleDigestPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModuleSampleDigestPage", () => {
  it("affiche les digests et le bouton de génération pour un admin", async () => {
    stubFetch([meWithRole("admin"), digestsList([digest()])]);
    renderPage();

    expect(await screen.findByText("• 3 sujets clés")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Générer maintenant" })).toBeInTheDocument();
  });

  it("un member ne voit pas le bouton de génération", async () => {
    stubFetch([meWithRole("member"), digestsList([digest()])]);
    renderPage();

    await screen.findByText("• 3 sujets clés");
    expect(screen.queryByRole("button", { name: "Générer maintenant" })).not.toBeInTheDocument();
  });

  it("affiche l'état « module non activé » sur un 403 (l'API pilote l'affichage)", async () => {
    stubFetch([meWithRole("owner"), digestsList([], 403)]);
    renderPage();

    expect(await screen.findByText(/n'est pas activé/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Générer maintenant" })).not.toBeInTheDocument();
  });

  it("déclenche la génération manuelle", async () => {
    stubFetch([
      meWithRole("owner"),
      digestsList([digest()]),
      { method: "POST", path: "/api/v1/modules/sample_digest/run", status: 202, body: { status: "scheduled" } },
    ]);
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Générer maintenant" }));
    expect(await screen.findByText("Génération lancée")).toBeInTheDocument();
  });
});

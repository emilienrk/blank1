import { ToastProvider } from "@app/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountSecurityPage } from "@/pages/account-security";
import { stubFetch } from "@/test/mock-fetch";

function meResponse(totpEnabled: boolean) {
  return {
    method: "GET",
    path: "/api/v1/auth/me",
    body: {
      id: "u1",
      email: "alice@example.com",
      display_name: null,
      totp_enabled: totpEnabled,
      memberships: [],
    },
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AccountSecurityPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AccountSecurityPage", () => {
  it("parcours setup -> activate -> codes de récupération affichés une seule fois", async () => {
    stubFetch([
      meResponse(false),
      {
        method: "POST",
        path: "/api/v1/auth/totp/setup",
        body: { secret: "SECRET123", otpauth_uri: "otpauth://totp/x?secret=SECRET123" },
      },
      {
        method: "POST",
        path: "/api/v1/auth/totp/activate",
        body: { recovery_codes: ["aaaa1111", "bbbb2222"] },
      },
    ]);
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByText("Configurer la double authentification"));
    expect(await screen.findByText(/SECRET123/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Code de l'application"), "123456");
    await user.click(screen.getByRole("button", { name: "Activer" }));

    expect(await screen.findByText("aaaa1111")).toBeInTheDocument();
    expect(screen.getByText("bbbb2222")).toBeInTheDocument();
    expect(
      screen.getByText(/Ils ne seront plus jamais affichés/),
    ).toBeInTheDocument();

    // Aucun secret/token dans le storage navigateur (invariant C3).
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("code invalide affiche une erreur sans révéler la cause exacte", async () => {
    stubFetch([
      meResponse(false),
      {
        method: "POST",
        path: "/api/v1/auth/totp/setup",
        body: { secret: "SECRET123", otpauth_uri: "otpauth://totp/x?secret=SECRET123" },
      },
      { method: "POST", path: "/api/v1/auth/totp/activate", status: 400, body: { detail: "x" } },
    ]);
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByText("Configurer la double authentification"));
    await user.type(screen.getByLabelText("Code de l'application"), "000000");
    await user.click(screen.getByRole("button", { name: "Activer" }));

    expect(await screen.findByText("Code invalide.")).toBeInTheDocument();
  });

  it("compte avec TOTP déjà actif : propose la désactivation par mot de passe", async () => {
    stubFetch([
      meResponse(true),
      { method: "POST", path: "/api/v1/auth/totp/disable", body: { status: "ok" } },
    ]);
    renderPage();

    expect(await screen.findByText("La double authentification est active.")).toBeInTheDocument();
    expect(screen.queryByText("Configurer la double authentification")).not.toBeInTheDocument();
  });
});

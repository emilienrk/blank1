import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginPage } from "@/pages/login";
import { renderPage } from "@/test/render-route";
import { stubFetch } from "@/test/mock-fetch";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LoginPage", () => {
  it("connexion directe redirige vers la page cible", async () => {
    stubFetch([{ method: "POST", path: "/api/v1/auth/login", body: { status: "ok" } }]);
    await renderPage("/login", LoginPage);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Email"), "alice@example.com");
    await user.type(screen.getByLabelText("Mot de passe"), "un-mot-de-passe-solide");
    await user.click(screen.getByRole("button", { name: "Se connecter" }));

    expect(await screen.findByText("Accueil (test)")).toBeInTheDocument();
  });

  it("réponse totp_required affiche l'étape code, jamais le jeton n'est persisté", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/v1/auth/login",
        body: { status: "totp_required", challenge_token: "chal-123" },
      },
      { method: "POST", path: "/api/v1/auth/login/totp", body: { status: "ok" } },
    ]);
    await renderPage("/login", LoginPage);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Email"), "alice@example.com");
    await user.type(screen.getByLabelText("Mot de passe"), "un-mot-de-passe-solide");
    await user.click(screen.getByRole("button", { name: "Se connecter" }));

    expect(
      await screen.findByText("Entrez le code de votre application d'authentification."),
    ).toBeInTheDocument();
    // Le jeton de login partiel ne vit qu'en mémoire de page (invariant C3) :
    // rien dans localStorage/sessionStorage.
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);

    await user.type(screen.getByLabelText("Code"), "123456");
    await user.click(screen.getByRole("button", { name: "Valider" }));
    expect(await screen.findByText("Accueil (test)")).toBeInTheDocument();
  });

  it("identifiants invalides affichent une erreur indistincte", async () => {
    stubFetch([{ method: "POST", path: "/api/v1/auth/login", status: 401, body: { detail: "x" } }]);
    await renderPage("/login", LoginPage);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Email"), "alice@example.com");
    await user.type(screen.getByLabelText("Mot de passe"), "mauvais-mot-de-passe");
    await user.click(screen.getByRole("button", { name: "Se connecter" }));

    expect(await screen.findByText("Identifiants invalides.")).toBeInTheDocument();
  });

  it("propose les liens OAuth Google et Microsoft", async () => {
    stubFetch([]);
    await renderPage("/login", LoginPage);
    expect(screen.getByRole("link", { name: "Se connecter avec Google" })).toHaveAttribute(
      "href",
      "/api/v1/auth/oauth/google/start",
    );
    expect(screen.getByRole("link", { name: "Se connecter avec Microsoft" })).toHaveAttribute(
      "href",
      "/api/v1/auth/oauth/microsoft/start",
    );
  });

  it("validation cliente : email et mot de passe requis", async () => {
    stubFetch([]);
    await renderPage("/login", LoginPage);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Se connecter" }));

    await waitFor(() => {
      expect(screen.getByText("Email requis")).toBeInTheDocument();
      expect(screen.getByText("Mot de passe requis")).toBeInTheDocument();
    });
  });
});

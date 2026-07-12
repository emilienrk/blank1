import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginPage } from "@/pages/login";
import { stubFetch } from "@/test/mock-fetch";
import { renderPage } from "@/test/render-route";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LoginPage (admin)", () => {
  it("connexion directe redirige vers la page cible", async () => {
    stubFetch([{ method: "POST", path: "/api/v1/auth/login", body: { status: "ok" } }]);
    await renderPage("/login", LoginPage, {
      search: { redirect: "/" },
      validateSearch: (search) => ({ redirect: search.redirect as string | undefined }),
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Email"), "alice@example.com");
    await user.type(screen.getByLabelText("Mot de passe"), "un-mot-de-passe-solide");
    await user.click(screen.getByRole("button", { name: "Se connecter" }));

    expect(await screen.findByText("Accueil (test)")).toBeInTheDocument();
  });

  it("réponse totp_required affiche l'étape code", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/v1/auth/login",
        body: { status: "totp_required", challenge_token: "chal-123" },
      },
    ]);
    await renderPage("/login", LoginPage);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Email"), "alice@example.com");
    await user.type(screen.getByLabelText("Mot de passe"), "un-mot-de-passe-solide");
    await user.click(screen.getByRole("button", { name: "Se connecter" }));

    expect(
      await screen.findByText("Entrez le code de votre application d'authentification."),
    ).toBeInTheDocument();
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
});

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AcceptInvitationPage } from "@/pages/accept-invitation";
import { stubFetch } from "@/test/mock-fetch";
import { renderPage } from "@/test/render-route";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AcceptInvitationPage", () => {
  it("sans token dans l'URL, affiche un lien invalide", async () => {
    stubFetch([]);
    await renderPage("/accept-invitation", AcceptInvitationPage);
    expect(await screen.findByText("Lien invalide")).toBeInTheDocument();
  });

  it("avec token : nouveau compte, formulaire mot de passe puis confirmation", async () => {
    stubFetch([
      { method: "POST", path: "/api/v1/auth/invitations/accept", body: { status: "ok" } },
    ]);
    await renderPage("/accept-invitation", AcceptInvitationPage, {
      search: { token: "tok-123" },
      validateSearch: (search) => ({ token: search.token as string | undefined }),
    });
    const user = userEvent.setup();

    expect(screen.getByLabelText("Mot de passe")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Mot de passe"), "un-mot-de-passe-de-12-caracteres");
    await user.click(screen.getByRole("button", { name: "Accepter l'invitation" }));

    expect(await screen.findByText("Invitation acceptée")).toBeInTheDocument();
  });

  it("compte existant : pas de champ mot de passe requis", async () => {
    stubFetch([
      { method: "POST", path: "/api/v1/auth/invitations/accept", body: { status: "ok" } },
    ]);
    await renderPage("/accept-invitation", AcceptInvitationPage, {
      search: { token: "tok-123" },
      validateSearch: (search) => ({ token: search.token as string | undefined }),
    });
    const user = userEvent.setup();

    await user.click(screen.getByLabelText("J'ai déjà un compte sur cette plateforme"));
    expect(screen.queryByLabelText("Mot de passe")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accepter l'invitation" }));

    expect(await screen.findByText("Invitation acceptée")).toBeInTheDocument();
  });

  it("token expiré : le message d'erreur du serveur est affiché", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/v1/auth/invitations/accept",
        status: 400,
        body: { detail: "Invitation invalide ou expirée." },
      },
    ]);
    await renderPage("/accept-invitation", AcceptInvitationPage, {
      search: { token: "tok-expired" },
      validateSearch: (search) => ({ token: search.token as string | undefined }),
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Mot de passe"), "un-mot-de-passe-de-12-caracteres");
    await user.click(screen.getByRole("button", { name: "Accepter l'invitation" }));

    expect(await screen.findByText("Invitation invalide ou expirée.")).toBeInTheDocument();
  });
});

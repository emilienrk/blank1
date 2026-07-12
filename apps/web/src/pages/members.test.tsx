import { ToastProvider } from "@app/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MembersPage } from "@/pages/members";
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

const membersList = {
  method: "GET",
  path: "/api/v1/directory/members",
  body: [
    { user_id: "u1", email: "me@example.com", display_name: null, role: "admin" },
    { user_id: "u2", email: "bob@example.com", display_name: "Bob", role: "member" },
  ],
};

const emptyInvitations = { method: "GET", path: "/api/v1/directory/invitations", body: [] };

function renderMembers() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MembersPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MembersPage", () => {
  it("affiche la liste des membres", async () => {
    stubFetch([meWithRole("admin"), membersList, emptyInvitations]);
    renderMembers();

    expect(await screen.findByText("bob@example.com")).toBeInTheDocument();
    expect(screen.getByText("me@example.com")).toBeInTheDocument();
  });

  it("un member ne voit ni le formulaire d'invitation ni les actions de gestion", async () => {
    stubFetch([meWithRole("member"), membersList, emptyInvitations]);
    renderMembers();

    await screen.findByText("bob@example.com");
    expect(screen.queryByText("Inviter un membre")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retirer" })).not.toBeInTheDocument();
    // Rôle affiché en lecture seule (badge), pas de <select>.
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("un admin invite un membre et voit l'URL d'acceptation affichée", async () => {
    stubFetch([
      meWithRole("admin"),
      membersList,
      emptyInvitations,
      {
        method: "POST",
        path: "/api/v1/directory/invitations",
        status: 201,
        body: {
          id: "inv-1",
          role: "member",
          expires_at: "2026-08-01T00:00:00Z",
          accept_url: "http://localhost:8000/accept-invitation?token=abc",
        },
      },
    ]);
    renderMembers();
    const user = userEvent.setup();

    await screen.findByText("Inviter un membre");
    await user.type(screen.getByLabelText("Email"), "carol@example.com");
    await user.click(screen.getByRole("button", { name: "Inviter" }));

    expect(
      await screen.findByText("http://localhost:8000/accept-invitation?token=abc"),
    ).toBeInTheDocument();
  });
});

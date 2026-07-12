import { ToastProvider } from "@app/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuditPage } from "@/pages/audit";
import { stubFetch } from "@/test/mock-fetch";

const firstPage = {
  method: "GET",
  path: "/api/v1/audit/events",
  body: {
    items: [
      {
        id: "e1",
        occurred_at: "2026-07-12T10:00:00Z",
        actor_user_id: "u1",
        actor_label: "Alice",
        action: "core.member.invited",
        resource_type: "invitation",
        resource_id: "inv-1",
        payload: { email: "bob@example.com", role: "member" },
      },
    ],
    next_cursor: "cursor-1",
  },
};

function renderAudit() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AuditPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AuditPage", () => {
  it("affiche les événements et leur acteur", async () => {
    stubFetch([firstPage]);
    renderAudit();

    expect(await screen.findByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("core.member.invited")).toBeInTheDocument();
    expect(screen.getByText("invitation/inv-1")).toBeInTheDocument();
  });

  it("affiche le détail du payload au clic sur l'action", async () => {
    stubFetch([firstPage]);
    renderAudit();
    const user = userEvent.setup();

    await screen.findByText("core.member.invited");
    await user.click(screen.getByText("core.member.invited"));

    expect(await screen.findByText(/bob@example.com/)).toBeInTheDocument();
  });

  it("charge la page suivante via le curseur", async () => {
    // stubFetch ne distingue pas la query string : deux réponses successives au
    // même GET exigent un mock dédié qui lit le paramètre `cursor`.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const url = new URL(request.url);
        const cursor = url.searchParams.get("cursor");
        const body =
          cursor === "cursor-1"
            ? {
                items: [
                  {
                    id: "e2",
                    occurred_at: "2026-07-12T09:00:00Z",
                    actor_user_id: null,
                    actor_label: "cli",
                    action: "core.tenant.provisioned",
                    resource_type: "tenant",
                    resource_id: "t1",
                    payload: {},
                  },
                ],
                next_cursor: null,
              }
            : firstPage.body;
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    renderAudit();
    const user = userEvent.setup();

    const loadMore = await screen.findByRole("button", { name: "Charger plus" });
    await user.click(loadMore);

    expect(await screen.findByText("cli")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Charger plus" })).not.toBeInTheDocument();
  });

  it("filtre par action", async () => {
    stubFetch([firstPage]);
    renderAudit();
    const user = userEvent.setup();

    await screen.findByText("core.member.invited");
    await user.selectOptions(screen.getByRole("combobox"), "core.member.invited");

    expect(screen.getByRole("combobox")).toHaveValue("core.member.invited");
  });
});

import { ToastProvider } from "@app/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MigrationsPage } from "@/pages/migrations";
import { stubFetch } from "@/test/mock-fetch";

function renderMigrations() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MigrationsPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MigrationsPage", () => {
  it("aucun rapport : message d'attente", async () => {
    stubFetch([{ method: "GET", path: "/api/v1/admin/migrations/last-report", status: 200, body: null }]);
    renderMigrations();

    expect(await screen.findByText("Aucun rapport pour l'instant.")).toBeInTheDocument();
  });

  it("rapport rendu base par base, y compris les échecs", async () => {
    stubFetch([
      {
        method: "GET",
        path: "/api/v1/admin/migrations/last-report",
        body: {
          id: "r1",
          status: "done",
          summary: "1/2 base(s) migrée(s)",
          error: null,
          outcomes: [
            {
              database: "controlplane",
              target: "controlplane",
              ok: true,
              revision: "0003_migration_reports",
              error: null,
            },
            {
              database: "tenant_globex",
              target: "globex",
              ok: false,
              revision: null,
              error: "RuntimeError: bogus revision",
            },
          ],
          started_at: "2026-07-12T07:00:00Z",
          finished_at: "2026-07-12T07:00:05Z",
        },
      },
    ]);
    renderMigrations();

    expect(await screen.findByText("1/2 base(s) migrée(s)")).toBeInTheDocument();
    expect(screen.getByText("0003_migration_reports")).toBeInTheDocument();
    expect(screen.getByText("RuntimeError: bogus revision")).toBeInTheDocument();
    expect(screen.getAllByText("OK")).toHaveLength(1);
    expect(screen.getByText("ÉCHEC")).toBeInTheDocument();
  });

  it("déclenche le runner et affiche l'état running renvoyé", async () => {
    stubFetch([
      { method: "GET", path: "/api/v1/admin/migrations/last-report", status: 200, body: null },
      {
        method: "POST",
        path: "/api/v1/admin/migrations/run",
        status: 202,
        body: {
          id: "r2",
          status: "running",
          summary: null,
          error: null,
          outcomes: [],
          started_at: "2026-07-12T07:10:00Z",
          finished_at: null,
        },
      },
    ]);
    renderMigrations();
    const user = userEvent.setup();

    await screen.findByText("Aucun rapport pour l'instant.");
    await user.click(screen.getByRole("button", { name: "Lancer le runner" }));

    expect(await screen.findByText("En cours…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lancer le runner" })).toBeDisabled();
  });
});

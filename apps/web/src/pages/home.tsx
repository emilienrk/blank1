import { StatusBadge } from "@app/ui";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

export function HomePage() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/health");
      if (error !== undefined || data === undefined) {
        throw new Error("L'API ne répond pas");
      }
      return data;
    },
  });

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col items-center justify-center gap-4 p-8">
      <h1 className="text-2xl font-semibold text-slate-900">Socle SaaS</h1>
      {health.isPending && <StatusBadge status="loading" label="Vérification…" />}
      {health.isError && <StatusBadge status="error" label="API injoignable" />}
      {health.isSuccess && (
        <div className="flex flex-col items-center gap-2">
          <StatusBadge status="ok" label={`API ${health.data.status}`} />
          <p className="text-sm text-slate-500">
            version {health.data.version} — environnement {health.data.env}
          </p>
        </div>
      )}
    </main>
  );
}

import { Badge, Button, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, useToast } from "@app/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

const lastReportQueryKey = ["admin", "migrations", "last-report"] as const;

async function fetchLastReport() {
  const { data, error } = await api.GET("/api/v1/admin/migrations/last-report");
  if (error !== undefined) throw new Error("Rapport indisponible.");
  return data ?? null;
}

export function MigrationsPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const report = useQuery({
    queryKey: lastReportQueryKey,
    queryFn: fetchLastReport,
    // Décision D6 : la route ne fait que déclencher (Celery) — on lit le rapport
    // persisté par polling tant qu'il est `running`.
    refetchInterval: (query) => (query.state.data?.status === "running" ? 2000 : false),
  });

  const run = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/admin/migrations/run");
      if (error !== undefined || data === undefined) throw new Error("Déclenchement impossible.");
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(lastReportQueryKey, data);
      toast({ title: "Runner déclenché", description: "Les migrations tournent en arrière-plan." });
    },
    onError: () => toast({ title: "Erreur", description: "Déclenchement impossible.", variant: "error" }),
  });

  const current = report.data;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Migrations</h1>
        <Button onClick={() => run.mutate()} disabled={run.isPending || current?.status === "running"}>
          Lancer le runner
        </Button>
      </div>

      {current === null || current === undefined ? (
        <p className="text-sm text-slate-500">Aucun rapport pour l'instant.</p>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3 rounded-md border border-slate-200 bg-white p-4">
            <Badge variant={current.status === "done" ? "default" : "secondary"}>{current.status}</Badge>
            <span className="text-sm text-slate-600">{current.summary ?? current.error ?? "En cours…"}</span>
            <span className="ml-auto text-xs text-slate-400">
              {new Date(current.started_at).toLocaleString()}
            </span>
          </div>

          <div className="rounded-md border border-slate-200 bg-white">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Base</TableHead>
                  <TableHead>Cible</TableHead>
                  <TableHead>État</TableHead>
                  <TableHead>Détail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {current.outcomes.map((outcome) => (
                  <TableRow key={outcome.database}>
                    <TableCell className="font-mono text-xs">{outcome.database}</TableCell>
                    <TableCell>{outcome.target}</TableCell>
                    <TableCell>
                      <Badge variant={outcome.ok ? "default" : "outline"}>
                        {outcome.ok ? "OK" : "ÉCHEC"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-slate-500">
                      {outcome.ok ? outcome.revision : outcome.error}
                    </TableCell>
                  </TableRow>
                ))}
                {current.outcomes.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={4} className="text-center text-slate-400">
                      En attente d'exécution…
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </div>
  );
}

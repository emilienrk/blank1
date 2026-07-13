import {
  Button,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useToast,
} from "@app/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useCurrentRole } from "@/lib/auth";

const digestsQueryKey = ["module", "sample_digest", "digests"] as const;

async function fetchDigests() {
  const { data, error, response } = await api.GET("/api/v1/modules/sample_digest/digests");
  if (response.status === 403) {
    // L'API 403 pilote l'affichage (T6) : module non activé pour ce tenant.
    throw new Error("MODULE_INACTIVE");
  }
  if (error !== undefined || data === undefined) {
    throw new Error("Impossible de charger les digests.");
  }
  return data;
}

function InactiveNotice() {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
      Le module « Digest » n'est pas activé pour cet espace. Contactez un administrateur
      de la plateforme pour l'activer.
    </div>
  );
}

export function ModuleSampleDigestPage() {
  const role = useCurrentRole();
  const canManage = role === "admin" || role === "owner";
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const digests = useQuery({ queryKey: digestsQueryKey, queryFn: fetchDigests, retry: false });

  const run = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/api/v1/modules/sample_digest/run");
      if (error !== undefined) throw new Error("Déclenchement impossible.");
    },
    onSuccess: () => {
      toast({
        title: "Génération lancée",
        description: "Le digest sera disponible dans quelques instants.",
      });
      // Laisser au worker le temps de produire le digest avant de recharger.
      window.setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: digestsQueryKey });
      }, 2000);
    },
    onError: () =>
      toast({ title: "Erreur", description: "Déclenchement impossible.", variant: "error" }),
  });

  if (digests.isError && digests.error instanceof Error && digests.error.message === "MODULE_INACTIVE") {
    return (
      <div className="flex flex-col gap-6">
        <h1 className="text-xl font-semibold text-slate-900">Digest</h1>
        <InactiveNotice />
      </div>
    );
  }

  const rows = digests.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Digest</h1>
        {canManage && (
          <Button disabled={run.isPending} onClick={() => run.mutate()}>
            Générer maintenant
          </Button>
        )}
      </div>

      <div className="rounded-md border border-slate-200 bg-white">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Généré le</TableHead>
              <TableHead>Emails</TableHead>
              <TableHead>Résumé</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((digest) => (
              <TableRow key={digest.id}>
                <TableCell className="whitespace-nowrap text-xs text-slate-500">
                  {new Date(digest.generated_at).toLocaleString()}
                </TableCell>
                <TableCell>{digest.message_count}</TableCell>
                <TableCell className="whitespace-pre-line text-sm">{digest.summary}</TableCell>
              </TableRow>
            ))}
            {rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={3} className="text-center text-sm text-slate-400">
                  Aucun digest pour l'instant.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

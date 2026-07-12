import {
  Badge,
  Button,
  Dialog,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useToast,
} from "@app/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import { useCurrentRole } from "@/lib/auth";

const connectorsQueryKey = ["connectors"] as const;

type Provider = "google" | "microsoft";

const PROVIDER_LABELS: Record<Provider, string> = {
  google: "Google Workspace",
  microsoft: "Microsoft 365",
};

const STATUS_LABELS: Record<string, string> = {
  active: "Active",
  needs_reconsent: "Re-consentement requis",
  revoked: "Révoquée",
  error: "En erreur",
};

async function fetchConnectors() {
  const { data, error } = await api.GET("/api/v1/connectors");
  if (error !== undefined || data === undefined) {
    throw new Error("Impossible de lister les connecteurs.");
  }
  return data;
}

function StatusBadge({ status }: { status: string }) {
  const variant = status === "active" ? "default" : status === "revoked" ? "outline" : "secondary";
  return (
    <Badge variant={variant} className={status === "needs_reconsent" || status === "error" ? "bg-amber-100 text-amber-800" : undefined}>
      {STATUS_LABELS[status] ?? status}
    </Badge>
  );
}

export function ConnectorsPage() {
  const role = useCurrentRole();
  const canManage = role === "admin" || role === "owner";
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [revokeTarget, setRevokeTarget] = useState<{ id: string; label: string } | null>(null);

  const connectors = useQuery({ queryKey: connectorsQueryKey, queryFn: fetchConnectors });

  const connect = useMutation({
    mutationFn: async (provider: Provider) => {
      const { data, error } = await api.GET("/api/v1/connectors/{provider}/start", {
        params: { path: { provider } },
      });
      if (error !== undefined || data === undefined) throw error ?? new Error("Démarrage impossible.");
      return data.authorization_url;
    },
    onSuccess: (url) => {
      // Consentement chez le provider : redirection pleine page.
      window.location.assign(url);
    },
    onError: (error: unknown) => {
      const detail =
        typeof error === "object" && error !== null && "detail" in error
          ? String((error as { detail?: unknown }).detail)
          : "Connexion impossible — provider non configuré ?";
      toast({ title: "Erreur", description: detail, variant: "error" });
    },
  });

  const reconsent = useMutation({
    mutationFn: async (connectionId: string) => {
      const { data, error } = await api.POST("/api/v1/connectors/{connection_id}/reconsent", {
        params: { path: { connection_id: connectionId } },
      });
      if (error !== undefined || data === undefined) throw error ?? new Error("Re-consentement impossible.");
      return data.authorization_url;
    },
    onSuccess: (url) => window.location.assign(url),
    onError: () =>
      toast({ title: "Erreur", description: "Re-consentement impossible.", variant: "error" }),
  });

  const revoke = useMutation({
    mutationFn: async (connectionId: string) => {
      const { error } = await api.DELETE("/api/v1/connectors/{connection_id}", {
        params: { path: { connection_id: connectionId } },
      });
      if (error !== undefined) throw new Error("Révocation impossible.");
    },
    onSuccess: () => {
      setRevokeTarget(null);
      void queryClient.invalidateQueries({ queryKey: connectorsQueryKey });
      toast({ title: "Connexion révoquée" });
    },
    onError: () =>
      toast({ title: "Erreur", description: "Révocation impossible.", variant: "error" }),
  });

  const rows = connectors.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Connecteurs</h1>
        {canManage && (
          <div className="flex gap-2">
            <Button
              variant="secondary"
              disabled={connect.isPending}
              onClick={() => connect.mutate("google")}
            >
              Connecter Google
            </Button>
            <Button
              variant="secondary"
              disabled={connect.isPending}
              onClick={() => connect.mutate("microsoft")}
            >
              Connecter Microsoft
            </Button>
          </div>
        )}
      </div>

      <div className="rounded-md border border-slate-200 bg-white">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Provider</TableHead>
              <TableHead>Compte</TableHead>
              <TableHead>Statut</TableHead>
              <TableHead>Santé</TableHead>
              {canManage && <TableHead />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((connection) => (
              <TableRow key={connection.id}>
                <TableCell>{PROVIDER_LABELS[connection.provider] ?? connection.provider}</TableCell>
                <TableCell>
                  <span className="font-medium">{connection.account_label}</span>
                  <span className="ml-2 text-xs text-slate-400">({connection.kind})</span>
                </TableCell>
                <TableCell>
                  <StatusBadge status={connection.status} />
                  {connection.last_error !== null && connection.last_error !== undefined && (
                    <p className="mt-1 max-w-xs text-xs text-slate-500">{connection.last_error}</p>
                  )}
                </TableCell>
                <TableCell className="whitespace-nowrap text-xs text-slate-500">
                  {connection.health_checked_at
                    ? new Date(connection.health_checked_at).toLocaleString()
                    : "—"}
                </TableCell>
                {canManage && (
                  <TableCell className="whitespace-nowrap">
                    <div className="flex justify-end gap-2">
                      {connection.status === "needs_reconsent" && (
                        <Button
                          size="sm"
                          disabled={reconsent.isPending}
                          onClick={() => reconsent.mutate(connection.id)}
                        >
                          Se reconnecter
                        </Button>
                      )}
                      {connection.status !== "revoked" && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            setRevokeTarget({ id: connection.id, label: connection.account_label })
                          }
                        >
                          Révoquer
                        </Button>
                      )}
                    </div>
                  </TableCell>
                )}
              </TableRow>
            ))}
            {rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={canManage ? 5 : 4} className="text-center text-sm text-slate-400">
                  Aucun compte connecté.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Dialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null);
        }}
        title="Révoquer la connexion ?"
        description={
          revokeTarget !== null
            ? `Les tokens de « ${revokeTarget.label} » seront détruits. Les modules qui utilisent ce compte cesseront de fonctionner.`
            : undefined
        }
      >
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setRevokeTarget(null)}>
            Annuler
          </Button>
          <Button
            disabled={revoke.isPending}
            onClick={() => revokeTarget !== null && revoke.mutate(revokeTarget.id)}
          >
            Révoquer
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

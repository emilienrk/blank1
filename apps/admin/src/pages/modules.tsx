import {
  Badge,
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
import { useState } from "react";

import { api } from "@/lib/api";

const tenantsQueryKey = ["admin", "tenants"] as const;
const modulesQueryKey = (slug: string) => ["admin", "tenants", slug, "modules"] as const;

export function ModulesPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  const tenants = useQuery({
    queryKey: tenantsQueryKey,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/admin/tenants");
      if (error !== undefined || data === undefined) throw new Error("Tenants indisponibles.");
      return data;
    },
  });

  const slug = selectedSlug ?? tenants.data?.[0]?.slug ?? null;

  const modules = useQuery({
    queryKey: modulesQueryKey(slug ?? ""),
    enabled: slug !== null,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/admin/tenants/{slug}/modules", {
        params: { path: { slug: slug ?? "" } },
      });
      if (error !== undefined || data === undefined) throw new Error("Modules indisponibles.");
      return data;
    },
  });

  const toggle = useMutation({
    mutationFn: async ({ name, enable }: { name: string; enable: boolean }) => {
      if (slug === null) throw new Error("Aucun tenant sélectionné.");
      const path = enable
        ? "/api/v1/admin/tenants/{slug}/modules/{name}/enable"
        : "/api/v1/admin/tenants/{slug}/modules/{name}/disable";
      const { error } = await api.POST(path, { params: { path: { slug, name } } });
      if (error !== undefined) {
        const detail =
          typeof error === "object" && error !== null && "detail" in error
            ? String((error as { detail?: unknown }).detail)
            : "Opération refusée.";
        throw new Error(detail);
      }
    },
    onSuccess: (_data, variables) => {
      if (slug !== null) void queryClient.invalidateQueries({ queryKey: modulesQueryKey(slug) });
      toast({ title: variables.enable ? "Module activé" : "Module désactivé" });
    },
    onError: (error: unknown) =>
      toast({
        title: "Erreur",
        description: error instanceof Error ? error.message : "Opération refusée.",
        variant: "error",
      }),
  });

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Modules</h1>
        <p className="text-sm text-slate-500">
          Activation des modules métier par tenant (onboarding manuel). Un module
          n'est activable que si ses capabilities requises sont satisfaites.
        </p>
      </div>

      <label className="flex items-center gap-2 text-sm text-slate-700">
        Tenant :
        <select
          className="rounded-md border border-slate-300 px-2 py-1 text-sm"
          value={slug ?? ""}
          onChange={(event) => setSelectedSlug(event.target.value)}
        >
          {tenants.data?.map((tenant) => (
            <option key={tenant.id} value={tenant.slug}>
              {tenant.slug}
            </option>
          ))}
        </select>
      </label>

      <div className="rounded-md border border-slate-200 bg-white">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Module</TableHead>
              <TableHead>Version</TableHead>
              <TableHead>État</TableHead>
              <TableHead>Capabilities manquantes</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {modules.data?.map((module) => (
              <TableRow key={module.name}>
                <TableCell>
                  <span className="font-medium">{module.title}</span>
                  <p className="max-w-md text-xs text-slate-500">{module.description}</p>
                </TableCell>
                <TableCell className="font-mono text-xs">{module.version}</TableCell>
                <TableCell>
                  <Badge variant={module.enabled ? "default" : "secondary"}>
                    {module.enabled ? "Activé" : "Inactif"}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-slate-500">
                  {module.missing_capabilities.length > 0
                    ? module.missing_capabilities.join(", ")
                    : "—"}
                </TableCell>
                <TableCell>
                  {module.enabled ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={toggle.isPending}
                      onClick={() => toggle.mutate({ name: module.name, enable: false })}
                    >
                      Désactiver
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      disabled={toggle.isPending || module.missing_capabilities.length > 0}
                      onClick={() => toggle.mutate({ name: module.name, enable: true })}
                    >
                      Activer
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {modules.data?.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-sm text-slate-400">
                  Aucun module au registre.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

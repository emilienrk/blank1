import { zodResolver } from "@hookform/resolvers/zod";
import {
  Badge,
  Button,
  Dialog,
  FormField,
  Input,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useToast,
} from "@app/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api } from "@/lib/api";

const tenantExportsQueryKey = (slug: string) => ["admin", "tenants", slug, "exports"] as const;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`;
  const units = ["Ko", "Mo", "Go"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

function TenantExports({ slug }: { slug: string }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const exports = useQuery({
    queryKey: tenantExportsQueryKey(slug),
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/admin/tenants/{slug}/exports", {
        params: { path: { slug } },
      });
      if (error !== undefined || data === undefined) throw new Error("Liste des exports indisponible.");
      return data;
    },
  });

  const triggerExport = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/api/v1/admin/tenants/{slug}/export", {
        params: { path: { slug } },
      });
      if (error !== undefined) throw new Error("Export impossible.");
    },
    onSuccess: () => {
      toast({ title: "Export lancé", description: "Rafraîchissez dans quelques instants." });
      void queryClient.invalidateQueries({ queryKey: tenantExportsQueryKey(slug) });
    },
    onError: () => toast({ title: "Erreur", description: "Export impossible.", variant: "error" }),
  });

  return (
    <div className="border-t border-slate-100 bg-slate-50 px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase text-slate-500">Exports RGPD</h3>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => void exports.refetch()}
          >
            Rafraîchir
          </Button>
          <Button size="sm" onClick={() => triggerExport.mutate()} disabled={triggerExport.isPending}>
            Lancer un export
          </Button>
        </div>
      </div>
      <ul className="flex flex-col gap-1">
        {exports.data?.map((file) => (
          <li key={file.filename} className="flex items-center justify-between text-sm">
            <span className="font-mono text-xs">{file.filename}</span>
            <span className="flex items-center gap-3 text-xs text-slate-500">
              {formatBytes(file.size_bytes)} · {new Date(file.created_at).toLocaleString()}
              <a
                className="text-slate-700 underline hover:text-slate-900"
                href={`/api/v1/admin/tenants/${slug}/exports/${file.filename}/download`}
              >
                Télécharger
              </a>
            </span>
          </li>
        ))}
        {exports.data?.length === 0 && (
          <li className="text-sm text-slate-400">Aucun export disponible.</li>
        )}
      </ul>
    </div>
  );
}

const tenantsQueryKey = ["admin", "tenants"] as const;

const createTenantSchema = z.object({
  slug: z
    .string()
    .min(2, "2 caractères minimum")
    .regex(/^[a-z][a-z0-9-]{1,38}$/, "minuscules, chiffres, tirets"),
  name: z.string().max(255).optional(),
  owner_email: z.string().email("Email invalide").optional().or(z.literal("")),
});
type CreateTenantForm = z.infer<typeof createTenantSchema>;

async function fetchTenants() {
  const { data, error } = await api.GET("/api/v1/admin/tenants");
  if (error !== undefined || data === undefined) throw new Error("Impossible de lister les tenants.");
  return data;
}

const stateVariant: Record<string, "default" | "secondary" | "outline"> = {
  active: "default",
  provisioning: "secondary",
  failed: "outline",
  suspended: "outline",
  pending_deletion: "outline",
};

function CreateTenantDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [acceptUrl, setAcceptUrl] = useState<string | null>(null);
  const form = useForm<CreateTenantForm>({
    resolver: zodResolver(createTenantSchema),
    defaultValues: { slug: "", name: "", owner_email: "" },
  });

  async function onSubmit(values: CreateTenantForm) {
    const { data, error, response } = await api.POST("/api/v1/admin/tenants", {
      body: {
        slug: values.slug,
        name: values.name || null,
        owner_email: values.owner_email || null,
      },
    });
    if (response.status !== 201 || error !== undefined || data === undefined) {
      const detail =
        typeof error === "object" && error !== null && "detail" in error
          ? String((error as { detail?: unknown }).detail)
          : "Provisioning refusé.";
      toast({ title: "Erreur", description: detail, variant: "error" });
      return;
    }
    await queryClient.invalidateQueries({ queryKey: tenantsQueryKey });
    if (data.owner_invitation_accept_url != null) {
      setAcceptUrl(data.owner_invitation_accept_url);
    } else {
      form.reset({ slug: "", name: "", owner_email: "" });
      onOpenChange(false);
    }
  }

  function close() {
    setAcceptUrl(null);
    form.reset({ slug: "", name: "", owner_email: "" });
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={close} title="Nouveau tenant">
      {acceptUrl !== null ? (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-slate-600">
            Tenant créé — URL d'invitation du premier owner :
          </p>
          <div className="flex items-center gap-2 rounded-md bg-slate-50 p-2 text-xs">
            <code className="flex-1 truncate">{acceptUrl}</code>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => void navigator.clipboard.writeText(acceptUrl)}
            >
              Copier
            </Button>
          </div>
          <Button onClick={close}>Fermer</Button>
        </div>
      ) : (
        <form className="flex flex-col gap-4" onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}>
          <FormField label="Slug" htmlFor="slug" error={form.formState.errors.slug?.message}>
            <Input id="slug" placeholder="acme" {...form.register("slug")} />
          </FormField>
          <FormField label="Nom (facultatif)" htmlFor="name">
            <Input id="name" {...form.register("name")} />
          </FormField>
          <FormField
            label="Email du premier owner (facultatif)"
            htmlFor="owner_email"
            error={form.formState.errors.owner_email?.message}
          >
            <Input id="owner_email" type="email" {...form.register("owner_email")} />
          </FormField>
          <Button type="submit" disabled={form.formState.isSubmitting}>
            Provisionner
          </Button>
        </form>
      )}
    </Dialog>
  );
}

export function TenantsPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const tenants = useQuery({ queryKey: tenantsQueryKey, queryFn: fetchTenants });
  const [dialogOpen, setDialogOpen] = useState(false);
  const [expandedSlug, setExpandedSlug] = useState<string | null>(null);

  const retry = useMutation({
    mutationFn: async (slug: string) => {
      const { error } = await api.POST("/api/v1/admin/tenants/{slug}/retry-provision", {
        params: { path: { slug } },
      });
      if (error !== undefined) throw new Error("Retry impossible.");
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: tenantsQueryKey });
      toast({ title: "Provisioning rejoué" });
    },
    onError: () => toast({ title: "Erreur", description: "Retry impossible.", variant: "error" }),
  });

  const requestErasure = useMutation({
    mutationFn: async (slug: string) => {
      const { error } = await api.POST("/api/v1/admin/tenants/{slug}/request-erasure", {
        params: { path: { slug } },
      });
      if (error !== undefined) throw new Error("Demande d'effacement impossible.");
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: tenantsQueryKey });
      toast({ title: "Effacement demandé", description: "Le tenant est inaccessible immédiatement." });
    },
    onError: () =>
      toast({ title: "Erreur", description: "Demande d'effacement impossible.", variant: "error" }),
  });

  const cancelErasure = useMutation({
    mutationFn: async (slug: string) => {
      const { error } = await api.POST("/api/v1/admin/tenants/{slug}/cancel-erasure", {
        params: { path: { slug } },
      });
      if (error !== undefined) throw new Error("Annulation impossible.");
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: tenantsQueryKey });
      toast({ title: "Effacement annulé" });
    },
    onError: () => toast({ title: "Erreur", description: "Annulation impossible.", variant: "error" }),
  });

  function confirmErasure(slug: string) {
    // Opération la plus destructrice du système (D2) : confirmation explicite
    // avant la demande, en plus du délai de grâce côté serveur.
    if (window.confirm(`Demander l'effacement RGPD du tenant « ${slug} » ?`)) {
      requestErasure.mutate(slug);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Tenants</h1>
        <Button onClick={() => setDialogOpen(true)}>Nouveau tenant</Button>
      </div>

      <div className="rounded-md border border-slate-200 bg-white">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Slug</TableHead>
              <TableHead>Nom</TableHead>
              <TableHead>État</TableHead>
              <TableHead>Plan</TableHead>
              <TableHead>Version de schéma</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {tenants.data?.map((tenant) => (
              <Fragment key={tenant.id}>
                <TableRow>
                  <TableCell className="font-medium">{tenant.slug}</TableCell>
                  <TableCell>{tenant.name}</TableCell>
                  <TableCell>
                    <Badge variant={stateVariant[tenant.state] ?? "secondary"}>{tenant.state}</Badge>
                    {tenant.state === "pending_deletion" && tenant.erasure_due_at != null && (
                      <div className="mt-1 text-xs text-slate-500">
                        Effacement le {new Date(tenant.erasure_due_at).toLocaleString()}
                      </div>
                    )}
                  </TableCell>
                  <TableCell>{tenant.plan}</TableCell>
                  <TableCell className="font-mono text-xs">{tenant.schema_revision ?? "—"}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-2">
                      {tenant.state === "failed" && (
                        <Button size="sm" variant="outline" onClick={() => retry.mutate(tenant.slug)}>
                          Rejouer le provisioning
                        </Button>
                      )}
                      {(tenant.state === "active" || tenant.state === "suspended") && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => confirmErasure(tenant.slug)}
                        >
                          Demander l'effacement
                        </Button>
                      )}
                      {tenant.state === "pending_deletion" && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => cancelErasure.mutate(tenant.slug)}
                        >
                          Annuler l'effacement
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() =>
                          setExpandedSlug(expandedSlug === tenant.slug ? null : tenant.slug)
                        }
                      >
                        RGPD
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
                {expandedSlug === tenant.slug && (
                  <tr>
                    <td colSpan={6} className="p-0">
                      <TenantExports slug={tenant.slug} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      </div>

      <CreateTenantDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}

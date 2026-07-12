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
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api } from "@/lib/api";

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
              <TableRow key={tenant.id}>
                <TableCell className="font-medium">{tenant.slug}</TableCell>
                <TableCell>{tenant.name}</TableCell>
                <TableCell>
                  <Badge variant={stateVariant[tenant.state] ?? "secondary"}>{tenant.state}</Badge>
                </TableCell>
                <TableCell>{tenant.plan}</TableCell>
                <TableCell className="font-mono text-xs">{tenant.schema_revision ?? "—"}</TableCell>
                <TableCell>
                  {tenant.state === "failed" && (
                    <Button size="sm" variant="outline" onClick={() => retry.mutate(tenant.slug)}>
                      Rejouer le provisioning
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <CreateTenantDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}

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

import { api } from "@/lib/api";

const PROVIDERS = ["mistral", "anthropic", "openai"] as const;

const usageQueryKey = ["admin", "ai", "usage"] as const;
const tenantsQueryKey = ["admin", "tenants"] as const;
const policyQueryKey = (slug: string) =>
  ["admin", "ai", "policy", slug] as const;

function formatTokens(value: number): string {
  return value.toLocaleString("fr-FR");
}

function formatEuros(microeur: number): string {
  return `${(microeur / 1_000_000).toFixed(4)} €`;
}

interface PolicyFormState {
  default_provider: string;
  default_model: string;
  allowed_providers: string[];
  zero_retention: boolean;
  monthly_token_quota: string;
  fallback_provider: string;
  fallback_model: string;
}

type PolicyData = {
  default_provider: string | null;
  default_model: string | null;
  allowed_providers: string[];
  zero_retention: boolean;
  monthly_token_quota: number | null;
  fallback_provider: string | null;
  fallback_model: string | null;
};

function toFormState(policy: PolicyData): PolicyFormState {
  return {
    default_provider: policy.default_provider ?? "",
    default_model: policy.default_model ?? "",
    allowed_providers: policy.allowed_providers,
    zero_retention: policy.zero_retention,
    monthly_token_quota:
      policy.monthly_token_quota != null
        ? String(policy.monthly_token_quota)
        : "",
    fallback_provider: policy.fallback_provider ?? "",
    fallback_model: policy.fallback_model ?? "",
  };
}

function PolicyDialog({
  slug,
  onClose,
}: {
  slug: string;
  onClose: () => void;
}) {
  const policy = useQuery({
    queryKey: policyQueryKey(slug),
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/v1/admin/tenants/{slug}/ai-policy",
        {
          params: { path: { slug } },
        },
      );
      if (error !== undefined || data === undefined)
        throw new Error("Politique indisponible.");
      return data;
    },
  });

  return (
    <Dialog open onOpenChange={onClose} title={`Politique IA — ${slug}`}>
      {policy.data === undefined ? (
        <p className="text-sm text-slate-500">Chargement…</p>
      ) : (
        <PolicyForm
          slug={slug}
          initial={toFormState(policy.data)}
          onClose={onClose}
        />
      )}
    </Dialog>
  );
}

function PolicyForm({
  slug,
  initial,
  onClose,
}: {
  slug: string;
  initial: PolicyFormState;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [form, setForm] = useState<PolicyFormState>(initial);

  const save = useMutation({
    mutationFn: async () => {
      const { error } = await api.PUT(
        "/api/v1/admin/tenants/{slug}/ai-policy",
        {
          params: { path: { slug } },
          body: {
            default_provider: form.default_provider || null,
            default_model: form.default_model || null,
            allowed_providers: form.allowed_providers,
            zero_retention: form.zero_retention,
            monthly_token_quota:
              form.monthly_token_quota.trim() === ""
                ? null
                : Number(form.monthly_token_quota),
            hard_limit_enabled: false,
            fallback_provider: form.fallback_provider || null,
            fallback_model: form.fallback_model || null,
          },
        },
      );
      if (error !== undefined) throw new Error("Enregistrement refusé.");
    },
    onSuccess: () => {
      toast({
        title: "Politique enregistrée",
        description: `Tenant « ${slug} »`,
      });
      void queryClient.invalidateQueries({ queryKey: usageQueryKey });
      void queryClient.invalidateQueries({ queryKey: policyQueryKey(slug) });
      onClose();
    },
    onError: () =>
      toast({
        title: "Erreur",
        description: "Enregistrement refusé.",
        variant: "error",
      }),
  });

  function toggleAllowed(provider: string) {
    setForm((prev) => ({
      ...prev,
      allowed_providers: prev.allowed_providers.includes(provider)
        ? prev.allowed_providers.filter((p) => p !== provider)
        : [...prev.allowed_providers, provider],
    }));
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <FormField label="Provider par défaut" htmlFor="default_provider">
        <select
          id="default_provider"
          className="rounded-md border border-slate-300 px-2 py-1 text-sm"
          value={form.default_provider}
          onChange={(e) =>
            setForm((p) => ({ ...p, default_provider: e.target.value }))
          }
        >
          <option value="">Défaut plateforme</option>
          {PROVIDERS.map((provider) => (
            <option key={provider} value={provider}>
              {provider}
            </option>
          ))}
        </select>
      </FormField>
      <FormField label="Modèle par défaut" htmlFor="default_model">
        <Input
          id="default_model"
          value={form.default_model}
          onChange={(e) =>
            setForm((p) => ({ ...p, default_model: e.target.value }))
          }
        />
      </FormField>

      <fieldset className="flex flex-col gap-1">
        <legend className="text-sm font-medium text-slate-700">
          Providers autorisés
        </legend>
        <p className="text-xs text-slate-400">
          Aucun coché = tous les providers configurés.
        </p>
        <div className="flex gap-4">
          {PROVIDERS.map((provider) => (
            <label key={provider} className="flex items-center gap-1 text-sm">
              <input
                type="checkbox"
                checked={form.allowed_providers.includes(provider)}
                onChange={() => toggleAllowed(provider)}
              />
              {provider}
            </label>
          ))}
        </div>
      </fieldset>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.zero_retention}
          onChange={(e) =>
            setForm((p) => ({ ...p, zero_retention: e.target.checked }))
          }
        />
        Zéro-rétention (providers ZDR uniquement — Mistral)
      </label>

      <FormField
        label="Quota mensuel (tokens, vide = défaut)"
        htmlFor="monthly_token_quota"
      >
        <Input
          id="monthly_token_quota"
          type="number"
          value={form.monthly_token_quota}
          onChange={(e) =>
            setForm((p) => ({ ...p, monthly_token_quota: e.target.value }))
          }
        />
      </FormField>

      <FormField
        label="Fallback : provider (optionnel)"
        htmlFor="fallback_provider"
      >
        <select
          id="fallback_provider"
          className="rounded-md border border-slate-300 px-2 py-1 text-sm"
          value={form.fallback_provider}
          onChange={(e) =>
            setForm((p) => ({ ...p, fallback_provider: e.target.value }))
          }
        >
          <option value="">Aucun (désactivé)</option>
          {PROVIDERS.map((provider) => (
            <option key={provider} value={provider}>
              {provider}
            </option>
          ))}
        </select>
      </FormField>
      <FormField label="Fallback : modèle" htmlFor="fallback_model">
        <Input
          id="fallback_model"
          value={form.fallback_model}
          onChange={(e) =>
            setForm((p) => ({ ...p, fallback_model: e.target.value }))
          }
        />
      </FormField>

      <Button type="submit" disabled={save.isPending}>
        Enregistrer
      </Button>
    </form>
  );
}

export function AIUsagePage() {
  const [editingSlug, setEditingSlug] = useState<string | null>(null);

  const usage = useQuery({
    queryKey: usageQueryKey,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/admin/ai/usage");
      if (error !== undefined || data === undefined)
        throw new Error("Usage IA indisponible.");
      return data;
    },
  });

  const tenants = useQuery({
    queryKey: tenantsQueryKey,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/admin/tenants");
      if (error !== undefined || data === undefined)
        throw new Error("Tenants indisponibles.");
      return data;
    },
  });

  const usageBySlug = new Map((usage.data ?? []).map((u) => [u.slug, u]));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">
          Consommation IA
        </h1>
        <p className="text-sm text-slate-500">
          Agrégats du mois courant par tenant (après passage du beat quotidien)
          et politiques.
        </p>
      </div>

      <div className="rounded-md border border-slate-200 bg-white">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Tenant</TableHead>
              <TableHead>Tokens (in / out)</TableHead>
              <TableHead>Requêtes</TableHead>
              <TableHead>Erreurs</TableHead>
              <TableHead>Coût estimé</TableHead>
              <TableHead>Quota</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {tenants.data?.map((tenant) => {
              const row = usageBySlug.get(tenant.slug);
              return (
                <TableRow key={tenant.id}>
                  <TableCell className="font-medium">{tenant.slug}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {row
                      ? `${formatTokens(row.input_tokens)} / ${formatTokens(row.output_tokens)}`
                      : "—"}
                  </TableCell>
                  <TableCell>{row?.request_count ?? 0}</TableCell>
                  <TableCell>{row?.error_count ?? 0}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {row ? formatEuros(row.estimated_cost_microeur) : "—"}
                  </TableCell>
                  <TableCell>
                    {row ? (
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs">
                          {formatTokens(row.total_tokens)} /{" "}
                          {formatTokens(row.monthly_token_quota)}
                        </span>
                        {row.over_quota && (
                          <Badge variant="outline">Quota dépassé</Badge>
                        )}
                      </div>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setEditingSlug(tenant.slug)}
                    >
                      Politique
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {editingSlug !== null && (
        <PolicyDialog slug={editingSlug} onClose={() => setEditingSlug(null)} />
      )}
    </div>
  );
}

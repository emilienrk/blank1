import { zodResolver } from "@hookform/resolvers/zod";
import { Button, FormField, Input } from "@app/ui";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api } from "@/lib/api";

const schema = z.object({
  // `.or(z.literal(""))` : le champ est masqué (compte existant) sans être
  // désenregistré — sa valeur par défaut "" doit rester valide.
  password: z.string().min(12, "12 caractères minimum").max(256).optional().or(z.literal("")),
  display_name: z.string().max(255).optional().or(z.literal("")),
});
type FormValues = z.infer<typeof schema>;

export function AcceptInvitationPage() {
  const { token } = useSearch({ from: "/accept-invitation" });
  const navigate = useNavigate();
  const [existingAccount, setExistingAccount] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState(false);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { password: "", display_name: "" },
  });

  async function onSubmit(values: FormValues) {
    if (token === undefined) return;
    setError(null);
    const { error: apiError, response } = await api.POST("/api/v1/auth/invitations/accept", {
      body: {
        token,
        password: existingAccount ? undefined : (values.password || undefined),
        display_name: values.display_name || undefined,
      },
    });
    if (response.status !== 200 || apiError !== undefined) {
      setError(
        typeof apiError === "object" && apiError !== null && "detail" in apiError
          ? String((apiError as { detail?: unknown }).detail)
          : "Invitation invalide ou expirée.",
      );
      return;
    }
    setAccepted(true);
    setTimeout(() => void navigate({ to: "/login" }), 1500);
  }

  if (token === undefined) {
    return (
      <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 p-8 text-center">
        <h1 className="text-xl font-semibold text-slate-900">Lien invalide</h1>
        <p className="text-sm text-slate-500">Aucun jeton d'invitation dans ce lien.</p>
        <Link to="/login" className="text-sm text-slate-700 underline">
          Retour à la connexion
        </Link>
      </main>
    );
  }

  if (accepted) {
    return (
      <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 p-8 text-center">
        <h1 className="text-xl font-semibold text-slate-900">Invitation acceptée</h1>
        <p className="text-sm text-slate-500">Redirection vers la connexion…</p>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-6 p-8">
      <h1 className="text-xl font-semibold text-slate-900">Accepter l'invitation</h1>
      <form className="flex flex-col gap-4" onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}>
        <FormField label="Nom affiché (facultatif)" htmlFor="display_name">
          <Input id="display_name" {...form.register("display_name")} />
        </FormField>

        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={existingAccount}
            onChange={(event) => setExistingAccount(event.target.checked)}
          />
          J'ai déjà un compte sur cette plateforme
        </label>

        {!existingAccount && (
          <FormField
            label="Mot de passe"
            htmlFor="password"
            hint="12 caractères minimum"
            error={form.formState.errors.password?.message}
          >
            <Input id="password" type="password" autoComplete="new-password" {...form.register("password")} />
          </FormField>
        )}

        {error !== null && (
          <p className="text-sm text-red-600" role="alert">
            {error}
          </p>
        )}

        <Button type="submit" disabled={form.formState.isSubmitting}>
          Accepter l'invitation
        </Button>
      </form>
    </main>
  );
}

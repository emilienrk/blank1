import { zodResolver } from "@hookform/resolvers/zod";
import { Button, FormField, Input } from "@app/ui";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api } from "@/lib/api";
import { useInvalidateCurrentUser } from "@/lib/auth";

const credentialsSchema = z.object({
  email: z.string().min(1, "Email requis").email("Email invalide"),
  password: z.string().min(1, "Mot de passe requis"),
});
type CredentialsForm = z.infer<typeof credentialsSchema>;

const totpSchema = z.object({
  code: z.string().min(6, "Code à 6 chiffres").max(16),
});
type TotpForm = z.infer<typeof totpSchema>;

const OAUTH_PROVIDERS = [
  { id: "google", label: "Google" },
  { id: "microsoft", label: "Microsoft" },
] as const;

export function LoginPage() {
  const navigate = useNavigate();
  const search = useSearch({ from: "/login" });
  const invalidateMe = useInvalidateCurrentUser();
  const [challengeToken, setChallengeToken] = useState<string | null>(null);
  // Erreur volontairement indistincte (pas d'oracle email inconnu / mot de passe faux
  // / code invalide) — même politique que le backend (Phase 2).
  const [formError, setFormError] = useState<string | null>(null);

  async function afterLoginSuccess() {
    invalidateMe();
    await navigate({ to: search.redirect ?? "/" });
  }

  const credentialsForm = useForm<CredentialsForm>({
    resolver: zodResolver(credentialsSchema),
    defaultValues: { email: "", password: "" },
  });

  async function onSubmitCredentials(values: CredentialsForm) {
    setFormError(null);
    const { data, error, response } = await api.POST("/api/v1/auth/login", { body: values });
    if (response.status === 401 || error !== undefined || data === undefined) {
      setFormError("Identifiants invalides.");
      return;
    }
    if (data.status === "totp_required" && data.challenge_token != null) {
      setChallengeToken(data.challenge_token);
      return;
    }
    await afterLoginSuccess();
  }

  const totpForm = useForm<TotpForm>({
    resolver: zodResolver(totpSchema),
    defaultValues: { code: "" },
  });

  async function onSubmitTotp(values: TotpForm) {
    if (challengeToken === null) return;
    setFormError(null);
    const { data, error, response } = await api.POST("/api/v1/auth/login/totp", {
      body: { challenge_token: challengeToken, code: values.code },
    });
    if (response.status === 401 || error !== undefined || data === undefined) {
      setFormError("Code invalide ou expiré.");
      return;
    }
    await afterLoginSuccess();
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-6 p-8">
      <h1 className="text-xl font-semibold text-slate-900">Connexion</h1>

      {challengeToken === null ? (
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => void credentialsForm.handleSubmit(onSubmitCredentials)(event)}
        >
          <FormField
            label="Email"
            htmlFor="email"
            error={credentialsForm.formState.errors.email?.message}
          >
            <Input id="email" type="email" autoComplete="email" {...credentialsForm.register("email")} />
          </FormField>
          <FormField
            label="Mot de passe"
            htmlFor="password"
            error={credentialsForm.formState.errors.password?.message}
          >
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              {...credentialsForm.register("password")}
            />
          </FormField>
          {formError !== null && (
            <p className="text-sm text-red-600" role="alert">
              {formError}
            </p>
          )}
          <Button type="submit" disabled={credentialsForm.formState.isSubmitting}>
            Se connecter
          </Button>
        </form>
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => void totpForm.handleSubmit(onSubmitTotp)(event)}
        >
          <p className="text-sm text-slate-600">Entrez le code de votre application d'authentification.</p>
          <FormField label="Code" htmlFor="code" error={totpForm.formState.errors.code?.message}>
            <Input id="code" inputMode="numeric" autoComplete="one-time-code" {...totpForm.register("code")} />
          </FormField>
          {formError !== null && (
            <p className="text-sm text-red-600" role="alert">
              {formError}
            </p>
          )}
          <Button type="submit" disabled={totpForm.formState.isSubmitting}>
            Valider
          </Button>
        </form>
      )}

      {challengeToken === null && (
        <div className="flex flex-col gap-2 border-t border-slate-200 pt-4">
          {OAUTH_PROVIDERS.map((provider) => (
            <a
              key={provider.id}
              href={`/api/v1/auth/oauth/${provider.id}/start`}
              className="rounded-md border border-slate-300 px-4 py-2 text-center text-sm text-slate-700 hover:bg-slate-50"
            >
              Se connecter avec {provider.label}
            </a>
          ))}
        </div>
      )}
    </main>
  );
}

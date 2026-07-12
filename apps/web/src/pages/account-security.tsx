import { zodResolver } from "@hookform/resolvers/zod";
import { Button, FormField, Input } from "@app/ui";
import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api } from "@/lib/api";
import { useCurrentUser, useInvalidateCurrentUser } from "@/lib/auth";

const codeSchema = z.object({ code: z.string().min(6, "Code à 6 chiffres").max(8) });
type CodeForm = z.infer<typeof codeSchema>;

const passwordSchema = z.object({ password: z.string().min(1, "Mot de passe requis") });
type PasswordForm = z.infer<typeof passwordSchema>;

function EnrollTotp({ onEnrolled }: { onEnrolled: () => void }) {
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const codeForm = useForm<CodeForm>({
    resolver: zodResolver(codeSchema),
    defaultValues: { code: "" },
  });

  async function startSetup() {
    setError(null);
    const { data, error: apiError } = await api.POST("/api/v1/auth/totp/setup");
    if (apiError !== undefined || data === undefined) {
      setError("Impossible de démarrer l'enrôlement TOTP.");
      return;
    }
    setSetup(data);
  }

  async function onActivate(values: CodeForm) {
    setError(null);
    const { data, error: apiError, response } = await api.POST("/api/v1/auth/totp/activate", {
      body: { code: values.code },
    });
    if (response.status !== 200 || apiError !== undefined || data === undefined) {
      setError("Code invalide.");
      return;
    }
    setRecoveryCodes(data.recovery_codes);
  }

  if (recoveryCodes !== null) {
    return (
      <div className="flex flex-col gap-3 rounded-md border border-amber-300 bg-amber-50 p-4">
        <p className="text-sm font-medium text-amber-900">
          Codes de récupération — à conserver en lieu sûr. Ils ne seront plus jamais affichés.
        </p>
        <ul className="grid grid-cols-2 gap-1 font-mono text-sm text-slate-900">
          {recoveryCodes.map((code) => (
            <li key={code}>{code}</li>
          ))}
        </ul>
        <Button onClick={onEnrolled} className="self-start">
          J'ai enregistré mes codes
        </Button>
      </div>
    );
  }

  if (setup !== null) {
    return (
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => void codeForm.handleSubmit(onActivate)(event)}
      >
        <QRCodeSVG value={setup.otpauth_uri} size={180} />
        <p className="text-xs text-slate-500">
          Saisie manuelle possible : <span className="font-mono">{setup.secret}</span>
        </p>
        <FormField label="Code de l'application" htmlFor="totp-code" error={codeForm.formState.errors.code?.message}>
          <Input id="totp-code" inputMode="numeric" {...codeForm.register("code")} />
        </FormField>
        {error !== null && (
          <p className="text-sm text-red-600" role="alert">
            {error}
          </p>
        )}
        <Button type="submit" className="self-start" disabled={codeForm.formState.isSubmitting}>
          Activer
        </Button>
      </form>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {error !== null && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}
      <Button onClick={() => void startSetup()} className="self-start">
        Configurer la double authentification
      </Button>
    </div>
  );
}

function DisableTotp({ onDisabled }: { onDisabled: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const form = useForm<PasswordForm>({
    resolver: zodResolver(passwordSchema),
    defaultValues: { password: "" },
  });

  async function onSubmit(values: PasswordForm) {
    setError(null);
    const { error: apiError, response } = await api.POST("/api/v1/auth/totp/disable", {
      body: { password: values.password },
    });
    if (response.status !== 200 || apiError !== undefined) {
      setError("Mot de passe incorrect.");
      return;
    }
    onDisabled();
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}>
      <p className="text-sm text-slate-600">La double authentification est active.</p>
      <FormField label="Mot de passe" htmlFor="disable-password" error={form.formState.errors.password?.message}>
        <Input id="disable-password" type="password" {...form.register("password")} />
      </FormField>
      {error !== null && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}
      <Button type="submit" variant="destructive" className="self-start" disabled={form.formState.isSubmitting}>
        Désactiver
      </Button>
    </form>
  );
}

export function AccountSecurityPage() {
  const { data: me } = useCurrentUser();
  const invalidateMe = useInvalidateCurrentUser();

  if (me === null || me === undefined) return null;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold text-slate-900">Sécurité du compte</h1>
      <section className="rounded-md border border-slate-200 bg-white p-6">
        <h2 className="mb-4 text-sm font-semibold text-slate-900">
          Double authentification (TOTP)
        </h2>
        {me.totp_enabled ? (
          <DisableTotp onDisabled={invalidateMe} />
        ) : (
          <EnrollTotp onEnrolled={invalidateMe} />
        )}
      </section>
    </div>
  );
}

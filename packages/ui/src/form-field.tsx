import type { ReactNode } from "react";

import { Label } from "./label";
import { cn } from "./lib/utils";

export interface FormFieldProps {
  label: string;
  htmlFor: string;
  error?: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}

/** Bloc label + champ + erreur — la validation elle-même vit dans les pages
 * (react-hook-form + zod, décision D3 Phase 3) : ce composant est purement présentationnel. */
export function FormField({ label, htmlFor, error, hint, children, className }: FormFieldProps) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint !== undefined && error === undefined && (
        <p className="text-xs text-slate-500">{hint}</p>
      )}
      {error !== undefined && (
        <p className="text-xs text-red-600" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

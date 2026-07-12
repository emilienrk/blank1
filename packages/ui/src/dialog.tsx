import { useEffect, type ReactNode } from "react";

import { cn } from "./lib/utils";

export interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children?: ReactNode;
  className?: string;
}

/** Modale minimale (sans Radix — périmètre volontairement réduit, décision T5) :
 * Escape + clic sur le fond ferment, `role="dialog"` + `aria-modal` pour l'a11y. */
export function Dialog({ open, onOpenChange, title, description, children, className }: DialogProps) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onOpenChange]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Fermer"
        className="fixed inset-0 bg-slate-950/40"
        onClick={() => onOpenChange(false)}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        className={cn(
          "relative z-10 w-full max-w-md rounded-lg bg-white p-6 shadow-lg",
          className,
        )}
      >
        <h2 id="dialog-title" className="text-lg font-semibold text-slate-900">
          {title}
        </h2>
        {description !== undefined && (
          <p className="mt-1 text-sm text-slate-500">{description}</p>
        )}
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}

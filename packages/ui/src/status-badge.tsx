export interface StatusBadgeProps {
  status: "ok" | "error" | "loading";
  label?: string;
}

const styles: Record<StatusBadgeProps["status"], string> = {
  ok: "bg-emerald-100 text-emerald-800",
  error: "bg-red-100 text-red-800",
  loading: "bg-slate-100 text-slate-600",
};

export function StatusBadge({ status, label }: StatusBadgeProps) {
  return (
    <span
      data-status={status}
      className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-medium ${styles[status]}`}
    >
      {label ?? status}
    </span>
  );
}

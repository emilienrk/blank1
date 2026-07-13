import { useEffect, useState, type ReactNode } from "react";

import { Link, Outlet, useNavigate } from "@tanstack/react-router";

import { api } from "@/lib/api";
import { useCurrentRole, useCurrentUser, useInvalidateCurrentUser } from "@/lib/auth";
import { currentTenantSlug } from "@/lib/tenant";

/** Écouté depuis `lib/api.ts` : un 403 sur n'importe quel appel affiche cet état
 * plutôt que le contenu protégé (T1 — le front relaie, il ne décide jamais seul). */
function useForbidden(): boolean {
  const [forbidden, setForbidden] = useState(false);
  useEffect(() => {
    const onForbidden = () => setForbidden(true);
    window.addEventListener("api:forbidden", onForbidden);
    return () => window.removeEventListener("api:forbidden", onForbidden);
  }, []);
  return forbidden;
}

const navItems = [
  { to: "/", label: "Accueil" },
  { to: "/members", label: "Membres" },
  { to: "/teams", label: "Équipes" },
  { to: "/connectors", label: "Connecteurs" },
  { to: "/modules/sample-digest", label: "Digest" },
  { to: "/account-security", label: "Sécurité du compte" },
] as const;

function NavBar() {
  const { data: me } = useCurrentUser();
  const invalidateMe = useInvalidateCurrentUser();
  const navigate = useNavigate();
  const tenant = currentTenantSlug();
  const role = useCurrentRole();
  const canReadAudit = role === "admin" || role === "owner";

  async function logout() {
    await api.POST("/api/v1/auth/logout");
    invalidateMe();
    await navigate({ to: "/login" });
  }

  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-3">
        <div className="flex items-center gap-6">
          <span className="text-sm font-semibold text-slate-900">
            {tenant ?? "Socle SaaS"}
          </span>
          <nav className="flex gap-4 text-sm text-slate-600">
            {navItems.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className="hover:text-slate-900 [&.active]:font-semibold [&.active]:text-slate-900"
              >
                {item.label}
              </Link>
            ))}
            {canReadAudit && (
              <Link
                to="/audit"
                className="hover:text-slate-900 [&.active]:font-semibold [&.active]:text-slate-900"
              >
                Journal d'audit
              </Link>
            )}
          </nav>
        </div>
        <div className="flex items-center gap-3 text-sm text-slate-600">
          {me !== null && me !== undefined && <span>{me.email}</span>}
          <button type="button" onClick={() => void logout()} className="hover:text-slate-900">
            Déconnexion
          </button>
        </div>
      </div>
    </header>
  );
}

function ForbiddenPage() {
  return (
    <main className="mx-auto flex max-w-xl flex-col items-center gap-2 p-16 text-center">
      <h1 className="text-xl font-semibold text-slate-900">Accès refusé</h1>
      <p className="text-sm text-slate-500">
        Vous n'avez pas la permission d'effectuer cette action sur cet espace.
      </p>
    </main>
  );
}

export function AppLayout({ children }: { children?: ReactNode }) {
  const forbidden = useForbidden();

  return (
    <div className="min-h-screen bg-slate-50">
      <NavBar />
      <div className="mx-auto max-w-4xl px-6 py-8">
        {forbidden ? <ForbiddenPage /> : (children ?? <Outlet />)}
      </div>
    </div>
  );
}

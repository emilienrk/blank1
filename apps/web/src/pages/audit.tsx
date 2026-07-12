import {
  Badge,
  Button,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@app/ui";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";

import { api } from "@/lib/api";

const auditEventsQueryKey = (action: string) => ["audit", "events", action] as const;

async function fetchAuditEvents(action: string, cursor: string | undefined) {
  const { data, error } = await api.GET("/api/v1/audit/events", {
    params: { query: { action: action || undefined, cursor } },
  });
  if (error !== undefined || data === undefined) {
    throw new Error("Impossible de lister le journal d'audit.");
  }
  return data;
}

export function AuditPage() {
  const [action, setAction] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const events = useInfiniteQuery({
    queryKey: auditEventsQueryKey(action),
    queryFn: ({ pageParam }: { pageParam: string | undefined }) => fetchAuditEvents(action, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const rows = events.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Journal d'audit</h1>
        <select
          className="h-9 rounded-md border border-slate-300 px-2 text-sm"
          value={action}
          onChange={(event) => setAction(event.target.value)}
        >
          <option value="">Toutes les actions</option>
          <option value="core.member.invited">Invitation créée</option>
          <option value="core.member.invitation_revoked">Invitation révoquée</option>
          <option value="core.member.invitation_accepted">Invitation acceptée</option>
          <option value="core.member.role_changed">Rôle modifié</option>
          <option value="core.member.removed">Membre retiré</option>
          <option value="core.team.created">Équipe créée</option>
          <option value="core.team.deleted">Équipe supprimée</option>
          <option value="core.team.member_added">Membre ajouté à une équipe</option>
          <option value="core.team.member_removed">Membre retiré d'une équipe</option>
          <option value="core.tenant.provisioned">Tenant provisionné</option>
        </select>
      </div>

      <div className="rounded-md border border-slate-200 bg-white">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead>
              <TableHead>Acteur</TableHead>
              <TableHead>Action</TableHead>
              <TableHead>Ressource</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((event) => (
              <Fragment key={event.id}>
                <TableRow>
                  <TableCell className="whitespace-nowrap text-xs text-slate-500">
                    {new Date(event.occurred_at).toLocaleString()}
                  </TableCell>
                  <TableCell>{event.actor_label}</TableCell>
                  <TableCell>
                    <button
                      type="button"
                      className="text-left hover:underline"
                      onClick={() => setExpandedId(expandedId === event.id ? null : event.id)}
                    >
                      <Badge>{event.action}</Badge>
                    </button>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {event.resource_type}/{event.resource_id}
                  </TableCell>
                </TableRow>
                {expandedId === event.id && (
                  <tr>
                    <td colSpan={4} className="border-t border-slate-100 bg-slate-50 px-4 py-3">
                      <pre className="overflow-x-auto text-xs text-slate-700">
                        {JSON.stringify(event.payload, null, 2)}
                      </pre>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={4} className="text-center text-sm text-slate-400">
                  Aucun événement.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {events.hasNextPage && (
        <Button
          variant="secondary"
          onClick={() => void events.fetchNextPage()}
          disabled={events.isFetchingNextPage}
        >
          Charger plus
        </Button>
      )}
    </div>
  );
}

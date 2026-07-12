import { zodResolver } from "@hookform/resolvers/zod";
import {
  Badge,
  Button,
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
import { useCurrentRole } from "@/lib/auth";

const ROLES = ["member", "admin", "owner"] as const;

const inviteSchema = z.object({
  email: z.string().email("Email invalide"),
  role: z.enum(ROLES),
});
type InviteForm = z.infer<typeof inviteSchema>;

const membersQueryKey = ["directory", "members"] as const;
const invitationsQueryKey = ["directory", "invitations"] as const;

async function fetchMembers() {
  const { data, error } = await api.GET("/api/v1/directory/members");
  if (error !== undefined || data === undefined) throw new Error("Impossible de lister les membres.");
  return data;
}

async function fetchInvitations() {
  const { data, error } = await api.GET("/api/v1/directory/invitations");
  if (error !== undefined || data === undefined) {
    throw new Error("Impossible de lister les invitations.");
  }
  return data;
}

function InviteForm({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [acceptUrl, setAcceptUrl] = useState<string | null>(null);
  const form = useForm<InviteForm>({
    resolver: zodResolver(inviteSchema),
    defaultValues: { email: "", role: "member" },
  });

  async function onSubmit(values: InviteForm) {
    const { data, error, response } = await api.POST("/api/v1/directory/invitations", {
      body: values,
    });
    if (response.status !== 201 || error !== undefined || data === undefined) {
      const detail =
        typeof error === "object" && error !== null && "detail" in error
          ? String((error as { detail?: unknown }).detail)
          : "Invitation refusée.";
      toast({ title: "Erreur", description: detail, variant: "error" });
      return;
    }
    setAcceptUrl(data.accept_url);
    form.reset({ email: "", role: "member" });
    await queryClient.invalidateQueries({ queryKey: invitationsQueryKey });
  }

  if (!canManage) return null;

  return (
    <div className="rounded-md border border-slate-200 bg-white p-4">
      <h2 className="mb-3 text-sm font-semibold text-slate-900">Inviter un membre</h2>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}
      >
        <FormField label="Email" htmlFor="invite-email" error={form.formState.errors.email?.message}>
          <Input id="invite-email" type="email" {...form.register("email")} />
        </FormField>
        <FormField label="Rôle" htmlFor="invite-role">
          <select
            id="invite-role"
            className="h-9 rounded-md border border-slate-300 px-2 text-sm"
            {...form.register("role")}
          >
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
        </FormField>
        <Button type="submit" disabled={form.formState.isSubmitting}>
          Inviter
        </Button>
      </form>
      {acceptUrl !== null && (
        <div className="mt-3 flex items-center gap-2 rounded-md bg-slate-50 p-2 text-xs">
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
      )}
    </div>
  );
}

function PendingInvitations({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const invitations = useQuery({ queryKey: invitationsQueryKey, queryFn: fetchInvitations });

  const revoke = useMutation({
    mutationFn: async (invitationId: string) => {
      const { error } = await api.DELETE("/api/v1/directory/invitations/{invitation_id}", {
        params: { path: { invitation_id: invitationId } },
      });
      if (error !== undefined) throw new Error("Révocation impossible.");
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: invitationsQueryKey });
      toast({ title: "Invitation révoquée" });
    },
    onError: () => toast({ title: "Erreur", description: "Révocation impossible.", variant: "error" }),
  });

  if (invitations.data === undefined || invitations.data.length === 0) return null;

  return (
    <div className="rounded-md border border-slate-200 bg-white p-4">
      <h2 className="mb-3 text-sm font-semibold text-slate-900">Invitations en attente</h2>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Email</TableHead>
            <TableHead>Rôle</TableHead>
            <TableHead>Expire le</TableHead>
            {canManage && <TableHead />}
          </TableRow>
        </TableHeader>
        <TableBody>
          {invitations.data.map((invitation) => (
            <TableRow key={invitation.id}>
              <TableCell>{invitation.email}</TableCell>
              <TableCell>
                <Badge>{invitation.role}</Badge>
              </TableCell>
              <TableCell>{new Date(invitation.expires_at).toLocaleDateString()}</TableCell>
              {canManage && (
                <TableCell>
                  <Button size="sm" variant="outline" onClick={() => revoke.mutate(invitation.id)}>
                    Révoquer
                  </Button>
                </TableCell>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function MembersPage() {
  const role = useCurrentRole();
  const canManage = role === "admin" || role === "owner";
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const members = useQuery({ queryKey: membersQueryKey, queryFn: fetchMembers });

  const changeRole = useMutation({
    mutationFn: async ({ userId, newRole }: { userId: string; newRole: string }) => {
      const { error } = await api.PATCH("/api/v1/directory/members/{user_id}", {
        params: { path: { user_id: userId } },
        body: { role: newRole },
      });
      if (error !== undefined) throw error;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: membersQueryKey }),
    onError: (error: unknown) => {
      const detail =
        typeof error === "object" && error !== null && "detail" in error
          ? String((error as { detail?: unknown }).detail)
          : "Changement de rôle refusé.";
      toast({ title: "Erreur", description: detail, variant: "error" });
    },
  });

  const removeMember = useMutation({
    mutationFn: async (userId: string) => {
      const { error } = await api.DELETE("/api/v1/directory/members/{user_id}", {
        params: { path: { user_id: userId } },
      });
      if (error !== undefined) throw error;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: membersQueryKey }),
    onError: (error: unknown) => {
      const detail =
        typeof error === "object" && error !== null && "detail" in error
          ? String((error as { detail?: unknown }).detail)
          : "Retrait refusé.";
      toast({ title: "Erreur", description: detail, variant: "error" });
    },
  });

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold text-slate-900">Membres</h1>

      {canManage && <InviteForm canManage={canManage} />}
      <PendingInvitations canManage={canManage} />

      <div className="rounded-md border border-slate-200 bg-white p-4">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Email</TableHead>
              <TableHead>Nom</TableHead>
              <TableHead>Rôle</TableHead>
              {canManage && <TableHead />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {members.data?.map((member) => (
              <TableRow key={member.user_id}>
                <TableCell>{member.email}</TableCell>
                <TableCell>{member.display_name ?? "—"}</TableCell>
                <TableCell>
                  {canManage ? (
                    <select
                      className="h-8 rounded-md border border-slate-300 px-2 text-sm"
                      value={member.role}
                      onChange={(event) =>
                        changeRole.mutate({ userId: member.user_id, newRole: event.target.value })
                      }
                    >
                      {ROLES.map((role) => (
                        <option key={role} value={role}>
                          {role}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <Badge>{member.role}</Badge>
                  )}
                </TableCell>
                {canManage && (
                  <TableCell>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => removeMember.mutate(member.user_id)}
                    >
                      Retirer
                    </Button>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

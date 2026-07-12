import { zodResolver } from "@hookform/resolvers/zod";
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
import { Fragment, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api } from "@/lib/api";
import { useCurrentRole } from "@/lib/auth";

const teamsQueryKey = ["directory", "teams"] as const;
const membersQueryKey = ["directory", "members"] as const;
const teamMembersQueryKey = (teamId: string) => ["directory", "teams", teamId, "members"] as const;

const createTeamSchema = z.object({
  name: z.string().min(1, "Nom requis").max(120),
  description: z.string().max(2000).optional(),
});
type CreateTeamForm = z.infer<typeof createTeamSchema>;

async function fetchTeams() {
  const { data, error } = await api.GET("/api/v1/directory/teams");
  if (error !== undefined || data === undefined) throw new Error("Impossible de lister les équipes.");
  return data;
}

async function fetchMembers() {
  const { data, error } = await api.GET("/api/v1/directory/members");
  if (error !== undefined || data === undefined) throw new Error("Impossible de lister les membres.");
  return data;
}

function CreateTeamDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const form = useForm<CreateTeamForm>({
    resolver: zodResolver(createTeamSchema),
    defaultValues: { name: "", description: "" },
  });

  async function onSubmit(values: CreateTeamForm) {
    const { error, response } = await api.POST("/api/v1/directory/teams", {
      body: { name: values.name, description: values.description || null },
    });
    if (response.status !== 201 || error !== undefined) {
      toast({ title: "Erreur", description: "Une équipe porte déjà ce nom.", variant: "error" });
      return;
    }
    form.reset({ name: "", description: "" });
    await queryClient.invalidateQueries({ queryKey: teamsQueryKey });
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Nouvelle équipe">
      <form className="flex flex-col gap-4" onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}>
        <FormField label="Nom" htmlFor="team-name" error={form.formState.errors.name?.message}>
          <Input id="team-name" {...form.register("name")} />
        </FormField>
        <FormField label="Description (facultatif)" htmlFor="team-description">
          <Input id="team-description" {...form.register("description")} />
        </FormField>
        <Button type="submit" disabled={form.formState.isSubmitting}>
          Créer
        </Button>
      </form>
    </Dialog>
  );
}

function TeamComposition({ teamId, canManage }: { teamId: string; canManage: boolean }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const teamMembers = useQuery({
    queryKey: teamMembersQueryKey(teamId),
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/directory/teams/{team_id}/members", {
        params: { path: { team_id: teamId } },
      });
      if (error !== undefined || data === undefined) throw new Error("Composition indisponible.");
      return data;
    },
  });
  const members = useQuery({ queryKey: membersQueryKey, queryFn: fetchMembers });
  const [selectedUserId, setSelectedUserId] = useState("");

  const addMember = useMutation({
    mutationFn: async (userId: string) => {
      const { error } = await api.POST("/api/v1/directory/teams/{team_id}/members", {
        params: { path: { team_id: teamId } },
        body: { user_id: userId },
      });
      if (error !== undefined) throw new Error("Ajout impossible.");
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: teamMembersQueryKey(teamId) }),
    onError: () => toast({ title: "Erreur", description: "Ajout impossible.", variant: "error" }),
  });

  const removeMember = useMutation({
    mutationFn: async (userId: string) => {
      const { error } = await api.DELETE("/api/v1/directory/teams/{team_id}/members/{user_id}", {
        params: { path: { team_id: teamId, user_id: userId } },
      });
      if (error !== undefined) throw new Error("Retrait impossible.");
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: teamMembersQueryKey(teamId) }),
    onError: () => toast({ title: "Erreur", description: "Retrait impossible.", variant: "error" }),
  });

  const availableMembers = (members.data ?? []).filter(
    (member) => !teamMembers.data?.some((tm) => tm.user_id === member.user_id),
  );

  return (
    <div className="border-t border-slate-100 bg-slate-50 px-4 py-3">
      <ul className="mb-3 flex flex-col gap-1">
        {teamMembers.data?.map((member) => (
          <li key={member.user_id} className="flex items-center justify-between text-sm">
            <span>{member.email}</span>
            {canManage && (
              <button
                type="button"
                className="text-xs text-slate-500 hover:text-red-600"
                onClick={() => removeMember.mutate(member.user_id)}
              >
                Retirer
              </button>
            )}
          </li>
        ))}
        {teamMembers.data?.length === 0 && (
          <li className="text-sm text-slate-400">Aucun membre dans cette équipe.</li>
        )}
      </ul>
      {canManage && (
        <div className="flex items-center gap-2">
          <select
            className="h-8 flex-1 rounded-md border border-slate-300 px-2 text-sm"
            value={selectedUserId}
            onChange={(event) => setSelectedUserId(event.target.value)}
          >
            <option value="">Ajouter un membre…</option>
            {availableMembers.map((member) => (
              <option key={member.user_id} value={member.user_id}>
                {member.email}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            variant="secondary"
            disabled={selectedUserId === ""}
            onClick={() => {
              addMember.mutate(selectedUserId);
              setSelectedUserId("");
            }}
          >
            Ajouter
          </Button>
        </div>
      )}
    </div>
  );
}

export function TeamsPage() {
  const role = useCurrentRole();
  const canManage = role === "admin" || role === "owner";
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const teams = useQuery({ queryKey: teamsQueryKey, queryFn: fetchTeams });
  const [dialogOpen, setDialogOpen] = useState(false);
  const [expandedTeamId, setExpandedTeamId] = useState<string | null>(null);

  const deleteTeam = useMutation({
    mutationFn: async (teamId: string) => {
      const { error } = await api.DELETE("/api/v1/directory/teams/{team_id}", {
        params: { path: { team_id: teamId } },
      });
      if (error !== undefined) throw new Error("Suppression impossible.");
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: teamsQueryKey }),
    onError: () => toast({ title: "Erreur", description: "Suppression impossible.", variant: "error" }),
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Équipes</h1>
        {canManage && <Button onClick={() => setDialogOpen(true)}>Nouvelle équipe</Button>}
      </div>

      <div className="rounded-md border border-slate-200 bg-white">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Nom</TableHead>
              <TableHead>Description</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {teams.data?.map((team) => (
              <Fragment key={team.id}>
                <TableRow>
                  <TableCell>
                    <button
                      type="button"
                      className="text-left font-medium hover:underline"
                      onClick={() =>
                        setExpandedTeamId(expandedTeamId === team.id ? null : team.id)
                      }
                    >
                      {team.name}
                    </button>
                  </TableCell>
                  <TableCell>{team.description ?? <Badge variant="outline">—</Badge>}</TableCell>
                  <TableCell>
                    {canManage && (
                      <Button size="sm" variant="outline" onClick={() => deleteTeam.mutate(team.id)}>
                        Supprimer
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
                {expandedTeamId === team.id && (
                  <tr>
                    <td colSpan={3} className="p-0">
                      <TeamComposition teamId={team.id} canManage={canManage} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      </div>

      <CreateTeamDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}

# Liste des sous-traitants ultérieurs (trame)

> Trame à compléter au fil de l'activation réelle de chaque service — à faire
> relire par un juriste avant les premiers clients réels.

| Sous-traitant | Rôle | Données concernées | Localisation | Statut |
|---|---|---|---|---|
| Hébergeur (machine de staging/prod) | Infrastructure (Compose, disques) | Toutes (chiffrées au repos) | France (auto-hébergement) | À documenter (fournisseur, contrat) dès la machine de staging en place. |
| Relais SMTP transactionnel | Envoi des emails d'invitation/notification | Email du destinataire, contenu du message (lien d'invitation) | France (Scaleway TEM / OVH pressentis) | Non activé (`SMTP_HOST` vide = aucun envoi ; l'URL d'invitation est toujours retournée à l'appelant). |
| Mistral | AI Gateway (chat, embeddings) — **provider par défaut** | Contenu des requêtes envoyées au modèle | France (UE) | **Intégré (Phase 6, `app.ai`)**. Provider de la liste **zéro-rétention (ZDR)** en code : le seul autorisé lorsqu'un tenant est en `zero_retention`. DPA à référencer. |
| Anthropic | AI Gateway (chat) | Contenu des requêtes envoyées au modèle, selon la politique du tenant | Hors UE (États-Unis) | **Intégré (Phase 6, `app.ai`)** — activé seulement si `ANTHROPIC_API_KEY` est configurée et le provider autorisé par la politique. Transfert hors UE : **DPA + SCC à référencer**. Refusé pour tout tenant en zéro-rétention (liste ZDR fermée en code). |
| OpenAI | AI Gateway (chat, embeddings) | Contenu des requêtes envoyées au modèle, selon la politique du tenant | Hors UE (États-Unis) | **Intégré (Phase 6, `app.ai`)** — activé seulement si `OPENAI_API_KEY` est configurée et le provider autorisé par la politique. Transfert hors UE : **DPA + SCC à référencer**. Refusé pour tout tenant en zéro-rétention (liste ZDR fermée en code). |
| Connecteurs Google Workspace / Microsoft 365 | Accès aux données du tenant via OAuth (mail, agenda…) | Données métier du tenant, selon les capabilities activées | Hors UE (Google, Microsoft) | **Intégré** (`app.connectors`). Le tenant consent explicitement à la connexion (flux OAuth dédié, distinct du login) ; tokens chiffrés au repos. DPA/SCC à référencer dès l'activation réelle. |

## À compléter (juridique)

- Contrats de sous-traitance (DPA) avec chaque fournisseur activé.
- Garanties de transfert hors UE (SCC) pour Anthropic/OpenAI/Google/Microsoft
  le jour de leur activation effective.
- Registre mis à jour à chaque nouveau sous-traitant (nouvelle ligne + PR).

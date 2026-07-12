# Liste des sous-traitants ultérieurs (trame)

> Trame à compléter au fil de l'activation réelle de chaque service (voir
> `docs/phase-4-audit-rgpd-plan.md` §F5) — à faire relire par un juriste avant
> les premiers clients réels.

| Sous-traitant | Rôle | Données concernées | Localisation | Statut |
|---|---|---|---|---|
| Hébergeur (machine de staging/prod) | Infrastructure (Compose, disques) | Toutes (chiffrées au repos) | France (auto-hébergement, §1 du plan global) | À documenter (fournisseur, contrat) dès la machine de staging en place. |
| Relais SMTP transactionnel | Envoi des emails d'invitation/notification | Email du destinataire, contenu du message (lien d'invitation) | France (Scaleway TEM / OVH pressentis, §8.4 du plan global) | Non activé en Phase 4 (`smtp_host` vide = aucun envoi, décision D8 Phase 2). |
| Providers IA (Anthropic, OpenAI, Mistral) | AI Gateway (chat, embeddings) | Contenu des requêtes envoyées au modèle, selon la politique de rétention du tenant | Hors UE (Anthropic, OpenAI) sauf Mistral (France) | **Pas encore intégré** — arrive en Phase 6 (`app.ai`). Politique « zéro rétention » par tenant prévue pour neutraliser le sujet des transferts hors UE (DPA + SCC à référencer). |
| Connecteurs Google Workspace / Microsoft 365 | Accès aux données du tenant via OAuth (mail, agenda…) | Données métier du tenant, selon les capabilities activées | Hors UE (Google, Microsoft) | **Pas encore intégré** — arrive en Phase 5 (`app.connectors`). Le tenant consent explicitement à la connexion (flux OAuth dédié, distinct du login). |

## À compléter (juridique)

- Contrats de sous-traitance (DPA) avec chaque fournisseur activé.
- Garanties de transfert hors UE (SCC) pour Anthropic/OpenAI/Google/Microsoft
  le jour de leur activation effective.
- Registre mis à jour à chaque nouveau sous-traitant (nouvelle ligne + PR).

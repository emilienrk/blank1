# Procédure de notification de violation de données (trame)

> Trame opérationnelle — à faire relire par un juriste avant les premiers
> clients réels (voir `docs/phase-4-audit-rgpd-plan.md` §F5). L'obligation
> légale est une notification à l'autorité de contrôle **sous 72 heures**
> après constat (article 33 RGPD), et aux personnes concernées « dans les
> meilleurs délais » si le risque est élevé (article 34).

## 1. Détection

Sources possibles : alerte Uptime Kuma (indisponibilité anormale), anomalie
dans les logs techniques (Loki/Grafana — accès admin uniquement), alerte d'un
tenant, scan de dépendances CI (Phase 8), signalement externe.

## 2. Qualification (dans l'heure suivant la détection)

Réunir l'équipe technique disponible et répondre à :

1. Quelle est la nature de l'incident (accès non autorisé, perte, altération,
   divulgation) ?
2. Quelles données sont concernées ? Quels tenants ?
3. Combien de personnes sont potentiellement affectées ?
4. L'incident est-il en cours (à contenir) ou terminé ?

**Action immédiate si en cours** : révoquer les sessions/tokens concernés
(`app.auth`), suspendre le ou les tenants affectés si nécessaire
(`saas admin` / back-office), couper l'accès compromis.

## 3. Décision de notification (dans les 24h suivant la détection)

- Si un risque pour les droits et libertés des personnes est identifié :
  notification à la CNIL (ou autorité de contrôle compétente) **sous 72h**
  à compter de la connaissance de la violation.
- Si le risque est élevé : notification également aux personnes concernées,
  en clair et dans un langage simple.
- Si aucun risque identifié : documenter la décision de ne pas notifier
  (obligation de traçabilité même en cas de non-notification).

## 4. Contenu de la notification à l'autorité

- Nature de la violation.
- Catégories et nombre approximatif de personnes/enregistrements concernés.
- Contact du DPO ou point de contact.
- Conséquences probables.
- Mesures prises ou proposées (contenir, corriger, limiter les impacts).

## 5. Modèle de message (personnes concernées)

> Objet : Information relative à un incident de sécurité concernant vos données
>
> Bonjour,
>
> Nous vous informons qu'un incident de sécurité a affecté [nature des
> données] entre le [date] et le [date]. [Description factuelle et mesures
> prises]. Nous vous recommandons de [action éventuelle : changer votre mot
> de passe, activer la double authentification…].
>
> Pour toute question : [contact].

## 6. Contacts à tenir à jour

| Rôle | Contact | À compléter |
|---|---|---|
| Point de contact technique | — | |
| DPO / référent RGPD | — | |
| Autorité de contrôle (CNIL) | https://www.cnil.fr/fr/notifier-une-violation-de-donnees-personnelles | |

## 7. Post-mortem

Une fois l'incident clos : documenter la chronologie, la cause racine, les
actions correctives, et mettre à jour cette procédure si nécessaire — le
journal d'audit (`app.audit`) et les logs techniques (Loki) sont les deux
sources factuelles à croiser pour la chronologie.

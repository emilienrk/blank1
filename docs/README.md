# Documentation

Chaque document a un public et un rôle précis — avant d'en créer un nouveau, se
demander lequel des trois il sert.

## Comprendre le projet (pour les humains)

À lire dans cet ordre pour monter en compétence :

| Document | Contenu |
|---|---|
| [README racine](../README.md) | Démarrage local, commandes, exploitation (staging, CLI `saas`) |
| [`architecture.md`](architecture.md) | Vue d'ensemble : composants, flux d'une requête et d'une tâche, sécurité |
| [`backend.md`](backend.md) | Chaque brique du socle près du code : mécanismes, patterns, points d'entrée |
| [`frontend.md`](frontend.md) | La SPA : client généré, état d'auth, routage, conventions |
| [`creer-un-module.md`](creer-un-module.md) | **La phase actuelle** : guide pas à pas pour créer un module métier |

## Conventions et invariants (contexte pour l'IA — et référence pour tous)

Les trois `CLAUDE.md` ([racine](../CLAUDE.md), [`apps/api`](../apps/api/CLAUDE.md),
[`app/modules`](../apps/api/app/modules/CLAUDE.md)) condensent les règles non
négociables. Ils sont chargés automatiquement dans le contexte des sessions
assistées par IA : denses à dessein, ils disent *quoi respecter* ; les documents
ci-dessus expliquent *comment ça marche*.

## Historique et décisions

| Document | Contenu |
|---|---|
| [`adr/`](adr/README.md) | Les décisions structurantes, datées, avec procédure de réintroduction |
| [`archive/`](archive/README.md) | Ce qui a été retiré à la simplification MVP et comment le retrouver (tag git `archive/pre-mvp-simplification` — y compris les plans de phase historiques) |
| [`superpowers/specs/`](superpowers/specs/) | Design docs validés (ex. la simplification MVP) |

## Conformité

[`rgpd/`](rgpd/) : registre des traitements, sous-traitants, procédure de
notification de violation — trames techniques à faire relire par un juriste avant
les premiers clients réels.

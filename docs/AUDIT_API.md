# Audit API et réutilisation — 4 novembre 2026

Aucun accès au serveur local. L’image installée, les réseaux, l’URL interne et
les personnalisations sont à vérifier sur Ubuntu. Le document joint est utilisé
comme cahier des charges ; la demande explicite fixe le MVP aux actions positives.

## Sources et projets

Code examiné au tag [awinterstein/habitica v5.50.6](https://github.com/awinterstein/habitica/tree/v5.50.6),
correspondant à l’image citée. La [dernière release retournée par GitHub](https://github.com/awinterstein/habitica/releases)
était `v5.51.0`, publiée le 6 octobre 2026. `website/server/libs/webhook.js` est
identique entre ces deux tags. Cela ne vérifie pas l’image actuellement installée.
Aucune mise à jour Habitica n’est nécessaire pour installer HPM.

L’[API officielle v3](https://apidoc.habitica.com/) est complétée par le code
du tag ciblé : le site de documentation dynamique n’a pas fourni son contenu
complet dans l’outil de consultation. Les champs ci-dessous viennent du code.

| Projet | Licence et état observé | Décision |
|---|---|---|
| [Habitica amont](https://github.com/HabitRPG/habitica) / [fork auto-hébergé](https://github.com/awinterstein/habitica) | Code GPLv3, releases du fork actives | Utiliser le serveur et son API. Aucun code ni asset copié ; HPM reste indépendant, MIT |
| [Home Assistant Habitica](https://github.com/home-assistant/core/tree/dev/homeassistant/components/habitica) | Home Assistant Apache-2.0, intégration dans core ; `habiticalib==0.4.7` lors de l’audit | Conserver l’automatisation existante sur Kaell. Pas de file durable et de distribution multi-compagnons correspondantes |
| [Habitica Full Sync](https://github.com/MidwestGuru/habitica-fullsync) | MIT ; dernière activité retournée : 8 janvier 2026 | Synchronisation Obsidian, commandes de score ; pas un service autonome de progression de plusieurs comptes |
| [Todoist → Habitica Sync](https://github.com/radeklat/todoist-habitica-sync) | MIT ; dernière activité retournée : 11 février 2026 | Synchronisation externe liée à Todoist, pas de réception des événements natifs de Kaell |

« Sync Habitica Tasks » n’a pas identifié un dépôt unique avec certitude : c’est
notamment une commande de Habitica Full Sync. Un lien précis permettrait de
réexaminer un autre candidat. Une activité récente ne garantit pas la maintenance.
Les licences seraient à revérifier avant toute copie de code.

Choix : un client REST httpx réduit et un moteur durable indépendant. Réutiliser
ces projets ajouterait leurs dépendances sans fournir le cœur du besoin.

## Webhooks vérifiés

Sources : [sender](https://github.com/awinterstein/habitica/blob/v5.50.6/website/server/libs/webhook.js),
[modèle](https://github.com/awinterstein/habitica/blob/v5.50.6/website/server/models/webhook.js),
[contrôleur](https://github.com/awinterstein/habitica/blob/v5.50.6/website/server/controllers/api-v3/webhook.js),
[scoring](https://github.com/awinterstein/habitica/blob/v5.50.6/website/common/script/ops/scoreTask.js).

`POST /api/v3/user/webhook` accepte le type `taskActivity` et les options
booléennes `scored`, `created`, `updated`, `deleted`, `checklistScored`.
GET liste les webhooks ; PUT `/user/webhook/:id` les modifie. HPM ne crée que
celui de Kaell, avec `scored=true`, les autres options à `false`.

Le payload de score contient `webhookType=taskActivity`, `type=scored`,
`direction`, `delta`, `task`, `user._id`, `user._tmp`, `user.stats`.
**Aucun ID d’événement, timestamp de livraison ou en-tête signé n’est ajouté.**
`task.id`/`task._id` identifie une tâche, pas une validation.

- Habitude : `value`, `counterUp`, `counterDown`, et dernière entrée d’historique
  avec `date` en millisecondes, `value`, `scoredUp`, `scoredDown`. L’historique
  est agrégé par jour, il n’est pas une liste de tous les clics.
- Quotidienne : `completed=true`, entrée d’historique datée et état de complétion.
- Todo : `completed=true` et `dateCompleted` sérialisé en date JSON.
- `created`, `updated`, `deleted` portent la tâche ; `checklistScored` porte
  aussi `item`. Aucun de ces quatre types ne récompense dans le MVP.

HPM exige Kaell, `scored`, `up`, `delta>0`, un type éligible et un état de score
renseigné. Les payloads incompatibles sont journalisés sans récompense. Seuls
les champs autorisés sont conservés : ni notes privées, ni clés, ni stats complètes.

L’empreinte SHA-256 utilise du JSON canonique : maître, type d’événement,
direction, delta, identité/type/valeur/compteurs/complétion de la tâche,
dernière entrée d’historique et date de fin du todo. Les champs inconnus et
l’heure de réception sont exclus. Contraintes uniques sur l’empreinte et sur
`(event_id, companion)`, sans expiration. Pas de fenêtre « même tâche X secondes ».

## Livraison et sécurité réelles

Le sender appelle `got.post` avec timeout 30 secondes et `retry: 3`. Cette
option n’établit pas trois reprises POST : [Got 11.8.6](https://github.com/sindresorhus/got/tree/v11.8.6)
exclut POST des méthodes réessayées par défaut. Pas de file durable ni de rejeu
des événements exposé par cette API. Après dix échecs comptabilisés, le webhook
est désactivé ; un échec de plus d’un mois remet ce compteur à zéro.

Dans [libs/tasks/index.js](https://github.com/awinterstein/habitica/blob/v5.50.6/website/server/libs/tasks/index.js),
l’émission est lancée avant la sauvegarde finale des tâches et de l’utilisateur.
Un échec de sauvegarde peut donc laisser un événement sans action source
persistée. Les scores batch et concurrents restent à qualifier sur l’instance
réelle. HPM ne peut rendre atomiques les écritures des deux applications.

Pas de signature HMAC ni de secret d’en-tête configurable dans le code audité.
HPM emploie un secret aléatoire dans le chemin et un réseau Docker restreint.
`user._id` filtre l’origine logique sans preuve cryptographique : une personne
ayant ce secret et l’accès réseau peut forger des événements. Désactiver les
logs d’accès sur ce chemin ; ne pas copier les URL de webhook dans le chat.
L’interface et la CLI ne retournent pas ces URL.

## REST utilisé

En-têtes `x-api-user`, `x-api-key`, `x-client`. Clés en fichiers, vérification
TLS active, redirections refusées.

| Endpoint v3 | Usage |
|---|---|
| GET `/user` | Vérification d’identité à la demande |
| GET `/user/webhook` | Diagnostic et installation du webhook |
| POST `/user/webhook`, PUT `/user/webhook/:id` | Installation explicite en CLI sur Kaell |
| GET `/tasks/user` | Audit des tâches des compagnons |
| POST `/tasks/user` | Création explicite de l’habitude après audit |
| GET `/tasks/:id` | Contrôle avant chaque récompense |
| POST `/tasks/:id/score/up` | Un score positif par compagnon |

Pas de score négatif, de modification directe d’XP/or, de suppression ou
modification des tâches existantes, ni d’appel MongoDB. GET peut être repris
sans écriture. Connexion échouée avant envoi et 429 : reprise temporisée,
`Retry-After`, backoff et limite de tentatives. POST 5xx/timeout après envoi :
incertain, décision manuelle. Un proxy qui réécrirait un succès en 429 casserait
l’hypothèse qu’un 429 est un refus avant application.

## Rendements, journée et Cron

Le score utilise `0.9747 ** clamp(value, -47.27, 21.27)` : le rendement baisse
mais possède un plancher d’environ **0,58**, avant les autres multiplicateurs.
Le [Cron serveur](https://github.com/awinterstein/habitica/blob/v5.50.6/website/server/libs/cron.js)
ramène la valeur d’une habitude à signe unique vers zéro par division par deux.
`frequency` règle ses compteurs ; ce n’est pas un réglage libre d’XP.

Depuis la version 0.2, HPM conserve `task.priority` et sélectionne une des douze
habitudes positives par type/difficulté. Chaque destination a sa priorité native
(0.1, 1, 1.5, 2) ; un seul score par événement. Titre de base et fréquence restent
configurables à la création. `habit_priority` v0.1 reste accepté dans le TOML
sans effet sur ce routage. Aucun reset artificiel ni multiplicateur caché ;
les habitudes existantes ne sont pas modifiées. L'empreinte reste celle de v0.1
pour reconnaître les livraisons anciennes, la priorité est conservée séparément
comme donnée de routage et dans le snapshot.

HPM **n’appelle pas `/cron`** : cet endpoint applique les dommages des quotidiennes
manquées, qui exigent un choix distinct sur les tâches existantes. Sans exécution
native du Cron, diminution de valeur et résolution de certaines progressions
de quête peuvent rester différées. Le MVP fournit la progression par scoring,
pas l’automatisation complète du cycle de quête. `TZ=Europe/Paris` ne change
pas les préférences Habitica : harmoniser fuseau et `dayStart` séparément.

## Vérification périodique future

On peut surveiller connexion, existence des habitudes et `enabled/failures`
des webhooks. Les états et historiques agrégés ne reconstruisent pas toutes
les annulations et revalidations. Le MVP ne récompense pas à partir d’une
différence de compteurs : risque de doublons et de rattrapage massif. Une
extension devra proposer des corrections unitaires revues et bornées.

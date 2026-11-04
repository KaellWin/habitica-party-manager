# Recette — simulations puis instance locale

## Ce qui est vérifié automatiquement

`pytest` teste les trois types positifs, deux scores de la même habitude dans
la même milliseconde, doublons canoniques et concurrents, source étrangère,
actions négatives, achats/checklists et événements de maintenance ignorés,
observation/simulation sans écriture, absence de rejeu et de rattrapage ancien,
désactivation, arrêt, 429, connexion indisponible, GET 503, POST 5xx, timeout,
réponse perdue après application distante, échec définitif, tâche devenue
incompatible, redémarrage, isolation des compagnons, audit et acquittement,
authentification, limite de corps, secrets expurgés, verrou unique, installation
idempotente du webhook et sauvegarde cohérente.

`scripts/check_demo.py` teste de vrais échanges HTTP entre deux conteneurs
(FastAPI HPM et API Habitica synthétique) : simulation sans score, douze couples type/difficulté
positifs → trente-six scores, deux doublons et négatif → aucun score supplémentaire.
Ce simulateur ne reproduit pas le calcul complet d’XP, les classes, Cron ou quêtes.

## Ce qui reste à vérifier sur Ubuntu

Prérequis : mode observation, clés configurées localement, webhook unique sur
Kaell, pas de modification MongoDB. Noter les compteurs et l’heure Europe/Paris.
Le journal conserve un snapshot minimal, pas l’intégralité des données personnelles.

| Essai réel | Attendu |
|---|---|
| Validation manuelle d’une habitude Kaell | Un événement `positive_score`, `direction=up`, bonne identité, historique daté ; aucune opération en observation |
| Validation d’une quotidienne | `completed=true` et dernière date d’historique ; aucune opération en observation |
| Todo terminé | `completed=true`, `dateCompleted` ; aucune opération en observation |
| Deux clics positifs légitimes sur la même habitude | Deux empreintes, compteurs/valeurs différents, même ID de tâche |
| Un palier attribué par Home Assistant | Même contrat que la validation manuelle ; un seul score source pour ce palier |
| Passage en simulation puis nouvelles validations | Trois opérations `simulated`, aucun changement chez les compagnons |
| Préparation après audit des tâches | Douze habitudes techniques positives par compagnon ; aucune tâche existante modifiée |
| Actif sur Heal seul, une nouvelle validation | Un `succeeded`, un seul score sur Heal ; aucun score supplémentaire sur Kaell |
| Actif sur les trois, une nouvelle validation | Trois `succeeded`, un par compte |
| Redémarrage HPM | Mode et journal conservés ; seules opérations sûres reprennent ; `sending` devient incertain |
| Arrêt via interface | Opérations en attente annulées ; pas de reprise lors du réarmement |
| Connexion/état webhook | Webhook activé, compteur d’échec surveillé ; pas d’exposition de son URL |

Pour le test HA, garder les seuils `input_number.objectif_quotidien_pas` et
`input_number.habitica_palier_de_pas` en place. Ne pas rajouter un appel direct
aux compagnons : HA valide Kaell, HPM suit son score. Ne pas modifier les
automatisations sans les examiner. Vérifier qu’aucun autre flux ne récompense
déjà Heal/Magus/War pour les mêmes pas ou le brossage.

Les essais de pannes, rejeu de webhook et actions négatives se font avec la
stack de démonstration, sans provoquer de pénalités sur les vrais personnages.
Un événement rejeté `missing_score_time` ou `missing_habit_counter` indique une
divergence de payload à étudier avant activation, pas un champ à inventer.

## Accepter une mise en service

Les captures manuelle et HA doivent confirmer les champs, les validations
successives doivent rester distinctes et l’audit doit être examiné pour les
trois compagnons. Vérifier les gains natifs sur les comptes réels et les effets
sur les quêtes ; le MVP ne résout pas le cycle Cron des compagnons.

En cas de résultat `uncertain`, vérifier l’heure, le compagnon et l’historique
Habitica : « Déjà attribuée » clôt sans score, « Abandonner » clôt sans score,
« Réessayer » envoie à nouveau et peut doubler une récompense. La décision et
sa note sont conservées. Un changement de mode n’efface jamais cette incertitude.

## Recette 0.2 — types et difficultés

Après migration, vérifier le retour en observation et l'absence de rejeu des
anciens événements. Auditer et préparer les douze habitudes par compagnon ;
l'ancienne habitude doit conserver son contenu et sa valeur. Tester en simulation
une tâche de chaque type, aux quatre difficultés : le couple affiché doit
correspondre au webhook. En actif, une validation donne une opération par
compagnon et un seul score de l'habitude correspondante. Modifier une difficulté
ne doit pas transformer un doublon du même état de score en nouvel événement.
Les tests automatisés couvrent les douze combinaisons, une réponse de création
perdue, l'interruption atomique de migration et la conservation des identités v0.1.

Vérifier l'accès à http://192.168.0.2:8088 depuis un autre appareil du LAN,
l'authentification par le même jeton et l'absence de tunnel SSH. Cet accès réel
LAN reste à vérifier chez vous : le cloud utilise sa propre interface réseau.

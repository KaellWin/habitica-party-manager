# Mise à jour 0.1 → 0.2 : difficulté, type et accès LAN

Cette procédure concerne votre installation déjà fonctionnelle. Aucune clé ne
doit être recopiée dans GitHub ou dans le chat. Les fichiers de configuration et
de secrets existants sont compatibles ; inutile de relancer `init-host.py`.

## 1. Arrêter la distribution et sauvegarder

Dans l'interface actuelle, passer en **Arrêté**. Dans la console Portainer du
conteneur HPM encore en fonctionnement :

```bash
python -m hpm.cli backup --destination /data/backups/avant-0.2.sqlite3
```

Si le nom existe déjà, utiliser un nouveau nom. Conserver également le TOML et
les secrets via votre sauvegarde habituelle. Arrêter ensuite ce seul conteneur
HPM dans Portainer, sans supprimer les données et sans toucher Habitica.

## 2. Récupérer et construire la nouvelle version sur Ubuntu

```bash
cd /opt/stacks/habitica-party-manager
sudo git pull --ff-only origin feat/positive-sync-mvp
sudo docker build -t habitica-party-manager:0.2.0 .
```

La nouvelle image est construite sur le même Docker que Portainer. L'ancienne
image `0.1.0` reste disponible pour un retour arrière avec restauration de la base.

## 3. Mettre à jour la stack dans Portainer

Dans **Stacks → habitica-party-manager → Editor**, remplacer le Compose par le
nouveau `compose.yaml` du dépôt. Conserver les montages et le réseau existants.
Dans les variables de la stack, définir explicitement :

```text
HPM_IMAGE=habitica-party-manager:0.2.0
HPM_ROOT=/srv/habitica-party-manager
HABITICA_NETWORK=habitica_habitica
HPM_BIND_IP=192.168.0.2
HPM_PORT=8088
HPM_NO_PROXY=habitica-server-1,hpm.internal,localhost,127.0.0.1
```

Une ancienne variable `HPM_BIND_IP=127.0.0.1` ou `HPM_IMAGE=...:0.1.0` prime sur
les nouveaux défauts du Compose : il faut bien la modifier. Mettre à jour la
stack avec l'image locale, sans pull forcé depuis un registre.

La publication devient `192.168.0.2:8088:8080`. Depuis un appareil du LAN, ouvrir
directement **http://192.168.0.2:8088** et saisir le jeton d'administration
habituel. Aucun tunnel SSH n'est nécessaire. Ne pas publier ce port sur Internet.
Le webhook interne `http://hpm.internal:8080/...` reste identique : ne pas le
réenregistrer. En cas de pare-feu, vérifier l'accès depuis le LAN ; Docker peut
gérer ses propres règles de publication de ports.

## 4. Préparer les nouvelles habitudes

Au premier démarrage sur une base 0.1, la migration atomique :

- conserve les événements, leurs empreintes, l'historique et les activations ;
- remet le service en **Observation** et les compagnons « À préparer » ;
- annule les anciennes opérations encore en attente ;
- garde les opérations incertaines pour vérification sans les réessayer ;
- ne modifie aucune tâche et ne contacte pas Habitica pour créer des habitudes.

Pour chaque compagnon, cliquer **Auditer les tâches**, lire le rapport, puis
**Préparer les 12 habitudes**. HPM crée uniquement les destinations manquantes :
Habitude / Quotidienne / À faire × Triviale / Facile / Moyenne / Difficile.
Exemple : `⚙️ Progression Kaell · Quotidienne · Difficile`.
Chaque destination est une habitude positive avec la difficulté native associée.

L'ancienne habitude `⚙️ Progression Kaell` reste intacte et apparaît dans le rapport
comme habitude de l'ancienne version ; elle ne reçoit plus les nouveaux scores.
Une préparation interrompue peut être reprise en faisant un nouvel audit : les
habitudes déjà créées sont retrouvées par alias. En cas de limite HTTP 429,
attendre avant de relancer l'audit et la préparation. Aucune tâche existante
n'est corrigée ou supprimée automatiquement.

## 5. Vérifier puis réactiver

Passer en **Simulation**. Valider sur Kaell des tâches de types et difficultés
différents, puis vérifier les colonnes **Type** et **Difficulté** des opérations.
Il doit toujours y avoir une seule opération par compagnon activé et par score
positif. Une priorité source absente ou invalide produit
`invalid_source_priority`, sans score ni difficulté supposée.

Repasser en observation, désactiver Magus et War pour un premier essai, passer en
actif et vérifier Heal. Réactiver ensuite les deux autres. Les événements passés
ne seront pas rejoués. Les montants exacts d'XP/or restent propres aux compagnons,
à leurs statistiques et à la valeur de chaque habitude ; aucun bonus de type
ou multiplicateur de validations n'est ajouté.

Les options v0.1 `habit_priority` et `companions.task_id`, si elles sont présentes,
restent acceptées pour compatibilité du TOML mais ne choisissent plus la
destination des nouveaux scores. C'est `task.priority` du webhook qui détermine
la difficulté et `task.type` qui détermine la catégorie.

## Retour arrière

Arrêter HPM, conserver la base v2 à part, restaurer le snapshot v1 et ses fichiers
de configuration en suivant la procédure de restauration du guide Portainer.
Forcer l'observation dans la base restaurée avant redémarrage. Revenir à l'image
0.1.0 seulement avec cette base v1 : l'ancienne image refuse le nouveau schéma.
Les habitudes créées en 0.2 restent en place et ne provoquent aucune pénalité
de quotidienne ; ne pas les supprimer automatiquement. Vérifier les récompenses
déjà reçues depuis le snapshot avant toute réactivation.

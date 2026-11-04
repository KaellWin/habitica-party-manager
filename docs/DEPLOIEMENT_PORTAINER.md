# Ubuntu et Portainer — Docker Standalone

Les commandes suivantes sont à exécuter **sur votre serveur Ubuntu**, pas dans
le cloud. Elles n’altèrent ni Habitica, ni MongoDB, ni Home Assistant. Leurs
valeurs de réseau et d’URL doivent être relevées localement. Ne transmettre
aucune clé API, export complet d’environnement ou URL secrète dans le chat.

## 1. Relever uniquement les informations utiles

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
docker network ls
# Remplacer NOM_SERVEUR_API par le conteneur backend Habitica, pas le client web.
docker inspect NOM_SERVEUR_API --format '{{json .NetworkSettings.Networks}}'
```

Noter le nom exact du réseau commun, le nom/alias du backend et son port interne
(souvent 3000, à confirmer). Exemple de `base_url` :
`http://habitica-server:3000`. Ne pas prendre le port d’un frontal qui ne sert
que les fichiers de l’interface Habitica. Ne pas copier le résultat d’un
`docker inspect` intégral, susceptible de contenir des secrets.

## 2. Installer le projet et préparer les secrets

Copier le projet complet dans `/opt/stacks/habitica-party-manager`.
Depuis ce dossier :

```bash
cd /opt/stacks/habitica-party-manager
sudo python3 scripts/init-host.py
sudoedit /srv/habitica-party-manager/config/config.toml
cp .env.example .env
editor .env
```

Le script crée seulement les fichiers absents, génère deux jetons aléatoires
distincts et demande les quatre clés API avec saisie masquée. Il ne contacte
pas Habitica. Ne pas utiliser les `config_entry` Home Assistant comme clés.
Vérifier `base_url` dans le TOML, `initial_mode="observe"`, puis
`HABITICA_NETWORK` dans `.env`.
Si Docker fournit un proxy sortant, inclure le nom du backend interne dans
`HPM_NO_PROXY` ; la stack définit les formes majuscule et minuscule de cette
variable. Les requêtes locales entre conteneurs ne doivent pas partir au proxy.

Arborescence :

```text
/srv/habitica-party-manager/
  config/config.toml          # root:10001, 0640
  secrets/admin_token        # secret local d'administration
  secrets/webhook_token      # secret local de rappel
  secrets/kaell_api_key
  secrets/heal_api_key
  secrets/magus_api_key
  secrets/war_api_key
  data/                      # 10001:10001, 0750, SQLite et WAL
```

Les autres fichiers de secrets sont aussi root:10001, 0640 ; les répertoires
root:10001, 0750, sauf `data`. Garder ce GID réservé à l’application et protéger
les accès administrateur Docker. La configuration accepte aussi
`HPM_ADMIN_TOKEN`/`HPM_WEBHOOK_TOKEN` en environnement, mais les fichiers évitent
leur présence dans les variables de stack et les inspections Docker.

## 3. Construire l’image sur Ubuntu

```bash
docker build -t habitica-party-manager:0.1.0 .
docker compose --env-file .env config --quiet
```

Le Dockerfile fixe l’image Python par digest et les dépendances par versions.
Le build nécessite Docker BuildKit et l’accès à Docker Hub/PyPI. Pour une CA
de proxy, passer `--secret id=proxy_ca,src=/chemin/ca-complete.pem` ; ne pas
désactiver TLS. Il n’est pas nécessaire de construire dans Portainer.

## 4. Créer la stack Portainer

1. Choisir l’environnement **Docker Standalone** correspondant à Ubuntu.
2. Stacks → Add stack → nom `habitica-party-manager` → Web editor.
3. Coller le contenu de `compose.yaml`.
4. Renseigner les variables non secrètes de `.env.example` : image locale,
   racine `/srv/habitica-party-manager`, réseau existant, IP et port.
5. Déployer sans demander de pull forcé d’une image locale inexistante en registre.

Les chemins sont absolus côté hôte ; aucun dossier du poste où tourne le navigateur
n’est monté. Le réseau externe doit déjà exister sur ce Docker. Une seule réplique.
La stack ne contient ni Habitica ni MongoDB. Alternative CLI :

```bash
docker compose -p habitica-party-manager --env-file .env up -d
docker compose -p habitica-party-manager --env-file .env ps
```

Si Portainer signale une erreur de permission, vérifier UID/GID de `data` et
les droits de lecture des secrets pour le groupe 10001, sans passer en privilégié.
La santé du conteneur doit devenir `healthy` ; elle ne valide pas encore les
identifiants ou le webhook Habitica.

## 5. Accéder à l’interface et enregistrer le webhook

Par défaut le port écoute uniquement `127.0.0.1:8088`. Depuis votre ordinateur :

```bash
ssh -L 8088:127.0.0.1:8088 UTILISATEUR@192.168.0.2
```

Ouvrir `http://127.0.0.1:8088`. Lire le jeton sur Ubuntu avec
`sudo cat /srv/habitica-party-manager/secrets/admin_token`, le copier dans
l’interface, puis fermer l’affichage. Ne pas le transmettre au cloud.
Pour un accès LAN direct, fixer `HPM_BIND_IP=192.168.0.2` et utiliser un proxy
HTTPS privé/une restriction LAN ; ne pas exposer publiquement ce port sur `kaell.fr`.

Dans la console Portainer du conteneur HPM (utilisateur par défaut 10001) :

```bash
python -m hpm.cli register-webhook --callback-base http://hpm.internal:8080
```

La même commande via CLI, après avoir relevé le nom du conteneur :

```bash
docker exec NOM_CONTENEUR_HPM python -m hpm.cli register-webhook \
  --callback-base http://hpm.internal:8080
```

`hpm.internal` est un alias du conteneur sur le réseau partagé. Il comporte un
suffixe car Habitica peut refuser un nom sans TLD en mode production. Vérifier
sa résolution depuis le backend si les callbacks échouent. C’est un nom Docker
interne, sans publication DNS publique. L’origine de rappel n’est pas celle
de l’interface sur `127.0.0.1`.

La commande ne crée que le webhook de Kaell, avec un UUID déterministe. La relancer
ne crée pas de doublon. Si sa configuration diffère, inspecter la situation puis
utiliser `--update` pour mettre à jour ce seul webhook. La CLI n’affiche pas son
URL secrète. Ne pas enregistrer un second webhook manuellement pour HPM.

## 6. Qualifier puis activer progressivement

1. Garder le mode **Observation**. Vérifier la connexion dans l’interface.
2. Faire une validation manuelle puis une validation Home Assistant sur Kaell.
   Vérifier les événements distincts et zéro opération distribuée.
3. Auditer chaque compagnon ; lire les quotidiennes à risque et habitudes
   existantes. Examiner aussi les automatisations Home Assistant qui toucheraient
   déjà ces comptes. Corriger les conflits vous-même après décision.
4. Cliquer « Préparer l’habitude technique » après examen. Seule l’habitude
   technique manquante sera créée ; aucune tâche existante n’est modifiée.
5. Passer en **Simulation**, reproduire les cas de `RECETTE.md`.
6. Revenir en observation, désactiver Magus et War, puis passer en **Actif**
   pour un essai avec Heal. Contrôler une validation et son résultat.
7. Activer les autres compagnons pour les événements suivants uniquement.

Ne pas valider volontairement une action négative sur vos vrais comptes juste
pour tester le filtrage : ce cas est couvert par le simulateur. Les événements
précédents ne seront jamais rattrapés lors du passage en actif.

## 7. Sauvegarde Kopia et restauration

SQLite est en WAL : ne pas copier seulement `hpm.sqlite3` pendant l’exécution.
Créer une copie cohérente en ligne, par exemple depuis la console du conteneur :

```bash
python -m hpm.cli backup --destination /data/backups/hpm-2026-11-04.sqlite3
```

Choisir un nouveau nom daté à chaque sauvegarde, la CLI refuse d’écraser un fichier.
Elle utilise l’API backup SQLite et `integrity_check`. Inclure dans Kopia :
`data/backups`, `config`, `secrets`, ainsi que le projet/image ou sa référence.
Protéger/chiffrer le dépôt Kopia contenant les clés. Vous pouvez aussi arrêter
HPM puis sauvegarder tout `/srv/habitica-party-manager`, WAL compris.

Restauration : arrêter uniquement HPM dans Portainer ; conserver les fichiers
actuels dans un dossier daté ; restaurer configuration, secrets et snapshot
sous `data/hpm.sqlite3` ; écarter les anciens `hpm.sqlite3-wal` et `-shm` de la
nouvelle base arrêtée ; remettre le propriétaire 10001:10001 sur `data`.
Avant tout redémarrage, forcer l’observation dans la base restaurée :

```bash
sudo python3 - <<'PY'
import sqlite3
path = '/srv/habitica-party-manager/data/hpm.sqlite3'
with sqlite3.connect(path) as db:
    db.execute("UPDATE settings SET value='\"observe\"' WHERE key='mode'")
    db.execute("UPDATE operations SET status='cancelled',error='restore_review' WHERE status IN ('queued','retry')")
    db.execute("UPDATE operations SET status='uncertain',error='restore_review' WHERE status='sending'")
PY
```

Une sauvegarde antérieure peut oublier des récompenses déjà attribuées depuis :
ne pas rejouer ces opérations à l’aveugle. Faire une recette en observation,
vérifier le webhook et réactiver pour les futurs événements uniquement.

## 8. Mise à jour, arrêt et retour arrière

Passer en `off`, vérifier la fin des requêtes en vol, sauvegarder. Construire
une image avec un nouveau tag, tester la démonstration, puis changer `HPM_IMAGE`
dans Portainer. Garder l’ancien tag et le snapshot cohérent correspondant.
Revenir à l’image antérieure uniquement avec un schéma compatible ; sinon
restaurer le snapshot en observation selon la procédure précédente.

Pour suspendre : mode `off`, puis arrêt du seul conteneur HPM. Pour désinstaller,
désactiver le webhook « Habitica Party Manager » dans les paramètres Habitica
de Kaell, retirer cette seule stack et conserver les données jusqu’à décision.
L’habitude technique peut rester inutilisée, sans quotidienne ni pénalité liée
au MVP. Ne pas supprimer l’installation Habitica ou les autres stacks.

Les tâches existantes ne sont pas corrigées automatiquement. HPM ne lance pas
le Cron : vérifier séparément son fonctionnement natif et les quêtes après recette.

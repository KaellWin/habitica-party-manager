# Vérifications effectuées — 4 novembre 2026

## Version 0.2 — difficulté, type et LAN

- **83 tests pytest réussis**, couverture globale **87 %**, Ruff et syntaxe JS validés.
- Les douze couples type/difficulté produisent chacun un score par compagnon,
  sur une habitude positive de même priorité. Difficulté absente/invalide :
  aucun score. Une modification de priorité ne transforme pas un ancien état
  de score en nouvel événement.
- Migration du schéma 0.1 réel conservé dans une fixture : historique et
  empreintes préservés, observation, annulation des anciennes opérations en
  attente, incertitudes conservées, aucun score ni modification de tâche.
  Une interruption de migration annule atomiquement l'intégralité du changement.
- Réponse de création perdue : reprise après nouvel audit par alias, sans
  création en double. Redémarrages et erreurs du MVP restent couverts.
- Démonstration HTTP entre deux conteneurs : **12 couples → 36 scores**,
  sans supplément pour les doublons, le négatif ou la simulation.
- Redémarrage Docker : mode et routage conservés, rejeu reconnu, total inchangé.
- Chromium : authentification, trois banques de douze habitudes, colonnes
  type/difficulté, détails des destinations, changement de mode et audit vérifiés.
- Compose de production exécuté en observation avec un backend synthétique,
  ancien TOML compatible, audit connecté. La configuration Compose rendue publie
  par défaut `192.168.0.2:8088` et conserve l'authentification. Le test cloud
  d'exécution utilise sa propre interface locale ; l'accès depuis un appareil
  de votre LAN reste à vérifier après la mise à jour Portainer.

Image 0.2 construite :
`sha256:56fdf05e730ed1b84d17d5fbca92e4ef2bd5e3e97d189c2075959647b1ad9506`.
Il s'agit d'un build local de validation, pas d'une image publiée en registre.

## Résultats 0.1 conservés pour historique

- **57 tests pytest réussis**, couverture globale **87 %**. Le total comprend
  les branches du simulateur et de la CLI ; ce pourcentage ne prouve pas la
  fidélité d’un serveur Habitica réel.
- `ruff check hpm tests scripts` : succès.
- Image Docker construite avec les dépendances d’exécution verrouillées,
  vérification TLS active et CA du proxy montée uniquement au build.
- Stack `compose.demo.yaml` exécutée : échanges HTTP entre deux conteneurs,
  quatre validations positives → **12 scores** sur les compagnons simulés,
  doublons et négatif → aucun score supplémentaire. Simulation sans score.
- Redémarrage du conteneur HPM : mode `live` et journal conservés ; réémission
  du dernier événement détectée comme doublon ; total conservé à **12**.
- Le fichier de production `compose.yaml` est validé par Docker Compose.
- La stack de production a aussi été exécutée avec un backend simulé : état
  `healthy`, mode observation, audit connecté, UID/GID 10001, racine en lecture
  seule, toutes capacités supprimées, 256 Mio et 64 PID maximum.

Image construite : `sha256:0bc6da2d595f8921727a4d44be5a1c072e768f6b77d4f232615b87b65673b0eb`,
environ 141 Mo décompressés. Ce digest est celui du build de validation,
pas celui d’une image publiée sur un registre.

La suite couvre aussi un puis quatre compagnons, l’épuisement des reprises,
et un changement de cible invalidant la préparation sans distribuer les
anciennes opérations. Les cas de pertes de réponse et interruptions `sending`
ne sont jamais réessayés automatiquement.

Un avertissement de dépréciation provient de l’usage de BlockingPortal par
Starlette TestClient avec AnyIO ; aucun échec associé. Les tests du thread
applicatif et du serveur HTTP sont complétés par les essais Docker.

## À réaliser chez vous

La version 0.1 a été déclarée fonctionnelle sur votre serveur. Pour la 0.2,
vérifier la mise à jour Portainer, l’accès LAN, les couples type/difficulté des
payloads réels (manuels et Home Assistant), les nouvelles habitudes et les gains
natifs. Le Cron/heure de début et les effets sur les quêtes restent hors de cette modification.
Le cloud n’a pas contacté votre instance et n’a attribué aucune récompense réelle.
Suivre `RECETTE.md` et conserver l’observation tant que ces vérifications
ne sont pas terminées.

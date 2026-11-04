# Vérifications effectuées — 4 novembre 2026

## Résultats obtenus dans le cloud

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

Installation effective dans Portainer, DNS et réseau Docker de votre backend,
clés réelles, payloads manuels et Home Assistant, gains XP/or des personnages,
état des tâches existantes, Cron/heure de début et effets sur les quêtes.
Le cloud n’a pas contacté votre instance et n’a attribué aucune récompense réelle.
Suivre `RECETTE.md` et conserver l’observation tant que ces vérifications
ne sont pas terminées.

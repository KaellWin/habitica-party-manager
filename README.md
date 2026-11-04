# Habitica Party Manager — MVP 0.1.0

Une validation positive de **Kaell** déclenche un score `up` de l’habitude
**⚙️ Progression Kaell** sur **Heal**, **Magus** et **War**. Habitudes, quotidiennes
et tâches à faire sont éligibles. Aucun score négatif, achat, élément de checklist
ni pénalité n’est répercuté.

Python 3.12, FastAPI, SQLite, httpx, pytest et Docker Compose. Projet indépendant
sous licence MIT, sans accès MongoDB ni dépendance à Home Assistant. Les quatre
identifiants sont préconfigurés ; aucune vraie clé API n’est incluse.

## Essai entièrement simulé

```bash
docker compose -p hpm-demo -f compose.demo.yaml up -d --build
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/python scripts/check_demo.py
```

Interface : **http://127.0.0.1:8088**. Le jeton public de démonstration se trouve
dans `demo/secrets/admin_token`. Ne jamais l’utiliser en production.

Le scénario HTTP prépare trois comptes fictifs, teste la simulation puis quatre
validations positives, deux doublons et une action négative. Résultat : **12
scores**, aucun sur Kaell. Pour poursuivre les essais :

```bash
curl -X POST http://127.0.0.1:8099/simulate/habit
curl -X POST http://127.0.0.1:8099/simulate/daily
curl -X POST http://127.0.0.1:8099/simulate/todo
curl -X POST http://127.0.0.1:8099/simulate/duplicate
curl -X POST http://127.0.0.1:8099/simulate/negative
curl http://127.0.0.1:8099/simulation-status
docker compose -p hpm-demo -f compose.demo.yaml down
```

Le simulateur garde les comptes en mémoire : après son redémarrage, relancer
`check_demo.py` pour préparer les nouvelles habitudes. Les données HPM restent
dans le volume `hpm-demo_demo-data`. Cette stack ne doit recevoir aucune vraie clé.

## Déploiement et documentation

- [Guide Ubuntu et Portainer](docs/DEPLOIEMENT_PORTAINER.md) : audit local, secrets,
  image, réseau, webhook, observation, sauvegarde Kopia et retour arrière.
- [Audit des API et des solutions existantes](docs/AUDIT_API.md).
- [Architecture et feuille de route](docs/ARCHITECTURE.md).
- [Recette sur votre instance](docs/RECETTE.md).

La stack cible Docker **Standalone**, une seule instance, SQLite sur disque
local et `/srv/habitica-party-manager` pour la persistance. Pas de Swarm ni NFS.
Elle utilise une image construite sur votre serveur, indépendante d’Habitica.

## Modes

| Mode | Journal durable | Récompenses | Rejeu ultérieur |
|---|---|---|---|
| `observe` initial | Oui | Aucune | Jamais |
| `dry-run` | Oui, opérations `simulated` | Aucune | Jamais |
| `live` | Oui, file persistante | `score/up` seulement | Opérations sûres en attente |
| `off` | Oui | Aucune | Jamais |

Le mode persiste en SQLite ; `initial_mode` ne s’applique qu’à une base neuve.
Changer de mode annule les opérations encore en attente. Une requête déjà
partie peut aboutir ; les résultats incertains restent visibles.
La création d’une habitude est une action d’installation explicite après audit,
possible en observation mais interdite en simulation.

L’interface permet de consulter les événements, les résultats par compagnon,
les erreurs, la connexion et l’audit des tâches ; de changer de mode ; d’activer
ou désactiver un compagnon ; de résoudre une opération incertaine.
Les comptes et leurs clés se configurent par fichiers puis redémarrage.

## Garanties et limites

- Empreinte de l’état de score et contraintes uniques SQLite : un événement et
  une opération par compagnon. Compteurs et valeurs distinguent deux scores
  successifs de la même habitude, même dans la même milliseconde.
- Réponse webhook après commit. File, historique et configuration non secrète
  persistants. Verrou de processus et `--workers 1` obligatoires.
- Reprises des connexions échouées avant envoi et des 429 avec temporisation.
  POST 5xx, timeout après envoi ou interruption : `uncertain`, sans reprise
  automatique. **Aucune garantie exactement une fois.**
- Le webhook audité ne fournit ni ID unique, ni signature, ni journal rejouable.
  Une collision d’états identiques ou un événement perdu ne peut pas être
  corrigé avec certitude. Aucun rattrapage massif à l’installation.
- Deux vrais scores Home Assistant sur Kaell restent deux actions distinctes :
  le producteur doit conserver son propre contrôle des paliers déjà récompensés.
- Aucune tâche existante n’est modifiée ou supprimée. Leurs pénalités natives
  peuvent toujours exister ; l’audit préalable les signale. HPM n’appelle pas `/cron`.
- Les fixtures sont synthétiques, fondées sur le code du fork 5.50.6. La
  capture manuelle et Home Assistant sur votre instance reste à réaliser avant
  le mode actif.

## Tests et exécution Python

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/ruff check hpm tests scripts
.venv/bin/pytest --cov=hpm --cov-report=term-missing
```

Sans Docker, adapter les chemins du TOML et définir `HPM_CONFIG`,
`HPM_ADMIN_TOKEN_FILE`, `HPM_WEBHOOK_TOKEN_FILE`, puis :

```bash
.venv/bin/uvicorn hpm.app:app --host 127.0.0.1 --port 8088 --workers 1 --no-access-log
```

API de gestion : `Authorization: Bearer <jeton_admin>`. Le navigateur garde le
jeton en mémoire seulement. `/healthz` vérifie la vie du service et SQLite ;
la connexion Habitica se vérifie séparément. Aucun secret en SQLite.

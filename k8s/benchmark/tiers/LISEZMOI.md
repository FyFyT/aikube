# Banc écrit par un tiers (test indépendant)

Ce dossier reçoit des cas écrits par **une autre personne que l'auteur** (par exemple un camarade).
L'auteur ne doit **pas les lire** avant de lancer la mesure : c'est ce qui rend le test indépendant.

## Ce que le camarade écrit

### 1. Des pannes à diagnostiquer (8 à 12 pannes + 1 ou 2 déploiements sains)

- `cas-tiers.yaml` : les objets Kubernetes, tous dans le namespace `aikube-tiers` (le créer en tête du fichier).
- `cas_tiers.json` : la description de chaque cas (modèle : `cas_tiers.exemple.json`).

Règles :
- **noms neutres** (`app-01`, `app-02`…) : le nom ne doit pas trahir la panne ;
- **aucune annotation, aucun commentaire** qui donne la cause ;
- **une seule cause** par cas, qu'on peut voir en regardant le cluster ;
- varier les pannes, et éviter de recopier celles du banc existant
  (image inexistante, variable manquante, OOM, CPU impossible, nodeSelector, ConfigMap absente,
  sonde sur le mauvais port, sélecteur de Service erroné, StorageClass inexistante).

Pour chaque cas dans `cas_tiers.json` :
- `cible` : ce qu'on demande d'examiner (« le déploiement app-01 », « le service web-03 ») ;
- `cause` : la vraie cause, en une phrase ;
- `mots_cles` : deux groupes de mots ; une bonne réponse contient au moins un mot de chaque groupe ;
- `preuve` : un détail qu'on ne connaît qu'en regardant le cluster (un nom, un port, une valeur) ;
- pour un déploiement sain : `"cause": "aucune panne (témoin)"`, `"mots_cles": null`, `"preuve": null`.

### 2. Des demandes de manifestes (10 à 15)

- `demandes_tiers.json` (modèle : `demandes_tiers.exemple.json`) : des demandes en français,
  les types d'objets attendus, et si la demande est impossible pour la machine (trop de RAM ou de CPU).
- En mettre 3 ou 4 « impossibles ».

## Ce que fait l'auteur (terminal Ubuntu WSL, dans ~/aikube)

```bash
kubectl apply -f k8s/benchmark/tiers/cas-tiers.yaml      # attendre 3 minutes
python3 scripts/campaign_agent.py --bench tiers --conditions agent,chat-claude,chat-claude-obs
python3 scripts/campaign_yaml.py --prompts k8s/benchmark/tiers/demandes_tiers.json \
        --conditions claude,claude-noctx,groq,groq-noctx
python3 scripts/campaign_report.py
```

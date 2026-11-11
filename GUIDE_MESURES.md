# Guide des campagnes de mesure

Toutes les commandes se lancent dans le **terminal Ubuntu (WSL)**, dans `~/aikube`.
Le backend doit tourner (uvicorn, port 8000).
Lance **une seule campagne à la fois**, car la machine est déjà chargée.

Chaque campagne écrit dans un **nouveau** fichier de `data/observations/`. Aucun journal existant n'est modifié.
Si une campagne est interrompue (Ctrl+C, panne de l'apiserver), relance la même commande : ce qui est déjà mesuré est sauté.

## 0. Avant de commencer

```bash
cd ~/aikube
kubectl get nodes                      # les 3 nœuds doivent être Ready
curl -s localhost:8000/api/ai/host-context | head -c 300   # le backend répond
```

## 1. Générateur YAML (C1) : environ 1 h, environ 2 $ d'API

```bash
python3 scripts/campaign_yaml.py --limit 3     # essai : 6 générations
python3 scripts/campaign_yaml.py               # 30 demandes × (avec contexte, sans contexte)
```
Fichier produit : `campaign_yaml.jsonl`. Pour ajouter Groq : `--conditions claude,claude-noctx,groq`.

## 2. Agent de dépannage (C2) : environ 30 min, environ 2 $ d'API

```bash
kubectl apply -f k8s/benchmark/agent-benchmark.yaml
sleep 180                                       # laisser les pannes apparaître
kubectl get pods -n aikube-agent                # on doit voir ImagePullBackOff, CrashLoopBackOff, Pending…
python3 scripts/campaign_agent.py
kubectl delete namespace aikube-agent           # nettoyage
```
Fichiers produits : `campaign_agent.jsonl` et `campaign_agent_review.csv`.
Ouvre le CSV et mets 1, 0.5 ou 0 dans la colonne `note_manuelle` pour vérifier la note automatique.

## 3. Prédiction OOM : environ 1 h 40, gratuit

```bash
python3 scripts/campaign_oom.py --reps 1 --rates 24   # essai : environ 10 min
python3 scripts/campaign_oom.py                       # 3 manches, fuites 6 / 12 / 24 Mio/min + témoin
kubectl delete namespace aikube-oom
```
Fichier produit : `oom_campaign.jsonl`.

## 4. Rapport

```bash
python3 scripts/campaign_report.py
```
Produit `RAPPORT_MESURES_<date>.md`, un nouveau fichier à chaque lancement. Envoie-le moi avec les `.jsonl`.

## En cas de problème

| Message | Que faire |
|---|---|
| `Backend injoignable` | relancer uvicorn |
| `Unable to connect to the server: EOF` | attendre 2 min, `kubectl get nodes`, puis relancer la même commande |
| `⚠ … HTTP 502` sur une ligne | ligne notée en erreur ; elle sera refaite au prochain lancement |

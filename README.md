# AI-Kube

**Ancrer et vérifier : fiabilité d’un assistant d’intelligence artificielle pour l’exploitation de Kubernetes**

Projet de recherche (Master Recherche en informatique).

AI-Kube est une plateforme web et mobile qui aide à exploiter un cluster Kubernetes avec l’IA :
écrire des manifestes, diagnostiquer une panne, prévoir un manque de mémoire, corriger la sécurité et déployer sans casser.

L’idée centrale : **ne pas croire le modèle sur parole**.
Avant de répondre, l’assistant lit l’état réel du système (*ancrer*).
Après, sa réponse est jugée par un arbitre qui n’est pas un modèle de langage (*vérifier*) :
le serveur Kubernetes (dry-run), des pannes à cause connue, l’instant réel d’un arrêt, le retour arrière si l’action casse.

---

## Fonctionnalités

| Page | Ce qu’elle fait |
|---|---|
| Tableau de bord | État des nœuds, des pods, de la charge et de la mémoire de la machine |
| Génération YAML | Manifeste écrit à partir d’une demande en français, vérifié par le serveur, réparé si besoin |
| Agent de dépannage | Enquête sur le cluster (lecture seule), cite ses preuves, propose une correction |
| Détection | Repère les pods vraiment instables et compare quatre détecteurs |
| Prédiction mémoire | Annonce le temps restant avant l’arrêt d’un conteneur (OOM) |
| Sécurité | Défauts reliés à MITRE ATT&CK, correctifs vérifiés avec retour arrière |
| Déploiement sûr | Aperçu, surveillance des nouveaux pods, retour arrière automatique |
| Mes applications | Importer, modifier, construire et déployer son propre code |
| Mobile | Application Expo + alertes sur le téléphone par ntfy |

Trois modèles sont comparés dans les mêmes conditions :
Claude (API Anthropic), gpt-oss-120b (poids ouverts, via Groq ou Cerebras)
et **KubeLLM**, un petit modèle local spécialisé (Qwen2.5-Coder-1.5B + QLoRA, servi par Ollama).

---

## Organisation du dépôt

```
backend/     API FastAPI (routeurs dans backend/api, logique dans backend/ai_module)
frontend/    Interface web React + Vite
mobile/      Application mobile Expo (voir mobile/README_MOBILE.md)
scripts/     Campagnes de mesure, rapports, outils (scripts/windows : connexion du mobile)
notebooks/   Entraînement de KubeLLM v2, v3 et v4 sur Google Colab
k8s/         Manifestes de test (bancs d’essai)
tests/       Tests unitaires, d’intégration et de bout en bout
data/        Résultats des campagnes de mesure
```

---

## Installation (PC Windows avec WSL2 Ubuntu)

Toutes les commandes ci-dessous se lancent dans le **terminal Ubuntu (WSL)**.

**Prérequis** : Python 3.10 ou plus, Node.js 20, Docker, minikube, kubectl, et Ollama (pour KubeLLM).

### 1. Récupérer le code

```bash
git clone https://github.com/FyFyT/aikube.git ~/aikube
cd ~/aikube
```

### 2. Créer le fichier `.env`

```bash
cp .env.example .env
nano .env
```

Remplir au moins une clé de modèle (`ANTHROPIC_API_KEY`, `GROQ_API_KEY` ou `CEREBRAS_API_KEY`).
**Ne jamais publier le fichier `.env`** (il est déjà exclu par `.gitignore`).

### 3. Démarrer le cluster (3 nœuds)

```bash
minikube start --nodes 3 --cpus 2 --memory 3000
minikube addons enable metrics-server
```

### 4. Lancer le backend

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd backend && python main.py
```

API : http://localhost:8000 — documentation : http://localhost:8000/docs

### 5. Lancer l’interface web (dans un deuxième terminal Ubuntu)

```bash
cd ~/aikube/frontend
npm install
npm run dev
```

Ouvrir http://localhost:5173 dans le navigateur Windows.

### 6. KubeLLM en local (facultatif)

Les poids de KubeLLM (fichier `.gguf`, environ 1 Go) ne sont pas dans ce dépôt.
Les notebooks de `notebooks/` permettent de les reproduire sur Google Colab (GPU T4 gratuit).
Ensuite :

```bash
ollama create kubellm-v2 -f Modelfile
```

Les noms attendus sont réglables dans `.env` (`KUBELLM_MODEL`, `KUBELLM_V3_MODEL`, `KUBELLM_V4_MODEL`).

### 7. Application mobile (facultatif)

Voir `mobile/README_MOBILE.md`. L’accès distant est protégé par `MOBILE_TOKEN`.

---

## Principaux résultats

| Mesure | Résultat |
|---|---|
| Demandes de capacité réussies par Claude, sans puis avec le contexte de la machine | 6/30 → 30/30 |
| Manifestes valides du petit modèle, avant puis après fine-tuning | 39 % → 75 % |
| Pannes dont l’agent trouve la cause exacte (même modèle sans outils : 0) | 16 / 18 |
| Préavis avant un arrêt mémoire (fuite rapide), contre 1,0 min pour l’alerte à 75 % | 4,6 min |
| Corrections de sécurité sans régression (les 4 régressions réelles annulées) | 12 / 13 |

Conclusion : la fiabilité tient d’abord à l’architecture qui entoure le modèle, et seulement ensuite au modèle.

**Limites** : un seul cluster local, des bancs conçus par l’auteur, des arbitres qui jugent la forme et la santé des pods mais pas l’intention de l’utilisateur.

---

## Sécurité

- Les clés d’API restent dans `.env`, jamais dans le code.
- L’agent n’a que des outils en lecture seule, sans shell, et ne lit jamais les Secrets.
- Toute action passe par un dry-run, une surveillance et un retour arrière automatique.
- Si l’application est mise en ligne, la protéger par un mot de passe : elle peut modifier le cluster et consommer des crédits d’API.

---

## Corpus d’entraînement

Le corpus de KubeLLM est écrit par **gpt-oss-120b** (poids ouverts), puis filtré par les arbitres :
seuls les exemples validés par le serveur Kubernetes y entrent.
Aucune sortie de Claude n’est utilisée pour l’entraînement.

# AI-Kube mobile (Expo / React Native)

Ce dossier contient le code de l'application (App.js et src/).
Le projet Expo lui-même se crée sur ta machine : il prend ainsi la version d'Expo compatible avec l'application Expo Go de ton téléphone.

Écrans :
- **Accueil** : état du cluster et de la machine hôte, alertes (OOM annoncés, pods en panne, ressources, failles, panne de metrics-server, déploiements en échec). Notification sur le téléphone pour chaque nouvelle alerte critique.
- **Agent** : l'agent de dépannage enquête sur le cluster, avec la liste de ses vérifications.
- **Actions** : versions des workloads, retour arrière vérifié, redémarrage. Toujours avec confirmation.
- **YAML** : génération vérifiée, en consultation seulement. Le déploiement se fait depuis le poste.
- **Réglages** : adresse du backend et jeton d'accès.

Sécurité : le backend refuse toute requête distante sans le jeton `MOBILE_TOKEN` du fichier `.env`.

Notifications : Expo Go ne peut plus afficher de notifications sur Android. Le backend les envoie donc par **ntfy**, application libre (Play Store / App Store) :
1. dans `~/aikube/.env`, ajoute `NTFY_TOPIC=aikube-<un-mot-secret-long>` puis redémarre le backend ;
2. sur le téléphone, installe ntfy et abonne-toi à ce même nom de canal ;
3. test : `python3 scripts/ntfy_test.py`.
Les alertes importantes (critiques, crashs mémoire annoncés, déploiements en échec, panne des mesures) arrivent alors même téléphone verrouillé. Leur titre et leur détail passent par le serveur ntfy (ntfy.sh par défaut) : noms de pods et de namespaces, jamais de secret.

#!/usr/bin/env bash
# =============================================================================
# AI-Kube : lancement d'Expo pour l'application mobile (terminal Ubuntu WSL)
# A lancer APRES connexion-mobile.bat sous Windows, qui a ecrit l'adresse
# Windows du PC dans C:\Users\<toi>\aikube-mobile-ip.txt.
#   bash ~/aikube/scripts/mobile-demarrer.sh
# =============================================================================
F=$(ls /mnt/c/Users/*/aikube-mobile-ip.txt 2>/dev/null | head -1)
if [ -z "$F" ]; then
  echo "Adresse introuvable : lance d'abord connexion-mobile.bat sous Windows (double-clic)."
  exit 1
fi
IP=$(tr -d '\r\n ' < "$F")

# node installé par nvm : un script ne lit pas ~/.bashrc, on charge nvm ici
[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh"

if curl -s -m 3 http://localhost:8000/health >/dev/null; then
  echo "[OK] Backend lancé."
else
  echo "[!] Le backend ne répond pas. Dans un autre terminal Ubuntu :"
  echo "    cd ~/aikube && source venv/bin/activate && cd backend && uvicorn main:app --host 0.0.0.0 --port 8000"
fi

cd "$HOME/aikube-mobile" || { echo "Dossier ~/aikube-mobile introuvable."; exit 1; }
echo
echo "Dans l'application, onglet Réglages, adresse du serveur : http://$IP:8000"
echo "Scanne le QR code ci-dessous avec Expo Go (téléphone sur le MÊME réseau que le PC)."
echo
REACT_NATIVE_PACKAGER_HOSTNAME="$IP" exec npx expo start

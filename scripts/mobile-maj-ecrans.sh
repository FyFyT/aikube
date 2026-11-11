#!/usr/bin/env bash
# Met à jour deux écrans de l'application mobile, dans les DEUX copies du code
# (~/aikube/mobile et le projet Expo ~/aikube-mobile), sans rien écraser d'autre :
#   · YAML : KubeLLM v2 (supprimé d'Ollama) remplacé par KubeLLM v4 ;
#   · Réglages : le message sur les notifications indique l'application ntfy.
# Sans effet si c'est déjà fait. Usage (terminal Ubuntu WSL) : bash ~/aikube/scripts/mobile-maj-ecrans.sh
for d in "$HOME/aikube/mobile/src/screens" "$HOME/aikube-mobile/src/screens"; do
  [ -d "$d" ] || { echo "absent : $d"; continue; }
  sed -i "s/\['kubellm', 'KubeLLM v2'\], \['kubellm-v3', 'KubeLLM v3'\]\]/['kubellm-v3', 'KubeLLM v3'], ['kubellm-v4', 'KubeLLM v4']]/" "$d/Yaml.js"
  sed -i "s/Notifications indisponibles dans Expo Go : les alertes restent visibles dans l'accueil (badge rouge)\./Expo Go ne peut pas afficher de notifications : elles arrivent par l'application ntfy (abonne-toi au canal NTFY_TOPIC du fichier .env). Les alertes restent aussi visibles dans l'accueil./" "$d/Settings.js"
  printf '%s\n  YAML      : %s\n  Réglages  : %s\n' "$d" \
    "$(grep -q "KubeLLM v4" "$d/Yaml.js" && echo 'OK (v3, v4)' || echo 'NON MODIFIÉ, à vérifier')" \
    "$(grep -q "application ntfy" "$d/Settings.js" && echo 'OK (ntfy)' || echo 'NON MODIFIÉ, à vérifier')"
done

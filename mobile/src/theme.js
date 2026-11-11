// Couleurs partagées — mêmes teintes que l'application web.
// Deux palettes (clair, sombre). `C` est un objet unique que applyTheme() remplit :
// les écrans lisent C.xxx au moment de l'affichage, donc un changement de thème
// suivi d'un nouvel affichage suffit, sans redémarrer l'application.
// Les noms historiques sont gardés : `purple` désigne la couleur d'accent (bleu) ;
// vert, ocre et rouge sont réservés aux états (sain, attention, critique).
import { Appearance } from 'react-native'

const LIGHT = {
  bg: '#F6F7F8', card: '#FFFFFF', elevated: '#EEF1F4', border: '#D8DDE3',
  text: '#17212B', muted: '#5F6B78', purple: '#1F5FA6', purpleLight: '#1F5FA6',
  green: '#2E7D4F', red: '#B42318', amber: '#9A6312', teal: '#2F7F7A',
  onAccent: '#FFFFFF', codeBg: '#F1F3F5', overlay: 'rgba(17,24,32,0.45)',
  deep: '#15202B', deepText: '#E4E8EC', deepMuted: '#9AA5B1', deepAccent: '#72A9E6',
}

const DARK = {
  bg: '#12161B', card: '#181E25', elevated: '#1F262E', border: '#2A323C',
  text: '#E4E8EC', muted: '#9AA5B1', purple: '#72A9E6', purpleLight: '#72A9E6',
  green: '#5CB585', red: '#F07167', amber: '#D9A548', teal: '#5FB3AC',
  onAccent: '#0F1A24', codeBg: '#141A20', overlay: 'rgba(0,0,0,0.6)',
  deep: '#0C1015', deepText: '#E4E8EC', deepMuted: '#9AA5B1', deepAccent: '#72A9E6',
}

export const C = { ...LIGHT }

// choix : 'system' (suit le téléphone), 'light' ou 'dark'. Renvoie le thème effectif.
export function applyTheme(choice = 'system') {
  const sys = Appearance.getColorScheme?.() === 'dark' ? 'dark' : 'light'
  const name = choice === 'system' ? sys : choice
  Object.assign(C, name === 'dark' ? DARK : LIGHT)
  C.name = name
  return name
}

// Couleur par gravité, lue au moment de l'affichage
export const SEV = {
  get critical() { return C.red }, get warning() { return C.amber },
  get info() { return C.purple }, get ok() { return C.green },
}
export const mono = { fontFamily: 'monospace' }

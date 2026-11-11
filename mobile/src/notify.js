// Notifications LOCALES : l'application interroge le backend toutes les minutes tant
// qu'elle est ouverte (ou récemment mise en arrière-plan) et notifie chaque NOUVELLE
// alerte critique. Une vraie notification « push » serveur demanderait un service
// externe (Firebase) : hors périmètre, documenté comme limite.
//
// Chargement PARESSEUX et protégé : dans Expo Go (Android, SDK récents) le module
// natif de expo-notifications est incomplet (« ExpoTopicSubscriptionModule »
// introuvable) et un import direct fait planter toute l'application au démarrage.
// Ici, si le module manque, les notifications sont simplement désactivées : les
// alertes restent visibles dans l'accueil (badge rouge sur l'onglet).
//
// Attention : un try/catch autour de require() NE SUFFIT PAS — en mode développement,
// Metro signale l'erreur de chargement comme fatale avant de la relancer. Il faut donc
// vérifier AVANT de charger : on ne charge rien dans Expo Go, ni si le module natif
// Android manque.
import Constants from 'expo-constants'
import { requireOptionalNativeModule } from 'expo-modules-core'
import { Platform } from 'react-native'
import { loadSeen, saveSeen } from './storage'

let N = null
let tried = false
function nativeReady() {
  if (Platform.OS === 'web') return false
  if (Constants.executionEnvironment === 'storeClient') return false // Expo Go
  if (Platform.OS === 'android' && !requireOptionalNativeModule('ExpoTopicSubscriptionModule')) return false
  return true
}
function mod() {
  if (tried) return N
  tried = true
  if (!nativeReady()) return null
  try {
    N = require('expo-notifications')
    N.setNotificationHandler({
      handleNotification: async () => ({
        shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false,
      }),
    })
  } catch { N = null }
  return N
}

export const notificationsAvailable = () => !!mod()

export async function askPermission() {
  const n = mod(); if (!n) return false
  try {
    const cur = await n.getPermissionsAsync()
    if (cur.granted) return true
    const r = await n.requestPermissionsAsync()
    return r.granted
  } catch { return false }
}

export async function notifyNew(alerts) {
  const seen = await loadSeen()
  const fresh = alerts.filter(a => a.severity === 'critical' && !seen.has(a.id))
  const n = mod()
  if (n) {
    for (const a of fresh.slice(0, 3)) {
      try {
        await n.scheduleNotificationAsync({
          content: { title: `AI-Kube · ${a.title}`, body: a.detail?.slice(0, 180) || '' }, trigger: null,
        })
      } catch { /* notifications indisponibles : l'alerte reste visible dans l'accueil */ }
    }
  }
  alerts.forEach(a => seen.add(a.id))
  await saveSeen(seen)
  return fresh.length
}

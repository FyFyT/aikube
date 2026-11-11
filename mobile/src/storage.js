// Adresse du backend et jeton, gardés dans le stockage chiffré du téléphone.
import * as SecureStore from 'expo-secure-store'

const KEYS = { url: 'aikube_url', token: 'aikube_token', notify: 'aikube_notify', seen: 'aikube_seen', theme: 'aikube_theme' }

// Repli en mémoire si le stockage chiffré n'est pas disponible (navigateur, émulateur incomplet)
const MEM = {}
const get = async (k) => { try { return await SecureStore.getItemAsync(k) } catch { return MEM[k] ?? null } }
const set = async (k, v) => { try { await SecureStore.setItemAsync(k, v) } catch { MEM[k] = v } }

export async function loadSettings() {
  const [url, token, notify, theme] = await Promise.all([
    get(KEYS.url), get(KEYS.token), get(KEYS.notify), get(KEYS.theme)])
  return { url: url || '', token: token || '', notify: notify !== 'off', theme: theme || 'system' }
}

export async function saveSettings({ url, token, notify }) {
  await set(KEYS.url, (url || '').trim().replace(/\/+$/, ''))
  await set(KEYS.token, (token || '').trim())
  await set(KEYS.notify, notify ? 'on' : 'off')
}

// Thème choisi : 'system', 'light' ou 'dark' (enregistré dès le choix)
export async function saveTheme(theme) {
  await set(KEYS.theme, theme)
}

export async function loadSeen() {
  try { return new Set(JSON.parse((await get(KEYS.seen)) || '[]')) } catch { return new Set() }
}

export async function saveSeen(set) {
  await set(KEYS.seen, JSON.stringify([...set].slice(-200)))
}

// Accès au backend AI-Kube. Chaque requête porte le jeton (Authorization: Bearer …) :
// le backend refuse tout accès distant sans lui.
let CONFIG = { url: '', token: '' }
export const setApiConfig = (c) => { CONFIG = { ...CONFIG, ...c } }
export const apiConfigured = () => Boolean(CONFIG.url && CONFIG.token)

export async function api(path, { method = 'GET', body, timeout = 30000 } = {}) {
  if (!CONFIG.url) throw new Error('Serveur non configuré (onglet Réglages)')
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeout)
  try {
    const res = await fetch(`${CONFIG.url}/api${path}`, {
      method, signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${CONFIG.token}` },
      body: body ? JSON.stringify(body) : undefined,
    })
    const text = await res.text()
    let data
    try { data = text ? JSON.parse(text) : {} } catch { data = { detail: text } }
    if (!res.ok) {
      const d = data?.detail
      throw new Error(typeof d === 'string' ? d : d?.error || JSON.stringify(d || data).slice(0, 300) || `Erreur ${res.status}`)
    }
    return data
  } catch (e) {
    if (e.name === 'AbortError') throw new Error(`Pas de réponse après ${Math.round(timeout / 1000)} s`)
    if (String(e.message).includes('Network request failed'))
      throw new Error('Serveur injoignable : même Wi-Fi ? backend lancé avec --host 0.0.0.0 ? redirection du port 8000 ?')
    throw e
  } finally { clearTimeout(timer) }
}

// Suit une opération longue (retour arrière…) jusqu'à son verdict.
export async function waitJob(id, onTick, maxMs = 600000) {
  const t0 = Date.now()
  while (Date.now() - t0 < maxMs) {
    const j = await api(`/devops/v2/jobs/${id}`)
    onTick?.(j)
    if (j.state === 'done') return j
    await new Promise(r => setTimeout(r, 2000))
  }
  throw new Error('Opération toujours en cours : vérifier depuis l\'application web')
}

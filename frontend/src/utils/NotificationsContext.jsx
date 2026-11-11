// src/utils/NotificationsContext.jsx
// -----------------------------------------------------------------------------
// Source de vérité UNIQUE pour toutes les alertes de l'application.
//
// Pourquoi un contexte ? Le badge 🔔 (NotificationCenter) et le badge de la
// navigation "Détection IA" (Layout) doivent partager le même état lu/non-lu.
// Avec deux useState séparés, marquer une alerte comme lue d'un côté n'a aucun
// effet de l'autre — c'est exactement le bug du chiffre rouge persistant.
//
// Ce provider expose :
//   · allNotifs      : alertes normalisées et dédupliquées (detect + devops)
//   · unreadCount    : total non lu           -> badge 🔔
//   · unreadCritical : critiques non lues     -> badge "Détection IA"
//   · isUnread(uid)  : état d'une alerte      -> affichage de la liste
//   · markRead / markAllRead / markAllUnread
//   · panelOpen / setPanelOpen
//
// Règles de lecture automatique :
//   · ouvrir le panel 🔔        -> marque TOUT comme lu
//   · visiter /detection        -> marque les ANOMALIES comme lues
// -----------------------------------------------------------------------------

import { createContext, useContext, useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import api, { aiAPI } from './api'

const SEEN_KEY = 'aikube:notifications:seen'

// ── Persistance localStorage ─────────────────────────────────────────────────
const loadSeen = () => {
  try {
    const raw = window.localStorage.getItem(SEEN_KEY)
    return new Set(raw ? JSON.parse(raw) : [])
  } catch { return new Set() }
}
const persistSeen = (set) => {
  try { window.localStorage.setItem(SEEN_KEY, JSON.stringify([...set])) } catch { /* quota / navigation privée */ }
}

const NotificationsContext = createContext(null)

export function useNotifications() {
  const ctx = useContext(NotificationsContext)
  if (!ctx) throw new Error('useNotifications() doit être appelé dans <NotificationsProvider>')
  return ctx
}

// ── Helpers de normalisation ─────────────────────────────────────────────────
const makeUid = (source, ns, res, id) => `${source}:${ns || '-'}/${res || '-'}:${id || ''}`

const parseTarget = (n) => {
  if (n.resource) return { resource: n.resource, namespace: n.namespace || null }
  const m = /^([a-zA-Z0-9][a-zA-Z0-9._-]*)\s*(?:\(([^)]+)\))?/.exec(n.message || '')
  return { resource: m?.[1] || null, namespace: m?.[2] || n.namespace || null }
}

// Pods d'un même Deployment : « web-7c9f6d8b5-2xk4p » -> « web ». Sert à ne pas
// annoncer « résolu » quand un pod en panne est simplement remplacé par un autre
// pod du même Deployment qui a le même problème.
const ownerKey = (n) => `${n.namespace || '-'}|${n.type || '-'}|${(n.resource || '').replace(/-[a-z0-9]{6,10}-[a-z0-9]{5}$/, '').replace(/-[a-z0-9]{5}$/, '')}`

const RESOLVED_TTL_MS = 15 * 60 * 1000   // une alerte résolue reste visible 15 min

const podRoute = (resource, namespace) => {
  const p = new URLSearchParams()
  if (namespace) p.set('namespace', namespace)
  if (resource)  p.set('search', resource)
  const qs = p.toString()
  return qs ? `/pods?${qs}` : '/pods'
}

export function NotificationsProvider({ children }) {
  const [seen, setSeen]           = useState(loadSeen)
  const [highlight, setHighlight] = useState(() => new Set())
  const [panelOpen, setPanelOpen] = useState(false)
  const [resolved, setResolved]   = useState([])   // alertes disparues récemment
  const { pathname } = useLocation()

  // ── Données ────────────────────────────────────────────────────────────────
  const { data: detect } = useQuery({
    queryKey: ['detect'],
    queryFn: () => aiAPI.detect().then(r => r.data),
    refetchInterval: 30000,
  })

  const { data: notifs } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => api.get('/devops/notifications').then(r => r.data),
    refetchInterval: 20000,
  })

  // ── Normalisation + déduplication ──────────────────────────────────────────
  const activeNotifs = useMemo(() => {
    const anomalies = (detect?.problems || []).map(p => ({
      uid:       makeUid('anomaly', p.namespace, p.resource, p.id),
      id:        p.id,
      source:    'anomaly',
      type:      'anomaly',
      severity:  p.severity,
      title:     p.title,
      message:   p.description?.substring(0, 60),
      time:      'Maintenant',
      resource:  p.resource,
      namespace: p.namespace,
      to:        `/detection?focus=${encodeURIComponent(p.id || '')}`,
      podTo:     podRoute(p.resource, p.namespace),
      fix:       "Demander à l'agent",
      fixTo:     `/assistant?q=${encodeURIComponent(`Le pod ${p.namespace}/${p.resource} est signalé (${p.type} : ${(p.reasons || []).join(' ; ') || p.description}). Quelle est la cause, et comment le corriger ?`)}`,
    }))

    // Un pod déjà signalé par la détection IA n'est pas redondé par /devops.
    const covered = new Set(anomalies.map(a => `${a.namespace}/${a.resource}`))

    const devops = (notifs?.notifications || [])
      .map(n => {
        const { resource, namespace } = parseTarget(n)
        const isOom = n.type === 'oom'
        return {
          uid:       makeUid('devops', namespace, resource, n.id),
          id:        n.id,
          source:    'devops',
          type:      n.type,
          severity:  n.severity,
          title:     n.title,
          message:   n.message,
          time:      isOom ? 'Prévision' : 'Récent',
          resource,
          namespace,
          to:        isOom ? '/prediction' : podRoute(resource, namespace),
          podTo:     podRoute(resource, namespace),
          fix:       isOom ? 'Voir la prédiction' : null,
          fixTo:     isOom ? '/prediction' : undefined,
        }
      })
      // Une prévision mémoire n'est jamais redondante avec une panne constatée
      .filter(n => n.type === 'oom' || !covered.has(`${n.namespace}/${n.resource}`))

    return [...anomalies, ...devops]
  }, [detect, notifs])

  // ── Alertes résolues ───────────────────────────────────────────────────────
  // Une alerte présente au relevé précédent et absente du relevé courant est
  // annoncée comme résolue, sauf si le même problème touche toujours le même
  // Deployment (pod remplacé). Le premier relevé ne produit rien.
  const prevActiveRef = useRef(null)
  useEffect(() => {
    if (!detect && !notifs) return
    const prev = prevActiveRef.current
    prevActiveRef.current = activeNotifs
    if (!prev) return
    const nowUids   = new Set(activeNotifs.map(n => n.uid))
    const nowOwners = new Set(activeNotifs.map(ownerKey))
    const gone = prev.filter(n => !nowUids.has(n.uid) && !nowOwners.has(ownerKey(n)))
    if (gone.length === 0) return
    const at = new Date()
    const hhmm = at.toLocaleTimeString('fr', { hour: '2-digit', minute: '2-digit' })
    setResolved(r => [
      ...gone.map(n => ({
        ...n,
        uid:      `resolved:${n.uid}:${at.getTime()}`,
        source:   'resolved',
        type:     'resolved',
        severity: 'resolved',
        title:    `Résolu : ${n.resource || n.title}`,
        message:  n.type === 'oom'
          ? "La prévision de crash mémoire a disparu (la mémoire ne monte plus, ou le pod a redémarré)."
          : `${n.title} n'est plus détecté (pod rétabli ou supprimé).`,
        time:     hhmm,
        fix:      null,
        expires:  at.getTime() + RESOLVED_TTL_MS,
      })),
      ...r,
    ].slice(0, 20))
  }, [activeNotifs, detect, notifs])

  // Retrait des annonces « résolu » après 15 minutes
  useEffect(() => {
    const t = setInterval(() => setResolved(r => {
      const kept = r.filter(n => n.expires > Date.now())
      return kept.length === r.length ? r : kept
    }), 30000)
    return () => clearInterval(t)
  }, [])

  const allNotifs = useMemo(() => [...activeNotifs, ...resolved], [activeNotifs, resolved])

  // Clé stable : évite les boucles d'effets dues à l'identité du tableau
  const uidsKey = useMemo(() => allNotifs.map(n => n.uid).join('|'), [allNotifs])
  const notifsRef = useRef([])
  notifsRef.current = allNotifs

  // ── Persistance ────────────────────────────────────────────────────────────
  useEffect(() => { persistSeen(seen) }, [seen])

  // ── Actions ────────────────────────────────────────────────────────────────
  const markRead = useCallback((uids) => {
    if (!uids || uids.length === 0) return
    setSeen(prev => {
      const fresh = uids.filter(u => !prev.has(u))
      if (fresh.length === 0) return prev          // évite un re-render inutile
      const next = new Set(prev)
      fresh.forEach(f => next.add(f))
      return next
    })
  }, [])

  const markAllRead = useCallback(() => {
    markRead(notifsRef.current.map(n => n.uid))
    setHighlight(new Set())
  }, [markRead])

  const markAllUnread = useCallback(() => {
    const current = new Set(notifsRef.current.map(n => n.uid))
    setSeen(prev => new Set([...prev].filter(u => !current.has(u))))
    setHighlight(new Set())
  }, [])

  // ── Lecture auto : ouverture du panel 🔔 ───────────────────────────────────
  useEffect(() => {
    if (!panelOpen) return
    const fresh = notifsRef.current.map(n => n.uid).filter(u => !seen.has(u))
    if (fresh.length === 0) return
    // surlignage visuel conservé tant que le panel reste ouvert
    setHighlight(h => { const next = new Set(h); fresh.forEach(f => next.add(f)); return next })
    markRead(fresh)
  }, [panelOpen, uidsKey, seen, markRead])

  useEffect(() => { if (!panelOpen) setHighlight(new Set()) }, [panelOpen])

  // ── Lecture auto : visite de la page Détection IA ──────────────────────────
  // C'est ce qui fait disparaître le chiffre rouge de la navigation.
  useEffect(() => {
    if (pathname !== '/detection') return
    markRead(notifsRef.current.filter(n => n.source === 'anomaly').map(n => n.uid))
  }, [pathname, uidsKey, markRead])

  // ── Purge des alertes résolues ─────────────────────────────────────────────
  // Sans ça, un pod réparé puis re-cassé resterait "lu" à tort et le
  // localStorage grossirait indéfiniment.
  useEffect(() => {
    if (panelOpen) return
    if (!detect && !notifs) return               // données pas encore chargées
    const current = new Set(notifsRef.current.map(n => n.uid))
    setSeen(prev => {
      const kept = [...prev].filter(u => current.has(u))
      if (kept.length === prev.size) return prev
      return new Set(kept)
    })
  }, [panelOpen, uidsKey, detect, notifs])

  // ── Valeurs dérivées ───────────────────────────────────────────────────────
  const value = useMemo(() => {
    const unread         = allNotifs.filter(n => !seen.has(n.uid))
    const unreadCritical = allNotifs.filter(n => !seen.has(n.uid) && n.severity === 'critical').length
    return {
      allNotifs,
      total:          activeNotifs.length,
      critical:       allNotifs.filter(n => n.severity === 'critical').length,
      unreadCount:    unread.length,
      unreadCritical,
      isUnread:       (uid) => highlight.has(uid) || !seen.has(uid),
      hasUnreadUi:    unread.length > 0 || highlight.size > 0,
      markRead, markAllRead, markAllUnread,
      panelOpen, setPanelOpen,
    }
  }, [allNotifs, activeNotifs, seen, highlight, panelOpen, markRead, markAllRead, markAllUnread])

  return (
    <NotificationsContext.Provider value={value}>
      {children}
    </NotificationsContext.Provider>
  )
}

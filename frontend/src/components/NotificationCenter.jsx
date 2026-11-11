// src/components/NotificationCenter.jsx
// -----------------------------------------------------------------------------
// Cloche + panel de notifications.
//
// Toute la logique métier (fetch, déduplication, état lu/non-lu, persistance)
// vit désormais dans utils/NotificationsContext.jsx — ce composant ne fait plus
// que de l'affichage et du positionnement. C'est ce qui permet au badge de la
// navigation "Détection IA" de partager exactement le même état.
// -----------------------------------------------------------------------------

import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { useNotifications } from '../utils/NotificationsContext'
import { TrendingUp, Bell, X, AlertCircle, Info, CheckCircle2 } from 'lucide-react'

const PANEL_WIDTH = 340

export default function NotificationCenter() {
  const {
    allNotifs, total, critical, unreadCount,
    isUnread, hasUnreadUi, markAllRead, markAllUnread,
    panelOpen, setPanelOpen,
  } = useNotifications()

  const [coords, setCoords] = useState({ top: 0, left: 0 })
  const btnRef   = useRef(null)
  const panelRef = useRef(null)
  const navigate = useNavigate()

  // ── Positionnement du panel (portal => position fixed) ─────────────────────
  const updateCoords = useCallback(() => {
    if (!btnRef.current) return
    const r = btnRef.current.getBoundingClientRect()
    let left = r.right - PANEL_WIDTH
    if (left < 12) left = r.left
    if (left + PANEL_WIDTH > window.innerWidth - 12) {
      left = window.innerWidth - PANEL_WIDTH - 12
    }
    setCoords({ top: r.bottom + 8, left: Math.max(12, left) })
  }, [])

  useEffect(() => {
    if (!panelOpen) return
    updateCoords()
    window.addEventListener('resize', updateCoords)
    window.addEventListener('scroll', updateCoords, true)
    return () => {
      window.removeEventListener('resize', updateCoords)
      window.removeEventListener('scroll', updateCoords, true)
    }
  }, [panelOpen, updateCoords])

  // ── Fermeture : clic extérieur + Échap ─────────────────────────────────────
  useEffect(() => {
    if (!panelOpen) return
    const onMouseDown = (e) => {
      const inBtn   = btnRef.current   && btnRef.current.contains(e.target)
      const inPanel = panelRef.current && panelRef.current.contains(e.target)
      if (!inBtn && !inPanel) setPanelOpen(false)
    }
    const onKey = (e) => { if (e.key === 'Escape') setPanelOpen(false) }
    document.addEventListener('mousedown', onMouseDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [panelOpen, setPanelOpen])

  const openNotif = (to) => {
    setPanelOpen(false)
    navigate(to)
  }

  // ── Couleurs ───────────────────────────────────────────────────────────────
  const severityColor = (s) =>
    s === 'critical' ? 'var(--color-red)' : s === 'warning' ? 'var(--color-amber)' : s === 'resolved' ? 'var(--ok)' : 'var(--color-blue)'
  const severityBg = (s) =>
    s === 'critical' ? 'var(--color-danger-bg)' : s === 'warning' ? 'var(--color-warning-bg)' : s === 'resolved' ? 'var(--color-success-bg)' : 'var(--color-info-bg)'
  const severityBorder = (s) =>
    s === 'critical' ? 'var(--color-danger-border)' : s === 'warning' ? 'var(--color-warning-border)' : s === 'resolved' ? 'var(--color-success-border)' : 'var(--color-info-border)'

  // ── Panel ──────────────────────────────────────────────────────────────────
  const panel = (
    <div
      ref={panelRef}
      className="notif-panel"
      style={{ position: 'fixed', top: coords.top, left: coords.left, right: 'auto', width: PANEL_WIDTH }}
    >
      {/* Header */}
      <div className="notif-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
            Notifications
          </span>
          {critical > 0 && (
            <span className="badge badge-danger">
              {critical} critique{critical > 1 ? 's' : ''}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            onClick={hasUnreadUi ? markAllRead : markAllUnread}
            title={hasUnreadUi ? 'Tout marquer comme lu' : 'Tout marquer comme non lu'}
            style={{
              background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit',
              fontSize: 11.5, color: 'var(--color-purple-light)', padding: 0,
            }}
          >
            {hasUnreadUi ? 'Tout lire' : 'Non lu'}
          </button>
          <button
            onClick={() => setPanelOpen(false)}
            style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex' }}
            aria-label="Fermer les notifications"
          ><X size={15} /></button>
        </div>
      </div>

      {/* Liste */}
      <div style={{ maxHeight: 400, overflowY: 'auto' }}>
        {allNotifs.length === 0 ? (
          <div style={{ padding: '32px 16px', textAlign: 'center', color: 'var(--text-muted)' }}>
            <div style={{ fontSize: 14 }}>Aucune alerte active.</div>
          </div>
        ) : allNotifs.map((notif, i) => {
          const unread = isUnread(notif.uid)
          return (
            <div
              key={notif.uid}
              className="notif-item"
              style={{ opacity: unread ? 1 : 0.65 }}
              role="button"
              tabIndex={0}
              onClick={() => openNotif(notif.to)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openNotif(notif.to) } }}
            >
              {/* Icône severity */}
              <div style={{ flexShrink: 0, marginTop: 2, color: severityColor(notif.severity), display: 'flex' }}>
                {notif.severity === 'resolved' ? <CheckCircle2 size={15} />
                  : notif.type === 'oom' ? <TrendingUp size={15} />
                  : notif.severity === 'critical' || notif.type === 'anomaly' ? <AlertCircle size={15} /> : <Info size={15} />}
              </div>

              {/* Contenu */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: unread ? 600 : 500, color: 'var(--text-primary)', marginBottom: 2, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{notif.title}</span>
                  <span className="badge badge-neutral" style={{ fontSize: 11, flexShrink: 0 }}>{notif.time}</span>
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginBottom: 4 }}>
                  {notif.message}
                </div>

                {/* Actions rapides */}
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  <button
                    onClick={(e) => { e.stopPropagation(); openNotif(notif.to) }}
                    style={{
                      fontSize: 11.5, padding: '2px 8px',
                      background: severityBg(notif.severity),
                      color: severityColor(notif.severity),
                      border: `1px solid ${severityBorder(notif.severity)}`,
                      borderRadius: 'var(--radius-sm)',
                      cursor: 'pointer', fontFamily: 'inherit',
                    }}
                  >Voir</button>

                  {notif.resource && (
                    <button
                      onClick={(e) => { e.stopPropagation(); openNotif(notif.podTo) }}
                      style={{
                        fontSize: 11.5, padding: '2px 8px',
                        background: 'var(--bg-elevated)',
                        color: 'var(--text-secondary)',
                        border: '1px solid var(--border)',
                        borderRadius: 'var(--radius-sm)',
                        cursor: 'pointer', fontFamily: 'inherit',
                      }}
                    >Ouvrir le pod</button>
                  )}

                  {notif.fix && (
                    <button
                      onClick={(e) => { e.stopPropagation(); openNotif(notif.fixTo || `/detection?focus=${encodeURIComponent(notif.id || '')}`) }}
                      style={{
                        fontSize: 11.5, padding: '2px 8px',
                        background: 'var(--color-info-bg)',
                        color: 'var(--color-purple-light)',
                        border: '1px solid var(--color-info-border)',
                        borderRadius: 'var(--radius-sm)',
                        cursor: 'pointer', fontFamily: 'inherit',
                      }}
                    >{notif.fix}</button>
                  )}
                </div>
              </div>

              {/* Pastille "non lu" */}
              {unread && (
                <div style={{
                  width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                  background: 'var(--accent)', alignSelf: 'center',
                }} />
              )}
            </div>
          )
        })}
      </div>

      {/* Footer */}
      {allNotifs.length > 0 && (
        <div style={{
          padding: '10px 16px', borderTop: '1px solid var(--border)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <button
            onClick={() => openNotif('/detection')}
            style={{ fontSize: 12.5, color: 'var(--color-purple-light)', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit' }}
          >
            Voir toutes les alertes
          </button>
          <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{total} alerte{total > 1 ? 's' : ''}</span>
        </div>
      )}
    </div>
  )

  // ── Rendu ──────────────────────────────────────────────────────────────────
  return (
    <>
      <button
        ref={btnRef}
        onClick={() => setPanelOpen(o => !o)}
        aria-haspopup="dialog"
        aria-expanded={panelOpen}
        aria-label={`Notifications${unreadCount > 0 ? ` (${unreadCount} non lue${unreadCount > 1 ? 's' : ''})` : ''}`}
        style={{
          position: 'relative',
          width: 32, height: 32,
          background: panelOpen ? 'var(--bg-active)' : 'transparent',
          border: `1px solid ${panelOpen ? 'var(--border-focus)' : 'transparent'}`,
          borderRadius: 'var(--radius-md)',
          cursor: 'pointer', flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: 'var(--ink-2)',
        }}
      >
        <Bell size={15} strokeWidth={1.75} />
        {/* Le badge ne compte QUE les non lues : il disparaît après ouverture */}
        {unreadCount > 0 && (
          <span
            className="notif-badge"
            style={{
              position: 'absolute', top: -4, right: -6,
              background: critical > 0 ? 'var(--color-red)' : 'var(--color-amber)',
              color: 'var(--surface)', fontSize: 11, fontWeight: 600, lineHeight: '14px',
              padding: '0 4px', borderRadius: 'var(--radius-sm)',
              minWidth: 16, textAlign: 'center',
              border: '2px solid var(--surface)',
            }}
          >{unreadCount > 99 ? '99+' : unreadCount}</span>
        )}

        {/* Alertes toutes lues mais toujours actives : point discret, sans chiffre */}
        {unreadCount === 0 && total > 0 && (
          <span
            title={`${total} alerte${total > 1 ? 's' : ''} active${total > 1 ? 's' : ''} (déjà consultée${total > 1 ? 's' : ''})`}
            style={{
              position: 'absolute', top: -2, right: -2,
              width: 7, height: 7, borderRadius: '50%',
              background: 'var(--text-muted)',
              border: '2px solid var(--surface)',
            }}
          />
        )}
      </button>

      {panelOpen && createPortal(panel, document.body)}
    </>
  )
}

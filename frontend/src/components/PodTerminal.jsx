// src/components/PodTerminal.jsx
// -----------------------------------------------------------------------------
// Terminal de pod : logs en streaming + shell interactif.
//
// CORRECTIFS / AJOUTS :
//  1. Import cassé supprimé ('../../utils/ThemeContext' pointait hors de src/).
//     Le composant s'appuie uniquement sur les variables CSS du design system.
//  2. Historique des commandes (↑ / ↓) et Ctrl+L pour effacer.
//  3. Prompt dynamique : le répertoire courant renvoyé par le serveur est
//     affiché, ce qui rend les `cd` visibles.
//  4. Sélecteur de conteneur pour les pods multi-conteneurs.
//  5. Reconnexion manuelle en cas de coupure.
// -----------------------------------------------------------------------------

import { useState, useEffect, useRef, useCallback } from 'react'
import { X, RefreshCw } from 'lucide-react'

export default function PodTerminal({ pod, onClose }) {
  const [lines, setLines]         = useState([])
  const [input, setInput]         = useState('')
  const [connected, setConnected] = useState(false)
  const [mode, setMode]           = useState('logs')   // 'logs' | 'exec'
  const [cwd, setCwd]             = useState('/')
  const [container, setContainer] = useState((pod.containers || [])[0] || '')
  const [history, setHistory]     = useState([])
  const [histIndex, setHistIndex] = useState(-1)

  const wsRef     = useRef(null)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

  const addLine = useCallback((text, type = 'log') => {
    setLines(prev => [...prev.slice(-500), { text, type, id: `${Date.now()}-${Math.random()}` }])
  }, [])

  const getLineType = (line) => {
    if (/error|fail|exception|crit/i.test(line)) return 'error'
    if (/warn/i.test(line)) return 'warn'
    if (/info|\[.*\]/i.test(line)) return 'info'
    return 'log'
  }

  // ── Connexion WebSocket ────────────────────────────────────────────────────
  const connect = useCallback(() => {
    wsRef.current?.close()
    setLines([])
    setConnected(false)
    setCwd('/')

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const base  = `${proto}//${window.location.host}`

    if (mode === 'logs') {
      const ws = new WebSocket(`${base}/ws/logs/${pod.namespace}/${pod.name}`)
      wsRef.current = ws
      ws.onopen = () => { setConnected(true); addLine(`Connexion aux logs de ${pod.name}…`, 'system') }
      ws.onmessage = e => {
        try {
          const d = JSON.parse(e.data)
          if (d.line) addLine(d.line, getLineType(d.line))
        } catch { /* trame non JSON ignorée */ }
      }
      ws.onerror = () => addLine('Erreur de connexion aux logs', 'error')
      ws.onclose = () => { setConnected(false); addLine('Connexion fermée', 'system') }
    } else {
      const ws = new WebSocket(`${base}/ws/exec/${pod.namespace}/${pod.name}`)
      wsRef.current = ws
      ws.onopen = () => {
        setConnected(true)
        addLine(`Terminal interactif : ${pod.name}`, 'system')
      }
      ws.onmessage = e => {
        try {
          const d = JSON.parse(e.data)
          if (d.type === 'clear') { setLines([]); return }
          if (d.type === 'cwd' && d.cwd) setCwd(d.cwd)
          if (d.output) addLine(d.output, 'output')
          if (d.error)  addLine(d.error, 'error')
        } catch {
          if (e.data) addLine(e.data, 'output')
        }
      }
      ws.onerror = () => addLine('Erreur de connexion au terminal', 'error')
      ws.onclose = () => { setConnected(false); addLine('Session terminée', 'system') }
    }
  }, [mode, pod.namespace, pod.name, addLine])

  useEffect(() => {
    connect()
    return () => wsRef.current?.close()
  }, [connect])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [lines])

  // ── Envoi de commande ──────────────────────────────────────────────────────
  const sendCommand = (cmd) => {
    const trimmed = (cmd || '').trim()
    if (!trimmed) return
    if (wsRef.current?.readyState !== WebSocket.OPEN) {
      addLine('Session fermée — cliquez sur ⟳ pour reconnecter', 'error')
      return
    }
    addLine(`${cwd} $ ${trimmed}`, 'system')
    wsRef.current.send(JSON.stringify({ command: trimmed, container: container || undefined }))
    setHistory(h => (h[h.length - 1] === trimmed ? h : [...h, trimmed]).slice(-100))
    setHistIndex(-1)
    setInput('')
  }

  const switchContainer = (name) => {
    setContainer(name)
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ container: name }))
    }
  }

  // ── Clavier : historique + raccourcis ──────────────────────────────────────
  const handleKeyDown = (e) => {
    if (e.key === 'Enter') { sendCommand(input); return }

    if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (history.length === 0) return
      const next = histIndex === -1 ? history.length - 1 : Math.max(0, histIndex - 1)
      setHistIndex(next)
      setInput(history[next])
      return
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (histIndex === -1) return
      const next = histIndex + 1
      if (next >= history.length) { setHistIndex(-1); setInput('') }
      else { setHistIndex(next); setInput(history[next]) }
      return
    }

    if (e.ctrlKey && e.key.toLowerCase() === 'l') { e.preventDefault(); setLines([]) }
    if (e.key === 'Escape') onClose?.()
  }

  const lineColor = (type) => ({
    error:  'var(--color-red)',
    warn:   'var(--color-amber)',
    info:   'var(--color-blue)',
    system: 'var(--color-purple-light)',
    output: 'var(--text-primary)',
    log:    'var(--text-secondary)',
  }[type] || 'var(--text-secondary)')

  const quickCmds = [
    { label: 'ls -la',        cmd: 'ls -la' },
    { label: 'env',           cmd: 'env' },
    { label: 'ps aux',        cmd: 'ps aux' },
    { label: 'df -h',         cmd: 'df -h' },
    { label: 'cat /etc/hosts',cmd: 'cat /etc/hosts' },
    { label: 'netstat',       cmd: 'netstat -tlnp 2>/dev/null || ss -tlnp' },
    { label: 'top',           cmd: 'top -bn1 | head -20' },
    { label: 'free -m',       cmd: 'free -m 2>/dev/null || cat /proc/meminfo | head -5' },
  ]

  const containers = pod.containers || []

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onClose?.() }}
      style={{
        position: 'fixed', inset: 0, background:'rgba(17,24,32,.45)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div data-theme="dark" style={{
        width: '85vw', maxWidth: 900, height: '75vh',
        background: 'var(--paper)', color: 'var(--ink)',
        border: '1px solid var(--border-strong)',
        borderRadius: 'var(--radius-lg)',
        display: 'flex', flexDirection: 'column',
        boxShadow: 'var(--shadow-lg)',
        overflow: 'hidden',
      }}>

        {/* Header */}
        <div style={{
          background: 'var(--bg-elevated)',
          borderBottom: '1px solid var(--border)',
          padding: '10px 16px',
          display: 'flex', alignItems: 'center', gap: 12,
        }}>

          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-primary)', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {pod.name}
            </span>
            <span className="badge badge-neutral" style={{ fontSize: 11.5, flexShrink: 0 }}>{pod.namespace}</span>
            <div style={{ width: 7, height: 7, borderRadius: '50%', background: connected ? 'var(--color-green)' : 'var(--color-red)', marginLeft: 'auto', flexShrink: 0 }} />
            <span style={{ fontSize: 11.5, color: connected ? 'var(--color-green)' : 'var(--color-red)', flexShrink: 0 }}>
              {connected ? 'Connecté' : 'Déconnecté'}
            </span>
            {!connected && (
              <button onClick={connect} className="btn btn-ghost btn-sm" style={{ flexShrink: 0 }}>
                <RefreshCw size={12} /> Reconnecter
              </button>
            )}
          </div>

          <div className="tabs" style={{ width: 'auto', borderBottom: 'none', gap: 14 }}>
            <button className={`tab ${mode === 'logs' ? 'active' : ''}`} onClick={() => setMode('logs')} style={{ padding: '2px 0' }}>
              Logs
            </button>
            <button className={`tab ${mode === 'exec' ? 'active' : ''}`} onClick={() => setMode('exec')} style={{ padding: '2px 0' }}>
              Terminal
            </button>
          </div>

          <button onClick={onClose} title="Fermer" aria-label="Fermer" style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex', padding: 2 }}><X size={16} /></button>
        </div>

        {/* Barre commandes rapides + conteneur */}
        {mode === 'exec' && (
          <div style={{
            padding: '6px 16px',
            borderBottom: '1px solid var(--border)',
            display: 'flex', gap: 4, alignItems: 'center', overflowX: 'auto',
            background: 'var(--bg-surface)',
          }}>
            {containers.length > 1 && (
              <select
                value={container}
                onChange={e => switchContainer(e.target.value)}
                style={{
                  fontSize: 11.5, padding: '3px 6px', flexShrink: 0, marginRight: 4,
                  background: 'var(--bg-elevated)', color: 'var(--text-secondary)',
                  border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
                  fontFamily: 'var(--font-mono)', cursor: 'pointer',
                }}
              >
                {containers.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            )}
            {quickCmds.map((c, i) => (
              <button key={i} onClick={() => sendCommand(c.cmd)} style={{
                fontSize: 11.5, padding: '3px 10px', flexShrink: 0,
                background: 'var(--bg-elevated)', color: 'var(--color-purple-light)',
                border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
                cursor: 'pointer', fontFamily: 'var(--font-mono)',
              }}>{c.label}</button>
            ))}
            <button onClick={() => setLines([])} title="Effacer (Ctrl+L)" style={{
              fontSize: 11.5, padding: '3px 10px', flexShrink: 0, marginLeft: 'auto',
              background: 'transparent', color: 'var(--text-muted)',
              border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
              cursor: 'pointer', fontFamily: 'var(--font-mono)',
            }}>clear</button>
          </div>
        )}

        {/* Sortie */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px', fontFamily: 'var(--font-mono)', fontSize: 12.5, lineHeight: 1.7 }}>
          {lines.length === 0 && (
            <div style={{ color: 'var(--text-muted)', textAlign: 'center', paddingTop: 40 }}>
              {pod.status === 'Pending' ? 'Pod en attente : le terminal sera disponible quand il tournera.' : 'Connexion en cours…'}
            </div>
          )}
          {lines.map(line => (
            <div key={line.id} style={{
              color: lineColor(line.type),
              borderLeft: line.type === 'error' ? '2px solid var(--color-red)'
                        : line.type === 'warn'  ? '2px solid var(--color-amber)'
                        : '2px solid transparent',
              paddingLeft: 8, marginBottom: 1,
              whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            }}>{line.text}</div>
          ))}
          <div ref={bottomRef} />
        </div>

        {/* Saisie */}
        {mode === 'exec' && (
          <div style={{
            borderTop: '1px solid var(--border)',
            padding: '8px 16px',
            display: 'flex', alignItems: 'center', gap: 8,
            background: 'var(--bg-surface)',
          }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--color-green)', flexShrink: 0, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {cwd} $
            </span>
            <input
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Commande (flèche haut : historique, Ctrl+L : effacer)"
              autoFocus
              spellCheck={false}
              autoComplete="off"
              style={{
                flex: 1, background: 'transparent', border: 'none', outline: 'none',
                fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-primary)',
              }}
            />
            <button onClick={() => sendCommand(input)} disabled={!connected} style={{
              padding: '4px 12px', background: 'var(--color-info-bg)',
              color: 'var(--color-purple-light)', border: '1px solid var(--color-info-border)',
              borderRadius: 'var(--radius-sm)', cursor: connected ? 'pointer' : 'not-allowed',
              fontSize: 12.5, fontFamily: 'inherit', opacity: connected ? 1 : 0.5,
            }}>Exécuter</button>
          </div>
        )}
      </div>
    </div>
  )
}

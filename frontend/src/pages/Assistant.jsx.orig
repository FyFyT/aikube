// src/pages/Assistant.jsx
// -----------------------------------------------------------------------------
// Agent de dépannage.
//
// L'agent consulte lui-même le cluster (pods en difficulté, describe, logs,
// événements, top, recommandations, prédictions OOM) avant de répondre. Chaque
// vérification est affichée sous la réponse : l'utilisateur voit sur quelles
// preuves repose le diagnostic, et peut ouvrir l'extrait brut.
//
// Le contenu du modèle est échappé avant rendu (les logs cités peuvent contenir
// du HTML). STYLES : classes du design system et variables CSS.
// -----------------------------------------------------------------------------

import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Bot, Search, CheckCircle2, XCircle, Trash2, Send, Wrench } from 'lucide-react'
import api from '../utils/api'

const SUGGESTIONS = [
  'Quels pods ont un problème, et pourquoi ?',
  'Y a-t-il des conteneurs qui risquent un crash mémoire ?',
  'Quels déploiements gaspillent des ressources ?',
  'Explique les avertissements récents du cluster',
  'Pourquoi un pod resterait-il en Pending ici ?',
]

const MODES = [
  { id: 'agent', label: 'Agent (Claude + outils)', sub: 'Enquête sur le cluster avant de répondre' },
  { id: 'groq',  label: 'Groq gpt-oss-120b',          sub: 'Contexte résumé seulement, sans outils' },
]

const STORE_KEY = 'aikube-agent-history'
const WELCOME = {
  role: 'assistant',
  content: "Je suis l'agent de dépannage d'AI-Kube. Pose-moi une question sur ton cluster : je vais consulter les pods, les logs et les événements avant de répondre, et je te montrerai chaque vérification.",
}

const escapeHtml = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

function renderMarkdown(text) {
  const blocks = []
  const keep = h => { blocks.push(h); return `\u0000${blocks.length - 1}\u0000` }
  let html = escapeHtml(text || '').replace(/```(\w+)?\n?([\s\S]*?)```/g, (_, lang, code) =>
    keep(`<pre class="code-block" style="white-space:pre-wrap;margin:8px 0;overflow:auto">${(code.includes('\n') ? code : code.replace(/\\n/g, '\n')).replace(/^\n+|\n+$/g, '')}</pre>`))
  html = html
    .replace(/`([^`\n]+)`/g, (_, c) => keep(`<code class="text-mono" style="color:var(--text-code)">${c.replace(/\\\|/g, '|')}</code>`))
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^#{1,4} (.+)$/gm, '<strong>$1</strong>')
    .replace(/^\s*[-•] (.+)$/gm, '• $1')
    .replace(/^-{3,}\s*$/gm, '')

  // Tableaux markdown : | a | b | suivi d'une ligne |---|---|
  const lines = html.split('\n')
  const out = []
  const cells = l => l.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, '|').replace(/&lt;br\s*\/?&gt;/g, '<br>'))
  const isSep = l => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l)
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trim().startsWith('|') && i + 1 < lines.length && isSep(lines[i + 1])) {
      const head = cells(lines[i])
      const rows = []
      i += 2
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(cells(lines[i++]))
      i--
      out.push(keep(
        '<div class="md-table-wrap"><table class="md-table"><thead><tr>' +
        head.map(h => `<th>${h}</th>`).join('') + '</tr></thead><tbody>' +
        rows.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>').join('') +
        '</tbody></table></div>'))
    } else out.push(lines[i])
  }
  html = out.join('\n').replace(/\n{3,}/g, '\n\n').replace(/\n/g, '<br>')
  for (let k = 0; k < 3 && /\u0000\d+\u0000/.test(html); k++)
    html = html.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[Number(i)])
  return html.replace(/(<br>)+(<div class="md-table-wrap">)/g, '$2').replace(/(<\/table><\/div>)(<br>)+/g, '$1')
}

function Trace({ trace, duration, tools }) {
  const [open, setOpen] = useState(false)
  if (!trace?.length) return null
  return (
    <div style={{ marginTop: 6 }}>
      <button className="btn btn-ghost btn-sm" onClick={() => setOpen(o => !o)}>
        <Search size={11} /> {tools} vérification{tools > 1 ? 's' : ''} sur le cluster, en {(duration / 1000).toFixed(1)} s
      </button>
      {open && (
        <div className="card" style={{ marginTop: 6, padding: 10 }}>
          {trace.map((t, i) => (
            <details key={i} style={{ marginBottom: 4 }}>
              <summary className="text-xs flex items-center gap-sm" style={{ cursor: 'pointer', listStyle: 'none' }}>
                {t.ok ? <CheckCircle2 size={12} color="var(--color-green)" /> : <XCircle size={12} color="var(--color-red)" />}
                <span className="text-primary">{i + 1}. {t.label}</span>
                <span className="text-muted">· {t.ms} ms · {t.chars} car.</span>
              </summary>
              <pre className="code-block" style={{ marginTop: 4, fontSize: 11.5, whiteSpace: 'pre-wrap', maxHeight: 220, overflow: 'auto' }}>{t.preview}{t.chars > 600 ? '\n…' : ''}</pre>
            </details>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Assistant() {
  const [messages, setMessages] = useState(() => {
    try { const s = localStorage.getItem(STORE_KEY); return s ? JSON.parse(s) : [WELCOME] } catch { return [WELCOME] }
  })
  // ?q=… : question préparée depuis la page Détection ou une notification.
  // Elle est seulement pré-remplie : l'utilisateur la relit et l'envoie lui-même.
  const [params, setParams] = useSearchParams()
  const [input, setInput] = useState(() => params.get('q') || '')
  useEffect(() => {
    const q = params.get('q')
    if (q) { setInput(q); setParams({}, { replace: true }) }
  }, [params, setParams])
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState('agent')
  const bottomRef = useRef(null)

  useEffect(() => {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(messages.slice(-40))) } catch { /* stockage indisponible */ }
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const send = async (text) => {
    const msg = (text ?? input).trim()
    if (!msg || loading) return
    const next = [...messages, { role: 'user', content: msg }]
    setMessages(next); setInput(''); setLoading(true)
    const history = next.slice(-10).map(m => ({ role: m.role, content: m.content }))
    try {
      const { data } = mode === 'agent'
        ? await api.post('/ai/agent', { message: msg, history }, { timeout: 240000 })
        : await api.post('/ai/chat', { message: msg, history, model: 'groq' }, { timeout: 60000 })
      setMessages(prev => [...prev, {
        role: 'assistant', content: data.response, model: data.model_used,
        trace: data.trace || [], tools: data.tools_used || 0, duration: data.duration_ms || 0,
        mode: data.mode || (mode === 'agent' ? 'agent' : 'contexte seul'),
      }])
    } catch (e) {
      setMessages(prev => [...prev, { role: 'assistant', error: true,
        content: `Erreur : ${e.response?.data?.detail || e.message}. Vérifie que le backend tourne (port 8000).` }])
    }
    setLoading(false)
  }

  const clear = () => setMessages([WELCOME])

  return (
    <div style={{ padding: 20, display: 'flex', gap: 16, height: '100vh' }}>
      {/* Colonne gauche */}
      <div className="flex-col gap-md" style={{ display: 'flex', width: 230, flexShrink: 0 }}>
        <div>
          <h1 className="page-title">Agent de dépannage</h1>
          <p className="page-subtitle">Enquête en lecture seule, preuves à l'appui</p>
        </div>

        <div className="card" style={{ padding: 10 }}>
          <div className="metric-label">Mode</div>
          {MODES.map(m => (
            <div key={m.id} onClick={() => setMode(m.id)} style={{
              padding: '7px 9px', borderRadius: 'var(--radius-md)', cursor: 'pointer', marginBottom: 3,
              background: mode === m.id ? 'var(--bg-active)' : 'transparent',
              border: `1px solid ${mode === m.id ? 'var(--border-focus)' : 'transparent'}`,
            }}>
              <div className="text-sm fw-500" style={{ color: mode === m.id ? 'var(--color-purple-light)' : 'var(--text-primary)' }}>{m.label}</div>
              <div className="text-xs text-muted">{m.sub}</div>
            </div>
          ))}
        </div>

        <div className="card" style={{ padding: 10, flex: 1, overflowY: 'auto' }}>
          <div className="metric-label">Questions rapides</div>
          {SUGGESTIONS.map(s => (
            <button key={s} className="btn btn-ghost btn-sm" onClick={() => send(s)} disabled={loading}
              style={{ width: '100%', justifyContent: 'flex-start', whiteSpace: 'normal', textAlign: 'left', marginBottom: 4, border: 'none' }}>{s}</button>
          ))}
        </div>

        <div className="card text-xs text-muted" style={{ padding: 10 }}>
          <div className="flex items-center gap-sm text-secondary fw-500" style={{ marginBottom: 4 }}><Wrench size={12} /> Outils de l'agent</div>
          pods en difficulté, get, describe, logs (y compris avant redémarrage), événements, top, capacité de l'hôte, recommandations, prédictions OOM. Lecture seule ; les Secrets ne sont jamais lus.
        </div>

        <button className="btn btn-ghost btn-sm" onClick={clear}><Trash2 size={12} /> Nouvelle conversation</button>
      </div>

      {/* Conversation */}
      <div className="flex-col" style={{ display: 'flex', flex: 1, minWidth: 0 }}>
        <div className="card" style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 14, marginBottom: 12 }}>
          {messages.map((m, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
              <div style={{ maxWidth: '82%', minWidth: 0 }}>
                <div style={{
                  padding: '10px 14px', fontSize: 14, lineHeight: 1.65,
                  borderRadius: m.role === 'user' ? '14px 14px 4px 14px' : '14px 14px 14px 4px',
                  background: m.role === 'user' ? 'var(--color-purple)' : m.error ? 'var(--color-danger-bg)' : 'var(--bg-elevated)',
                  color: m.role === 'user' ? '#fff' : 'var(--text-primary)',
                  border: m.role === 'user' ? 'none' : '1px solid var(--border)',
                }} dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }} />
                {m.role === 'assistant' && (m.model || m.trace?.length > 0) && (
                  <>
                    <Trace trace={m.trace} duration={m.duration} tools={m.tools} />
                    <div className="text-xs text-muted" style={{ marginTop: 4, paddingLeft: 4 }}>
                      {m.model} · {m.mode === 'agent' ? (m.tools ? `${m.tools} outil(s) consulté(s)` : 'réponse sans consultation') : 'contexte résumé, sans outils'}
                    </div>
                  </>
                )}
              </div>
            </div>
          ))}
          {loading && (
            <div className="text-sm text-muted flex items-center gap-sm">
              <Search size={13} className="pulse" />
              {mode === 'agent' ? "L'agent consulte le cluster (pods, logs, événements)… compte 15 à 60 s" : 'Réponse en cours…'}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="flex gap-sm">
          <textarea className="input" rows={2} value={input} disabled={loading}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            placeholder="Par exemple : pourquoi le pod api redémarre-t-il ?"
            style={{ resize: 'none', fontFamily: 'var(--font-sans)' }} />
          <button className="btn btn-primary" onClick={() => send()} disabled={loading || !input.trim()} style={{ minWidth: 110, justifyContent: 'center' }}>
            <Send size={13} /> Envoyer
          </button>
        </div>
      </div>
    </div>
  )
}

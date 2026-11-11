// src/pages/MyApps.jsx
// -----------------------------------------------------------------------------
// Mes applications : déployer et faire évoluer sa propre application, sans terminal.
//
//   Application   choisir une application connue, ou en créer une
//   1. Importer   dossier du PC (WSL ou Windows), dossier choisi dans le
//                 navigateur, ou .zip → ESPACE DE TRAVAIL d'AI-Kube (copie :
//                 le dossier d'origine n'est jamais modifié)
//   2. Fichiers   éditeur : modifier, créer, supprimer, télécharger en .zip,
//                 « Modifier avec l'IA » (propositions à valider une par une)
//   3. Construire et déployer   image sur TOUS les nœuds, puis déploiement
//                 surveillé (page Déploiement : aperçu, retour arrière)
//   4. Ouvrir     accès depuis le navigateur (port-forward géré par le backend)
//
// API : /api/apps (backend/api/apps_router.py). STYLES : classes du design system.
// -----------------------------------------------------------------------------

import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import Editor from '@monaco-editor/react'
import {
  FolderOpen, FolderUp, Upload, Hammer, Rocket, ExternalLink, X, Loader2, CheckCircle2, XCircle,
  AlertTriangle, Globe, RefreshCw, Save, FilePlus, Trash2, Download, Sparkles, FileText, Plus,
} from 'lucide-react'
import api from '../utils/api'
import { useTheme } from '../utils/ThemeContext'

const errText = e => e?.response?.data?.detail || e?.message || String(e)
const inp = { background: 'var(--bg-elevated)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 9px', fontSize: 13 }
const slug = s => (s || '').toLowerCase().replace(/\.zip$/, '').replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)
const SKIP = ['node_modules', '.git', '__pycache__', '.venv', 'venv']
const LANG = { js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript', py: 'python', html: 'html', htm: 'html',
  css: 'css', json: 'json', md: 'markdown', yml: 'yaml', yaml: 'yaml', sh: 'shell', go: 'go', java: 'java', php: 'php', sql: 'sql', xml: 'xml' }
const langOf = p => p === 'Dockerfile' || p.endsWith('/Dockerfile') ? 'dockerfile' : (LANG[p.split('.').pop().toLowerCase()] || 'plaintext')

const STEP_ICON = {
  running: <Loader2 size={13} className="spin" style={{ color: 'var(--color-purple-light)' }} />,
  ok: <CheckCircle2 size={13} style={{ color: 'var(--color-green)' }} />,
  failed: <XCircle size={13} style={{ color: 'var(--color-red)' }} />,
  warning: <AlertTriangle size={13} style={{ color: 'var(--color-amber)' }} />,
}

function Step({ n, title, right, children, muted }) {
  return (
    <div className="card" style={{ opacity: muted ? 0.55 : 1 }}>
      <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 22, height: 22, borderRadius: 11, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, background: 'var(--accent)', color: '#fff' }}>{n}</span>
        {title}
        <span style={{ marginLeft: 'auto' }}>{right}</span>
      </div>
      {children}
    </div>
  )
}

function Msg({ kind = 'info', children }) {
  const color = { info: 'var(--text-secondary)', ok: 'var(--color-green)', err: 'var(--color-red)', warn: 'var(--color-amber)' }[kind]
  return <div className="text-sm" style={{ color, marginTop: 8, lineHeight: 1.5 }}>{children}</div>
}

function Diff({ text }) {
  return (
    <pre className="code-block" style={{ fontSize: 12, maxHeight: 260, overflow: 'auto', whiteSpace: 'pre', margin: '6px 0 0' }}>
      {text.split('\n').map((l, i) => {
        const c = l.startsWith('+++') || l.startsWith('---') ? 'var(--text-muted)'
          : l.startsWith('+') ? 'var(--color-green)' : l.startsWith('-') ? 'var(--color-red)'
          : l.startsWith('@@') ? 'var(--color-purple-light)' : undefined
        return <div key={i} style={{ color: c }}>{l || ' '}</div>
      })}
    </pre>
  )
}

// ── Choix de l'application ──────────────────────────────────────────────────
function AppPicker({ apps, name, setName }) {
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState('')
  const known = apps.some(a => a.name === name)
  return (
    <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
      <span className="text-sm fw-500">Application</span>
      {apps.length > 0 && !creating && (
        <select value={known ? name : ''} onChange={e => setName(e.target.value)} style={{ ...inp, minWidth: 200 }}>
          <option value="" disabled>choisir…</option>
          {apps.map(a => <option key={a.name} value={a.name}>{a.name}{a.last_tag ? ` (${a.last_tag})` : ''}</option>)}
        </select>
      )}
      {(creating || apps.length === 0) ? (
        <>
          <input autoFocus value={draft} onChange={e => setDraft(slug(e.target.value))} placeholder="nom de la nouvelle application (ex. monsite)"
            className="text-mono" style={{ ...inp, width: 300 }}
            onKeyDown={e => { if (e.key === 'Enter' && draft) { setName(draft); setCreating(false) } }} />
          <button className="btn btn-primary btn-sm" disabled={!draft} onClick={() => { setName(draft); setCreating(false) }}>Créer</button>
          {apps.length > 0 && <button className="btn btn-ghost btn-sm" onClick={() => setCreating(false)}>Annuler</button>}
        </>
      ) : (
        <button className="btn btn-ghost btn-sm" onClick={() => { setDraft(''); setCreating(true) }}><Plus size={12} /> Nouvelle application</button>
      )}
      <span className="text-xs text-muted" style={{ flexBasis: '100%' }}>
        Chaque application a son espace de travail dans AI-Kube : une copie de tes fichiers, que tu peux modifier ici sans toucher au dossier d'origine.
      </span>
    </div>
  )
}

// ── 1. Importer ─────────────────────────────────────────────────────────────
function ImportStep({ name, app, onImported }) {
  const [mode, setMode] = useState('folder')
  const [path, setPath] = useState('~/monapp')
  const [busy, setBusy] = useState(null)
  const [err, setErr] = useState(null)
  const [result, setResult] = useState(null)
  const dirRef = useRef(null)
  const zipRef = useRef(null)

  useEffect(() => { setResult(null); setErr(null) }, [name])

  const confirmReplace = () => !app?.has_source || window.confirm(
    `Remplacer les fichiers de « ${name} » ? La version actuelle de l'espace de travail est gardée en copie (source.precedent).`)

  const done = data => { setResult(data); onImported() }

  const fromPath = async () => {
    if (!confirmReplace()) return
    setErr(null); setBusy('Copie du dossier…')
    try { done((await api.post(`/apps/${name}/import/path`, { path }, { timeout: 120000 })).data) }
    catch (e) { setErr(errText(e)) } finally { setBusy(null) }
  }

  const fromDir = async fileList => {
    const files = Array.from(fileList || []).filter(f => !(f.webkitRelativePath || f.name).split('/').some(p => SKIP.includes(p)))
    if (dirRef.current) dirRef.current.value = ''
    if (!files.length) { setErr('Ce dossier est vide.'); return }
    if (!confirmReplace()) return
    setErr(null)
    try {
      await api.post(`/apps/${name}/import/begin`)
      for (let i = 0; i < files.length; i++) {
        const f = files[i]
        const rel = (f.webkitRelativePath || f.name).split('/').slice(1).join('/') || f.name   // sans le dossier racine
        setBusy(`Envoi des fichiers… ${i + 1} / ${files.length}`)
        await api.put(`/apps/${name}/import/file`, f, { params: { path: rel }, headers: { 'Content-Type': 'application/octet-stream' }, timeout: 120000 })
      }
      setBusy('Finalisation…')
      done((await api.post(`/apps/${name}/import/finish`)).data)
    } catch (e) { setErr(errText(e)) } finally { setBusy(null) }
  }

  const fromZip = async file => {
    if (zipRef.current) zipRef.current.value = ''
    if (!file || !confirmReplace()) return
    setErr(null); setBusy('Import du .zip…')
    try { done((await api.post(`/apps/${name}/import/zip`, file, { headers: { 'Content-Type': 'application/zip' }, timeout: 180000 })).data) }
    catch (e) { setErr(errText(e)) } finally { setBusy(null) }
  }

  const tabs = [
    { id: 'folder', icon: FolderOpen, label: 'Dossier du PC' },
    { id: 'browser', icon: FolderUp, label: 'Choisir un dossier' },
    { id: 'zip', icon: Upload, label: 'Fichier .zip' },
  ]
  return (
    <Step n={1} title={app?.has_source ? 'Importer (remplace les fichiers actuels)' : 'Importer les fichiers'} muted={!name}>
      <div className="tabs" style={{ width: 'fit-content', marginBottom: 10 }}>
        {tabs.map(t => (
          <button key={t.id} className={`tab ${mode === t.id ? 'active' : ''}`} onClick={() => setMode(t.id)}>
            <t.icon size={13} style={{ verticalAlign: -2, marginRight: 5 }} />{t.label}
          </button>
        ))}
      </div>
      {mode === 'folder' && (
        <>
          <div className="flex gap-sm">
            <input value={path} onChange={e => setPath(e.target.value)} onKeyDown={e => e.key === 'Enter' && name && fromPath()}
              className="text-mono" style={{ ...inp, flex: 1 }} placeholder="~/monapp  ou  C:\Users\…\monapp" />
            <button className="btn btn-primary btn-sm" onClick={fromPath} disabled={!name || !!busy || !path.trim()}><FolderOpen size={12} /> Importer</button>
          </div>
          <div className="text-xs text-muted" style={{ marginTop: 6 }}>Un dossier de WSL (~/…) ou de Windows (C:\Users\…). Il est copié ; node_modules et .git sont ignorés.</div>
        </>
      )}
      {mode === 'browser' && (
        <>
          <input ref={dirRef} type="file" webkitdirectory="" directory="" multiple disabled={!name || !!busy} onChange={e => fromDir(e.target.files)} />
          <div className="text-xs text-muted" style={{ marginTop: 6 }}>Choisis le dossier de ton application ; le navigateur demande de confirmer l'envoi. node_modules et .git sont ignorés.</div>
        </>
      )}
      {mode === 'zip' && (
        <>
          <input ref={zipRef} type="file" accept=".zip,application/zip" disabled={!name || !!busy} onChange={e => fromZip(e.target.files?.[0])} />
          <div className="text-xs text-muted" style={{ marginTop: 6 }}>100 Mo au plus.</div>
        </>
      )}
      {busy && <Msg><Loader2 size={12} className="spin" /> {busy}</Msg>}
      {err && <Msg kind="err">{err}</Msg>}
      {result && (
        <Msg kind={result.kind ? 'ok' : 'warn'}>
          {result.kind ? <CheckCircle2 size={13} style={{ verticalAlign: -2 }} /> : <AlertTriangle size={13} style={{ verticalAlign: -2 }} />} Importé. {result.message}
        </Msg>
      )}
    </Step>
  )
}

// ── 2. Fichiers ─────────────────────────────────────────────────────────────
function FilesStep({ name, app, onChanged }) {
  const { themeName } = useTheme()
  const { data: files = [], refetch } = useQuery({
    queryKey: ['app-files', name], enabled: !!name && !!app?.has_source,
    queryFn: () => api.get(`/apps/${name}/files`).then(r => r.data.files),
  })
  const [current, setCurrent] = useState(null)       // { path, editable, content }
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [ready, setReady] = useState(false)
  const [fallback, setFallback] = useState(false)
  const [ai, setAi] = useState('')
  const [aiBusy, setAiBusy] = useState(false)
  const [aiRes, setAiRes] = useState(null)
  const dirty = current?.editable && text !== current.content

  useEffect(() => { setCurrent(null); setText(''); setAiRes(null); setMsg(null) }, [name])
  useEffect(() => {
    if (!current || ready) return
    const t = setTimeout(() => { if (!ready) setFallback(true) }, 6000)   // éditeur indisponible hors ligne
    return () => clearTimeout(t)
  }, [current, ready])

  const open = async path => {
    if (dirty && !window.confirm('Modifications non enregistrées. Les abandonner ?')) return
    setMsg(null)
    try {
      const r = await api.get(`/apps/${name}/file`, { params: { path } })
      setCurrent(r.data); setText(r.data.content || '')
    } catch (e) { setMsg({ kind: 'err', text: errText(e) }) }
  }

  const save = async () => {
    if (!current?.editable) return
    setBusy(true)
    try {
      await api.put(`/apps/${name}/file`, { path: current.path, content: text })
      setCurrent({ ...current, content: text })
      setMsg({ kind: 'ok', text: `${current.path} enregistré.` })
      onChanged()
    } catch (e) { setMsg({ kind: 'err', text: errText(e) }) } finally { setBusy(false) }
  }

  // Ctrl+S
  const saveRef = useRef(save); saveRef.current = save
  useEffect(() => {
    const h = e => { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveRef.current() } }
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h)
  }, [])

  const create = async () => {
    const path = window.prompt('Nom du nouveau fichier (ex. about.html, css/style.css) :')
    if (!path) return
    setMsg(null)
    try {
      await api.put(`/apps/${name}/file`, { path, content: '' })
      await refetch(); onChanged()
      setCurrent({ path, editable: true, content: '' }); setText('')
    } catch (e) { setMsg({ kind: 'err', text: errText(e) }) }
  }

  const remove = async () => {
    if (!current || !window.confirm(`Supprimer ${current.path} ?`)) return
    try {
      await api.delete(`/apps/${name}/file`, { params: { path: current.path } })
      setCurrent(null); setText(''); await refetch(); onChanged()
    } catch (e) { setMsg({ kind: 'err', text: errText(e) }) }
  }

  const askAi = async () => {
    setAiBusy(true); setAiRes(null)
    try {
      const r = await api.post(`/apps/${name}/ai-edit`, { instruction: ai, path: current?.path || null }, { timeout: 240000 })
      setAiRes({ ...r.data, proposals: r.data.proposals.map(p => ({ ...p, state: 'pending' })) })
    } catch (e) { setAiRes({ error: errText(e) }) } finally { setAiBusy(false) }
  }

  const applyProposal = async (i) => {
    const p = aiRes.proposals[i]
    try {
      await api.put(`/apps/${name}/file`, { path: p.path, content: p.content })
      setAiRes(r => ({ ...r, proposals: r.proposals.map((q, j) => j === i ? { ...q, state: 'applied' } : q) }))
      await refetch(); onChanged()
      if (current?.path === p.path) { setCurrent({ ...current, content: p.content }); setText(p.content) }
    } catch (e) { setMsg({ kind: 'err', text: errText(e) }) }
  }
  const skipProposal = i => setAiRes(r => ({ ...r, proposals: r.proposals.map((q, j) => j === i ? { ...q, state: 'skipped' } : q) }))

  if (!app?.has_source) {
    return <Step n={2} title="Fichiers" muted><Msg>Importe d'abord les fichiers de l'application.</Msg></Step>
  }
  return (
    <Step n={2} title="Fichiers" right={
      <span className="flex gap-sm">
        <button className="btn btn-ghost btn-sm" onClick={create}><FilePlus size={12} /> Nouveau</button>
        <a className="btn btn-ghost btn-sm" href={`/api/apps/${name}/export`} download={`${name}.zip`}><Download size={12} /> .zip</a>
      </span>}>
      <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 10, minHeight: 360 }}>
        <div style={{ border: '1px solid var(--border)', borderRadius: 6, overflow: 'auto', maxHeight: 460, padding: '4px 0' }}>
          {files.map(f => (
            <div key={f.path} onClick={() => open(f.path)} title={f.editable ? f.path : `${f.path} (non modifiable : binaire ou trop gros)`}
              className="text-sm text-mono" style={{
                padding: '3px 8px', cursor: 'pointer', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                background: current?.path === f.path ? 'var(--bg-active)' : 'transparent', opacity: f.editable ? 1 : 0.55,
              }}>
              <FileText size={11} style={{ verticalAlign: -1, marginRight: 4 }} />
              {f.path.includes('/') && <span className="text-muted">{f.path.slice(0, f.path.lastIndexOf('/') + 1)}</span>}
              {f.path.split('/').pop()}
            </div>
          ))}
          {files.length === 0 && <div className="text-xs text-muted" style={{ padding: 8 }}>Aucun fichier.</div>}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          {!current ? (
            <div className="text-sm text-muted" style={{ padding: 16 }}>Choisis un fichier à gauche pour le modifier.</div>
          ) : !current.editable ? (
            <div className="text-sm text-muted" style={{ padding: 16 }}>{current.path} n'est pas modifiable ici (fichier binaire ou de plus de 1 Mo).
              <div style={{ marginTop: 8 }}><button className="btn btn-ghost btn-sm" onClick={remove}><Trash2 size={12} /> Supprimer</button></div></div>
          ) : (
            <>
              <div className="flex items-center gap-sm" style={{ marginBottom: 6 }}>
                <span className="text-mono text-sm">{current.path}</span>
                {dirty && <span className="badge badge-warning">non enregistré</span>}
                <span style={{ flex: 1 }} />
                <button className="btn btn-ghost btn-sm" onClick={remove}><Trash2 size={12} /> Supprimer</button>
                <button className="btn btn-primary btn-sm" onClick={save} disabled={!dirty || busy}><Save size={12} /> Enregistrer</button>
              </div>
              <div style={{ border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden', flex: 1 }}>
                {fallback ? (
                  <textarea value={text} onChange={e => setText(e.target.value)} spellCheck={false}
                    style={{ width: '100%', height: 420, border: 'none', fontFamily: 'var(--font-mono)', fontSize: 13, padding: 10, boxSizing: 'border-box', background: 'var(--bg-elevated)', color: 'var(--text-primary)', resize: 'vertical' }} />
                ) : (
                  <Editor height="420px" path={current.path} language={langOf(current.path)} value={text}
                    theme={themeName === 'light' ? 'light' : 'vs-dark'} onMount={() => setReady(true)}
                    onChange={v => setText(v ?? '')} options={{ minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false, wordWrap: 'on' }} />
                )}
              </div>
            </>
          )}
          {msg && <Msg kind={msg.kind}>{msg.text}</Msg>}
        </div>
      </div>

      <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
        <div className="flex gap-sm items-center">
          <Sparkles size={14} style={{ color: 'var(--color-purple-light)' }} />
          <input value={ai} onChange={e => setAi(e.target.value)} onKeyDown={e => e.key === 'Enter' && ai.trim() && !aiBusy && askAi()}
            placeholder={current ? `Modifier avec l'IA (fichier ouvert : ${current.path}), ex. « ajoute une page contact »` : "Modifier avec l'IA, ex. « ajoute une page contact avec un formulaire »"}
            style={{ ...inp, flex: 1 }} />
          <button className="btn btn-primary btn-sm" onClick={askAi} disabled={!ai.trim() || aiBusy}>
            {aiBusy ? <><Loader2 size={12} className="spin" /> L'IA réfléchit…</> : 'Proposer'}
          </button>
        </div>
        <div className="text-xs text-muted" style={{ marginTop: 4 }}>L'IA propose ; rien n'est écrit avant que tu cliques « Appliquer » sur chaque fichier.</div>
        {aiRes?.error && <Msg kind="err">{aiRes.error}</Msg>}
        {aiRes && !aiRes.error && (
          <div style={{ marginTop: 8 }}>
            <div className="text-sm" style={{ lineHeight: 1.5 }}>{aiRes.explanation} {aiRes.model && <span className="badge badge-info">{aiRes.model}</span>}</div>
            {aiRes.proposals.length === 0 && <Msg kind="warn">Aucune modification proposée.</Msg>}
            {aiRes.proposals.map((p, i) => (
              <div key={p.path} style={{ marginTop: 8 }}>
                <div className="flex items-center gap-sm">
                  <span className="text-mono text-sm fw-500">{p.path}</span>
                  {p.new && <span className="badge badge-success">nouveau</span>}
                  <span style={{ flex: 1 }} />
                  {p.state === 'pending' && <>
                    <button className="btn btn-ghost btn-sm" onClick={() => skipProposal(i)}>Ignorer</button>
                    <button className="btn btn-success btn-sm" onClick={() => applyProposal(i)}><CheckCircle2 size={12} /> Appliquer</button>
                  </>}
                  {p.state === 'applied' && <span className="badge badge-success">appliqué</span>}
                  {p.state === 'skipped' && <span className="badge badge-neutral">ignoré</span>}
                </div>
                {p.state === 'pending' && <Diff text={p.diff} />}
              </div>
            ))}
          </div>
        )}
      </div>
    </Step>
  )
}

// ── 3. Construire et déployer ───────────────────────────────────────────────
function BuildStep({ name, app, filesVersion, onBuilt }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [tag, setTag] = useState('1.0')
  const [port, setPort] = useState('80')
  const [replicas, setReplicas] = useState(2)
  const [ns, setNs] = useState('default')
  const [job, setJob] = useState(null)
  const [err, setErr] = useState(null)
  const [thenDeploy, setThenDeploy] = useState(false)
  const logRef = useRef(null)

  useEffect(() => { setJob(null); setErr(null) }, [name])
  useEffect(() => { if (app) { setTag(app.next_tag || '1.0'); if (app.port) setPort(String(app.port)) } }, [app?.name, app?.next_tag, app?.port]) // eslint-disable-line

  const goDeploy = async (t) => {
    try {
      const r = await api.post('/apps/manifest', { name, tag: t, port: Number(port), replicas: Number(replicas), namespace: ns })
      navigate('/devops', { state: { yaml: r.data.yaml, namespace: ns } })
    } catch (e) { setErr(errText(e)) }
  }

  useEffect(() => {
    if (!job || job.status !== 'running') return
    const t = setInterval(async () => {
      try {
        const r = await api.get(`/apps/jobs/${job.id}`)
        setJob(r.data)
        if (r.data.status !== 'running') {
          qc.invalidateQueries({ queryKey: ['apps-list'] })
          if (r.data.status === 'ok') onBuilt()
          if (r.data.status === 'ok' && thenDeploy) goDeploy(r.data.tag)
        }
      } catch (e) { setErr(errText(e)) }
    }, 1000)
    return () => clearInterval(t)
  }, [job?.id, job?.status]) // eslint-disable-line

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight }, [job?.log?.length])

  const build = async (deploy) => {
    setErr(null); setThenDeploy(deploy)
    try { setJob((await api.post('/apps/build', { name, tag, port: Number(port) || null })).data) }
    catch (e) { setErr(errText(e)) }
  }

  const running = job?.status === 'running'
  const canBuild = app?.has_source && app?.kind && tag && !running
  return (
    <Step n={3} title="Construire et déployer" muted={!app?.has_source}>
      <div className="flex gap-sm items-center" style={{ flexWrap: 'wrap' }}>
        <label className="text-xs text-muted">Version</label>
        <input value={tag} onChange={e => setTag(e.target.value.trim())} className="text-mono" style={{ ...inp, width: 80 }} />
        <label className="text-xs text-muted">Port</label>
        <input value={port} onChange={e => setPort(e.target.value.replace(/\D/g, ''))} className="text-mono" style={{ ...inp, width: 70 }} />
        <label className="text-xs text-muted">Copies</label>
        <input value={replicas} onChange={e => setReplicas(e.target.value.replace(/\D/g, ''))} className="text-mono" style={{ ...inp, width: 50 }} />
        <label className="text-xs text-muted">Namespace</label>
        <input value={ns} onChange={e => setNs(e.target.value.trim())} className="text-mono" style={{ ...inp, width: 110 }} />
      </div>
      <div className="flex gap-sm items-center" style={{ marginTop: 10, flexWrap: 'wrap' }}>
        <button className="btn btn-success" onClick={() => build(true)} disabled={!canBuild || !port}>
          {running && thenDeploy ? <><Loader2 size={13} className="spin" /> Construction…</> : <><Rocket size={13} /> Construire et déployer la version {tag} →</>}
        </button>
        <button className="btn btn-ghost btn-sm" onClick={() => build(false)} disabled={!canBuild}>
          {running && !thenDeploy ? <Loader2 size={12} className="spin" /> : <Hammer size={12} />} Construire seulement
        </button>
        {app?.last_tag && (
          <button className="btn btn-ghost btn-sm" onClick={() => goDeploy(app.last_tag)} disabled={running || !port}>Déployer la version {app.last_tag} déjà construite</button>
        )}
      </div>
      <div className="text-xs text-muted" style={{ marginTop: 6, lineHeight: 1.5 }}>
        L'image est construite sur tous les nœuds, puis la page Déploiement montre l'aperçu : tu confirmes, les nouveaux pods sont surveillés,
        et si la nouvelle version casse, l'ancienne revient toute seule.
        {app?.versions?.length > 0 && <> Versions déjà construites : {app.versions.map(v => v.tag).join(', ')}.</>}
        {filesVersion > 0 && app?.last_tag && <> Les fichiers ont changé depuis : construis une nouvelle version pour les prendre en compte.</>}
      </div>
      {app?.has_source && !app?.kind && <Msg kind="warn">{app.message}</Msg>}
      {err && <Msg kind="err">{err}</Msg>}
      {job && (
        <div style={{ marginTop: 12 }}>
          {job.steps.map(s => (
            <div key={s.key} className="flex items-center gap-sm text-sm" style={{ marginBottom: 4 }}>
              {STEP_ICON[s.status]} <span className="fw-500">{s.label}</span>
              <span className="text-xs text-muted">{s.detail}</span>
              <span className="text-xs text-muted" style={{ marginLeft: 'auto' }}>{s.t} s</span>
            </div>
          ))}
          {job.error && <Msg kind="err">{job.error}</Msg>}
          {job.status === 'ok' && <Msg kind="ok">Image <span className="text-mono">{job.image}</span> prête sur tous les nœuds ({job.elapsed} s).{thenDeploy && ' Ouverture de la page Déploiement…'}</Msg>}
          {job.log?.length > 0 && (
            <pre ref={logRef} className="code-block" style={{ fontSize: 11.5, maxHeight: 180, overflow: 'auto', marginTop: 8, whiteSpace: 'pre-wrap' }}>{job.log.join('\n')}</pre>
          )}
        </div>
      )}
    </Step>
  )
}

// ── 4. Ouvrir dans le navigateur ────────────────────────────────────────────
function OpenStep() {
  const qc = useQueryClient()
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['apps-services'], queryFn: () => api.get('/apps/services').then(r => r.data), refetchInterval: 5000,
  })
  const [busy, setBusy] = useState(null)
  const [err, setErr] = useState(null)

  const open = async (s) => {
    setErr(null); setBusy(s.namespace + '/' + s.name)
    const w = window.open('', '_blank')            // ouvert tout de suite : sinon le navigateur bloque la fenêtre
    try {
      const r = await api.post('/apps/access', { namespace: s.namespace, service: s.name, port: s.ports[0] }, { timeout: 20000 })
      if (w) w.location = r.data.url
      qc.invalidateQueries({ queryKey: ['apps-services'] })
    } catch (e) { if (w) w.close(); setErr(errText(e)) } finally { setBusy(null) }
  }
  const close = async (s) => {
    await api.delete(`/apps/access/${s.namespace}/${s.name}`).catch(() => {})
    qc.invalidateQueries({ queryKey: ['apps-services'] })
  }

  const services = data?.services || []
  return (
    <Step n={4} title="Ouvrir dans le navigateur" right={
      <button className="btn btn-ghost btn-sm" onClick={() => refetch()}>{isFetching ? <Loader2 size={12} className="spin" /> : <RefreshCw size={12} />}</button>}>
      <div className="text-xs text-muted" style={{ marginBottom: 8 }}>Une fois le déploiement réussi, ouvre l'application ici. L'accès reste ouvert tant que le backend tourne, et se rétablit tout seul après une mise à jour des pods.</div>
      {err && <Msg kind="err">{err}</Msg>}
      {isLoading ? <Msg>Chargement…</Msg> : services.length === 0 ? <Msg>Aucun service à ouvrir pour l'instant.</Msg> : (
        <table className="table" style={{ width: '100%' }}>
          <thead><tr><th>Service</th><th>Namespace</th><th>Port</th><th>Accès</th><th /></tr></thead>
          <tbody>
            {services.map(s => {
              const key = s.namespace + '/' + s.name
              const a = s.access
              return (
                <tr key={key}>
                  <td className="text-mono">{s.name} {s.managed && <span className="badge badge-info" style={{ marginLeft: 4 }}>mes applications</span>}</td>
                  <td className="text-mono text-xs">{s.namespace}</td>
                  <td className="text-mono text-xs">{s.ports.join(', ')}</td>
                  <td className="text-sm">
                    {a ? (
                      <a href={a.url} target="_blank" rel="noreferrer" className="text-mono" style={{ color: a.running ? 'var(--color-green)' : 'var(--color-amber)' }}>
                        {a.url} <ExternalLink size={11} style={{ verticalAlign: -1 }} />
                      </a>
                    ) : <span className="text-muted text-xs">fermé</span>}
                    {a && !a.running && <div className="text-xs" style={{ color: 'var(--color-amber)' }}>reconnexion… {a.last_error}</div>}
                  </td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {a ? (
                      <button className="btn btn-ghost btn-sm" onClick={() => close(s)}><X size={12} /> Fermer</button>
                    ) : (
                      <button className="btn btn-primary btn-sm" onClick={() => open(s)} disabled={busy === key}>
                        {busy === key ? <Loader2 size={12} className="spin" /> : <Globe size={12} />} Ouvrir
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Step>
  )
}

export default function MyApps() {
  const qc = useQueryClient()
  const { data: apps = [] } = useQuery({ queryKey: ['apps-list'], queryFn: () => api.get('/apps/list').then(r => r.data.apps) })
  const [name, setName] = useState(() => { try { return localStorage.getItem('aikube.apps.current') || '' } catch { return '' } })
  const [filesVersion, setFilesVersion] = useState(0)
  useEffect(() => { try { localStorage.setItem('aikube.apps.current', name) } catch { /* stockage indisponible */ } setFilesVersion(0) }, [name])
  useEffect(() => { if (!name && apps.length) setName(apps[0].name) }, [apps.length]) // eslint-disable-line
  const app = useMemo(() => apps.find(a => a.name === name) || (name ? { name, has_source: false, versions: [] } : null), [apps, name])
  const refresh = () => { qc.invalidateQueries({ queryKey: ['apps-list'] }); qc.invalidateQueries({ queryKey: ['app-files', name] }) }

  return (
    <div style={{ padding: 20, overflowY: 'auto', height: '100vh' }}>
      <div style={{ marginBottom: 14 }}>
        <h1 className="page-title">Mes applications</h1>
        <p className="page-subtitle">Importer ton application, modifier ses fichiers, la construire sur tous les nœuds, la déployer avec surveillance, puis l'ouvrir dans le navigateur.</p>
      </div>
      <div style={{ display: 'grid', gap: 14, maxWidth: 1100 }}>
        <AppPicker apps={apps} name={name} setName={setName} />
        {name && <>
          <ImportStep name={name} app={app} onImported={() => { refresh(); setFilesVersion(0) }} />
          <FilesStep name={name} app={app} onChanged={() => { refresh(); setFilesVersion(v => v + 1) }} />
          <BuildStep name={name} app={app} filesVersion={filesVersion} onBuilt={() => setFilesVersion(0)} />
        </>}
        <OpenStep />
      </div>
    </div>
  )
}

// src/pages/DevOps.jsx
// -----------------------------------------------------------------------------
// DevOps : déployer sans casser, revenir en arrière, Helm, intégration continue.
//
//   Déployer    aperçu (différences avec ce qui tourne, dry-run serveur, capacité,
//               sécurité) → confirmation → application → surveillance → retour
//               arrière automatique si casse → explication par l'IA
//   Historique  versions de chaque workload, différences, retour à une version
//   Helm        releases, historique, valeurs (secrets masqués), rollback, mise à jour
//   CI/CD       exécutions réelles du workflow GitHub Actions (.github/workflows/ci.yml)
//   Événements  événements Kubernetes récents
//
// Aucune donnée simulée : l'ancienne page (pipeline en partie inventé) est
// supprimée le 01/11/2026.
// STYLES : classes du design system et variables CSS.
// -----------------------------------------------------------------------------

import { useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Rocket, History, Package, GitBranch, ListChecks, RefreshCw, Eye, Undo2, Play,
  CheckCircle2, XCircle, AlertTriangle, Loader2, ExternalLink, ShieldAlert, ArrowUpCircle,
} from 'lucide-react'
import api from '../utils/api'

const V2 = '/devops/v2'
const errText = e => e?.response?.data?.detail || e?.message || String(e)
const fmtDate = s => (s ? new Date(s).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }) : '—')
const fmtDur = s => (s == null ? '—' : s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`)

// ── Petits composants communs ───────────────────────────────────────────────

function Diff({ text, max = 340 }) {
  if (!text) return <div className="text-xs text-muted">Aucune différence à afficher.</div>
  return (
    <pre className="code-block" style={{ fontSize: 12.5, maxHeight: max, overflow: 'auto', whiteSpace: 'pre', margin: 0 }}>
      {text.split('\n').map((l, i) => {
        const c = l.startsWith('+++') || l.startsWith('---') ? 'var(--text-muted)'
          : l.startsWith('+') ? 'var(--color-green)' : l.startsWith('-') ? 'var(--color-red)'
          : l.startsWith('@@') ? 'var(--color-purple-light)' : undefined
        return <div key={i} style={{ color: c }}>{l || ' '}</div>
      })}
    </pre>
  )
}

const STEP_ICON = {
  running: <Loader2 size={13} className="spin" style={{ color: 'var(--color-purple-light)' }} />,
  ok: <CheckCircle2 size={13} style={{ color: 'var(--color-green)' }} />,
  failed: <XCircle size={13} style={{ color: 'var(--color-red)' }} />,
  warning: <AlertTriangle size={13} style={{ color: 'var(--color-amber)' }} />,
}

const OUTCOME = {
  ok:            { cls: 'success',  title: 'Réussi : pods prêts et stables' },
  regression:    { cls: 'critical', title: 'Casse détectée' },
  inconclusive:  { cls: 'warning',  title: 'Non concluant : toujours en attente, sans preuve de casse' },
  apply_failed:  { cls: 'critical', title: 'Application refusée' },
  failed:        { cls: 'critical', title: 'Échec' },
  rolled_back:   { cls: 'warning',  title: 'Échec : Helm est revenu à la version précédente (--atomic)' },
  error:         { cls: 'critical', title: 'Erreur interne' },
}

// Suit une opération longue (déploiement, retour arrière, Helm) jusqu'à son verdict.
function JobProgress({ jobId, onDone }) {
  const [job, setJob] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    let stop = false, timer
    const tick = async () => {
      try {
        const { data } = await api.get(`${V2}/jobs/${jobId}`)
        if (stop) return
        setJob(data)
        if (data.state === 'done') { onDone?.(data); return }
      } catch (e) { if (!stop) setErr(errText(e)) }
      timer = setTimeout(tick, 1500)
    }
    tick()
    return () => { stop = true; clearTimeout(timer) }
  }, [jobId])   // eslint-disable-line react-hooks/exhaustive-deps

  if (err) return <div className="issue-item block critical text-xs">{err}</div>
  if (!job) return <div className="text-xs text-muted"><Loader2 size={12} className="spin" /> démarrage…</div>
  const o = job.state === 'done' ? (OUTCOME[job.outcome] || OUTCOME.error) : null
  return (
    <div style={{ marginTop: 10 }}>
      {(job.steps || []).map(s => (
        <div key={s.name} className="flex gap-sm" style={{ alignItems: 'flex-start', padding: '5px 0', borderBottom: '1px solid var(--border)' }}>
          <span style={{ marginTop: 2 }}>{STEP_ICON[s.status] || STEP_ICON.running}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="text-sm" style={{ fontWeight: 600 }}>{s.name} <span className="text-xs text-muted">· {s.t} s</span></div>
            {s.detail && <div className="text-xs text-muted" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{s.detail}</div>}
          </div>
        </div>
      ))}
      {o && (
        <div className={`issue-item block ${o.cls}`} style={{ marginTop: 10 }}>
          <div className="text-sm" style={{ fontWeight: 600 }}>{o.title}{job.duration_s != null && <span className="text-xs text-muted" style={{ fontWeight: 400 }}> · {fmtDur(job.duration_s)}</span>}</div>
          {job.reason && <div className="text-xs" style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{job.reason}</div>}
          {job.rollback && (
            <div className="text-xs" style={{ marginTop: 6 }}>
              <b>Retour arrière {job.rollback.ok ? 'effectué' : 'incomplet'} :</b> {job.rollback.actions.join(' ; ')}
            </div>
          )}
          {job.outcome === 'regression' && !job.rollback && (
            <div className="text-xs" style={{ marginTop: 6 }}>Retour arrière automatique désactivé : le cluster est resté dans l'état cassé.</div>
          )}
        </div>
      )}
      {job.service_warnings?.length > 0 && (
        <div className="issue-item block warning text-xs" style={{ marginTop: 8 }}>
          <b>Pods sains, mais service injoignable :</b> {job.service_warnings.join(' ; ')}
        </div>
      )}
      {job.explanation && (
        <div className="card" style={{ marginTop: 10, background: 'var(--bg-elevated)' }}>
          <div className="text-sm" style={{ fontWeight: 600, marginBottom: 6 }}>Explication de l'échec {job.explanation.model && <span className="badge badge-info">{job.explanation.model}</span>}</div>
          <div className="text-sm" style={{ whiteSpace: 'pre-wrap', lineHeight: 1.55 }}>{job.explanation.text}</div>
          <details style={{ marginTop: 8 }}><summary className="text-xs text-muted" style={{ cursor: 'pointer' }}>Preuves envoyées au modèle</summary>
            <pre className="code-block" style={{ fontSize: 10.5, maxHeight: 220, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{job.explanation.evidence}</pre>
          </details>
        </div>
      )}
    </div>
  )
}

function Confirm({ text, onYes, onNo, danger }) {
  return (
    <div className={`issue-item ${danger ? 'critical' : 'warning'} flex items-center gap-sm`} style={{ marginTop: 10, fontSize: 13, flexWrap: 'wrap' }}>
      <span style={{ flex: 1, minWidth: 220 }}>{text}</span>
      <button className={`btn btn-sm ${danger ? 'btn-danger' : 'btn-success'}`} onClick={onYes}>Oui, continuer</button>
      <button className="btn btn-ghost btn-sm" onClick={onNo}>Annuler</button>
    </div>
  )
}

// ── Onglet Déployer ─────────────────────────────────────────────────────────

const ACTION_BADGE = { 'création': 'badge-success', 'modification': 'badge-warning', 'inchangé': 'badge-neutral' }
const EXAMPLE = `apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-web
  labels: {app: demo-web}
spec:
  replicas: 2
  selector:
    matchLabels: {app: demo-web}
  template:
    metadata:
      labels: {app: demo-web}
    spec:
      containers:
        - name: web
          image: nginxinc/nginx-unprivileged:1.27
          ports: [{containerPort: 8080}]
          readinessProbe: {httpGet: {path: /, port: 8080}}
          resources:
            requests: {cpu: 50m, memory: 64Mi}
            limits: {cpu: 200m, memory: 128Mi}
---
apiVersion: v1
kind: Service
metadata:
  name: demo-web
spec:
  selector: {app: demo-web}
  ports: [{port: 80, targetPort: 8080}]
`

function DeployTab() {
  const location = useLocation()
  const qc = useQueryClient()
  const [text, setText] = useState(location.state?.yaml || '')
  const [ns, setNs] = useState(location.state?.namespace || 'default')
  const [prev, setPrev] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState({})
  const [auto, setAuto] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [jobId, setJobId] = useState(null)
  const { data: stats } = useQuery({ queryKey: ['deploy-stats'], queryFn: () => api.get(`${V2}/deploy/stats`).then(r => r.data) })

  const preview = async () => {
    setLoading(true); setError(null); setPrev(null); setJobId(null); setConfirming(false)
    try {
      const { data } = await api.post(`${V2}/deploy/preview`, { yaml: text, namespace: ns }, { timeout: 120000 })
      if (!data.ok) setError(data.error); else { setPrev(data); setOpen({ 0: true }) }
    } catch (e) { setError(errText(e)) }
    setLoading(false)
  }
  const apply = async () => {
    setConfirming(false)
    try {
      const { data } = await api.post(`${V2}/deploy/${prev.id}/apply`, { auto_rollback: auto })
      setJobId(data.id)
    } catch (e) { setError(errText(e)) }
  }

  const refused = prev?.server_validated === false
  const capErrors = (prev?.errors || []).filter(e => e.source === 'capacité')
  const otherErrors = (prev?.errors || []).filter(e => e.source !== 'capacité' && e.source !== 'serveur Kubernetes')
  const serverErrors = (prev?.errors || []).filter(e => e.source === 'serveur Kubernetes')
  const crit = prev?.summary?.['sécurité_critique'] || 0
  const risky = capErrors.length > 0 || crit > 0

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 1fr) minmax(360px, 1.2fr)', gap: 14, alignItems: 'start' }}>
      <div className="card">
        <div className="card-title">Manifeste à déployer</div>
        <textarea value={text} onChange={e => { setText(e.target.value); setPrev(null); setJobId(null) }}
          placeholder="Colle ici un manifeste YAML, ou envoie-le depuis la page Génération YAML…" spellCheck={false}
          style={{ width: '100%', minHeight: 360, fontFamily: 'var(--font-mono)', fontSize: 13, background: 'var(--bg-elevated)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 6, padding: 10, resize: 'vertical', boxSizing: 'border-box' }} />
        <div className="flex items-center gap-sm" style={{ marginTop: 10, flexWrap: 'wrap' }}>
          <label className="text-xs text-muted">Namespace par défaut</label>
          <input value={ns} onChange={e => setNs(e.target.value.trim())} className="text-mono"
            style={{ width: 140, background: 'var(--bg-elevated)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 6, padding: '4px 8px', fontSize: 13 }} />
          <div style={{ flex: 1 }} />
          {!text && <button className="btn btn-ghost btn-sm" onClick={() => setText(EXAMPLE)}>Exemple</button>}
          <button className="btn btn-primary btn-sm" onClick={preview} disabled={!text.trim() || loading}>
            {loading ? <><Loader2 size={12} className="spin" /> Vérification…</> : <><Eye size={12} /> Prévisualiser</>}
          </button>
        </div>
        <div className="text-xs text-muted" style={{ marginTop: 8, lineHeight: 1.5 }}>
          Rien n'est modifié avant ta confirmation. L'aperçu compare chaque objet à ce qui tourne (dry-run du serveur), vérifie la capacité réelle de la machine et la sécurité du manifeste.
        </div>
        {stats?.deployments > 0 && (
          <div className="flex gap-sm" style={{ marginTop: 12, flexWrap: 'wrap' }}>
            <span className="badge badge-neutral">{stats.deployments} déploiement(s)</span>
            {Object.entries(stats.by_outcome || {}).map(([k, v]) => <span key={k} className={`badge ${k === 'ok' ? 'badge-success' : k === 'regression' ? 'badge-danger' : 'badge-warning'}`}>{OUTCOME[k]?.title.split(' :')[0] || k} : {v}</span>)}
            <span className="badge badge-info">{stats.rollbacks_ok}/{stats.rollbacks} retours arrière automatiques réussis</span>
          </div>
        )}
      </div>

      <div>
        {error && <div className="issue-item block critical text-sm" style={{ whiteSpace: 'pre-wrap' }}>{error}</div>}
        {!prev && !error && (
          <div className="card text-sm text-muted" style={{ lineHeight: 1.7 }}>
            <Rocket size={16} style={{ verticalAlign: -3 }} /> <b className="text-primary">Déploiement sûr</b><br />
            1. Aperçu : ce qui sera créé, modifié, et si les pods redémarrent.<br />
            2. Application après confirmation.<br />
            3. Surveillance des nouveaux pods (une casse = preuve : CrashLoop, image introuvable, sonde en échec…).<br />
            4. En cas de casse : retour automatique à l'état d'avant, puis explication par l'IA à partir des événements et des logs.
          </div>
        )}
        {prev && (
          <div className="card">
            <div className="flex items-center gap-sm" style={{ flexWrap: 'wrap', marginBottom: 10 }}>
              {refused ? <span className="badge badge-danger">Refusé par le serveur</span>
                : prev.server_validated ? <span className="badge badge-success">Accepté par le serveur (dry-run)</span>
                : <span className="badge badge-warning">Serveur injoignable : non vérifié</span>}
              {['création', 'modification', 'inchangé'].map(k => prev.summary[k] > 0 && <span key={k} className={`badge ${ACTION_BADGE[k]}`}>{prev.summary[k]} {k}</span>)}
              {prev.summary['pods_redémarrés'] > 0 && <span className="badge badge-info">{prev.summary['pods_redémarrés']} workload(s) redémarre(nt) ses pods</span>}
              {crit > 0 && <span className="badge badge-danger"><ShieldAlert size={11} /> {crit} risque(s) critique(s)</span>}
            </div>
            {serverErrors.map((e, i) => <div key={i} className="issue-item block critical text-xs" style={{ whiteSpace: 'pre-wrap' }}><b>{e.category} :</b> {e.message}</div>)}
            {capErrors.map((e, i) => <div key={i} className="issue-item block critical text-xs"><b>Capacité — {e.category} :</b> {e.message}</div>)}
            {otherErrors.map((e, i) => <div key={i} className="issue-item block warning text-xs"><b>{e.category} :</b> {e.message}</div>)}
            {(prev.warnings || []).slice(0, 4).map((w, i) => <div key={i} className="text-xs text-muted">• {w}</div>)}

            <div className="card-title" style={{ marginTop: 12 }}>Plan</div>
            {prev.plan.map((p, i) => (
              <div key={i} style={{ borderTop: '1px solid var(--border)', padding: '6px 0' }}>
                <div className="flex items-center gap-sm" style={{ cursor: 'pointer' }} onClick={() => setOpen(o => ({ ...o, [i]: !o[i] }))}>
                  <span className={`badge ${ACTION_BADGE[p.action]}`}>{p.action}</span>
                  <span className="text-sm text-mono" style={{ flex: 1 }}>{p.object}</span>
                  {p.restarts_pods && <span className="badge badge-info">pods redémarrés</span>}
                  {p.current_revision && <span className="text-xs text-muted">rév. {p.current_revision}</span>}
                  <span className="text-xs text-muted">{open[i] ? '▾' : '▸'}</span>
                </div>
                {p.note && <div className="text-xs text-muted" style={{ marginTop: 4 }}>{p.note}</div>}
                {open[i] && <div style={{ marginTop: 6 }}><Diff text={p.diff} /></div>}
              </div>
            ))}

            {prev.security?.length > 0 && (
              <details style={{ marginTop: 10 }} open={crit > 0}>
                <summary className="text-sm" style={{ cursor: 'pointer', fontWeight: 600 }}>Sécurité du manifeste ({prev.security.length} constat(s))</summary>
                {prev.security.slice(0, 12).map((f, i) => (
                  <div key={i} className={`issue-item block ${f.severity === 'critical' ? 'critical' : f.severity === 'warning' ? 'warning' : 'info'} text-xs`} style={{ marginTop: 4 }}>
                    <b>{f.title}</b> — {f.object}{f.container ? ` · ${f.container}` : ''}{f.detail ? ` : ${f.detail}` : ''}
                  </div>
                ))}
              </details>
            )}

            {!jobId && !refused && (
              <>
                <label className="flex items-center gap-sm text-sm" style={{ marginTop: 12, cursor: 'pointer' }}>
                  <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} />
                  Retour arrière automatique si une casse est détectée
                </label>
                {!confirming
                  ? <button className="btn btn-success btn-sm" style={{ marginTop: 10 }} onClick={() => setConfirming(true)}><Play size={12} /> Déployer</button>
                  : <Confirm danger={risky}
                      text={risky ? `Attention : ${capErrors.length ? 'la capacité de la machine est dépassée' : ''}${capErrors.length && crit ? ' et ' : ''}${crit ? `${crit} risque(s) de sécurité critique(s)` : ''}. Déployer quand même ?`
                        : `Appliquer ${prev.plan.length} objet(s) sur le cluster${auto ? ', avec retour arrière automatique' : ', SANS retour arrière automatique'} ?`}
                      onYes={apply} onNo={() => setConfirming(false)} />}
              </>
            )}
            {jobId && <JobProgress jobId={jobId} onDone={() => qc.invalidateQueries({ queryKey: ['deploy-stats'] })} />}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Onglet Historique ───────────────────────────────────────────────────────

function Revisions({ wl }) {
  const key = `${wl.kind}/${wl.namespace}/${wl.name}`
  const { data, isLoading, refetch } = useQuery({ queryKey: ['revisions', key], queryFn: () => api.get(`${V2}/workloads/${key}/revisions`).then(r => r.data) })
  const [sel, setSel] = useState(null)
  const [diff, setDiff] = useState(null)
  const [rb, setRb] = useState(null)
  const [confirming, setConfirming] = useState(false)
  const [jobId, setJobId] = useState(null)

  const pick = async r => {
    setSel(r.revision); setDiff(null); setRb(null); setConfirming(false); setJobId(null)
    if (r.current) return
    const { data: d } = await api.get(`${V2}/workloads/${key}/diff`, { params: { a: data.current, b: r.revision } })
    setDiff(d)
  }
  const preview = async () => {
    const { data: p } = await api.post(`${V2}/workloads/${key}/rollback/preview`, { revision: sel }, { timeout: 60000 })
    setRb(p); if (p.ok) setConfirming(true)
  }
  const go = async () => {
    setConfirming(false)
    const { data: j } = await api.post(`${V2}/workloads/${key}/rollback`, { revision: sel })
    setJobId(j.id)
  }

  if (isLoading) return <div className="text-xs text-muted">Lecture des versions…</div>
  if (data?.error) return <div className="issue-item block critical text-xs">{data.error}</div>
  return (
    <div className="card" style={{ marginTop: 10, background: 'var(--bg-elevated)' }}>
      <div className="flex items-center gap-sm" style={{ marginBottom: 8 }}>
        <span className="text-sm" style={{ fontWeight: 600, flex: 1 }}>Versions de {wl.kind}/{wl.name}</span>
        <button className="btn btn-ghost btn-sm" onClick={() => refetch()}><RefreshCw size={11} /></button>
      </div>
      <table className="table" style={{ width: '100%', fontSize: 13 }}>
        <thead><tr><th>Révision</th><th>Créée</th><th>Images</th><th>Cause</th><th /></tr></thead>
        <tbody>
          {(data?.revisions || []).map(r => (
            <tr key={r.revision} onClick={() => pick(r)} style={{ background: sel === r.revision ? 'var(--bg-hover)' : undefined }}>
              <td className="text-mono">{r.revision} {r.current && <span className="badge badge-success">en cours</span>}</td>
              <td>{fmtDate(r.created)}</td>
              <td className="text-mono text-xs">{r.images.join(', ')}</td>
              <td className="text-xs text-muted">{r.cause || '—'}</td>
              <td className="text-xs text-muted">{r.current ? '' : 'comparer ▸'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data?.revisions?.length <= 1 && <div className="text-xs text-muted" style={{ marginTop: 6 }}>Une seule version connue : rien à comparer.</div>}
      {diff && (
        <div style={{ marginTop: 10 }}>
          <div className="text-xs text-muted" style={{ marginBottom: 4 }}>Ce qui changerait en revenant de la révision {data.current} à la {sel} :</div>
          <Diff text={diff.diff || diff.error} />
          {!jobId && !confirming && <button className="btn btn-ghost btn-sm" style={{ marginTop: 8 }} onClick={preview}><Undo2 size={12} /> Revenir à la révision {sel}</button>}
          {rb && !rb.ok && <div className="issue-item block critical text-xs" style={{ marginTop: 6 }}>{rb.error}</div>}
          {confirming && <Confirm text={`Revenir à la révision ${sel} (vérifié par le serveur) ? Les pods vont redémarrer.`} onYes={go} onNo={() => setConfirming(false)} />}
          {jobId && <JobProgress jobId={jobId} onDone={() => refetch()} />}
        </div>
      )}
    </div>
  )
}

function HistoryTab() {
  const [ns, setNs] = useState('')
  const [open, setOpen] = useState(null)
  const { data, isLoading, refetch } = useQuery({ queryKey: ['workloads', ns], queryFn: () => api.get(`${V2}/workloads`, { params: ns ? { namespace: ns } : {} }).then(r => r.data) })
  return (
    <div className="card">
      <div className="flex items-center gap-sm" style={{ marginBottom: 10 }}>
        <div className="card-title" style={{ margin: 0, flex: 1 }}>Workloads et leurs versions</div>
        <input value={ns} onChange={e => setNs(e.target.value.trim())} placeholder="namespace (tous)" className="text-mono"
          style={{ width: 160, background: 'var(--bg-elevated)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 6, padding: '4px 8px', fontSize: 13 }} />
        <button className="btn btn-ghost btn-sm" onClick={() => refetch()}><RefreshCw size={11} /></button>
      </div>
      {isLoading && <div className="text-xs text-muted">Chargement…</div>}
      {(data?.errors || []).map((e, i) => <div key={i} className="issue-item block warning text-xs">{e}</div>)}
      <table className="table" style={{ width: '100%', fontSize: 13 }}>
        <thead><tr><th>Type</th><th>Namespace</th><th>Nom</th><th>Révision</th><th>Images</th><th>Prêts</th></tr></thead>
        <tbody>
          {(data?.workloads || []).map(w => {
            const k = `${w.kind}/${w.namespace}/${w.name}`
            return [
              <tr key={k} onClick={() => setOpen(open === k ? null : k)}>
                <td>{w.kind}</td><td className="text-mono">{w.namespace}</td><td className="text-mono">{w.name}</td>
                <td className="text-mono">{w.revision ?? '—'}</td>
                <td className="text-mono text-xs">{w.images.join(', ')}</td>
                <td><span className={`badge ${w.healthy ? 'badge-success' : 'badge-danger'}`}>{w.ready}/{w.desired}</span></td>
              </tr>,
              open === k && <tr key={k + '-d'}><td colSpan={6} style={{ cursor: 'default' }}><Revisions wl={w} /></td></tr>,
            ]
          })}
        </tbody>
      </table>
      {data && !data.workloads?.length && <div className="text-sm text-muted">Aucun workload.</div>}
    </div>
  )
}

// ── Onglet Helm ─────────────────────────────────────────────────────────────

function HelmRelease({ rel }) {
  const base = `${V2}/helm/${rel.namespace}/${rel.name}`
  const { data: hist, refetch } = useQuery({ queryKey: ['helm-hist', base], queryFn: () => api.get(`${base}/history`).then(r => r.data) })
  const { data: vals } = useQuery({ queryKey: ['helm-vals', base], queryFn: () => api.get(`${base}/values`).then(r => r.data) })
  const [sel, setSel] = useState(null)
  const [view, setView] = useState(null)
  const [confirm, setConfirm] = useState(null)
  const [jobId, setJobId] = useState(null)
  const [up, setUp] = useState({ chart: '', version: '', set: '' })
  const cur = (hist?.history || []).find(h => h.status === 'deployed')?.revision

  const pickRev = async r => {
    setSel(r); setConfirm(null); setJobId(null)
    const { data } = await api.post(`${base}/rollback/preview`, { revision: Number(r) }, { timeout: 60000 })
    setView({ title: `Différence révision ${cur} → ${r}`, ...data })
  }
  const upPreview = async () => {
    setConfirm(null); setJobId(null)
    const body = { chart: up.chart.trim(), version: up.version.trim() || null, set: up.set.split('\n').map(s => s.trim()).filter(Boolean) }
    const { data } = await api.post(`${base}/upgrade/preview`, body, { timeout: 150000 })
    setView({ title: `Mise à jour vers ${body.chart}${body.version ? ' ' + body.version : ''}`, ...data, upgrade: body })
  }
  const run = async () => {
    const c = confirm; setConfirm(null)
    const { data } = c === 'rollback'
      ? await api.post(`${base}/rollback`, { revision: Number(sel) })
      : await api.post(`${base}/upgrade`, view.upgrade)
    if (data.ok === false) setView(v => ({ ...v, error: data.error })); else setJobId(data.id)
  }

  return (
    <div className="card" style={{ marginTop: 10, background: 'var(--bg-elevated)' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <div className="text-sm" style={{ fontWeight: 600, marginBottom: 6 }}>Historique</div>
          {hist?.error && <div className="issue-item block critical text-xs">{hist.error}</div>}
          {(hist?.history || []).map(h => (
            <div key={h.revision} className="flex items-center gap-sm text-xs" style={{ padding: '4px 0', borderBottom: '1px solid var(--border)' }}>
              <span className="text-mono" style={{ width: 28 }}>{h.revision}</span>
              <span className={`badge ${h.status === 'deployed' ? 'badge-success' : h.status === 'failed' ? 'badge-danger' : 'badge-neutral'}`}>{h.status}</span>
              <span className="text-muted" style={{ flex: 1 }}>{h.chart} · {h.description}</span>
              {h.status !== 'deployed' && <button className="btn btn-ghost btn-sm" onClick={() => pickRev(h.revision)}>comparer</button>}
            </div>
          ))}
        </div>
        <div>
          <div className="text-sm" style={{ fontWeight: 600, marginBottom: 6 }}>Valeurs saisies <span className="text-xs text-muted">(secrets masqués)</span></div>
          <pre className="code-block" style={{ fontSize: 12.5, maxHeight: 160, overflow: 'auto', margin: 0 }}>{vals?.values || vals?.error || '…'}</pre>
          <div className="text-sm" style={{ fontWeight: 600, margin: '10px 0 6px' }}><ArrowUpCircle size={13} style={{ verticalAlign: -2 }} /> Mettre à jour</div>
          <div className="flex gap-sm">
            <input placeholder="chart (ex. bitnami/nginx)" value={up.chart} onChange={e => setUp({ ...up, chart: e.target.value })} className="text-mono" style={inp} />
            <input placeholder="version (option.)" value={up.version} onChange={e => setUp({ ...up, version: e.target.value })} className="text-mono" style={{ ...inp, width: 110 }} />
          </div>
          <textarea placeholder={'--set, un par ligne\nex. replicaCount=2'} value={up.set} onChange={e => setUp({ ...up, set: e.target.value })} className="text-mono" style={{ ...inp, width: '100%', height: 54, marginTop: 6, boxSizing: 'border-box' }} />
          <button className="btn btn-ghost btn-sm" style={{ marginTop: 6 }} disabled={!up.chart.trim()} onClick={upPreview}><Eye size={12} /> Prévisualiser la mise à jour</button>
        </div>
      </div>
      {view && (
        <div style={{ marginTop: 12 }}>
          <div className="text-xs text-muted" style={{ marginBottom: 4 }}>{view.title}</div>
          {view.error ? <div className="issue-item block critical text-xs" style={{ whiteSpace: 'pre-wrap' }}>{view.error}</div> : <Diff text={view.diff} />}
          {!view.error && !jobId && !confirm && (
            view.upgrade
              ? <button className="btn btn-success btn-sm" style={{ marginTop: 8 }} onClick={() => setConfirm('upgrade')}><Play size={12} /> Mettre à jour</button>
              : <button className="btn btn-ghost btn-sm" style={{ marginTop: 8 }} onClick={() => setConfirm('rollback')}><Undo2 size={12} /> Revenir à la révision {sel}</button>
          )}
          {confirm && <Confirm text={confirm === 'rollback' ? `helm rollback ${rel.name} ${sel} — confirmer ?` : `helm upgrade ${rel.name} avec --atomic (retour automatique si échec) — confirmer ?`} onYes={run} onNo={() => setConfirm(null)} />}
          {jobId && <JobProgress jobId={jobId} onDone={() => refetch()} />}
        </div>
      )}
    </div>
  )
}

const inp = { flex: 1, background: 'var(--bg-elevated)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 6, padding: '4px 8px', fontSize: 13 }

function HelmTab() {
  const { data, isLoading, refetch } = useQuery({ queryKey: ['helm-releases'], queryFn: () => api.get(`${V2}/helm/releases`).then(r => r.data) })
  const [open, setOpen] = useState(null)
  if (isLoading) return <div className="card text-sm text-muted">Chargement…</div>
  if (data && !data.available) return (
    <div className="card text-sm" style={{ lineHeight: 1.7 }}>
      <b>Helm n'est pas installé sur la machine du backend.</b><br />
      <span className="text-muted">Dans le terminal Ubuntu (WSL) :</span>
      <pre className="code-block" style={{ fontSize: 13 }}>curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash</pre>
      <span className="text-muted">Puis recharger cette page.</span>
    </div>
  )
  return (
    <div className="card">
      <div className="flex items-center gap-sm" style={{ marginBottom: 10 }}>
        <div className="card-title" style={{ margin: 0, flex: 1 }}>Releases Helm</div>
        <button className="btn btn-ghost btn-sm" onClick={() => refetch()}><RefreshCw size={11} /></button>
      </div>
      {data?.error && <div className="issue-item block critical text-xs">{data.error}</div>}
      <table className="table" style={{ width: '100%', fontSize: 13 }}>
        <thead><tr><th>Release</th><th>Namespace</th><th>Chart</th><th>Version appli.</th><th>Révision</th><th>État</th><th>Mise à jour</th></tr></thead>
        <tbody>
          {(data?.releases || []).map(r => {
            const k = `${r.namespace}/${r.name}`
            return [
              <tr key={k} onClick={() => setOpen(open === k ? null : k)}>
                <td className="text-mono">{r.name}</td><td className="text-mono">{r.namespace}</td><td className="text-mono">{r.chart}</td>
                <td>{r.app_version}</td><td className="text-mono">{r.revision}</td>
                <td><span className={`badge ${r.status === 'deployed' ? 'badge-success' : 'badge-danger'}`}>{r.status}</span></td>
                <td className="text-xs text-muted">{(r.updated || '').slice(0, 16)}</td>
              </tr>,
              open === k && <tr key={k + '-d'}><td colSpan={7} style={{ cursor: 'default' }}><HelmRelease rel={r} /></td></tr>,
            ]
          })}
        </tbody>
      </table>
      {data && !data.releases?.length && !data.error && <div className="text-sm text-muted">Aucune release. Exemple : <span className="text-mono">helm install demo oci://registry-1.docker.io/bitnamicharts/nginx -n default</span></div>}
    </div>
  )
}

// ── Onglet CI/CD ────────────────────────────────────────────────────────────

const CONCL = { success: 'badge-success', failure: 'badge-danger', cancelled: 'badge-neutral', skipped: 'badge-neutral' }

function CiTab() {
  const { data, isLoading, refetch, isFetching } = useQuery({ queryKey: ['ci-runs'], queryFn: () => api.get(`${V2}/ci/runs`).then(r => r.data), refetchInterval: 60000 })
  if (isLoading) return <div className="card text-sm text-muted">Chargement…</div>
  if (!data?.configured) return (
    <div className="card text-sm" style={{ lineHeight: 1.7 }}>
      <b>Intégration continue non reliée.</b> Le workflow <span className="text-mono">.github/workflows/ci.yml</span> est prêt dans le projet (tests backend, construction du frontend, analyse Trivy).<br />
      <span className="text-muted">{data?.hint}</span>
    </div>
  )
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 14, alignItems: 'start' }}>
      <div className="card">
        <div className="flex items-center gap-sm" style={{ marginBottom: 10 }}>
          <div className="card-title" style={{ margin: 0, flex: 1 }}>Exécutions — <span className="text-mono">{data.repo}</span></div>
          {data.success_rate != null && <span className="badge badge-info">{Math.round(data.success_rate * 100)} % de réussite</span>}
          <button className="btn btn-ghost btn-sm" onClick={() => api.get(`${V2}/ci/runs`, { params: { refresh: true } }).then(() => refetch())}>
            <RefreshCw size={11} className={isFetching ? 'spin' : ''} />
          </button>
        </div>
        {data.error && <div className="issue-item block critical text-xs">{data.error}</div>}
        <table className="table" style={{ width: '100%', fontSize: 13 }}>
          <thead><tr><th>Résultat</th><th>Commit</th><th>Branche</th><th>Durée</th><th>Date</th><th /></tr></thead>
          <tbody>
            {(data.runs || []).map(r => (
              <tr key={r.id} onClick={() => window.open(r.url, '_blank', 'noopener')}>
                <td><span className={`badge ${r.status !== 'completed' ? 'badge-warning' : CONCL[r.conclusion] || 'badge-neutral'}`}>{r.status !== 'completed' ? r.status : r.conclusion}</span></td>
                <td><span className="text-mono">{r.commit}</span> <span className="text-xs text-muted">{r.message}</span></td>
                <td className="text-mono text-xs">{r.branch}</td>
                <td className="text-xs">{fmtDur(r.duration_s)}</td>
                <td className="text-xs text-muted">{fmtDate(r.created)}</td>
                <td><ExternalLink size={11} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!data.runs?.length && !data.error && <div className="text-sm text-muted">Aucune exécution : pousse le dépôt pour lancer la première.</div>}
      </div>
      <div className="card">
        <div className="card-title">Dernière exécution — jobs</div>
        {(data.latest_jobs || []).map(j => (
          <div key={j.name} style={{ marginBottom: 10 }}>
            <div className="flex items-center gap-sm">
              <span className={`badge ${CONCL[j.conclusion] || 'badge-warning'}`}>{j.conclusion || j.status}</span>
              <span className="text-sm" style={{ fontWeight: 600, flex: 1 }}>{j.name}</span>
              <span className="text-xs text-muted">{fmtDur(j.duration_s)}</span>
            </div>
            {j.steps.filter(s => s.conclusion && s.conclusion !== 'success' && s.conclusion !== 'skipped').map(s => (
              <div key={s.name} className="text-xs text-danger" style={{ marginLeft: 8 }}>✗ {s.name}</div>
            ))}
          </div>
        ))}
        {!data.latest_jobs?.length && <div className="text-xs text-muted">—</div>}
      </div>
    </div>
  )
}

// ── Onglet Événements ───────────────────────────────────────────────────────

function EventsTab() {
  const { data, refetch } = useQuery({ queryKey: ['events'], queryFn: () => api.get('/devops/events').then(r => r.data), refetchInterval: 30000 })
  return (
    <div className="card">
      <div className="flex items-center gap-sm" style={{ marginBottom: 10 }}>
        <div className="card-title" style={{ margin: 0, flex: 1 }}>Événements Kubernetes récents</div>
        <button className="btn btn-ghost btn-sm" onClick={() => refetch()}><RefreshCw size={11} /></button>
      </div>
      {(data?.events || []).slice(0, 30).map((e, i) => (
        <div key={i} className="flex gap-sm" style={{ padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
          <span className={`badge ${e.type === 'Warning' ? 'badge-warning' : 'badge-success'}`} style={{ alignSelf: 'flex-start' }}>{e.type}</span>
          <div style={{ flex: 1 }}>
            <div className="text-sm"><b>{e.reason}</b> · <span className="text-mono text-xs">{e.namespace}/{e.involved_object}</span></div>
            <div className="text-xs text-muted">{e.message}</div>
          </div>
          <span className="text-xs text-muted">{fmtDate(e.last_seen)}</span>
        </div>
      ))}
      {!data?.events?.length && <div className="text-sm text-muted">Aucun événement récent.</div>}
    </div>
  )
}

// ── Page ────────────────────────────────────────────────────────────────────

const TABS = [
  { id: 'deploy', label: 'Déployer', icon: Rocket, C: DeployTab },
  { id: 'history', label: 'Historique', icon: History, C: HistoryTab },
  { id: 'helm', label: 'Helm', icon: Package, C: HelmTab },
  { id: 'ci', label: 'CI/CD', icon: GitBranch, C: CiTab },
  { id: 'events', label: 'Événements', icon: ListChecks, C: EventsTab },
]

export default function DevOps() {
  const location = useLocation()
  const [tab, setTab] = useState(location.state?.yaml ? 'deploy' : (() => {
    try { return localStorage.getItem('aikube.devops.tab') || 'deploy' } catch { return 'deploy' }
  })())
  useEffect(() => { try { localStorage.setItem('aikube.devops.tab', tab) } catch { /* stockage indisponible */ } }, [tab])
  const Current = useMemo(() => (TABS.find(t => t.id === tab) || TABS[0]).C, [tab])
  return (
    <div style={{ padding: 20, overflowY: 'auto', height: '100vh' }}>
      <div style={{ marginBottom: 14 }}>
        <h1 className="page-title">Déploiement</h1>
        <p className="page-subtitle">Déploiement surveillé avec retour arrière automatique, historique des révisions, Helm et intégration continue.</p>
      </div>
      <div className="tabs" style={{ marginBottom: 14, width: 'fit-content' }}>
        {TABS.map(t => (
          <button key={t.id} className={`tab ${tab === t.id ? 'active' : ''}`} onClick={() => setTab(t.id)}>
            <t.icon size={13} style={{ verticalAlign: -2, marginRight: 5 }} />{t.label}
          </button>
        ))}
      </div>
      <Current />
    </div>
  )
}

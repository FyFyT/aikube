// src/pages/Resources.jsx
// -----------------------------------------------------------------------------
// Ressources en temps réel et recommandations de dimensionnement.
//
// Pour chaque conteneur : ce qu'il RÉSERVE (requests/limits) face à ce qu'il
// CONSOMME (p95 CPU, maximum mémoire, échantillons toutes les 30 s).
// Les recommandations sont déterministes et sourcées ; quand une croissance
// mémoire est détectée, la limite couvre la mémoire projetée (prédiction OOM)
// et la recommandation est signalée comme palliative.
//
// Application : vérification par le serveur (dry-run) puis `kubectl set
// resources`, après confirmation. Les namespaces système ne sont pas modifiables.
//
// STYLES : classes du design system et variables CSS.
// -----------------------------------------------------------------------------

import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Gauge, RefreshCw, ArrowRight, ShieldCheck, Rocket, AlertTriangle, Clock } from 'lucide-react'
import api from '../utils/api'

const STATUS = {
  oom_imminent:     { label: 'OOM imminent',     badge: 'badge-danger',  issue: 'critical' },
  sous_dimensionne: { label: 'Sous-dimensionné', badge: 'badge-danger',  issue: 'critical' },
  manquant:         { label: 'Limites absentes', badge: 'badge-warning', issue: 'warning'  },
  sur_dimensionne:  { label: 'Sur-dimensionné',  badge: 'badge-info',    issue: 'info'     },
  adapte:           { label: 'Adapté',           badge: 'badge-success', issue: 'success'  },
  observation:      { label: 'En observation',   badge: 'badge-neutral', issue: 'info'     },
}

const FILTERS = [
  { id: 'all',     label: 'Tous',            match: () => true },
  { id: 'risk',    label: 'À risque',        match: r => ['oom_imminent', 'sous_dimensionne'].includes(r.status) },
  { id: 'over',    label: 'Sur-dimensionnés', match: r => r.status === 'sur_dimensionne' },
  { id: 'missing', label: 'Limites absentes', match: r => r.status === 'manquant' },
  { id: 'ok',      label: 'Adaptés',         match: r => r.status === 'adapte' },
  { id: 'obs',     label: 'En observation',  match: r => r.status === 'observation' },
]

const cpu = v => (v == null ? '—' : `${v}m`)
const mem = v => (v == null ? '—' : v >= 1024 ? `${(v / 1024).toFixed(1)}Gi` : `${v}Mi`)
const pct = (a, b) => (b ? Math.min(100, Math.round((a / b) * 100)) : 0)
const ago = ts => {
  if (!ts) return 'jamais'
  const s = Math.round(Date.now() / 1000 - ts)
  return s < 60 ? `il y a ${s} s` : `il y a ${Math.round(s / 60)} min`
}

function Bar({ value, color = 'var(--color-teal)' }) {
  return (
    <div style={{ height: 6, borderRadius: 'var(--radius-full)', background: 'var(--bg-elevated)', overflow: 'hidden' }}>
      <div style={{ width: `${value}%`, height: '100%', background: color }} />
    </div>
  )
}

function Cell({ before, after, fmt }) {
  const changed = after != null && after !== before
  return (
    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
      <span className={changed ? 'text-muted' : 'text-primary'}>{fmt(before)}</span>
      {changed && (
        <>
          <ArrowRight size={11} style={{ margin: '0 4px', verticalAlign: -1, color: 'var(--text-muted)' }} />
          <span className="fw-500" style={{ color: after > (before ?? 0) ? 'var(--color-amber)' : 'var(--color-teal)' }}>{fmt(after)}</span>
        </>
      )}
    </td>
  )
}

function RecoCard({ r, onApplied }) {
  const s = STATUS[r.status] || STATUS.observation
  const [busy, setBusy] = useState(null)
  const [check, setCheck] = useState(null)
  const [done, setDone] = useState(null)

  const body = (dry) => ({
    kind: r.kind, namespace: r.namespace, name: r.workload, container: r.container,
    recommended: r.recommended, dry_run: dry, before: r.current, usage: r.usage, status: r.status,
  })
  const verify = async () => {
    setBusy('verify'); setDone(null)
    try { setCheck({ ok: true, ...(await api.post('/resources/apply', body(true))).data }) }
    catch (e) { setCheck({ ok: false, ...(e.response?.data?.detail || { error: e.message }) }) }
    setBusy(null)
  }
  const applyNow = async () => {
    if (!window.confirm(`Appliquer sur ${r.kind.toLowerCase()}/${r.workload} ?\nLes pods vont redémarrer progressivement.`)) return
    setBusy('apply')
    try { setDone({ ok: true, ...(await api.post('/resources/apply', body(false))).data }); onApplied() }
    catch (e) { setDone({ ok: false, ...(e.response?.data?.detail || { error: e.message }) }) }
    setBusy(null)
  }

  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <div className="flex justify-between items-center" style={{ gap: 10, flexWrap: 'wrap' }}>
        <div className="flex items-center gap-sm" style={{ minWidth: 0 }}>
          <span className={`badge ${s.badge}`}>{s.label}</span>
          <span className="text-primary fw-500 truncate">{r.kind.toLowerCase()}/{r.workload}</span>
          <span className="text-muted text-xs truncate">conteneur {r.container}, namespace {r.namespace}, {r.replicas} réplica{r.replicas > 1 ? 's' : ''}</span>
          {r.system && <span className="badge badge-neutral">système</span>}
        </div>
        {r.usage && (
          <span className="text-xs text-muted flex items-center gap-sm">
            <Clock size={11} /> {r.usage.samples} échantillons sur {r.usage.window_min} min, {r.usage.pods} pod(s)
          </span>
        )}
      </div>

      {r.oom && (
        <div className="issue-item critical" style={{ marginTop: 10, fontSize: 13 }}>
          Limite mémoire atteinte dans environ <b>{Math.max(1, Math.round(r.oom.eta_seconds / 60))} min</b> (pod {r.oom.pod}, +{r.oom.slope_mib_per_min} Mi/min, confiance {r.oom.confidence})
        </div>
      )}

      {r.usage && (
        <table className="table" style={{ marginTop: 10, fontSize: 13, tableLayout: 'fixed', width: '100%' }}>
          <thead>
            <tr><th style={{ width: '22%' }}></th><th style={{ textAlign: 'right' }}>CPU request</th><th style={{ textAlign: 'right' }}>CPU limit</th><th style={{ textAlign: 'right' }}>RAM request</th><th style={{ textAlign: 'right' }}>RAM limit</th></tr>
          </thead>
          <tbody>
            <tr>
              <td className="text-muted">Usage observé</td>
              <td style={{ textAlign: 'right' }}>p95 {cpu(r.usage.cpu_p95_m)}</td>
              <td style={{ textAlign: 'right' }} className="text-muted">max {cpu(r.usage.cpu_max_m)}</td>
              <td style={{ textAlign: 'right' }}>p95 {mem(r.usage.mem_p95_mib)}</td>
              <td style={{ textAlign: 'right' }}>max {mem(r.usage.mem_max_mib)}</td>
            </tr>
            <tr>
              <td className="text-muted">Réservé → recommandé</td>
              <Cell before={r.current.cpu_request_m} after={r.recommended?.cpu_request_m} fmt={cpu} />
              <Cell before={r.current.cpu_limit_m} after={r.recommended?.cpu_limit_m} fmt={cpu} />
              <Cell before={r.current.mem_request_mib} after={r.recommended?.mem_request_mib} fmt={mem} />
              <Cell before={r.current.mem_limit_mib} after={r.recommended?.mem_limit_mib} fmt={mem} />
            </tr>
          </tbody>
        </table>
      )}

      <div style={{ marginTop: 8 }}>
        {r.reasons.map((t, i) => (
          <div key={i} className="text-xs" style={{ color: i === 0 ? 'var(--text-secondary)' : 'var(--text-muted)', marginBottom: 2 }}>• {t}</div>
        ))}
      </div>

      {(r.gain_cpu_m > 0 || r.gain_mem_mib > 0) && (
        <div className="text-xs text-success" style={{ marginTop: 6 }}>
          Place rendue au cluster : {r.gain_cpu_m > 0 ? `${r.gain_cpu_m}m CPU ` : ''}{r.gain_mem_mib > 0 ? `${mem(r.gain_mem_mib)} RAM` : ''}
        </div>
      )}

      {r.applicable && (
        <div className="flex gap-sm items-center" style={{ marginTop: 10, flexWrap: 'wrap' }}>
          <button className="btn btn-ghost btn-sm" onClick={verify} disabled={!!busy}>
            <ShieldCheck size={12} /> {busy === 'verify' ? 'Vérification…' : 'Vérifier (dry-run serveur)'}
          </button>
          <button className="btn btn-success btn-sm" onClick={applyNow} disabled={!!busy || !check?.ok}>
            <Rocket size={12} /> {busy === 'apply' ? 'Application…' : 'Appliquer'}
          </button>
          {check && (
            <span className={`text-xs ${check.ok ? 'text-success' : 'text-danger'}`}>
              {check.ok ? 'Accepté par le serveur Kubernetes' : `Refusé : ${String(check.error).slice(0, 160)}`}
            </span>
          )}
        </div>
      )}
      {check?.command && (
        <div className="code-block" style={{ marginTop: 8, fontSize: 11.5, whiteSpace: 'pre-wrap' }}>{check.command}</div>
      )}
      {done && (
        <div className={`issue-item ${done.ok ? 'success' : 'critical'}`} style={{ marginTop: 8, fontSize: 13 }}>
          {done.ok ? `${done.output || 'Appliqué'} — ${done.note || ''}` : `Échec : ${done.error}`}
        </div>
      )}
    </div>
  )
}

export default function Resources() {
  const qc = useQueryClient()
  const [filter, setFilter] = useState('all')
  const [includeSystem, setIncludeSystem] = useState(false)

  const { data, isLoading, error, isFetching, refetch } = useQuery({
    queryKey: ['resources', includeSystem],
    queryFn: async () => (await api.get(`/resources/recommendations?include_system=${includeSystem}`, { timeout: 60000 })).data,
    refetchInterval: 30000,
  })
  const { data: actions } = useQuery({
    queryKey: ['resources-actions'],
    queryFn: async () => (await api.get('/resources/actions')).data,
    refetchInterval: 60000,
  })

  const recos = data?.recommendations || []
  const shown = useMemo(() => recos.filter(FILTERS.find(f => f.id === filter).match), [recos, filter])
  const s = data?.summary
  const c = data?.collector

  return (
    <div style={{ padding: 20, overflowY: 'auto', height: '100vh' }}>
      <div className="flex justify-between items-center" style={{ marginBottom: 16 }}>
        <div>
          <h1 className="page-title">Ressources et recommandations</h1>
          <p className="page-subtitle">Ressources réservées comparées à la consommation réelle. Chaque recommandation indique sa source et passe par le serveur avant application.</p>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw size={12} className={isFetching ? 'spin' : ''} /> Actualiser
        </button>
      </div>

      {error && <div className="issue-item critical" style={{ marginBottom: 12 }}>{error.response?.data?.detail || error.message}</div>}
      {isLoading && <div className="skeleton" style={{ height: 120, borderRadius: 'var(--radius-lg)' }} />}

      {s && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, marginBottom: 12 }}>
            <div className="metric-card">
              <div className="metric-label">CPU réservé / utilisé (p95)</div>
              <div className="metric-value">{cpu(s.reserved_cpu_m)}</div>
              <div style={{ margin: '8px 0 4px' }}><Bar value={pct(s.used_cpu_p95_m, s.reserved_cpu_m)} /></div>
              <div className="metric-sub">{cpu(s.used_cpu_p95_m)} réellement utilisés ({pct(s.used_cpu_p95_m, s.reserved_cpu_m)} %)</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">RAM réservée / utilisée (max)</div>
              <div className="metric-value">{mem(s.reserved_mem_mib)}</div>
              <div style={{ margin: '8px 0 4px' }}><Bar value={pct(s.used_mem_max_mib, s.reserved_mem_mib)} /></div>
              <div className="metric-sub">{mem(s.used_mem_max_mib)} réellement utilisés ({pct(s.used_mem_max_mib, s.reserved_mem_mib)} %)</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Place récupérable</div>
              <div className="metric-value text-success">{cpu(s.freeable_cpu_m)}</div>
              <div className="metric-sub">et {mem(s.freeable_mem_mib)} de RAM, sur {s.by_status.sur_dimensionne} conteneur(s)</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">À risque</div>
              <div className="metric-value" style={{ color: (s.by_status.oom_imminent + s.by_status.sous_dimensionne) ? 'var(--color-red)' : 'var(--text-primary)' }}>
                {s.by_status.oom_imminent + s.by_status.sous_dimensionne}
              </div>
              <div className="metric-sub">{s.by_status.oom_imminent} OOM imminent, {s.by_status.manquant} sans limites</div>
            </div>
          </div>

          <div className="text-xs text-muted flex items-center gap-sm" style={{ marginBottom: 12, flexWrap: 'wrap' }}>
            <span>Collecte toutes les {c.interval_s} s, dernier relevé {ago(c.last_sample)}, {c.series} séries suivies</span>
            {s.by_status.observation > 0 && <span>· {s.by_status.observation} conteneur(s) encore en observation ({c.min_samples} relevés requis)</span>}
            <span>· Méthode : {data.method.cpu} ; mémoire {data.method.memory}</span>
          </div>
          {c.error && (
            <div className="issue-item warning flex items-center gap-sm" style={{ marginBottom: 12, fontSize: 13 }}>
              <AlertTriangle size={14} /> Collecte impossible : {c.error}. Vérifie que metrics-server est actif (minikube addons enable metrics-server).
            </div>
          )}

          <div className="flex items-center gap-sm" style={{ marginBottom: 12, flexWrap: 'wrap' }}>
            <div className="tabs">
              {FILTERS.map(f => (
                <button key={f.id} className={`tab ${filter === f.id ? 'active' : ''}`} onClick={() => setFilter(f.id)}>
                  {f.label} <span className="text-muted">({recos.filter(f.match).length})</span>
                </button>
              ))}
            </div>
            <label className="text-xs text-muted flex items-center gap-sm" style={{ marginLeft: 'auto', cursor: 'pointer' }}>
              <input type="checkbox" checked={includeSystem} onChange={e => setIncludeSystem(e.target.checked)} />
              Inclure les namespaces système (lecture seule)
            </label>
          </div>

          {shown.length === 0 && <div className="card text-muted text-sm">Aucun conteneur dans cette catégorie.</div>}
          {shown.map(r => (
            <RecoCard key={`${r.namespace}/${r.kind}/${r.workload}/${r.container}`} r={r}
              onApplied={() => { qc.invalidateQueries({ queryKey: ['resources'] }); qc.invalidateQueries({ queryKey: ['resources-actions'] }) }} />
          ))}
        </>
      )}

      {actions?.actions?.length > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-title">Changements appliqués</div>
          <table className="table" style={{ fontSize: 12.5, width: '100%' }}>
            <thead><tr><th>Date</th><th>Workload</th><th>Motif</th><th style={{ textAlign: 'right' }}>RAM limit</th><th style={{ textAlign: 'right' }}>CPU request</th><th>Résultat</th></tr></thead>
            <tbody>
              {actions.actions.map((a, i) => (
                <tr key={i}>
                  <td className="text-muted">{a.ts?.replace('T', ' ').slice(0, 16)}</td>
                  <td>{a.kind?.toLowerCase()}/{a.name} · {a.container}</td>
                  <td>{STATUS[a.status]?.label || a.status || '—'}</td>
                  <td style={{ textAlign: 'right' }}>{mem(a.before?.mem_limit_mib)} → {mem(a.after?.mem_limit_mib)}</td>
                  <td style={{ textAlign: 'right' }}>{cpu(a.before?.cpu_request_m)} → {cpu(a.after?.cpu_request_m)}</td>
                  <td className={a.ok ? 'text-success' : 'text-danger'}>{a.ok ? 'appliqué' : 'échec'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

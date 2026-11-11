// src/pages/Security.jsx
// -----------------------------------------------------------------------------
// Cybersécurité : scanner de configuration + remédiation vérifiée.
//
// Le scan seul n'est pas une contribution (Kubescape, Trivy, Polaris le font).
// Ce qui est mesuré ici est la REMÉDIATION : correctif (déterministe ou proposé
// par l'IA) → dry-run serveur → application → contrôle de non-régression →
// retour arrière automatique en cas de casse → nouveau scan. Le taux de
// correction sans régression est affiché, par méthode.
//
// STYLES : classes du design system et variables CSS.
// -----------------------------------------------------------------------------

import { useMemo, useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldAlert, ShieldCheck, Wrench, Sparkles, RefreshCw, CheckCircle2, XCircle, Loader2, Undo2, FlaskConical } from 'lucide-react'
import api from '../utils/api'

const SEV = {
  critical: { label: 'Critique', badge: 'badge-danger' },
  warning:  { label: 'Avertissement', badge: 'badge-warning' },
  info:     { label: 'Durcissement', badge: 'badge-neutral' },
}
const STATE = {
  success:     { label: 'Corrigé, sans régression', cls: 'success' },
  rolled_back: { label: 'Régression détectée : retour arrière effectué', cls: 'warning' },
  failed:      { label: 'Échec', cls: 'critical' },
  inconclusive:{ label: "Non concluant : pods toujours en attente, sans signe de casse (machine lente). Annulé par prudence, non compté dans le taux", cls: 'info' },
}

function StepIcon({ status }) {
  if (status === 'done') return <CheckCircle2 size={13} color="var(--color-green)" />
  if (status === 'failed') return <XCircle size={13} color="var(--color-red)" />
  return <Loader2 size={13} className="pulse" color="var(--color-purple-light)" />
}

function Diff({ text }) {
  if (!text) return null
  return (
    <pre className="code-block" style={{ fontSize: 10.5, maxHeight: 260, overflow: 'auto', whiteSpace: 'pre', marginTop: 8 }}>
      {text.split('\n').map((l, i) => (
        <div key={i} style={{
          color: l.startsWith('+') && !l.startsWith('+++') ? 'var(--color-green)'
               : l.startsWith('-') && !l.startsWith('---') ? 'var(--color-red)' : 'var(--text-secondary)',
        }}>{l || ' '}</div>
      ))}
    </pre>
  )
}

function Remediation({ finding, method, onClose, onDone }) {
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const [polling, setPolling] = useState(false)
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    let alive = true
    api.post('/security/v2/remediation/preview', { finding_id: finding.id, method }, { timeout: 180000 })
      .then(r => alive && setJob(r.data)).catch(e => alive && setError(e.response?.data?.detail || e.message))
    return () => { alive = false }
  }, [finding.id, method])

  useEffect(() => {
    if (!polling || !job) return
    const t = setInterval(async () => {
      try {
        const { data } = await api.get(`/security/v2/remediation/${job.id}`, { timeout: 60000 })
        setJob(data)
        if (!['running'].includes(data.state)) { setPolling(false); onDone() }
      } catch { /* on réessaie */ }
    }, 1500)
    return () => clearInterval(t)
  }, [polling, job?.id])

  const apply = async () => {
    setConfirming(false)
    try {
      await api.post(`/security/v2/remediation/${job.id}/apply`, null, { timeout: 90000 })
      setJob(j => ({ ...j, state: 'running' })); setPolling(true)
    } catch (e) {
      if (!e.response) {
        // Pas de réponse (délai dépassé) : la tâche a peut-être démarré, on suit son état
        setJob(j => ({ ...j, state: 'running' })); setPolling(true)
      } else {
        setError(e.response?.data?.detail || e.message)
      }
    }
  }

  const final = job && STATE[job.state]
  return (
    <div className="card" style={{ marginTop: 10, background: 'var(--bg-elevated)' }}>
      <div className="flex justify-between items-center" style={{ marginBottom: 8 }}>
        <span className="text-sm fw-500 flex items-center gap-sm">
          {method === 'ai' ? <Sparkles size={13} /> : <Wrench size={13} />}
          {method === 'ai' ? 'Correctif proposé par l\'IA' : 'Correctif déterministe'}
          {job?.model && <span className="badge badge-info">{job.model}</span>}
        </span>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>Fermer</button>
      </div>
      {!job && !error && <div className="text-xs text-muted flex items-center gap-sm"><Loader2 size={12} className="pulse" /> {method === 'ai' ? "L'IA prépare un correctif, puis le serveur le valide… (10 à 40 s)" : 'Préparation et validation par le serveur…'}</div>}
      {error && <div className="issue-item critical text-xs">{typeof error === 'string' ? error : JSON.stringify(error)}</div>}
      {job && (
        <>
          {job.steps.map((s, i) => (
            <div key={i} className="flex items-center gap-sm text-xs" style={{ padding: '3px 0' }}>
              <StepIcon status={s.status} />
              <span className="text-primary">{s.name}</span>
              <span className="text-muted truncate" style={{ maxWidth: 520 }}>{s.detail}</span>
              <span className="text-muted" style={{ marginLeft: 'auto' }}>{s.t} s</span>
            </div>
          ))}
          {job.error && <div className="issue-item critical text-xs" style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>{job.error}</div>}
          {job.choices?.length > 0 && (
            <div style={{ marginTop: 8 }}>
              {job.choices.map((c, i) => <div key={i} className="text-xs text-muted">• <span className="text-secondary fw-500">{c.title}</span> — {c.explanation}</div>)}
              {(job.warnings || []).map((w, i) => <div key={i} className="text-xs text-warning">{w}</div>)}
            </div>
          )}
          <Diff text={job.diff} />
          {job.state === 'ready' && !confirming && (
            <button className="btn btn-success btn-sm" style={{ marginTop: 10 }} onClick={() => setConfirming(true)}>
              <ShieldCheck size={12} /> Appliquer avec contrôle de non-régression
            </button>
          )}
          {job.state === 'ready' && confirming && (
            <div className="issue-item warning flex items-center gap-sm" style={{ marginTop: 10, fontSize: 13, flexWrap: 'wrap' }}>
              <span>Appliquer sur <b>{finding.kind.toLowerCase()}/{finding.name}</b> ? Les pods vont redémarrer ; en cas de casse, retour arrière automatique.</span>
              <button className="btn btn-success btn-sm" onClick={apply}>Oui, appliquer</button>
              <button className="btn btn-ghost btn-sm" onClick={() => setConfirming(false)}>Annuler</button>
            </div>
          )}
          {job.state === 'running' && (
            <div className="text-xs text-muted flex items-center gap-sm" style={{ marginTop: 8 }}>
              <Loader2 size={12} className="pulse" /> En cours : déploiement puis surveillance des pods (jusqu'à 7 min sur une machine chargée).
            </div>
          )}
          {final && (
            <div className={`issue-item ${final.cls} flex items-center gap-sm`} style={{ marginTop: 10, fontSize: 13 }}>
              {['rolled_back', 'inconclusive'].includes(job.state) ? <Undo2 size={14} /> : job.state === 'success' ? <ShieldCheck size={14} /> : <XCircle size={14} />}
              <div>
                <div className="fw-500">{final.label}</div>
                {job.health?.reason && <div className="text-xs">{job.health.reason}</div>}
                {job.state === 'inconclusive' && <div className="text-xs">Relance le correctif quand la machine sera moins chargée.</div>}
                {job.state === 'rolled_back' && method !== 'ai' && <div className="text-xs">Essaie « Corriger avec l'IA » : elle peut adapter l'image ou les volumes au lieu d'imposer le réglage brut.</div>}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function FindingRow({ f, open, setOpen, onDone }) {
  const s = SEV[f.severity]
  const canAuto = f.fix_kind === 'auto'
  const canAi = ['auto', 'ai'].includes(f.fix_kind) && !['Namespace', 'RoleBinding', 'ClusterRoleBinding'].includes(f.kind)
  return (
    <div style={{ padding: '9px 0', borderTop: '1px solid var(--border)' }}>
      <div className="flex items-center gap-sm" style={{ flexWrap: 'wrap' }}>
        <span className={`badge ${s.badge}`}>{s.label} · {f.cvss}</span>
        <span className="text-sm fw-500 text-primary">{f.title}</span>
        {f.container && <span className="text-xs text-muted text-mono">[{f.container}]</span>}
        <span className="text-xs text-muted">{f.detail}</span>
      </div>
      <div className="flex items-center gap-sm" style={{ marginTop: 4, flexWrap: 'wrap' }}>
        <span className="badge badge-info">{f.technique}</span>
        <span className="text-xs text-muted">{f.why}</span>
      </div>
      <div className="flex items-center gap-sm" style={{ marginTop: 6, flexWrap: 'wrap' }}>
        <span className="text-xs text-secondary">Correctif : {f.fix_desc}</span>
        {f.fix_note && <span className="text-xs text-warning">{f.fix_note}</span>}
        <span style={{ marginLeft: 'auto' }} className="flex gap-sm">
          {canAuto && <button className="btn btn-ghost btn-sm" onClick={() => setOpen({ id: f.id, method: 'auto' })}><Wrench size={11} /> Corriger</button>}
          {canAi && <button className="btn btn-ghost btn-sm" onClick={() => setOpen({ id: f.id, method: 'ai' })}><Sparkles size={11} /> Corriger avec l'IA</button>}
          {f.fix_kind === 'manual' && <span className="badge badge-neutral">correction manuelle</span>}
        </span>
      </div>
      {open?.id === f.id && <Remediation key={`${f.id}-${open.method}`} finding={f} method={open.method} onClose={() => setOpen(null)} onDone={onDone} />}
    </div>
  )
}

function Findings({ scan, ns, onDone }) {
  const [sev, setSev] = useState({ critical: true, warning: true, info: false })
  const [open, setOpen] = useState(null)
  const inNs = useMemo(() => (scan?.findings || []).filter(f => !ns || f.namespace === ns), [scan, ns])
  const counts = useMemo(() => ({
    critical: inNs.filter(f => f.severity === 'critical').length,
    warning: inNs.filter(f => f.severity === 'warning').length,
    info: inNs.filter(f => f.severity === 'info').length,
  }), [inNs])
  const groups = useMemo(() => {
    const g = new Map()
    for (const f of scan?.findings || []) {
      if (!sev[f.severity] || (ns && f.namespace !== ns)) continue
      const k = `${f.kind}|${f.namespace}|${f.name}`
      if (!g.has(k)) g.set(k, [])
      g.get(k).push(f)
    }
    return [...g.entries()]
  }, [scan, sev, ns])

  return (
    <>
      <div className="flex items-center gap-sm" style={{ marginBottom: 10, flexWrap: 'wrap' }}>
        {Object.entries(SEV).map(([k, v]) => (
          <button key={k} className={`btn btn-sm ${sev[k] ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setSev(s => ({ ...s, [k]: !s[k] }))}>
            {v.label} ({counts[k]})
          </button>
        ))}
      </div>
      {groups.length === 0 && <div className="card text-sm text-muted">Aucun constat avec ces filtres.</div>}
      {groups.map(([k, fs]) => {
        const [kind, nsName, name] = k.split('|')
        return (
          <div key={k} className="card" style={{ marginBottom: 10 }}>
            <div className="flex items-center gap-sm">
              <ShieldAlert size={14} color={fs.some(f => f.severity === 'critical') ? 'var(--color-red)' : 'var(--color-amber)'} />
              <span className="text-primary fw-500">{kind.toLowerCase()}/{name}</span>
              <span className="text-xs text-muted">{nsName}</span>
              <span className="text-xs text-muted" style={{ marginLeft: 'auto' }}>{fs.length} constat(s)</span>
            </div>
            {fs.map(f => <FindingRow key={f.id} f={f} open={open} setOpen={setOpen} onDone={onDone} />)}
          </div>
        )
      })}
    </>
  )
}

function Stats({ stats }) {
  if (!stats?.attempts) return <div className="card text-sm text-muted">Aucune remédiation appliquée pour l'instant. Déploie le banc <span className="text-mono">k8s/benchmark/remediation-benchmark.yaml</span> pour mesurer.</div>
  const row = (label, s) => (
    <tr key={label}>
      <td>{label}</td><td style={{ textAlign: 'right' }}>{s.attempts}</td>
      <td style={{ textAlign: 'right' }} className="text-success">{s.fixed_no_regression}</td>
      <td style={{ textAlign: 'right' }} className="text-warning">{s.regressions}</td>
      <td style={{ textAlign: 'right' }}>{s.rolled_back}</td>
      <td style={{ textAlign: 'right' }} className="fw-500">{s.rate != null ? `${Math.round(s.rate * 100)} %` : '—'}</td>
    </tr>
  )
  const u = stats.unique_cases
  return (
    <div className="card">
      <div className="card-title">Taux de remédiation sans régression</div>
      {u && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 8, marginBottom: 12 }}>
          <div className="metric-card"><div className="metric-label">Un résultat par cas</div><div className="metric-value text-success">{Math.round(u.rate * 100)} %</div><div className="metric-sub">{u.fixed_no_regression}/{u.attempts} cas (dernière tentative)</div></div>
          {Object.entries(stats.by_method_unique || {}).map(([m, v]) => (
            <div key={m} className="metric-card"><div className="metric-label">{m === 'ia' ? 'IA' : 'Correctif déterministe'}</div><div className="metric-value">{Math.round(v.rate * 100)} %</div><div className="metric-sub">{v.fixed_no_regression}/{v.attempts} cas · {v.regressions} régression(s)</div></div>
          ))}
          <div className="metric-card"><div className="metric-label">Non concluants</div><div className="metric-value">{stats.inconclusive || 0}</div><div className="metric-sub">exclus du taux{stats.requalified ? ` · dont ${stats.requalified} requalifiés` : ''}</div></div>
        </div>
      )}
      <table className="table" style={{ width: '100%', fontSize: 13 }}>
        <thead><tr><th></th><th style={{ textAlign: 'right' }}>Tentatives</th><th style={{ textAlign: 'right' }}>Corrigées sans régression</th><th style={{ textAlign: 'right' }}>Régressions</th><th style={{ textAlign: 'right' }}>Retours arrière</th><th style={{ textAlign: 'right' }}>Taux</th></tr></thead>
        <tbody>
          {row('Total', stats)}
          {Object.entries(stats.by_method || {}).map(([m, s]) => row(m === 'ia' ? 'Méthode : IA' : 'Méthode : déterministe', s))}
          {Object.entries(stats.by_check || {}).map(([c, s]) => row(`Défaut : ${c}`, s))}
        </tbody>
      </table>
      <div className="text-xs text-muted" style={{ marginTop: 8 }}>
        Le tableau compte toutes les tentatives (répétitions comprises). Réussite = le constat a disparu au nouveau scan ET les pods sont restés sains. Régression = preuve de casse (CrashLoop, redémarrage, arrêt en erreur, ou démarré mais jamais prêt après 90 s). Un pod seulement LENT à démarrer donne « non concluant », exclu du taux ; les deux fausses alertes de l'ancienne règle (délai fixe) sont requalifiées ainsi. Limite : le contrôle observe la santé des pods, pas le comportement métier.
      </div>
    </div>
  )
}

function Benchmark() {
  const [res, setRes] = useState(null)
  const [loading, setLoading] = useState(false)
  const run = async () => {
    setLoading(true)
    try { setRes((await api.get('/security/v2/benchmark', { timeout: 90000 })).data) } catch (e) { setRes({ error: e.message }) }
    setLoading(false)
  }
  return (
    <div className="card">
      <div className="flex justify-between items-center">
        <div className="card-title" style={{ margin: 0 }}>Évaluation sur le banc étiqueté (vuln-benchmark.yaml)</div>
        <button className="btn btn-ghost btn-sm" onClick={run} disabled={loading}><FlaskConical size={12} /> {loading ? 'Évaluation…' : 'Évaluer'}</button>
      </div>
      {res?.error && <div className="issue-item critical text-xs" style={{ marginTop: 8 }}>{res.error}</div>}
      {res && !res.error && (
        <>
          {res.resources === 0 && <div className="text-xs text-warning" style={{ marginTop: 8 }}>Banc non déployé : kubectl apply -f k8s/benchmark/vuln-benchmark.yaml</div>}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 8, margin: '10px 0' }}>
            {[['Précision', res.precision], ['Rappel', res.recall], ['F1', res.f1]].map(([l, v]) => (
              <div key={l} className="metric-card"><div className="metric-label">{l}</div><div className="metric-value">{v ?? '—'}</div></div>
            ))}
            <div className="metric-card"><div className="metric-label">VP / FN / FP / VN</div><div className="metric-value" style={{ fontSize: 16 }}>{res.tp} / {res.fn} / {res.fp} / {res.tn}</div></div>
          </div>
          <table className="table" style={{ width: '100%', fontSize: 12.5 }}>
            <thead><tr><th>Ressource</th><th>Attendu</th><th>Défaut injecté</th><th>Ancien scanner (prédit)</th><th>Nouveau scanner</th></tr></thead>
            <tbody>
              {res.details.map((d, i) => (
                <tr key={i}>
                  <td className="text-mono">{d.kind.toLowerCase()}/{d.name}</td>
                  <td>{d.expected}</td><td className="text-muted">{d.type}</td>
                  <td>{d.expected === 'vulnerable' ? (d.predicted_old ? '✓ détecté' : '✗ angle mort') : '—'}</td>
                  <td className={(d.expected === 'vulnerable') === d.detected ? 'text-success' : 'text-danger'}>
                    {d.detected ? `détecté (${d.checks.join(', ')})` : 'non signalé'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="text-xs text-warning" style={{ marginTop: 8 }}>
            Réserve méthodologique : les contrôles ont été écrits en connaissant ce banc (même circularité que le détecteur v3). Une validation indépendante exige un banc conçu par un tiers.
          </div>
        </>
      )}
    </div>
  )
}

export default function Security() {
  const qc = useQueryClient()
  const [tab, setTab] = useState('findings')
  const [includeSystem, setIncludeSystem] = useState(false)
  const { data: scan, isFetching, error, refetch } = useQuery({
    queryKey: ['sec-v2', includeSystem],
    queryFn: async () => (await api.get(`/security/v2/scan?include_system=${includeSystem}`, { timeout: 90000 })).data,
    refetchInterval: 120000,
  })
  const { data: stats } = useQuery({
    queryKey: ['sec-stats'],
    queryFn: async () => (await api.get('/security/v2/remediation-stats')).data,
    refetchInterval: 60000,
  })
  const [ns, setNs] = useState('')
  const namespaces = useMemo(() => [...new Set((scan?.findings || []).map(f => f.namespace))].sort(), [scan])
  const s = useMemo(() => {
    if (!scan) return null
    if (!ns) return scan.summary
    // Résumé recalculé sur le namespace choisi (même formule que le backend)
    const fs = scan.findings.filter(f => f.namespace === ns)
    const serious = fs.filter(f => f.severity !== 'info')
    const risk = serious.length ? Math.min(100, Math.round(Math.max(...serious.map(f => f.cvss)) * 10 + 2 * (serious.length - 1))) : 0
    // Tous les workloads du namespace, y compris ceux qui n'ont plus aucun constat
    const wl = new Set((scan.workload_ids || []).filter(id => id.split('|')[1] === ns).map(id => { const [k, , n] = id.split('|'); return `${k}/${n}` }))
    const bad = new Set(serious.filter(f => !['Namespace', 'RoleBinding', 'ClusterRoleBinding'].includes(f.kind)).map(f => `${f.kind}/${f.name}`))
    const crit = new Set(fs.filter(f => f.severity === 'critical' && !['Namespace', 'RoleBinding', 'ClusterRoleBinding'].includes(f.kind)).map(f => `${f.kind}/${f.name}`))
    return {
      ...scan.summary, risk_score: risk,
      risk_grade: risk < 20 ? 'A' : risk < 40 ? 'B' : risk < 60 ? 'C' : 'D',
      critical: fs.filter(f => f.severity === 'critical').length,
      warning: fs.filter(f => f.severity === 'warning').length,
      info: fs.filter(f => f.severity === 'info').length,
      workloads_clean_pct: wl.size ? Math.round(100 * (wl.size - bad.size) / wl.size) : 100,
      workloads_without_critical_pct: wl.size ? Math.round(100 * (wl.size - crit.size) / wl.size) : 100,
      auto_fixable: serious.filter(f => f.fix_kind === 'auto').length,
      techniques: [...new Set(serious.map(f => f.technique).filter(t => t.startsWith('T')))].sort(),
      _workloads: wl.size,
    }
  }, [scan, ns])
  const onDone = () => { qc.invalidateQueries({ queryKey: ['sec-v2'] }); qc.invalidateQueries({ queryKey: ['sec-stats'] }) }

  return (
    <div style={{ padding: 20, overflowY: 'auto', height: '100vh' }}>
      <div className="flex justify-between items-center" style={{ marginBottom: 16 }}>
        <div>
          <h1 className="page-title">Sécurité</h1>
          <p className="page-subtitle">Défauts de configuration classés selon MITRE ATT&CK for Containers, et remédiation vérifiée avec retour arrière automatique.</p>
        </div>
        <div className="flex items-center gap-sm">
          <select className="input" style={{ width: 230 }} value={ns} onChange={e => setNs(e.target.value)}>
            <option value="">Tous les namespaces</option>
            {namespaces.map(n => <option key={n} value={n}>{n}</option>)}
          </select>
          <label className="text-xs text-muted flex items-center gap-sm" style={{ whiteSpace: 'nowrap' }}><input type="checkbox" checked={includeSystem} onChange={e => setIncludeSystem(e.target.checked)} /> namespaces système</label>
          <button className="btn btn-ghost btn-sm" onClick={() => refetch()} disabled={isFetching}><RefreshCw size={12} className={isFetching ? 'spin' : ''} /> Scanner</button>
        </div>
      </div>

      {error && <div className="issue-item critical" style={{ marginBottom: 12 }}>{error.response?.data?.detail || error.message}</div>}
      {!scan && !error && <div className="skeleton" style={{ height: 110, borderRadius: 'var(--radius-lg)' }} />}

      {s && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10, marginBottom: 14 }}>
          <div className="metric-card"><div className="metric-label">Score de risque</div><div className="metric-value" style={{ color: s.risk_score >= 60 ? 'var(--color-red)' : s.risk_score >= 40 ? 'var(--color-amber)' : 'var(--color-green)' }}>{s.risk_score}</div><div className="metric-sub">grade {s.risk_grade} · max CVSS + accumulation</div></div>
          <div className="metric-card"><div className="metric-label">Critiques</div><div className="metric-value text-danger">{s.critical}</div><div className="metric-sub">{s.warning} avertissements</div></div>
          <div className="metric-card"><div className="metric-label">Workloads sans défaut</div><div className="metric-value">{s.workloads_clean_pct} %</div><div className="metric-sub">{s.workloads_without_critical_pct} % sans critique · {s._workloads ?? scan.workloads} analysés{ns ? ` (${ns})` : ''}</div></div>
          <div className="metric-card"><div className="metric-label">Corrigeables en un clic</div><div className="metric-value">{s.auto_fixable}</div><div className="metric-sub">les autres : IA ou manuel</div></div>
          <div className="metric-card"><div className="metric-label">Remédiations sans régression</div><div className="metric-value text-success">{stats?.unique_cases ? `${Math.round(stats.unique_cases.rate * 100)} %` : '—'}</div><div className="metric-sub">{stats?.unique_cases ? `${stats.unique_cases.fixed_no_regression}/${stats.unique_cases.attempts} cas · ${stats.inconclusive || 0} non concluant(s)` : 'aucune mesure'}</div></div>
        </div>
      )}

      {s?.techniques?.length > 0 && (
        <div className="flex gap-sm" style={{ flexWrap: 'wrap', marginBottom: 12 }}>
          {s.techniques.map(t => <span key={t} className="badge badge-danger">{t}</span>)}
        </div>
      )}

      <div className="tabs" style={{ marginBottom: 12 }}>
        {[['findings', 'Constats'], ['stats', 'Remédiations'], ['bench', "Banc d'évaluation"]].map(([id, l]) => (
          <button key={id} className={`tab ${tab === id ? 'active' : ''}`} onClick={() => setTab(id)}>{l}</button>
        ))}
      </div>
      {tab === 'findings' && scan && <Findings scan={scan} ns={ns} onDone={onDone} />}
      {tab === 'stats' && <Stats stats={stats} />}
      {tab === 'bench' && <Benchmark />}
    </div>
  )
}

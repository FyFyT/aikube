// src/pages/YAMLGenerator.jsx
// -----------------------------------------------------------------------------
// Génération de manifestes vérifiée.
//
// 1. La page lit d'abord la machine hôte et le cluster (/ai/host-context) :
//    capacité libre par nœud, versions d'API servies, namespaces, stockage.
// 2. Le modèle génère à partir de ce contexte réel.
// 3. Deux arbitres indépendants du modèle tranchent :
//      · le serveur Kubernetes (kubectl apply --dry-run=server)
//      · le contrôle de capacité (les requests tiennent-elles sur un nœud ?)
//    En cas de rejet, l'erreur est renvoyée au modèle, jusqu'à 3 tentatives.
// 4. Le YAML reste éditable ; toute modification doit être revalidée avant
//    déploiement.
//
// Plus de « confiance 94 % » déclarée par le modèle : le seul verdict affiché
// est celui des arbitres.
//
// STYLES : classes du design system et variables CSS.
// -----------------------------------------------------------------------------

import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Editor from '@monaco-editor/react'
import {
  Server, Cpu, HardDrive, RefreshCw, Sparkles, CheckCircle2, XCircle,
  AlertTriangle, Copy, Rocket, ShieldCheck, ListChecks,
} from 'lucide-react'
import api from '../utils/api'
import { useTheme } from '../utils/ThemeContext'

const SUGGESTIONS = [
  'Déployer nginx avec 3 replicas et limite 512Mi RAM, exposé sur le port 80',
  'API Flask (python:3.12-slim) 2 replicas avec HPA si CPU > 70 %',
  'StatefulSet PostgreSQL 16 avec volume persistant de 5Gi',
  'NetworkPolicy deny-all pour le namespace production',
  'CronJob de sauvegarde quotidien à 2 h du matin',
  'Redis avec la plus grande mémoire possible sur ce cluster',
]

const MODELS = [
  { id: 'auto',    label: 'Auto (Claude)' },
  { id: 'claude',  label: 'Claude' },
  { id: 'groq',    label: 'Groq gpt-oss-120b' },
  { id: 'kubellm', label: 'KubeLLM v2 local (fine-tuné)' },
  { id: 'kubellm-v3', label: 'KubeLLM v3 local (capacité + corrections)' },
  { id: 'kubellm-v4', label: 'KubeLLM v4 local (ses propres erreurs corrigées)' },
  { id: 'kubellm-base', label: 'Qwen2.5-Coder 1.5B local (témoin)' },
  { id: 'kubellm-v1', label: 'KubeLLM v1 (ancien, phi-2)' },
]

const pct = (part, whole) => (whole ? Math.min(100, Math.round((part / whole) * 100)) : 0)
const sec = (ms) => (ms == null ? '—' : `${(ms / 1000).toFixed(1)} s`)

function Bar({ value, danger = 85, warn = 65 }) {
  const color = value >= danger ? 'var(--color-red)' : value >= warn ? 'var(--color-amber)' : 'var(--color-teal)'
  return (
    <div style={{ height: 5, borderRadius: 'var(--radius-full)', background: 'var(--bg-elevated)', overflow: 'hidden' }}>
      <div style={{ width: `${value}%`, height: '100%', background: color, transition: 'width var(--transition-slow)' }} />
    </div>
  )
}

function Row({ label, value, sub }) {
  return (
    <div className="flex justify-between items-center" style={{ fontSize: 12.5, padding: '4px 0', borderBottom: '1px solid var(--border)' }}>
      <span className="text-muted">{label}</span>
      <span style={{ textAlign: 'right' }}>
        <span className="text-primary fw-500">{value}</span>
        {sub && <span className="text-muted" style={{ marginLeft: 6 }}>{sub}</span>}
      </span>
    </div>
  )
}

// ── Panneau « ce que l'IA sait de ta machine » ───────────────────────────────
function HostPanel({ ctx, loading, onRefresh }) {
  const h = ctx?.host
  const c = ctx?.cluster
  const errs = ctx?.errors ? Object.entries(ctx.errors) : []
  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <div className="flex justify-between items-center" style={{ marginBottom: 12 }}>
        <div className="card-title flex items-center gap-sm" style={{ margin: 0 }}>
          <Server size={14} /> Lecture de la machine hôte et du cluster
          {ctx && <span className="badge badge-neutral">lu en {ctx.collect_ms} ms</span>}
        </div>
        <button className="btn btn-ghost btn-sm" onClick={onRefresh} disabled={loading}>
          <RefreshCw size={12} className={loading ? 'pulse' : ''} /> Relire
        </button>
      </div>

      {!ctx && <div className="skeleton" style={{ height: 90, borderRadius: 'var(--radius-md)' }} />}

      {ctx && (
        <div className="grid-3" style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr) minmax(0,1.4fr)', gap: 12 }}>
          {/* Machine hôte */}
          <div>
            <div className="metric-label"><HardDrive size={11} /> Machine hôte</div>
            {h ? (
              <>
                <Row label="Système" value={h.wsl ? 'WSL' : h.os?.split(' ')[0]} sub={h.hostname} />
                <Row label="CPU" value={`${h.cpu_count} cœurs`} sub={h.load_avg_1m != null ? `charge ${h.load_avg_1m}` : null} />
                <Row label="RAM disponible" value={`${(h.ram_available_mib / 1024).toFixed(1)} Gio`} sub={`/ ${(h.ram_total_mib / 1024).toFixed(1)}`} />
                <div style={{ margin: '6px 0' }}><Bar value={pct(h.ram_total_mib - h.ram_available_mib, h.ram_total_mib)} /></div>
                <Row label="Disque libre" value={`${h.disk_free_gib} Gio`} sub={`/ ${h.disk_total_gib}`} />
              </>
            ) : <div className="text-muted text-xs">indisponible</div>}
          </div>

          {/* Cluster */}
          <div>
            <div className="metric-label"><Cpu size={11} /> Cluster</div>
            <Row label="Kubernetes" value={c?.server_version || '—'} />
            <Row label="Nœuds planifiables" value={c?.schedulable_nodes ?? '—'} />
            <Row label="CPU libre (réservable)" value={`${c?.total_cpu_free_m ?? '—'}m`} />
            <Row label="RAM libre (réelle)" value={`${c?.total_mem_free_mib ?? '—'}Mi`}
                 sub={c?.local_cluster && c?.declared_mem_free_mib !== c?.total_mem_free_mib ? `annoncé ${c?.declared_mem_free_mib}Mi` : null} />
            <Row label="Versions d'API lues" value={Object.keys(ctx.api_kinds || {}).length} sub="kinds" />
            <Row label="Risques OOM prédits" value={ctx.oom_risks?.length || 0} />
          </div>

          {/* Nœuds */}
          <div>
            <div className="metric-label">Réservé / allouable par nœud</div>
            {(c?.nodes || []).map(n => (
              <div key={n.name} style={{ marginBottom: 8 }}>
                <div className="flex justify-between" style={{ fontSize: 12.5, marginBottom: 3 }}>
                  <span className="text-primary fw-500 truncate">{n.name}</span>
                  <span className="text-muted">
                    {n.ready && n.schedulable ? `libre ${n.cpu_free_m}m · ${n.mem_free_mib}Mi` : 'non planifiable'}
                  </span>
                </div>
                <div className="flex gap-sm items-center" style={{ fontSize: 11.5 }}>
                  <span className="text-muted" style={{ width: 28 }}>CPU</span>
                  <div style={{ flex: 1 }}><Bar value={pct(n.cpu_requested_m, n.cpu_allocatable_m)} /></div>
                  <span className="text-muted" style={{ width: 30, textAlign: 'right' }}>{pct(n.cpu_requested_m, n.cpu_allocatable_m)}%</span>
                </div>
                <div className="flex gap-sm items-center" style={{ fontSize: 11.5, marginTop: 2 }}>
                  <span className="text-muted" style={{ width: 28 }}>RAM</span>
                  <div style={{ flex: 1 }}><Bar value={pct(n.mem_requested_mib, n.mem_allocatable_mib)} /></div>
                  <span className="text-muted" style={{ width: 30, textAlign: 'right' }}>{pct(n.mem_requested_mib, n.mem_allocatable_mib)}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {c?.local_cluster && c?.shared_host_memory && (
        <div className="issue-item info" style={{ marginTop: 10, fontSize: 12.5 }}>
          Cluster local ({c.context}) : les {c.nodes?.length} nœuds partagent la RAM de cette machine. Kubernetes annonce {c.declared_mem_free_mib}Mi libres, mais seuls {c.total_mem_free_mib}Mi existent réellement : c'est cette valeur qui est retenue pour la génération et le contrôle.
        </div>
      )}
      {errs.length > 0 && (
        <div className="issue-item warning" style={{ marginTop: 10, fontSize: 12.5 }}>
          Sources non lues : {errs.map(([k, v]) => `${k} (${String(v).slice(0, 80)})`).join(' · ')}
        </div>
      )}
      {ctx && ctx.providers?.length === 0 && (
        <div className="issue-item critical" style={{ marginTop: 10, fontSize: 12.5 }}>
          Aucune clé d'API : ajoute ANTHROPIC_API_KEY dans le fichier .env puis redémarre le backend.
        </div>
      )}
    </div>
  )
}

// ── Verdict des arbitres ─────────────────────────────────────────────────────
function Verdict({ check, attemptsUsed, maxAttempts, edited }) {
  if (!check) return null
  if (check.valid) {
    return (
      <div className="issue-item success flex items-center gap-sm" style={{ marginBottom: 12 }}>
        <ShieldCheck size={16} />
        <div>
          <div className="fw-500">Validé par le serveur Kubernetes et compatible avec la capacité du cluster</div>
          <div className="text-xs text-muted">
            {edited ? 'Version modifiée revalidée' : `Obtenu à la tentative ${attemptsUsed} sur ${maxAttempts}`} · dry-run serveur + contrôle de placement
          </div>
        </div>
      </div>
    )
  }
  if (check.server_validated == null) {
    return (
      <div className="issue-item warning flex items-center gap-sm" style={{ marginBottom: 12 }}>
        <AlertTriangle size={16} />
        <div>
          <div className="fw-500">Non vérifié : le serveur Kubernetes n'a pas pu être interrogé</div>
          <div className="text-xs text-muted">Le manifeste n'est pas garanti applicable.</div>
        </div>
      </div>
    )
  }
  return (
    <div className="issue-item critical" style={{ marginBottom: 12 }}>
      <div className="flex items-center gap-sm fw-500"><XCircle size={16} />
        {edited ? 'La version modifiée est rejetée' : `Rejeté après ${attemptsUsed} tentative${attemptsUsed > 1 ? 's' : ''}`}
      </div>
      {(check.errors || []).map((e, i) => (
        <div key={i} className="text-xs" style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>
          <span className="badge badge-danger" style={{ marginRight: 6 }}>{e.category}</span>{e.message}
        </div>
      ))}
    </div>
  )
}

export default function YAMLGenerator() {
  const navigate = useNavigate()
  const { themeName } = useTheme()

  const [ctx, setCtx] = useState(null)
  const [ctxLoading, setCtxLoading] = useState(false)
  const [description, setDescription] = useState('')
  const [model, setModel] = useState('auto')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [yamlText, setYamlText] = useState('')
  const [check, setCheck] = useState(null)        // dernier verdict (génération ou revalidation)
  const [checkedText, setCheckedText] = useState('')
  const [revalidating, setRevalidating] = useState(false)
  const [stats, setStats] = useState(null)
  // Monaco se charge depuis un CDN : sans Internet, on bascule sur un éditeur simple
  const [editorReady, setEditorReady] = useState(false)
  const [editorFallback, setEditorFallback] = useState(false)

  const loadCtx = async (refresh = false) => {
    setCtxLoading(true)
    try { setCtx((await api.get(`/ai/host-context${refresh ? '?refresh=true' : ''}`, { timeout: 60000 })).data) }
    catch (e) { setCtx({ errors: { backend: e.response?.data?.detail || e.message } }) }
    setCtxLoading(false)
  }
  const loadStats = async () => {
    try { setStats((await api.get('/ai/generate-yaml/stats')).data) } catch { /* facultatif */ }
  }
  useEffect(() => { loadCtx(); loadStats() }, [])
  useEffect(() => {
    if (!result || editorReady) return
    const t = setTimeout(() => { if (!editorReady) setEditorFallback(true) }, 6000)
    return () => clearTimeout(t)
  }, [result, editorReady])

  const dirty = yamlText !== checkedText
  const edited = result && checkedText !== result.yaml

  const generate = async () => {
    if (!description.trim() || loading) return
    setLoading(true); setError(null)
    try {
      const { data } = await api.post('/ai/generate-yaml', { description, model, max_attempts: 3, repair: true }, { timeout: 240000 })
      setResult(data)
      setYamlText(data.yaml || '')
      setCheckedText(data.yaml || '')
      setCheck(data.valid === undefined ? null : data)
      loadStats(); loadCtx()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    }
    setLoading(false)
  }

  const revalidate = async () => {
    setRevalidating(true)
    try {
      const { data } = await api.post('/ai/validate-yaml', { yaml: yamlText }, { timeout: 60000 })
      setCheck(data); setCheckedText(yamlText)
    } catch (e) { setError(e.response?.data?.detail || e.message) }
    setRevalidating(false)
  }

  const cap = check?.capacity
  const attempts = result?.attempts || []
  const legacy = result && result.valid === undefined   // réponse du modèle fine-tuné / ancien format

  const editorOptions = useMemo(() => ({
    minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false,
    wordWrap: 'on', tabSize: 2, renderLineHighlight: 'none', automaticLayout: true,
  }), [])

  return (
    <div style={{ padding: 20, overflowY: 'auto', height: '100vh' }}>
      <div style={{ marginBottom: 16 }}>
        <h1 className="page-title">Génération de manifestes vérifiée</h1>
        <p className="page-subtitle">
          Lecture de l'hôte → génération → validation par le serveur Kubernetes et contrôle de capacité → correction automatique
        </p>
      </div>

      <HostPanel ctx={ctx} loading={ctxLoading} onRefresh={() => loadCtx(true)} />

      {/* Saisie */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="flex gap-sm">
          <textarea
            className="input" rows={2} value={description}
            onChange={e => setDescription(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); generate() } }}
            placeholder="Décris ce que tu veux déployer. L'IA tiendra compte de la capacité réelle de ta machine."
            style={{ resize: 'vertical', fontFamily: 'var(--font-sans)' }}
          />
          <div className="flex-col gap-sm" style={{ display: 'flex', minWidth: 190 }}>
            <select className="input" value={model} onChange={e => setModel(e.target.value)}>
              {MODELS.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
            <button className="btn btn-primary" onClick={generate} disabled={loading || !description.trim()} style={{ justifyContent: 'center' }}>
              <Sparkles size={13} /> {loading ? 'Génération et validation…' : 'Générer'}
            </button>
          </div>
        </div>
        <div className="flex gap-sm" style={{ flexWrap: 'wrap', marginTop: 10 }}>
          {SUGGESTIONS.map(s => (
            <button key={s} className="btn btn-ghost btn-sm" onClick={() => setDescription(s)}>{s}</button>
          ))}
        </div>
        {loading && (
          <div className="text-xs text-muted" style={{ marginTop: 10 }}>
            Le modèle génère, puis le serveur Kubernetes vérifie. En cas de rejet, l'erreur lui est renvoyée (jusqu'à 3 essais). Compte 10 à 60 secondes.
          </div>
        )}
        {error && <div className="issue-item critical" style={{ marginTop: 10, fontSize: 13 }}>{error}</div>}
      </div>

      {result && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 340px', gap: 14 }}>
          {/* Colonne gauche : verdict + éditeur */}
          <div className="card">
            {!legacy && <Verdict check={check} attemptsUsed={result.attempts_used} maxAttempts={result.max_attempts} edited={edited} />}
            {/* Ce que les vérificateurs ne voient pas : écarts possibles avec la DEMANDE */}
            {result.intent_warnings?.length > 0 && (
              <div className="issue-item warning block" style={{ marginBottom: 12 }}>
                <div className="flex items-center gap-sm" style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>
                  <AlertTriangle size={14} color="var(--color-amber)" /> À vérifier par rapport à la demande
                </div>
                {result.intent_warnings.map((w, i) => (
                  <div key={i} className="text-sm" style={{ marginBottom: 3 }}>• {w}</div>
                ))}
                <div className="text-xs text-muted" style={{ marginTop: 6 }}>
                  Le serveur Kubernetes juge la forme, pas l'intention : ces écarts passent sa validation.
                  Contrôles par calcul, à relire ; le verdict ci-dessus n'est pas modifié.
                </div>
              </div>
            )}
            {legacy && (
              <div className="issue-item warning" style={{ marginBottom: 12, fontSize: 13 }}>
                Modèle fine-tuné : sortie non vérifiée automatiquement. Clique sur « Revalider ».
              </div>
            )}

            <div className="flex justify-between items-center" style={{ marginBottom: 8 }}>
              <span className="card-title" style={{ margin: 0 }}>Manifeste {dirty && <span className="badge badge-warning">modifié, à revalider</span>}</span>
              <span className="flex gap-sm">
                <span className="badge badge-info">{result.model_used || 'modèle'}</span>
                {result.duration_ms != null && <span className="badge badge-neutral">{sec(result.duration_ms)}</span>}
              </span>
            </div>

            <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
              {editorFallback ? (
                <textarea
                  className="code-block" value={yamlText} spellCheck={false}
                  onChange={e => setYamlText(e.target.value)}
                  style={{ width: '100%', height: 420, border: 'none', borderRadius: 0, resize: 'vertical', outline: 'none' }}
                />
              ) : (
                <Editor
                  height="420px" language="yaml" value={yamlText}
                  theme={themeName === 'light' ? 'light' : 'vs-dark'}
                  onMount={() => setEditorReady(true)}
                  onChange={v => setYamlText(v ?? '')} options={editorOptions}
                />
              )}
            </div>

            <div className="flex gap-sm" style={{ marginTop: 12 }}>
              <button className="btn btn-ghost" onClick={revalidate} disabled={revalidating}>
                <ListChecks size={13} /> {revalidating ? 'Validation…' : 'Revalider'}
              </button>
              <button className="btn btn-ghost" onClick={() => navigator.clipboard.writeText(yamlText)}>
                <Copy size={13} /> Copier
              </button>
              <button className="btn btn-success" onClick={() => navigate('/devops', { state: { yaml: yamlText } })}
                style={{ flex: 1, justifyContent: 'center' }} title="Aperçu des différences, surveillance et retour arrière automatique">
                <Rocket size={13} /> Déploiement sûr →
              </button>
            </div>

            {check?.dry_run?.actions?.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="metric-label">Effet prévu par le serveur</div>
                {check.dry_run.actions.map((a, i) => (
                  <div key={i} className="flex justify-between text-xs" style={{ padding: '3px 0' }}>
                    <span className="text-mono text-primary">{a.resource}</span>
                    <span className={`badge ${a.action === 'created' ? 'badge-success' : 'badge-warning'}`}>
                      {a.action === 'created' ? 'sera créé' : a.action === 'configured' ? 'existe déjà · sera modifié' : 'inchangé'}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Colonne droite */}
          <div className="flex-col gap-md" style={{ display: 'flex' }}>
            {attempts.length > 0 && (
              <div className="card">
                <div className="card-title">Tentatives</div>
                {attempts.map(a => (
                  <div key={a.n} style={{ padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
                    <div className="flex justify-between items-center text-sm">
                      <span className="flex items-center gap-sm">
                        {a.valid ? <CheckCircle2 size={13} color="var(--color-green)" /> : <XCircle size={13} color="var(--color-red)" />}
                        Tentative {a.n}
                      </span>
                      <span className="text-xs text-muted">{sec(a.latency_ms)}</span>
                    </div>
                    {!a.valid && (a.errors || []).map((e, i) => (
                      <div key={i} className="text-xs text-muted" style={{ marginTop: 3, paddingLeft: 21 }}>
                        {e.source} · <span className="text-danger">{e.category}</span>
                      </div>
                    ))}
                    {a.repair?.kept && (
                      <div className="text-xs" style={{ marginTop: 3, paddingLeft: 21, color: 'var(--color-amber)' }}>
                        Réparé automatiquement (le modèle avait : {(a.repair.errors_before || []).join(', ')}) :
                        {a.repair.changes.map((c, i) => <div key={i} className="text-muted">• {c}</div>)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {cap?.known && (
              <div className="card">
                <div className="card-title">Placement sur le cluster</div>
                {cap.workloads.map(w => (
                  <div key={w.kind + w.name} style={{ marginBottom: 8, fontSize: 12.5 }}>
                    <div className="flex justify-between">
                      <span className="text-primary fw-500 truncate">{w.kind}/{w.name}</span>
                      <span className="text-muted">× {w.replicas}</span>
                    </div>
                    <div className="text-muted">
                      requests {w.cpu_req_m}m · {w.mem_req_mib}Mi — limits {w.cpu_lim_m}m · {w.mem_lim_mib}Mi
                    </div>
                    <div className={w.fits_nodes?.length ? 'text-success' : 'text-danger'}>
                      {w.fits_nodes?.length ? `tient sur ${w.fits_nodes.join(', ')}` : 'ne tient sur aucun nœud'}
                    </div>
                  </div>
                ))}
                <div style={{ marginTop: 6 }}>
                  <div className="flex justify-between text-xs"><span className="text-muted">CPU demandé / libre</span><span>{cap.total_cpu_req_m}m / {cap.cluster_free_cpu_m}m</span></div>
                  <Bar value={pct(cap.total_cpu_req_m, cap.cluster_free_cpu_m)} warn={60} danger={90} />
                  <div className="flex justify-between text-xs" style={{ marginTop: 6 }}><span className="text-muted">RAM demandée / libre{cap.local_cluster ? ' (RAM réelle de l\'hôte)' : ''}</span><span>{cap.total_mem_req_mib}Mi / {cap.cluster_free_mem_mib}Mi</span></div>
                  <Bar value={pct(cap.total_mem_req_mib, cap.cluster_free_mem_mib)} warn={60} danger={90} />
                </div>
              </div>
            )}

            {result.reasoning?.length > 0 && (
              <div className="card">
                <div className="card-title">Raisonnement du modèle</div>
                {result.reasoning.map(r => (
                  <div key={r.step} style={{ marginBottom: 8, paddingLeft: 10, borderLeft: '2px solid var(--color-purple)', borderRadius: 0 }}>
                    <div className="text-sm fw-500 text-primary">{r.step}. {r.title}</div>
                    <div className="text-xs text-muted" style={{ lineHeight: 1.5 }}>{r.explanation}</div>
                  </div>
                ))}
              </div>
            )}

            {(check?.warnings?.length > 0 || result.warnings?.length > 0) && (
              <div className="card">
                <div className="card-title flex items-center gap-sm"><AlertTriangle size={13} color="var(--color-amber)" /> Points d'attention</div>
                {[...new Set([...(result.warnings || []), ...(check?.warnings || [])])].map((w, i) => (
                  <div key={i} className="text-xs text-muted" style={{ marginBottom: 4 }}>• {w}</div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {stats?.generations > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-title">Fiabilité mesurée de la génération</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
            <div className="metric-card"><div className="metric-label">Générations</div><div className="metric-value">{stats.generations}</div></div>
            <div className="metric-card"><div className="metric-label">Validées au 1er essai</div><div className="metric-value">{Math.round(stats.rate_first_try * 100)} %</div><div className="metric-sub">{stats.valid_first_try} / {stats.generations}</div></div>
            <div className="metric-card"><div className="metric-label">Validées après correction</div><div className="metric-value">{Math.round(stats.rate_final * 100)} %</div><div className="metric-sub">{stats.valid_final} / {stats.generations}</div></div>
            <div className="metric-card"><div className="metric-label">Tentatives moyennes</div><div className="metric-value">{stats.mean_attempts}</div></div>
          </div>
          {Object.keys(stats.error_categories || {}).length > 0 && (
            <div className="flex gap-sm" style={{ flexWrap: 'wrap', marginTop: 10 }}>
              {Object.entries(stats.error_categories).map(([k, v]) => (
                <span key={k} className="badge badge-neutral">{k} · {v}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

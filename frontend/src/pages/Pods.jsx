import { useState, useEffect, useRef, memo, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import axios from 'axios'
import { useTheme } from '../utils/ThemeContext'
import CreateDeploymentModal from '../components/CreateDeploymentModal'
import PodTerminal from '../components/PodTerminal'
import { Plus, Minus, RefreshCw, Trash2, RotateCw, SquareTerminal, X } from 'lucide-react'

const api = axios.create({ baseURL: '/api', timeout: 30000 })

function ConfirmModal({ isOpen, onClose, onConfirm, title, message, type='danger', confirmText='Confirmer' }) {
  const { theme: t } = useTheme()
  // Pendant l'action, le bouton affiche « En cours… » et ne peut pas être recliqué
  const [busy, setBusy] = useState(false)
  if (!isOpen) return null
  const run = async () => {
    setBusy(true)
    try { await onConfirm() } finally { setBusy(false) }
  }
  const colors = {
    danger:  { border:'color-mix(in srgb, var(--crit) 30%, transparent)',  btn:'var(--crit)',  btnBg:'color-mix(in srgb, var(--crit) 15%, transparent)' },
    warning: { border:'color-mix(in srgb, var(--warn) 30%, transparent)',  btn:'var(--warn)',  btnBg:'color-mix(in srgb, var(--warn) 15%, transparent)' },
    info:    { border:'color-mix(in srgb, var(--accent) 30%, transparent)', btn:'var(--accent)',  btnBg:'color-mix(in srgb, var(--accent) 15%, transparent)' },
  }
  const c = colors[type]
  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(17,24,32,.45)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:1000 }}>
      <div style={{ background:t.bg.card, border:`1px solid ${t.border.primary}`, borderRadius:6, padding:22, maxWidth:420, width:'90%', boxShadow:'var(--shadow-lg)' }}>
        <div style={{ fontSize:16, fontWeight:600, marginBottom:8, color:t.text.primary }}>{title}</div>
        <div style={{ fontSize:14, color:t.text.secondary, lineHeight:1.6, marginBottom:20 }}>{message}</div>
        <div style={{ display:'flex', gap:10 }}>
          <button onClick={onClose} disabled={busy} className="btn btn-ghost" style={{ marginLeft:'auto' }}>Annuler</button>
          <button onClick={run} disabled={busy} className={type==='danger' ? 'btn btn-danger' : 'btn btn-primary'}>
            {busy ? <><RefreshCw size={14} className="spin" /> En cours…</> : confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}

function CreateNamespaceModal({ isOpen, onClose, onSuccess }) {
  const { theme: t } = useTheme()
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  if (!isOpen) return null
  const submit = async () => {
    if (!name.match(/^[a-z0-9-]+$/)) { setError('Nom invalide — utilisez uniquement a-z, 0-9, -'); return }
    setLoading(true); setError('')
    try {
      const res = await api.post('/cluster/namespaces', { name })
      onSuccess(res.data.message); onClose(); setName('')
    } catch(e) { setError(e.response?.data?.detail || e.message) }
    setLoading(false)
  }
  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(17,24,32,.45)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:1000 }}>
      <div style={{ background:t.bg.card, border:`1px solid ${t.border.primary}`, borderRadius:6, padding:22, width:380, boxShadow:'var(--shadow-lg)' }}>
        <div style={{ fontSize:16, fontWeight:600, color:'var(--ink)', marginBottom:16 }}>Créer un namespace</div>
        <div style={{ fontSize:13, color:t.text.muted, marginBottom:5 }}>Nom du namespace</div>
        <input value={name} onChange={e => setName(e.target.value.toLowerCase())} onKeyDown={e => e.key==='Enter'&&submit()} placeholder="mon-namespace" autoFocus
          style={{ width:'100%', background:t.bg.input, border:`1px solid ${t.border.primary}`, borderRadius:4, padding:'8px 10px', fontSize:14, color:t.text.primary, outline:'none', marginBottom:12, boxSizing:'border-box' }} />
        {error && <div style={{ color:'var(--crit)', fontSize:13, marginBottom:12 }}>{error}</div>}
        <div style={{ display:'flex', gap:10 }}>
          <button onClick={onClose} className="btn btn-ghost" style={{ marginLeft:'auto' }}>Annuler</button>
          <button onClick={submit} disabled={loading} className="btn btn-primary">
            {loading ? 'Création…' : 'Créer'}
          </button>
        </div>
      </div>
    </div>
  )
}

const RefreshBtn = ({ onClick, loading, theme: t }) => (
  <button onClick={onClick} disabled={loading} className="btn btn-ghost">
    <RefreshCw size={14} className={loading ? 'spin' : ''} />
    {loading ? 'Actualisation…' : 'Actualiser'}
  </button>
)

export default function Pods() {
  const { theme: t } = useTheme()
  const queryClient = useQueryClient()
  const [tab, setTab] = useState('pods')
  const [selectedPod, setSelectedPod] = useState(null)
  const [logs, setLogs] = useState([])
  const [logsConnected, setLogsConnected] = useState(false)
  // ── Deep-linking : /pods?namespace=default&search=nginx-abc ────────────────
  // Permet au NotificationCenter de pointer directement sur le pod concerné.
  const [searchParams] = useSearchParams()
  const [filterNs, setFilterNs] = useState(searchParams.get('namespace') || 'all')
  const [search, setSearch] = useState(searchParams.get('search') || '')

  // Pod dont le terminal est ouvert (null = modal fermé)
  const [terminalPod, setTerminalPod] = useState(null)

  // Resynchronise si l'utilisateur reclique sur une autre notification
  // alors qu'il est déjà sur /pods (le composant n'est pas remonté).
  useEffect(() => {
    const ns = searchParams.get('namespace')
    const q  = searchParams.get('search')
    if (ns !== null) setFilterNs(ns)
    if (q  !== null) setSearch(q)
    if (ns !== null || q !== null) setTab('pods')
  }, [searchParams])

  const [toast, setToast] = useState(null)
  const [confirm, setConfirm] = useState(null)
  const [showCreateDep, setShowCreateDep] = useState(false)
  const [showCreateNs, setShowCreateNs] = useState(false)
  const [refreshing, setRefreshing] = useState({ pods:false, deployments:false, namespaces:false })
  const wsRef = useRef(null)
  const logsEndRef = useRef(null)

  const showToast = (text, type='success') => { setToast({ text, type }); setTimeout(() => setToast(null), 4000) }

  const { data: podsData, isFetching: podsFetching } = useQuery({ queryKey:['pods'], queryFn:()=>api.get('/cluster/pods').then(r=>r.data), refetchInterval:45000, staleTime:10000 })
  const { data: depsData, isFetching: depsFetching } = useQuery({ queryKey:['deployments'], queryFn:()=>api.get('/cluster/deployments').then(r=>r.data), refetchInterval:45000, staleTime:10000 })
  const { data: nsData,   isFetching: nsFetching   } = useQuery({ queryKey:['namespaces'], queryFn:()=>api.get('/cluster/namespaces').then(r=>r.data), refetchInterval:60000, staleTime:15000 })

  const pods        = podsData?.pods || []
  const deployments = depsData?.deployments || []
  const namespaces  = nsData?.namespaces?.map(n=>n.name) || []
  const allNs       = ['all', ...new Set(pods.map(p=>p.namespace))]
  const filteredPods = pods.filter(p => {
    if (filterNs !== 'all' && p.namespace !== filterNs) return false
    if (search && !p.name.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  const doRefresh = async (key) => {
    setRefreshing(r => ({ ...r, [key]:true }))
    await api.get('/cluster/summary?force=true')
    await queryClient.invalidateQueries([key])
    setTimeout(() => setRefreshing(r => ({ ...r, [key]:false })), 1000)
  }

  useEffect(() => {
    if (!selectedPod) return
    setLogs([]); setLogsConnected(false)
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/logs/${selectedPod.namespace}/${selectedPod.name}`)
    wsRef.current = ws
    ws.onopen  = () => setLogsConnected(true)
    ws.onmessage = e => {
      try {
        const d = JSON.parse(e.data)
        if (d.type === 'log' || d.type === 'info') setLogs(p => [...p.slice(-400), d.line])
      } catch {}
    }
    ws.onerror = ws.onclose = () => setLogsConnected(false)
    return () => { ws.close(); wsRef.current = null }
  }, [selectedPod])

  useEffect(() => { logsEndRef.current?.scrollIntoView({ behavior:'smooth' }) }, [logs])

  const deletePod = (pod) => setConfirm({ title:'Supprimer le pod', message:`Supprimer "${pod.name}" ?`, type:'danger', confirmText:'Supprimer',
    onConfirm: async () => {
      try { await api.delete(`/cluster/pods/${pod.namespace}/${pod.name}`); showToast(`Pod "${pod.name}" supprimé`); queryClient.invalidateQueries(['pods']); if (selectedPod?.name===pod.name) setSelectedPod(null) }
      catch(e) { showToast(e.response?.data?.detail||e.message, 'error') }
      setConfirm(null)
    }
  })

  const restartPod = (pod) => setConfirm({ title:'Redémarrer le pod', message:`Redémarrer "${pod.name}" ?`, type:'warning', confirmText:'Redémarrer',
    onConfirm: async () => {
      try { await api.delete(`/cluster/pods/${pod.namespace}/${pod.name}`); showToast(`Pod "${pod.name}" en cours de redémarrage...`); setTimeout(()=>queryClient.invalidateQueries(['pods']),2000) }
      catch(e) { showToast(e.response?.data?.detail||e.message, 'error') }
      setConfirm(null)
    }
  })

  const scaleDeployment = (dep, delta) => {
    const newReplicas = Math.max(0, dep.replicas + delta)
    setConfirm({ title:delta>0?'Ajouter un réplica':'Retirer un réplica', message:`"${dep.name}" : ${dep.replicas} → ${newReplicas} replica${newReplicas!==1?'s':''}`, type:'info', confirmText:delta>0?'Augmenter':'Réduire',
      onConfirm: async () => {
        try { const res = await api.patch(`/cluster/deployments/${dep.namespace}/${dep.name}/scale`, { replicas:newReplicas }); showToast(res.data.message); queryClient.invalidateQueries(['deployments']) }
        catch(e) { showToast(e.response?.data?.detail||e.message, 'error') }
        setConfirm(null)
      }
    })
  }

  const restartDeployment = (dep) => setConfirm({ title:'Redémarrer le Deployment', message:`Rolling restart de "${dep.name}" ?`, type:'warning', confirmText:'Redémarrer',
    onConfirm: async () => {
      try { const res = await api.post(`/cluster/deployments/${dep.namespace}/${dep.name}/restart`); showToast(res.data.message) }
      catch(e) { showToast(e.response?.data?.detail||e.message, 'error') }
      setConfirm(null)
    }
  })

  const deleteDeployment = (dep) => setConfirm({ title:'Supprimer le Deployment', message:`Supprimer "${dep.name}" et TOUS ses pods ?`, type:'danger', confirmText:'Supprimer définitivement',
    onConfirm: async () => {
      try { await api.delete(`/cluster/deployments/${dep.namespace}/${dep.name}`); showToast(`Deployment "${dep.name}" supprimé`); queryClient.invalidateQueries(['deployments','pods']) }
      catch(e) { showToast(e.response?.data?.detail||e.message, 'error') }
      setConfirm(null)
    }
  })

  const deleteNamespace = (ns) => setConfirm({ title:'Supprimer le Namespace', message:`Supprimer "${ns.name}" et TOUTES ses ressources ?`, type:'danger', confirmText:'Supprimer namespace',
    onConfirm: async () => {
      try { await api.delete(`/cluster/namespaces/${ns.name}`); showToast(`Namespace "${ns.name}" suppression en cours...`); queryClient.invalidateQueries(['namespaces','pods']) }
      catch(e) { showToast(e.response?.data?.detail||e.message, 'error') }
      setConfirm(null)
    }
  })

  const statusColor = s => ({ Running:'var(--ok)', Pending:'var(--warn)', Failed:'var(--crit)', Succeeded:'var(--series-2)' }[s]||'var(--ink-2)')

  // Étiquette : neutre par défaut, colorée seulement quand elle porte un état
  const Pill = ({ children, color }) => (
    <span className="badge" style={{ color: color || 'var(--ink-2)', borderColor: color ? `color-mix(in srgb, ${color} 35%, transparent)` : 'var(--rule)', background: color ? `color-mix(in srgb, ${color} 8%, transparent)` : 'transparent' }}>{children}</span>
  )

  const Btn = ({ onClick, variant='ghost', title, children }) => (
    <button onClick={onClick} title={title} aria-label={title} className={`btn btn-${variant}`}>{children}</button>
  )

  return (
    <div style={{ padding:20, height:'calc(100vh - 40px)', display:'flex', flexDirection:'column', background:t.bg.primary, color:t.text.primary }}>

      {confirm && <ConfirmModal isOpen={!!confirm} title={confirm.title} message={confirm.message} type={confirm.type} confirmText={confirm.confirmText} onConfirm={confirm.onConfirm} onClose={()=>setConfirm(null)} />}
      <CreateDeploymentModal isOpen={showCreateDep} onClose={()=>setShowCreateDep(false)} namespaces={namespaces} onSuccess={msg=>{showToast(msg);queryClient.invalidateQueries(['deployments','pods'])}} />
      {terminalPod && <PodTerminal pod={terminalPod} onClose={()=>setTerminalPod(null)} />}
      <CreateNamespaceModal isOpen={showCreateNs} onClose={()=>setShowCreateNs(false)} onSuccess={msg=>{showToast(msg);queryClient.invalidateQueries(['namespaces'])}} />

      {toast && (
        <div style={{ position:'fixed', bottom:24, right:24, padding:'12px 20px', borderRadius:4, fontSize:14, fontWeight:500, zIndex:999, maxWidth:380,
          background:t.bg.card, borderLeft:`3px solid ${toast.type==='error'?'var(--crit)':'var(--ok)'}`,
          color:t.text.primary,
          boxShadow:'var(--shadow-lg)',
        }}>{toast.text}</div>
      )}

      {/* HEADER */}
      <div style={{ marginBottom:14 }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:12 }}>
          <div>
            <h1 className="page-title">Pods et déploiements</h1>
            <p className="page-subtitle">Pods, déploiements et namespaces du cluster. Sélectionner un pod affiche ses logs en direct.</p>
          </div>
          <div style={{ display:'flex', gap:8 }}>
            <Btn onClick={()=>setShowCreateNs(true)}><Plus size={14} /> Namespace</Btn>
            <Btn onClick={()=>setShowCreateDep(true)} variant="primary"><Plus size={14} /> Deployment</Btn>
          </div>
        </div>

        {/* TABS */}
        <div className="tabs">
          {[
            { id:'pods',        label:`Pods (${pods.length})` },
            { id:'deployments', label:`Deployments (${deployments.length})` },
            { id:'namespaces',  label:`Namespaces (${nsData?.namespaces?.length||0})` },
          ].map(tab_ => (
            <button key={tab_.id} onClick={()=>{setTab(tab_.id);setSelectedPod(null)}} className={'tab' + (tab===tab_.id ? ' active' : '')}>{tab_.label}</button>
          ))}
        </div>
      </div>

      {/* ── TAB PODS ── */}
      {tab === 'pods' && (
        <div style={{ flex:1, display:'flex', gap:14, minHeight:0 }}>
          <div style={{ flex:selectedPod?'0 0 44%':1, display:'flex', flexDirection:'column', minWidth:0 }}>
            <div style={{ display:'flex', gap:8, marginBottom:10, alignItems:'center' }}>
              <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Rechercher un pod..."
                style={{ flex:1, background:t.bg.input, border:`1px solid ${t.border.primary}`, borderRadius:4, padding:'7px 10px', fontSize:14, color:t.text.primary, outline:'none' }} />
              <select value={filterNs} onChange={e=>setFilterNs(e.target.value)}
                style={{ background:t.bg.input, border:`1px solid ${t.border.primary}`, borderRadius:6, padding:'8px 10px', fontSize:13, color:t.text.secondary }}>
                {allNs.map(ns=><option key={ns} value={ns}>{ns==='all'?'Tous namespaces':ns}</option>)}
              </select>
              <RefreshBtn onClick={()=>doRefresh('pods')} loading={podsFetching||refreshing.pods} theme={t} />
            </div>

            <div style={{ flex:1, overflowY:'auto', background:t.bg.card, border:`1px solid ${t.border.primary}`, borderRadius:6 }}>
              {pods.length===0 && <div style={{ padding:40, textAlign:'center', color:t.text.muted }}>Chargement des pods...</div>}
              {filteredPods.map(pod => (
                <div key={`${pod.namespace}-${pod.name}`} onClick={()=>setSelectedPod(pod)}
                  style={{ display:'flex', alignItems:'center', gap:10, padding:'10px 14px', borderBottom:`1px solid ${t.border.secondary}`, cursor:'pointer',
                    background:selectedPod?.name===pod.name?'color-mix(in srgb, var(--accent) 8%, transparent)':'transparent',
                    borderLeft:selectedPod?.name===pod.name?'3px solid var(--accent)':'3px solid transparent'
                  }}>
                  <div style={{ width:7, height:7, borderRadius:'50%', background:statusColor(pod.status), flexShrink:0 }}></div>
                  <div style={{ flex:1, minWidth:0 }}>
                    <div style={{ fontSize:13, fontFamily:'var(--font-mono)', fontWeight:500, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis', color:t.text.primary }}>{pod.name}</div>
                    <div style={{ fontSize:12.5, color:t.text.muted }}>{pod.namespace}, {pod.node||'non planifié'}</div>
                  </div>
                  {pod.restarts>0 && <span title="Redémarrages" style={{ fontSize:12.5, color:'var(--ink-2)', fontVariantNumeric:'tabular-nums', display:'inline-flex', alignItems:'center', gap:3 }}><RotateCw size={12} />{pod.restarts}</span>}
                  <Pill color={pod.status==='Running' ? null : statusColor(pod.status)}>{pod.status}</Pill>
                </div>
              ))}
            </div>
          </div>

          {/* DETAIL + LOGS */}
          {selectedPod && (
            <div style={{ flex:1, display:'flex', flexDirection:'column', minWidth:0, gap:10 }}>
              <div style={{ background:t.bg.card, border:`1px solid ${t.border.primary}`, borderRadius:6, padding:16 }}>
                <div style={{ display:'flex', justifyContent:'space-between', marginBottom:10 }}>
                  <div>
                    <div style={{ fontSize:14, fontWeight:600, fontFamily:'var(--font-mono)', marginBottom:6, color:t.text.primary }}>{selectedPod.name}</div>
                    <div style={{ display:'flex', gap:6, flexWrap:'wrap' }}>
                      <Pill>{selectedPod.namespace}</Pill>
                      <Pill color={statusColor(selectedPod.status)}>{selectedPod.status}</Pill>
                      <Pill>{selectedPod.node||'non planifié'}</Pill>
                      {selectedPod.restarts>0 && <Pill color="var(--warn)">{selectedPod.restarts} redémarrage{selectedPod.restarts>1?'s':''}</Pill>}
                    </div>
                  </div>
                  <button onClick={()=>setSelectedPod(null)} className="btn btn-ghost btn-sm" title="Fermer" aria-label="Fermer" style={{ alignSelf:'flex-start' }}><X size={14} /></button>
                </div>
                <div style={{ display:'flex', gap:8, marginBottom:12 }}>
                  <Btn onClick={()=>setTerminalPod(selectedPod)}><SquareTerminal size={14} /> Terminal</Btn>
                  <Btn onClick={()=>restartPod(selectedPod)}><RotateCw size={14} /> Redémarrer</Btn>
                  <Btn onClick={()=>deletePod(selectedPod)} variant="danger"><Trash2 size={14} /> Supprimer</Btn>
                  <div style={{ marginLeft:'auto', display:'flex', alignItems:'center', gap:6, fontSize:12.5, color:logsConnected?'var(--ok)':'var(--warn)' }}>
                    <div style={{ width:6, height:6, borderRadius:'50%', background:logsConnected?'var(--ok)':'var(--warn)' }}></div>
                    {logsConnected?'Logs en direct':selectedPod.status==='Pending'?'Pod en attente':'Connexion...'}
                  </div>
                </div>
                <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:8, paddingTop:10, borderTop:`1px solid ${t.border.secondary}` }}>
                  <div><div style={{ fontSize:11.5, color:t.text.muted }}>IP</div><div style={{ fontSize:13, fontFamily:'var(--font-mono)', color:t.text.secondary }}>{selectedPod.ip||'—'}</div></div>
                  <div><div style={{ fontSize:11.5, color:t.text.muted }}>Créé</div><div style={{ fontSize:12.5, color:t.text.secondary }}>{selectedPod.age?.substring(0,10)||'—'}</div></div>
                  <div><div style={{ fontSize:11.5, color:t.text.muted }}>Containers</div><div style={{ fontSize:12.5, color:t.text.secondary }}>{(selectedPod.containers||[]).join(', ')||'—'}</div></div>
                </div>
              </div>

              <div style={{ flex:1, background:t.bg.card, border:`1px solid ${t.border.primary}`, borderRadius:6, padding:14, display:'flex', flexDirection:'column', minHeight:0 }}>
                <div style={{ fontSize:13, fontWeight:600, marginBottom:8, display:'flex', alignItems:'center', justifyContent:'space-between', color:t.text.primary }}>
                  <span>Logs en direct</span>
                  {logs.length>0 && <button onClick={()=>setLogs([])} style={{ fontSize:11.5, color:t.text.muted, background:'transparent', border:`1px solid ${t.border.primary}`, borderRadius:6, padding:'2px 8px', cursor:'pointer' }}>Vider</button>}
                </div>
                <div style={{ flex:1, overflowY:'auto', fontFamily:'var(--font-mono)', fontSize:12.5, lineHeight:1.7 }}>
                  {logs.length===0 && (
                    <div style={{ color:t.text.muted, textAlign:'center', padding:'30px 0', lineHeight:2 }}>
                      {selectedPod.status==='Pending' ? (
                        <>
                          <div>Pod en état <strong style={{ color:'var(--warn)' }}>Pending</strong></div>
                          <div style={{ fontSize:11.5, marginTop:4 }}>Logs disponibles quand le pod sera Running</div>
                        </>
                      ) : logsConnected ? 'En attente de logs...' : 'Connexion au stream...'}
                    </div>
                  )}
                  {logs.map((line,i) => {
                    const isErr  = /error|fail|exception|crit/i.test(line)
                    const isWarn = /warn/i.test(line)
                    return (
                      <div key={i} style={{ color:isErr?'var(--crit)':isWarn?'var(--warn)':t.text.secondary, whiteSpace:'pre-wrap', wordBreak:'break-all', borderLeft:`2px solid ${isErr?'var(--crit)':isWarn?'var(--warn)':'transparent'}`, paddingLeft:8, marginBottom:1 }}>{line}</div>
                    )
                  })}
                  <div ref={logsEndRef} />
                </div>
              </div>
            </div>
          )}

          {!selectedPod && (
            <div style={{ flex:1, display:'flex', alignItems:'center', justifyContent:'center', color:t.text.muted, fontSize:14, flexDirection:'column', gap:8 }}>
              <div>Sélectionner un pod pour voir ses logs en direct.</div>
              <div style={{ fontSize:13, color:t.text.muted }}>Le panneau propose aussi le terminal, le redémarrage et la suppression.</div>
            </div>
          )}
        </div>
      )}

      {/* ── TAB DEPLOYMENTS ── */}
      {tab === 'deployments' && (
        <div style={{ flex:1, display:'flex', flexDirection:'column', minHeight:0 }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10 }}>
            <span style={{ fontSize:13, color:t.text.muted }}>{deployments.length} deployment{deployments.length!==1?'s':''}</span>
            <RefreshBtn onClick={()=>doRefresh('deployments')} loading={depsFetching||refreshing.deployments} theme={t} />
          </div>
          <div style={{ flex:1, overflowY:'auto' }}>
            {deployments.map(dep => (
              <div key={`${dep.namespace}-${dep.name}`} style={{ background:t.bg.card, border:`1px solid ${t.border.primary}`, borderRadius:6, padding:16, marginBottom:10 }}>
                <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', marginBottom:12 }}>
                  <div>
                    <div style={{ fontSize:14, fontWeight:600, fontFamily:'var(--font-mono)', marginBottom:6, color:t.text.primary }}>{dep.name}</div>
                    <div style={{ display:'flex', gap:6, flexWrap:'wrap' }}>
                      <Pill>{dep.namespace}</Pill>
                      <Pill color={dep.ready===dep.replicas?'var(--ok)':'var(--warn)'}>{dep.ready}/{dep.replicas} prêts</Pill>
                      <Pill>{dep.strategy}</Pill>
                    </div>
                  </div>
                  <div style={{ display:'flex', gap:8 }}>
                    <Btn onClick={()=>restartDeployment(dep)}><RotateCw size={14} /> Redémarrer</Btn>
                    <Btn onClick={()=>deleteDeployment(dep)} variant="danger" title="Supprimer le deployment"><Trash2 size={14} /></Btn>
                  </div>
                </div>
                <div style={{ display:'grid', gridTemplateColumns:'1fr 2fr auto', gap:12, alignItems:'center' }}>
                  <div>
                    <div style={{ fontSize:11.5, color:t.text.muted, marginBottom:3 }}>Image</div>
                    <div style={{ fontSize:12.5, fontFamily:'var(--font-mono)', color:t.text.primary, wordBreak:'break-all' }}>{dep.image}</div>
                  </div>
                  <div>
                    <div style={{ fontSize:11.5, color:t.text.muted, marginBottom:6 }}>Replicas</div>
                    <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                      <div style={{ flex:1, height:6, background:t.bg.tertiary, borderRadius:3, overflow:'hidden' }}>
                        <div style={{ height:'100%', background:dep.ready===dep.replicas?'var(--ok)':'var(--warn)', width:`${dep.replicas>0?(dep.ready/dep.replicas)*100:0}%`, borderRadius:3 }}></div>
                      </div>
                      <span style={{ fontSize:13, color:t.text.primary, fontWeight:600 }}>{dep.ready}/{dep.replicas}</span>
                    </div>
                  </div>
                  <div style={{ display:'flex', gap:6, alignItems:'center' }}>
                    <button onClick={()=>scaleDeployment(dep,-1)} className="btn btn-ghost btn-sm" title="Retirer un réplica" aria-label="Retirer un réplica" style={{ padding:'5px 7px' }}><Minus size={14} /></button>
                    <div style={{ minWidth:28, textAlign:'center', fontSize:14, fontWeight:600, color:t.text.primary, fontVariantNumeric:'tabular-nums' }}>{dep.replicas}</div>
                    <button onClick={()=>scaleDeployment(dep,+1)} className="btn btn-ghost btn-sm" title="Ajouter un réplica" aria-label="Ajouter un réplica" style={{ padding:'5px 7px' }}><Plus size={14} /></button>
                  </div>
                </div>
              </div>
            ))}
            {deployments.length===0 && (
              <div style={{ textAlign:'center', color:t.text.muted, padding:'60px 0', fontSize:14 }}>
                Aucun deployment. Le bouton « Deployment » en haut à droite en crée un.
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── TAB NAMESPACES ── */}
      {tab === 'namespaces' && (
        <div style={{ flex:1, display:'flex', flexDirection:'column', minHeight:0 }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10 }}>
            <span style={{ fontSize:13, color:t.text.muted }}>{nsData?.namespaces?.length||0} namespace{(nsData?.namespaces?.length||0)!==1?'s':''}</span>
            <RefreshBtn onClick={()=>doRefresh('namespaces')} loading={nsFetching||refreshing.namespaces} theme={t} />
          </div>
          <div style={{ flex:1, overflowY:'auto' }}>
            <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill,minmax(260px,1fr))', gap:10 }}>
              {nsData?.namespaces?.map(ns => {
                const podCount   = pods.filter(p=>p.namespace===ns.name).length
                const protected_ = ['kube-system','kube-public','kube-node-lease','default'].includes(ns.name)
                return (
                  <div key={ns.name} style={{ background:t.bg.card, border:`1px solid ${t.border.primary}`, borderRadius:6, padding:16 }}>
                    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:10 }}>
                      <div>
                        <div style={{ fontSize:14, fontWeight:600, fontFamily:'var(--font-mono)', marginBottom:5, color:t.text.primary }}>{ns.name}</div>
                        <div style={{ display:'flex', gap:6 }}>
                          <Pill color={ns.status==='Active'?null:'var(--warn)'}>{ns.status}</Pill>
                          {protected_ && <Pill>protégé</Pill>}
                        </div>
                      </div>
                      {!protected_ && (
                        <button onClick={()=>deleteNamespace(ns)} className="btn btn-danger btn-sm" title="Supprimer le namespace" aria-label="Supprimer le namespace"><Trash2 size={13} /></button>
                      )}
                    </div>
                    <div style={{ display:'flex', gap:16, fontSize:13, color:t.text.muted }}>
                      <span>{podCount} pod{podCount!==1?'s':''}</span>
                      <span>créé le {ns.age?.substring(0,10)||'—'}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

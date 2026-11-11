// src/pages/Dashboard.jsx
// -----------------------------------------------------------------------------
// Tableau de bord. Principe de lecture : la couleur ne sert qu'à signaler un
// état anormal (attention, critique). Une valeur saine reste dans la couleur
// du texte, pour que l'œil aille directement à ce qui demande une action.
//
// Toutes les valeurs affichées sont mesurées. Deux éléments de l'ancienne
// version ont été retirés pour cette raison :
//   - le radar « Sécurité CIS », dont les six axes étaient dérivés du score
//     global par des décalages fixes (+10, +5, −10…) ; il est remplacé par les
//     contrôles CIS réellement en défaut ;
//   - les valeurs CPU/RAM aléatoires ajoutées quand la mesure échouait ; un
//     échec de mesure laisse désormais un trou, et le graphique le dit.
// -----------------------------------------------------------------------------

import { useQuery } from '@tanstack/react-query'
import { aiAPI, securityAPI } from '../utils/api'
import { useTheme } from '../utils/ThemeContext'
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useNotifications } from '../utils/NotificationsContext'
import GlobalSearch from '../components/GlobalSearch'
import { Search, AlertCircle, ArrowRight } from 'lucide-react'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  PieChart, Pie, Cell,
} from 'recharts'
import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 15000 })

// Quantité Kubernetes (« 8Gi », « 8152072Ki », « 2048Mi ») vers des Gio
const toGiB = q => {
  const m = String(q||'').match(/^([\d.]+)\s*([KMGT]i?)?/)
  if (!m) return null
  const f = { Ki:1/1048576, Mi:1/1024, Gi:1, Ti:1024, K:1e3/2**30, M:1e6/2**30, G:1e9/2**30, T:1e12/2**30 }[m[2]] ?? 1/2**30
  return parseFloat(m[1]) * f
}

const nowLabel = () => new Date().toLocaleTimeString('fr', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

export default function Dashboard() {
  const { theme: t } = useTheme()
  const navigate = useNavigate()
  const [cpuHistory, setCpuHistory] = useState([])
  const [metricsError, setMetricsError] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)

  // Raccourci clavier Ctrl+K pour la recherche
  useEffect(() => {
    const handler = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        setSearchOpen(true)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const { data: summary  } = useQuery({ queryKey:['summary'],   queryFn:()=>api.get('/cluster/summary').then(r=>r.data),        refetchInterval:45000 })
  const { data: detect   } = useQuery({ queryKey:['detect'],    queryFn:()=>aiAPI.detect().then(r=>r.data),                     refetchInterval:30000 })
  const { data: security } = useQuery({ queryKey:['security'],  queryFn:()=>securityAPI.scan().then(r=>r.data),                 refetchInterval:60000 })
  const { data: nodeMetrics } = useQuery({ queryKey:['node-metrics'], queryFn:()=>api.get('/devops/metrics/nodes').then(r=>r.data), refetchInterval:15000 })
  // Journal réel du déploiement surveillé (l'ancien « pipeline » était simulé)
  const { data: deployStats } = useQuery({ queryKey:['deploy-stats'], queryFn:()=>api.get('/devops/v2/deploy/stats').then(r=>r.data), refetchInterval:15000 })

  const pods     = summary?.pods?.pods || []
  const nodes    = summary?.nodes?.nodes || []
  const running  = pods.filter(p=>p.status==='Running').length
  const pending  = pods.filter(p=>p.status==='Pending').length
  const failed   = pods.filter(p=>p.status==='Failed').length
  const total    = pods.length
  const ready    = nodes.filter(n=>n.status==='Ready').length
  const criticals = detect?.critical || 0
  const cisScore  = security?.cis_score || 0

  // Pastille du header : anomalies critiques NON LUES. `criticals` reste la
  // métrique brute du cluster, qui doit refléter la réalité même après lecture.
  const { unreadCritical } = useNotifications()

  // Mesures CPU/RAM : l'historique serveur amorce le graphique, puis un point
  // est ajouté toutes les 10 s. Un échec n'ajoute rien.
  useEffect(() => {
    let first = true
    const fetchMetrics = async () => {
      try {
        const res = await api.get('/devops/metrics/history')
        const { current, history } = res.data
        setMetricsError(false)
        setCpuHistory(prev => {
          const base = first && prev.length === 0 && Array.isArray(history) ? history.map(h => ({ time: h.time, cpu: h.cpu, ram: h.ram })) : prev
          return [...base, { time: nowLabel(), cpu: current.cpu, ram: current.ram }].slice(-30)
        })
      } catch {
        setMetricsError(true)
      }
      first = false
    }
    fetchMetrics()
    const interval = setInterval(fetchMetrics, 10000)
    return () => clearInterval(interval)
  }, [])

  const namespaceData = pods.length > 0
    ? Object.entries(pods.reduce((acc,p)=>({...acc,[p.namespace]:(acc[p.namespace]||0)+1}),{}))
        .map(([ns,count])=>({ns:ns.length>14?ns.substring(0,14)+'…':ns,count}))
        .sort((a,b)=>b.count-a.count).slice(0,6)
    : []

  const restartData = pods.filter(p=>p.restarts>0).sort((a,b)=>b.restarts-a.restarts).slice(0,5)
    .map(p=>({name:p.name.length>16?p.name.substring(0,15)+'…':p.name,restarts:p.restarts}))

  // Contrôles CIS en défaut, regroupés : ce que le score global résume
  const cisControls = Object.entries((security?.issues||[]).reduce((acc,i)=>{
    const k = i.cis_control || 'autre'
    acc[k] = acc[k] || { count:0, example:i.description, severity:i.severity }
    acc[k].count++
    if (i.severity === 'critical') acc[k].severity = 'critical'
    return acc
  },{})).sort((a,b)=>b[1].count-a[1].count).slice(0,4)

  const stateColor = (bad, warn=false) => bad ? 'var(--crit)' : warn ? 'var(--warn)' : 'var(--ink)'

  const other = Math.max(0, total - running - pending - failed)
  const cisColor = !security ? 'var(--ink)' : cisScore>=75 ? 'var(--ink)' : cisScore>=60 ? 'var(--warn)' : 'var(--crit)'
  const lastUse = cpuHistory[cpuHistory.length-1]

  // Petits visuels de la bande d'état : uniquement des valeurs mesurées
  const Segments = ({ parts }) => {
    const sum = parts.reduce((a,p)=>a+p.v,0) || 1
    return (
      <div style={{ display:'flex', height:6, borderRadius:2, overflow:'hidden', background:'var(--surface-2)', gap:1 }}>
        {parts.filter(p=>p.v>0).map((p,i)=><div key={i} title={`${p.label} : ${p.v}`} style={{ width:`${p.v/sum*100}%`, background:p.c }} />)}
      </div>
    )
  }
  const Meter = ({ value, color }) => (
    <div style={{ height:6, borderRadius:2, background:'var(--surface-2)', overflow:'hidden' }}>
      <div style={{ width:`${Math.max(0,Math.min(100,value))}%`, height:'100%', background:color }} />
    </div>
  )
  const Spark = ({ dataKey, color }) => cpuHistory.length < 2 ? <div style={{ height:22 }} /> : (
    <ResponsiveContainer width="100%" height={22}>
      <AreaChart data={cpuHistory} margin={{ top:2, right:0, left:0, bottom:0 }}>
        <YAxis hide domain={[0,100]} />
        <Area type="monotone" dataKey={dataKey} stroke={color} strokeWidth={1.5} fill={color} fillOpacity={0.12} dot={false} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )

  const strip = [
    { label:'Pods en service', value:`${running}/${total}`, color:'var(--ink)', to:'/pods',
      viz:<Segments parts={[{label:'Running',v:running,c:'var(--accent)'},{label:'Pending',v:pending,c:'var(--warn)'},{label:'Failed',v:failed,c:'var(--crit)'},{label:'Autres',v:other,c:'var(--ink-3)'}]} />,
      foot: pending||failed ? `${pending} en attente, ${failed} en échec` : 'aucun pod bloqué' },
    { label:'Nœuds prêts', value:`${ready}/${nodes.length}`, color:stateColor(nodes.length>0 && ready<nodes.length), to:null,
      viz:<div style={{ display:'flex', gap:3 }}>{nodes.map(n=><div key={n.name} title={`${n.name} : ${n.status}`} style={{ flex:1, height:6, borderRadius:2, background:n.status==='Ready'?'var(--accent)':'var(--crit)' }} />)}</div>,
      foot: nodes.length ? `${nodes.length} nœud${nodes.length>1?'s':''} dans le cluster` : '—' },
    { label:'CPU du cluster', value:lastUse?`${lastUse.cpu} %`:'—', color:lastUse&&lastUse.cpu>85?'var(--crit)':'var(--ink)', to:null,
      viz:<Spark dataKey="cpu" color={t.brand.purple} />, foot:'évolution récente', tight:true },
    { label:'RAM du cluster', value:lastUse?`${lastUse.ram} %`:'—', color:lastUse&&lastUse.ram>85?'var(--crit)':'var(--ink)', to:null,
      viz:<Spark dataKey="ram" color={t.brand.teal} />, foot:'évolution récente', tight:true },
    { label:'Anomalies critiques', value:criticals, color:stateColor(criticals>0), to:'/detection',
      viz:<Segments parts={Object.entries((detect?.problems||[]).reduce((a,p)=>({...a,[p.type]:(a[p.type]||0)+1}),{})).map(([k,v],i)=>({label:k,v,c:i%2?'color-mix(in srgb, var(--crit) 55%, transparent)':'var(--crit)'}))} />,
      foot: (detect?.problems||[]).length ? Object.entries((detect.problems).reduce((a,p)=>({...a,[p.type]:(a[p.type]||0)+1}),{})).map(([k,v])=>`${v} ${k}`).join(', ') : 'aucune' },
    { label:'Score CIS', value:security?`${cisScore} %`:'—', sub:security?`grade ${security.grade}`:null, color:cisColor, to:'/security',
      viz:<Meter value={cisScore} color={cisColor==='var(--ink)'?'var(--accent)':cisColor} />,
      foot: security ? `${security.total_issues} défauts de configuration` : 'analyse en cours' },
  ]

  // Répartition des pods par état, pour l'anneau
  const podStates = [
    { name:'Running', value:running, color:t.brand.purple },
    { name:'Pending', value:pending, color:t.brand.amber },
    { name:'Failed',  value:failed,  color:t.brand.red },
    { name:'Autres',  value:other,   color:t.text.muted },
  ].filter(d=>d.value>0)

  const Panel = ({ title, action, children, style }) => (
    <section className="card" style={{ padding:'14px 16px', ...style }}>
      <div style={{ display:'flex', alignItems:'baseline', justifyContent:'space-between', gap:8, marginBottom:12 }}>
        <h2 style={{ fontSize:14, fontWeight:600, color:'var(--ink)' }}>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  )

  const LinkBtn = ({ to, children }) => (
    <button onClick={()=>navigate(to)} style={{ display:'inline-flex', alignItems:'center', gap:4, background:'none', border:'none', padding:0, cursor:'pointer', fontFamily:'inherit', fontSize:13, color:'var(--accent)' }}>
      {children} <ArrowRight size={13} />
    </button>
  )

  const Empty = ({ children, height=130 }) => (
    <div style={{ height, display:'flex', alignItems:'center', justifyContent:'center', color:'var(--ink-3)', fontSize:13, textAlign:'center' }}>{children}</div>
  )

  const chartTooltip = ({active,payload,label}) => {
    if (!active||!payload?.length) return null
    return (
      <div style={{ background:t.bg.card, border:`1px solid ${t.border.primary}`, borderRadius:4, padding:'6px 10px', fontSize:12.5, boxShadow:'var(--shadow-md)' }}>
        <div style={{ color:t.text.muted, marginBottom:2 }}>{label}</div>
        {payload.map((p,i)=><div key={i} style={{ color:t.text.primary }}><span style={{ color:p.color }}>■</span> {p.name} : {p.value}{['CPU','RAM'].includes(p.name)?' %':''}</div>)}
      </div>
    )
  }

  const axis = { fontSize:11.5, fill:t.text.muted }
  const nm = name => nodeMetrics?.nodes?.find(n=>n.name===name)

  const badge = (status) => {
    const c = status==='Running' ? null : status==='Pending' ? 'var(--warn)' : 'var(--crit)'
    return <span className={c ? (status==='Pending'?'badge badge-warning':'badge badge-danger') : 'badge badge-neutral'}>{status}</span>
  }

  return (
    <div style={{ padding:'20px 24px 32px', overflowY:'auto', height:'100vh', background:'var(--bg-base)', color:'var(--ink)' }}>

      <GlobalSearch isOpen={searchOpen} onClose={() => setSearchOpen(false)} />

      <header style={{ display:'flex', alignItems:'flex-end', justifyContent:'space-between', gap:16, marginBottom:18 }}>
        <div>
          <h1 className="page-title">Tableau de bord</h1>
          <p className="page-subtitle">État du cluster minikube. Les pods sont relus toutes les 45 s, l'utilisation toutes les 10 s.</p>
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          {unreadCritical>0 && (
            <button className="btn btn-danger" onClick={() => navigate('/detection')}>
              <AlertCircle size={14} /> {unreadCritical} alerte{unreadCritical>1?'s':''} non lue{unreadCritical>1?'s':''}
            </button>
          )}
          <button className="btn btn-ghost" onClick={() => setSearchOpen(true)} style={{ minWidth:220, justifyContent:'flex-start', color:'var(--ink-3)' }}>
            <Search size={14} />
            <span style={{ flex:1, textAlign:'left' }}>Rechercher</span>
            <kbd style={{ fontSize:11.5, padding:'0 5px', borderRadius:3, border:'1px solid var(--rule)', color:'var(--ink-3)' }}>Ctrl K</kbd>
          </button>
        </div>
      </header>

      {/* Bande d'état : un seul bloc, six mesures séparées par des filets */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(6,1fr)', background:'var(--surface)', border:'1px solid var(--rule)', borderRadius:'var(--radius-lg)', marginBottom:14 }}>
        {strip.map((m,i)=>(
          <button key={m.label} onClick={m.to?()=>navigate(m.to):undefined} disabled={!m.to}
            style={{
              textAlign:'left', padding:'12px 16px', background:'none', border:'none', fontFamily:'inherit',
              borderLeft: i ? '1px solid var(--rule)' : 'none', cursor: m.to ? 'pointer' : 'default', color:'inherit',
            }}>
            <div style={{ fontSize:13, color:'var(--ink-2)', marginBottom:4 }}>{m.label}</div>
            <div style={{ display:'flex', alignItems:'baseline', gap:6, marginBottom:8 }}>
              <span style={{ fontSize:26, fontWeight:600, lineHeight:1.1, color:m.color, fontVariantNumeric:'tabular-nums' }}>{m.value}</span>
              {m.sub && <span style={{ fontSize:13, color:'var(--ink-3)' }}>{m.sub}</span>}
            </div>
            <div style={{ height:22, display:'flex', flexDirection:'column', justifyContent: m.tight ? 'flex-end' : 'center' }}>{m.viz}</div>
            <div style={{ fontSize:12, color:'var(--ink-3)', marginTop:4, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }} title={m.foot}>{m.foot}</div>
          </button>
        ))}
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'2fr 1fr 1fr', gap:14, marginBottom:14 }}>
        <Panel title="Utilisation du cluster" action={
          <div style={{ display:'flex', gap:14, fontSize:12.5, color:'var(--ink-2)' }}>
            <span style={{ display:'inline-flex', alignItems:'center', gap:6 }}><span style={{ width:14, height:2, background:t.brand.purple }} />CPU</span>
            <span style={{ display:'inline-flex', alignItems:'center', gap:6 }}><span style={{ width:14, height:2, background:t.brand.teal }} />RAM</span>
          </div>
        }>
          {cpuHistory.length === 0 ? (
            <Empty height={170}>{metricsError ? 'Mesures indisponibles : metrics-server ne répond pas.' : 'Lecture des mesures…'}</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={170}>
              <AreaChart data={cpuHistory} margin={{ top:4, right:8, left:-18, bottom:0 }}>
                <CartesianGrid stroke={t.border.secondary} vertical={false} />
                <XAxis dataKey="time" tick={axis} interval="preserveStartEnd" stroke={t.border.primary} tickLine={false} />
                <YAxis tick={axis} domain={[0,100]} ticks={[0,25,50,75,100]} stroke={t.border.primary} tickLine={false} unit="%" />
                <Tooltip content={chartTooltip} />
                <Area type="monotone" dataKey="ram" name="RAM" stroke={t.brand.teal} strokeWidth={1.75} fill={t.brand.teal} fillOpacity={0.10} dot={false} isAnimationActive={false} />
                <Area type="monotone" dataKey="cpu" name="CPU" stroke={t.brand.purple} strokeWidth={1.75} fill={t.brand.purple} fillOpacity={0.10} dot={false} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
          {metricsError && cpuHistory.length > 0 && (
            <div style={{ fontSize:12.5, color:'var(--warn)', marginTop:6 }}>Dernière mesure en échec : le graphique s'arrête à la dernière valeur lue.</div>
          )}
        </Panel>

        <Panel title="État des pods" action={<LinkBtn to="/pods">Voir</LinkBtn>}>
          {total === 0 ? <Empty height={170}>Aucun pod.</Empty> : (
            <div style={{ display:'flex', alignItems:'center', gap:14 }}>
              <div style={{ position:'relative', width:128, height:128, flexShrink:0 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={podStates} dataKey="value" nameKey="name" innerRadius={44} outerRadius={60} paddingAngle={podStates.length>1?2:0} stroke="none" startAngle={90} endAngle={-270} isAnimationActive={false}>
                      {podStates.map(d=><Cell key={d.name} fill={d.color} />)}
                    </Pie>
                    <Tooltip content={chartTooltip} />
                  </PieChart>
                </ResponsiveContainer>
                <div style={{ position:'absolute', inset:0, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', pointerEvents:'none' }}>
                  <span style={{ fontSize:22, fontWeight:600, lineHeight:1, fontVariantNumeric:'tabular-nums' }}>{total}</span>
                  <span style={{ fontSize:12, color:'var(--ink-3)' }}>pods</span>
                </div>
              </div>
              <div style={{ flex:1, minWidth:0 }}>
                {podStates.map(d=>(
                  <div key={d.name} style={{ display:'flex', alignItems:'center', gap:8, fontSize:13, padding:'3px 0' }}>
                    <span style={{ width:10, height:10, borderRadius:2, background:d.color, flexShrink:0 }} />
                    <span style={{ flex:1 }}>{d.name}</span>
                    <span style={{ fontVariantNumeric:'tabular-nums', fontWeight:500 }}>{d.value}</span>
                  </div>
                ))}
                <div style={{ fontSize:12, color:'var(--ink-3)', marginTop:6 }}>{namespaceData.length} namespace{namespaceData.length>1?'s':''} actif{namespaceData.length>1?'s':''}</div>
              </div>
            </div>
          )}
        </Panel>

        <Panel title="Sécurité" action={<LinkBtn to="/security">Détails</LinkBtn>}>
          {!security ? <Empty>Analyse en cours…</Empty> : (
            <>
              <div style={{ display:'flex', alignItems:'baseline', gap:8, marginBottom:4 }}>
                <span style={{ fontSize:26, fontWeight:600, fontVariantNumeric:'tabular-nums', color:cisScore>=75?'var(--ink)':cisScore>=60?'var(--warn)':'var(--crit)' }}>{cisScore} %</span>
                <span style={{ fontSize:13, color:'var(--ink-2)' }}>score CIS, grade {security.grade}</span>
              </div>
              <div style={{ marginBottom:8 }}><Meter value={cisScore} color={cisColor==='var(--ink)'?'var(--accent)':cisColor} /></div>
              <div style={{ fontSize:13, color:'var(--ink-2)', marginBottom:10 }}>
                {security.total_issues} défaut{security.total_issues>1?'s':''} de configuration, dont {security.critical_count} critique{security.critical_count>1?'s':''}.
              </div>
              {cisControls.map(([ctrl, v]) => (
                <div key={ctrl} style={{ padding:'6px 0', borderTop:'1px solid var(--rule)', fontSize:13 }} title={v.example}>
                  <div style={{ display:'flex', gap:10, marginBottom:4 }}>
                    <span style={{ fontFamily:'var(--font-mono)', fontSize:12.5, color:'var(--ink-2)' }}>{ctrl}</span>
                    <span style={{ flex:1 }} />
                    <span style={{ color:v.severity==='critical'?'var(--crit)':'var(--ink-2)', fontVariantNumeric:'tabular-nums' }}>{v.count}</span>
                  </div>
                  <Meter value={v.count/(security.total_issues||1)*100} color={v.severity==='critical'?'var(--crit)':'var(--warn)'} />
                </div>
              ))}
            </>
          )}
        </Panel>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:14, marginBottom:14 }}>
        <Panel title="Pods par namespace">
          {namespaceData.length === 0 ? <Empty>Aucun pod.</Empty> : (
            <ResponsiveContainer width="100%" height={140}>
              <BarChart data={namespaceData} layout="vertical" barSize={10} margin={{ left:0, right:8 }}>
                <XAxis type="number" tick={axis} allowDecimals={false} stroke={t.border.primary} tickLine={false} />
                <YAxis type="category" dataKey="ns" interval={0} tick={{ fontSize:12, fill:t.text.secondary }} width={96} stroke={t.border.primary} tickLine={false} />
                <Tooltip content={chartTooltip} cursor={{ fill:t.bg.hover }} />
                <Bar dataKey="count" fill={t.brand.purple} name="pods" isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Panel>

        <Panel title="Redémarrages par pod">
          {restartData.length>0 ? (
            <ResponsiveContainer width="100%" height={140}>
              <BarChart data={restartData} layout="vertical" barSize={10} margin={{ left:0, right:8 }}>
                <XAxis type="number" tick={axis} allowDecimals={false} stroke={t.border.primary} tickLine={false} />
                <YAxis type="category" dataKey="name" interval={0} tick={{ fontSize:12, fill:t.text.secondary }} width={118} stroke={t.border.primary} tickLine={false} />
                <Tooltip content={chartTooltip} cursor={{ fill:t.bg.hover }} />
                <Bar dataKey="restarts" fill={t.brand.amber} name="redémarrages" isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          ) : <Empty>Aucun pod n'a redémarré.</Empty>}
          <div style={{ fontSize:12, color:'var(--ink-3)', marginTop:4 }}>Compteur cumulé depuis la création du pod.</div>
        </Panel>

        <Panel title="Derniers déploiements" action={<LinkBtn to="/devops">Déployer</LinkBtn>}>
          {(deployStats?.recent||[]).length > 0 ? (
            (deployStats.recent||[]).slice(0,5).map((d,i) => {
              const ok = d.outcome==='ok'
              const label = d.type==='rollback' ? 'retour arrière'
                : ok ? 'réussi' : d.outcome==='regression' ? (d.rollback_ok ? 'casse, annulé' : 'casse')
                : d.outcome==='inconclusive' ? 'non concluant' : (d.outcome || '—')
              return (
                <div key={i} style={{ display:'flex', alignItems:'center', gap:10, padding:'6px 0', borderTop: i ? '1px solid var(--rule)' : 'none' }}>
                  <div style={{ flex:1, minWidth:0 }}>
                    <div style={{ fontSize:13, fontWeight:500, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{(d.objects||[]).join(', ') || d.target || '—'}</div>
                    <div style={{ fontSize:12, color:'var(--ink-3)' }}>{d.ts ? new Date(String(d.ts).replace(/([+-]\d\d)(\d\d)$/, '$1:$2')).toLocaleString('fr-FR', { dateStyle:'short', timeStyle:'short' }) : ''}</div>
                  </div>
                  <span className={ok || d.type==='rollback' ? 'badge badge-neutral' : d.outcome==='inconclusive' ? 'badge badge-warning' : 'badge badge-danger'}>{label}</span>
                </div>
              )
            })
          ) : <Empty>Aucun déploiement enregistré.</Empty>}
        </Panel>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:14, marginBottom:14 }}>
        <Panel title="Nœuds">
          {nodes.map((node,i)=>{
            const m = nm(node.name)
            return (
              <div key={node.name} style={{ display:'grid', gridTemplateColumns:'1fr 110px 110px auto', alignItems:'center', gap:14, padding:'8px 0', borderTop: i ? '1px solid var(--rule)' : 'none' }}>
                <div style={{ minWidth:0 }}>
                  <div style={{ fontSize:13, fontWeight:500, fontFamily:'var(--font-mono)' }}>{node.name}</div>
                  <div style={{ fontSize:12, color:'var(--ink-3)' }}>{node.cpu} CPU, {toGiB(node.memory)!=null ? `${toGiB(node.memory).toFixed(1).replace('.', ',')} Gio` : '—'} de RAM</div>
                </div>
                {['cpu','ram'].map(k => {
                  const v = m?.[`${k}_percent`]
                  return (
                    <div key={k}>
                      <div style={{ display:'flex', justifyContent:'space-between', fontSize:12, color:'var(--ink-2)', marginBottom:3 }}>
                        <span>{k.toUpperCase()}</span><span style={{ fontVariantNumeric:'tabular-nums' }}>{v!=null?`${v} %`:'—'}</span>
                      </div>
                      <div className="progress-bar">
                        <div className="progress-bar-fill" style={{ width:`${v||0}%`, background: v>85?'var(--crit)':v>70?'var(--warn)':'var(--ink-3)' }} />
                      </div>
                    </div>
                  )
                })}
                <span className={node.status==='Ready'?'badge badge-neutral':'badge badge-danger'}>{node.status==='Ready'?'prêt':node.status}</span>
              </div>
            )
          })}
        </Panel>

        <Panel title="Anomalies actives" action={criticals>0 ? <LinkBtn to="/detection">{criticals} critique{criticals>1?'s':''}</LinkBtn> : null}>
          {(detect?.problems||[]).slice(0,5).map((p,i)=>(
            <div key={p.id} onClick={()=>navigate('/detection')} style={{ display:'flex', gap:10, padding:'7px 0', borderTop: i ? '1px solid var(--rule)' : 'none', cursor:'pointer' }}>
              <AlertCircle size={15} style={{ flexShrink:0, marginTop:2, color:p.severity==='critical'?'var(--crit)':'var(--warn)' }} />
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{ fontSize:13, display:'flex', gap:8, alignItems:'baseline' }}>
                  <span style={{ fontWeight:500 }}>{p.type}</span>
                  <span style={{ fontFamily:'var(--font-mono)', fontSize:12.5, color:'var(--ink-2)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{p.namespace}/{p.resource}</span>
                </div>
                <div style={{ fontSize:12.5, color:'var(--ink-3)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{p.description}</div>
              </div>
            </div>
          ))}
          {(!detect?.problems||detect.problems.length===0) && <Empty>{detect ? 'Aucune anomalie détectée.' : 'Analyse en cours…'}</Empty>}
        </Panel>
      </div>

      <Panel title={`Pods (${total})`} action={<LinkBtn to="/pods">Gérer</LinkBtn>}>
        <div style={{ overflowX:'auto' }}>
          <table className="table">
            <thead>
              <tr>{['Nom','Namespace','Nœud','IP','Redémarrages','État'].map(h=>(
                <th key={h} style={{ whiteSpace:'nowrap', textAlign:h==='Redémarrages'?'right':'left' }}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {pods.map(pod=>(
                <tr key={`${pod.namespace}/${pod.name}`} onClick={()=>navigate(`/pods?namespace=${pod.namespace}&search=${pod.name}`)}>
                  <td style={{ fontFamily:'var(--font-mono)', fontSize:12.5 }}>{pod.name}</td>
                  <td style={{ color:'var(--ink-2)' }}>{pod.namespace}</td>
                  <td style={{ color:'var(--ink-2)', fontFamily:'var(--font-mono)', fontSize:12.5 }}>{pod.node||'—'}</td>
                  <td style={{ color:'var(--ink-3)', fontFamily:'var(--font-mono)', fontSize:12.5 }}>{pod.ip||'—'}</td>
                  <td style={{ textAlign:'right', fontVariantNumeric:'tabular-nums', color:pod.restarts>0?'var(--ink)':'var(--ink-3)' }}>{pod.restarts}</td>
                  <td>{badge(pod.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

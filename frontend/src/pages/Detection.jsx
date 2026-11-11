import { useQuery } from '@tanstack/react-query'
import { aiAPI } from '../utils/api'
import { useTheme } from '../utils/ThemeContext'
import { RefreshCw, AlertTriangle, AlertCircle, MessageSquare } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'


export default function Detection() {
  const { theme: t } = useTheme()
  const navigate = useNavigate()
  // ?focus=<id> : alerte ouverte depuis une notification -> surlignée et affichée
  const [params] = useSearchParams()
  const focus = params.get('focus')
  const focusRef = useRef(null)

  // Question préparée pour l'agent de dépannage
  const agentQuestion = (p) => `Le pod ${p.namespace}/${p.resource} est signalé (${p.type} : ${(p.reasons||[]).join(' ; ') || p.description}). Quelle est la cause, et comment le corriger ?`

  const { data: detect, isLoading: dLoad, refetch } = useQuery({
    queryKey: ['detect'],
    queryFn: () => aiAPI.detect().then(r => r.data),
    refetchInterval: 30000,
  })

  useEffect(() => { focusRef.current?.scrollIntoView({ block:'center' }) }, [focus, detect])

  const Card = ({ children, style }) => (
    <div style={{ background:t.bg.card, border:`1px solid ${t.border.primary}`, borderRadius:6, padding:16, ...style }}>
      {children}
    </div>
  )

  return (
    <div style={{ padding:20, overflowY:'auto', height:'100vh', background:t.bg.primary, color:t.text.primary }}>

      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:18 }}>
        <div>
          <h1 className="page-title">Détection d'anomalies</h1>
          <p className="page-subtitle">Anomalies du cluster en temps réel (règles v3).</p>
        </div>
        <button onClick={() => refetch()} className="btn btn-ghost">
          <RefreshCw size={14} /> Actualiser
        </button>
      </div>

      {/* ANOMALIES DU CLUSTER — en premier : c'est ce que l'utilisateur vient voir */}
      <Card style={{ marginBottom:14 }}>
        <div style={{ display:'flex', alignItems:'baseline', justifyContent:'space-between', gap:8, marginBottom:6 }}>
          <div style={{ fontSize:14, fontWeight:600, color:t.text.primary }}>
            Anomalies sur le cluster
            {(detect?.critical||0) > 0 && (
              <span className="badge badge-danger" style={{ marginLeft:8 }}>
                {detect.critical} critique{detect.critical>1?'s':''}
              </span>
            )}
          </div>
          <span style={{ fontSize:12.5, color:t.text.muted }}>Relevé toutes les 30 s</span>
        </div>
        <div style={{ fontSize:13, color:t.text.secondary, marginBottom:10, maxWidth:'80ch' }}>
          Critère : règles v3. Un pod est signalé s'il redémarre encore (dernier redémarrage il y a moins
          de 2 min), s'il n'est jamais prêt, ou s'il est bloqué ou en échec. Un pod qui a redémarré
          autrefois mais tourne normalement depuis n'est pas signalé (mémoire, section 6.1.3).
        </div>
        {dLoad ? (
          <div style={{ textAlign:'center', color:t.text.muted, padding:'30px 0' }}>Analyse en cours…</div>
        ) : (detect?.problems||[]).map((p,i) => {
          const focused = focus && p.id === focus
          return (
            <div key={p.id||i} ref={focused ? focusRef : null} style={{
              display:'flex', alignItems:'flex-start', gap:10, padding:'10px 10px',
              borderTop: i ? `1px solid ${t.border.secondary}` : 'none',
              background: focused ? 'var(--bg-active)' : 'transparent',
              borderLeft: `3px solid ${focused ? 'var(--accent)' : 'transparent'}`,
            }}>
              <AlertCircle size={16} style={{ flexShrink:0, marginTop:2, color:p.severity==='critical'?'var(--crit)':'var(--warn)' }} />
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{ fontSize:13, marginBottom:2, color:t.text.primary, display:'flex', gap:8, alignItems:'baseline', flexWrap:'wrap' }}>
                  <span style={{ fontWeight:600 }}>{p.type}</span>
                  <span style={{ fontFamily:'var(--font-mono)', fontSize:12.5, color:t.text.secondary }}>{p.namespace}/{p.resource}</span>
                </div>
                <div style={{ fontSize:13, color:t.text.secondary }}>{(p.reasons && p.reasons.length ? p.reasons : [p.description]).join(' ; ')}</div>
              </div>
              <div style={{ display:'flex', gap:6, flexShrink:0 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/pods?namespace=${encodeURIComponent(p.namespace)}&search=${encodeURIComponent(p.resource)}`)}>Voir le pod</button>
                <button className="btn btn-primary btn-sm" onClick={() => navigate(`/assistant?q=${encodeURIComponent(agentQuestion(p))}`)}>
                  <MessageSquare size={13} /> Demander à l'agent
                </button>
              </div>
            </div>
          )
        })}
        {(!detect?.problems||detect.problems.length===0) && !dLoad && (
          <div style={{ color:t.text.secondary, fontSize:14, padding:'14px 0' }}>Aucune anomalie : tous les pods sont stables, prêts et planifiés.</div>
        )}
        {focus && !dLoad && !(detect?.problems||[]).some(p => p.id === focus) && (
          <div style={{ fontSize:13, color:t.text.secondary, marginTop:8 }}>
            L'alerte ouverte depuis la notification n'est plus active : le problème a disparu depuis.
          </div>
        )}
      </Card>

      {/* L'évaluation hors ligne du Random Forest (jeu synthétique) a été retirée de
          cette page : le modèle est comparé en direct, sur les pods réels, dans la
          page « Comparaison ». */}
      <div style={{ fontSize:13, color:t.text.secondary, lineHeight:1.6, marginTop:14 }}>
        Le modèle Random Forest est comparé aux règles, en direct sur les pods réels, dans la page{' '}
        <a href="/comparison" style={{ color:'var(--accent)', textDecoration:'none' }}>Comparaison</a>.
      </div>
    </div>
  )
}

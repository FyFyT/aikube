import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search } from 'lucide-react'
import { useTheme } from '../utils/ThemeContext'
import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 10000 })

export default function GlobalSearch({ isOpen, onClose }) {
  const { theme: t } = useTheme()
  const navigate = useNavigate()
  const inputRef = useRef(null)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState(0)

  useEffect(() => {
    if (isOpen) {
      setQuery('')
      setResults([])
      setSelected(0)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [isOpen])

  useEffect(() => {
    if (!query.trim()) { setResults([]); return }
    const timer = setTimeout(() => search(query), 200)
    return () => clearTimeout(timer)
  }, [query])

  const search = async (q) => {
    setLoading(true)
    try {
      const [pods, deps, ns] = await Promise.all([
        api.get('/cluster/pods').then(r => r.data.pods || []),
        api.get('/cluster/deployments').then(r => r.data.deployments || []),
        api.get('/cluster/namespaces').then(r => r.data.namespaces || []),
      ])

      const lower = q.toLowerCase()
      const matches = []

      pods.filter(p => p.name.toLowerCase().includes(lower) || p.namespace.toLowerCase().includes(lower))
        .slice(0,4).forEach(p => matches.push({
          type:'pod', label:p.name, sub:`${p.namespace}, ${p.status}`,
          action:() => { navigate(`/pods?namespace=${p.namespace}&search=${p.name}`); onClose() }
        }))

      deps.filter(d => d.name.toLowerCase().includes(lower))
        .slice(0,3).forEach(d => matches.push({
          type:'deployment', label:d.name, sub:`${d.namespace}, ${d.ready}/${d.replicas} prêts`,
          action:() => { navigate('/pods'); onClose() }
        }))

      ns.filter(n => n.name.toLowerCase().includes(lower))
        .slice(0,3).forEach(n => matches.push({
          type:'namespace', label:n.name, sub:n.status,
          action:() => { navigate(`/pods?namespace=${n.name}`); onClose() }
        }))

      // Pages de l'app
      const pages = [
        { label:'Tableau de bord',            path:'/dashboard',  sub:'État du cluster' },
        { label:'Pods et déploiements',       path:'/pods',       sub:'Gérer pods, deployments et namespaces' },
        { label:'Génération YAML',            path:'/yaml',       sub:'Générer et valider des manifestes' },
        { label:'Agent de dépannage',         path:'/assistant',  sub:'Poser une question sur le cluster' },
        { label:'Détection',                  path:'/detection',  sub:'Anomalies des pods' },
        { label:'Comparaison des détecteurs', path:'/comparison', sub:'Règles et modèle sur les mêmes pods' },
        { label:'Prédiction mémoire',         path:'/prediction', sub:'Épuisement mémoire à venir' },
        { label:'Ressources',                 path:'/resources',  sub:'Requests, limits et recommandations' },
        { label:'Sécurité',                   path:'/security',   sub:'Contrôles CIS et MITRE ATT&CK' },
        { label:'Déploiement',                path:'/devops',     sub:'Déploiement surveillé avec retour arrière' },
      ]
      pages.filter(p => p.label.toLowerCase().includes(lower) || p.sub.toLowerCase().includes(lower))
        .forEach(p => matches.push({
          type:'page', label:p.label, sub:p.sub,
          color:'var(--accent)',
          action:() => { navigate(p.path); onClose() }
        }))

      setResults(matches.slice(0,10))
      setSelected(0)
    } catch {}
    setLoading(false)
  }

  const handleKey = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setSelected(s => Math.min(s+1, results.length-1)) }
    if (e.key === 'ArrowUp')   { e.preventDefault(); setSelected(s => Math.max(s-1, 0)) }
    if (e.key === 'Enter' && results[selected]) results[selected].action()
    if (e.key === 'Escape') onClose()
  }

  if (!isOpen) return null

  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(17,24,32,.45)', zIndex:2000, display:'flex', alignItems:'flex-start', justifyContent:'center', paddingTop:100 }}
      onClick={onClose}>
      <div style={{ width:580, background:t.bg.card, border:`1px solid ${t.border.primary}`, borderRadius:6, boxShadow:'var(--shadow-lg)', overflow:'hidden' }}
        onClick={e => e.stopPropagation()}>

        {/* INPUT */}
        <div style={{ display:'flex', alignItems:'center', gap:12, padding:'14px 16px', borderBottom:`1px solid ${t.border.primary}` }}>
          <Search size={16} style={{ color:t.text.muted }} />
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Pod, deployment, namespace ou page"
            style={{ flex:1, background:'transparent', border:'none', outline:'none', fontSize:15, color:t.text.primary, fontFamily:'inherit' }}
          />
          {loading && <span style={{ fontSize:12.5, color:t.text.muted }}>...</span>}
          <kbd style={{ fontSize:11.5, color:t.text.muted, padding:'2px 6px', borderRadius:4, border:`1px solid ${t.border.primary}` }}>Échap</kbd>
        </div>

        {/* RÉSULTATS */}
        <div style={{ maxHeight:400, overflowY:'auto' }}>
          {results.length === 0 && query.trim() && !loading && (
            <div style={{ padding:'30px 0', textAlign:'center', color:t.text.muted, fontSize:14 }}>Aucun résultat pour "{query}"</div>
          )}
          {results.length === 0 && !query.trim() && (
            <div style={{ padding:'20px 16px' }}>
              <div style={{ fontSize:12.5, color:t.text.muted, marginBottom:10 }}>Navigation rapide</div>
              {[
                { label:'Pods et déploiements', path:'/pods' },
                { label:'Génération YAML',      path:'/yaml' },
                { label:'Sécurité',             path:'/security' },
                { label:'Déploiement',          path:'/devops' },
              ].map((item,i) => (
                <div key={i} onClick={() => { navigate(item.path); onClose() }} style={{
                  display:'flex', alignItems:'center', gap:10, padding:'8px 10px',
                  borderRadius:6, cursor:'pointer', fontSize:14, color:t.text.secondary,
                  marginBottom:2,
                }}
                onMouseEnter={e => e.currentTarget.style.background=t.bg.tertiary}
                onMouseLeave={e => e.currentTarget.style.background='transparent'}
                >{item.label}</div>
              ))}
            </div>
          )}
          {results.map((r,i) => (
            <div key={i} onClick={r.action} style={{
              display:'flex', alignItems:'center', gap:12, padding:'10px 16px',
              cursor:'pointer', borderBottom:`1px solid ${t.border.secondary}`,
              background:i===selected?t.bg.tertiary:'transparent',
            }}
            onMouseEnter={() => setSelected(i)}>
              <div style={{ flex:1 }}>
                <div style={{ fontSize:14, fontWeight:500, color:t.text.primary, fontFamily:r.type==='page'?'inherit':'var(--font-mono)' }}>{r.label}</div>
                <div style={{ fontSize:12.5, color:t.text.muted }}>{r.sub}</div>
              </div>
              <span className="badge badge-neutral">{r.type}</span>
            </div>
          ))}
        </div>

        {/* FOOTER */}
        <div style={{ padding:'8px 16px', borderTop:`1px solid ${t.border.primary}`, display:'flex', gap:12, fontSize:11.5, color:t.text.muted }}>
          <span>Flèches pour naviguer</span>
          <span>Entrée pour ouvrir</span>
          <span>Échap pour fermer</span>
        </div>
      </div>
    </div>
  )
}

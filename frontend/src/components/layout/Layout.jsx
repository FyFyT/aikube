// src/components/layout/Layout.jsx
// -----------------------------------------------------------------------------
// Barre latérale et zone principale.
//
// Le badge de "Détection" lit `unreadCritical` (anomalies critiques non lues) :
// il s'efface dès que l'utilisateur ouvre le panneau de notifications ou visite
// /detection, et réapparaît sur nouvelle anomalie.
//
// L'état du cluster (nœuds prêts) est lu dans le même cache React Query que le
// tableau de bord (clé 'summary') : aucune requête supplémentaire.
// -----------------------------------------------------------------------------

import { Outlet, NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutDashboard, Boxes, FileCode, MessageSquare, Activity, GitCompare,
  TrendingUp, Gauge, ShieldCheck, Workflow, Moon, Sun, AppWindow,
} from 'lucide-react'
import api from '../../utils/api'
import { useTheme } from '../../utils/ThemeContext'
import { useNotifications } from '../../utils/NotificationsContext'
import NotificationCenter from '../NotificationCenter'
import PlatformHealthBanner from './PlatformHealthBanner'

const sections = [
  { label: 'Cluster', items: [
    { path: '/dashboard',  icon: LayoutDashboard, label: 'Tableau de bord' },
    { path: '/pods',       icon: Boxes,           label: 'Pods et déploiements' },
  ]},
  { label: 'Générer et diagnostiquer', items: [
    { path: '/yaml',       icon: FileCode,        label: 'Génération YAML' },
    { path: '/assistant',  icon: MessageSquare,   label: 'Agent de dépannage' },
  ]},
  { label: 'Surveiller', items: [
    { path: '/detection',  icon: Activity,        label: 'Détection' },
    { path: '/comparison', icon: GitCompare,      label: 'Comparaison' },
    { path: '/prediction', icon: TrendingUp,      label: 'Prédiction mémoire' },
    { path: '/resources',  icon: Gauge,           label: 'Ressources' },
  ]},
  { label: 'Sécuriser', items: [
    { path: '/security',   icon: ShieldCheck,     label: 'Sécurité' },
  ]},
  { label: 'Déployer', items: [
    { path: '/devops',     icon: Workflow,        label: 'Déploiement' },
    { path: '/apps',       icon: AppWindow,       label: 'Mes applications' },
  ]},
]

// Marque : un hexagone au trait (clin d'œil à Kubernetes) et un nœud central
function Mark() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true" style={{ flexShrink: 0 }}>
      <path d="M14 2.5 24 8.25v11.5L14 25.5 4 19.75V8.25Z" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" />
      <path d="M14 14V7.5M14 14l5.6 3.25M14 14l-5.6 3.25" stroke="var(--accent)" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="14" cy="14" r="2.6" fill="var(--accent)" />
    </svg>
  )
}

export default function Layout() {
  const { themeName, toggle } = useTheme()
  const { unreadCritical } = useNotifications()

  const { data: summary, isError } = useQuery({
    queryKey: ['summary'],
    queryFn: () => api.get('/cluster/summary').then(r => r.data),
    refetchInterval: 45000,
  })
  const nodes = summary?.nodes?.nodes || []
  const ready = nodes.filter(n => n.status === 'Ready').length
  const clusterOk = !isError && nodes.length > 0 && ready === nodes.length
  const clusterLine = isError ? 'injoignable'
    : !summary ? 'connexion…'
    : `${ready} nœud${ready > 1 ? 's' : ''} prêt${ready > 1 ? 's' : ''} sur ${nodes.length}`

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: 'var(--bg-base)' }}>

      {/* data-theme="dark" : la barre latérale utilise toujours les couleurs sombres */}
      <aside data-theme="dark" style={{
        width: 232, flexShrink: 0, display: 'flex', flexDirection: 'column',
        background: 'var(--sidebar-deep)', color: 'var(--ink)',
      }}>

        <div style={{ padding: '18px 16px 16px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <Mark />
          <div>
            <div style={{ fontSize: 17, fontWeight: 600, letterSpacing: '-.01em', color: 'var(--ink)', lineHeight: 1.2 }}>AI-Kube</div>
            <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>Kubernetes, avec IA</div>
          </div>
        </div>

        <div style={{
          margin: '0 12px 8px', padding: '8px 8px 8px 10px',
          display: 'flex', alignItems: 'center', gap: 9,
          background: 'rgba(255,255,255,.04)', borderRadius: 'var(--radius-lg)',
        }}>
          <span style={{
            width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
            background: clusterOk ? 'var(--ok)' : isError ? 'var(--crit)' : 'var(--warn)',
          }} />
          <div style={{ flex: 1, minWidth: 0, lineHeight: 1.3 }}>
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>minikube</div>
            <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>{clusterLine}</div>
          </div>
          <NotificationCenter />
        </div>

        <nav style={{ padding: '4px 12px 12px', flex: 1, overflowY: 'auto' }}>
          {sections.map(section => (
            <div key={section.label} style={{ marginTop: 14 }}>
              <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--ink-3)', padding: '0 10px 4px' }}>
                {section.label}
              </div>
              {section.items.map(({ path, icon: Icon, label }) => (
                <NavLink key={path} to={path} className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
                  <Icon size={16} strokeWidth={1.75} style={{ flexShrink: 0 }} />
                  <span style={{ flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
                  {path === '/detection' && unreadCritical > 0 && (
                    <span title={`${unreadCritical} anomalie(s) critique(s) non lue(s)`} style={{
                      fontSize: 11.5, fontWeight: 600, lineHeight: '18px', minWidth: 18, padding: '0 5px',
                      textAlign: 'center', borderRadius: 'var(--radius-sm)',
                      background: 'var(--crit)', color: 'var(--surface)',
                    }}>{unreadCritical}</span>
                  )}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div style={{ borderTop: '1px solid rgba(255,255,255,.08)', padding: '10px 12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ fontSize: 12.5, color: 'var(--ink-3)', lineHeight: 1.35, paddingLeft: 4 }}>
            Tamft<br />Master recherche
          </div>
          <button
            onClick={toggle}
            className="btn btn-ghost btn-sm"
            title={themeName === 'dark' ? 'Passer au thème clair' : 'Passer au thème sombre'}
            aria-label={themeName === 'dark' ? 'Passer au thème clair' : 'Passer au thème sombre'}
          >
            {themeName === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
            {themeName === 'dark' ? 'Clair' : 'Sombre'}
          </button>
        </div>
      </aside>

      <main style={{ flex: 1, overflow: 'auto', background: 'var(--bg-base)' }}>
        <PlatformHealthBanner />
        <Outlet />
      </main>
    </div>
  )
}

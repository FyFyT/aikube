// src/components/layout/PlatformHealthBanner.jsx
// -----------------------------------------------------------------------------
// Bandeau affiché sur TOUTES les pages quand la source de mesure du cluster
// (metrics-server) est en panne. Constaté le 26/10/2026 : le prédicteur OOM et les
// recommandations restaient muets sans le dire. Une absence d'alerte n'est pas
// une absence de risque : la panne doit être visible.
// -----------------------------------------------------------------------------
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, RefreshCw, X } from 'lucide-react'
import api from '../../utils/api'

export default function PlatformHealthBanner() {
  const [hidden, setHidden] = useState(false)
  const { data, refetch, isFetching } = useQuery({
    queryKey: ['platform-health'],
    queryFn: () => api.get('/devops/v2/platform-health').then(r => r.data),
    refetchInterval: 60000,
    retry: false,
  })
  if (!data?.degraded || hidden) return null
  const ms = data.metrics_server || {}
  return (
    <div className="issue-item critical" style={{ margin: '12px 20px 0', display: 'flex', gap: 10, alignItems: 'flex-start' }}>
      <AlertTriangle size={16} style={{ color: 'var(--color-red)', flexShrink: 0, marginTop: 2 }} />
      <div style={{ flex: 1, fontSize: 12, lineHeight: 1.5 }}>
        <b>Mesures du cluster indisponibles.</b> {data.impact}
        <div className="text-muted">
          Cause : {ms.available === false ? `metrics-server — ${ms.reason}` : data.oom_predictor_error}
        </div>
        {data.fix && <div style={{ marginTop: 4 }}>Réparer (terminal Ubuntu WSL) : <span className="text-mono">{data.fix}</span></div>}
      </div>
      <button className="btn btn-ghost btn-sm" title="Revérifier"
        onClick={() => api.get('/devops/v2/platform-health', { params: { refresh: true } }).then(() => refetch())}>
        <RefreshCw size={11} className={isFetching ? 'spin' : ''} />
      </button>
      <button className="btn btn-ghost btn-sm" title="Masquer" onClick={() => setHidden(true)}><X size={11} /></button>
    </div>
  )
}

// src/pages/DetectorComparison.jsx
// -----------------------------------------------------------------------------
// Comparaison des détecteurs d'anomalies — instrument de mesure.
//
// POURQUOI CETTE PAGE
// -------------------
// La page « Détection IA » affiche F1 = 0,9130, métrique du Random Forest
// mesurée sur ses propres données synthétiques. Le chapitre 6 du mémoire
// établit que ce chiffre ne caractérise pas la performance sur des pods réels.
// Laisser l'interface l'afficher revient à ce que l'application contredise le
// manuscrit.
//
// Cette page substitue à une métrique déclarative une MESURE EN DIRECT : les
// quatre détecteurs sont appliqués aux pods du cluster à l'instant de la
// consultation, et leurs accords sont calculés sous les yeux de l'utilisateur.
//
// L'application cesse d'être une vitrine pour devenir l'instrument
// expérimental — argument défendable en soutenance : les résultats du
// chapitre 6 sont reproductibles à la demande.
//
// STYLES
// ------
// Écrite exclusivement avec les classes du design system (.card, .badge, .btn)
// et les variables CSS, sans objet de thème JavaScript. Sert de référence pour
// la migration progressive des autres pages.
// -----------------------------------------------------------------------------

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

const DETECTORS = [
  { key: 'rules_v1', label: 'Règles v1',    sub: 'seuil absolu ≥ 3 redémarrages' },
  { key: 'rules_v2', label: 'Règles v2',    sub: 'taux > 0,5 / heure' },
  { key: 'rules_v3', label: 'Règles v3',    sub: 'stabilité récente + disponibilité' },
  { key: 'ml',       label: 'Random Forest', sub: '9 variables, 700 échantillons' },
]

const PAIRS = [
  { key: 'v1_vs_v2', label: 'v1 / v2' },
  { key: 'v1_vs_v3', label: 'v1 / v3' },
  { key: 'v2_vs_v3', label: 'v2 / v3' },
  { key: 'v1_vs_ml', label: 'v1 / ML' },
  { key: 'v2_vs_ml', label: 'v2 / ML' },
  { key: 'v3_vs_ml', label: 'v3 / ML' },
]

export default function DetectorComparison() {
  const [tab, setTab] = useState('live')

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['detect-compare'],
    queryFn: () => api.get('/ai/detect/compare').then(r => r.data),
    refetchInterval: 60000,
    retry: 1,
  })

  const { data: history } = useQuery({
    queryKey: ['detect-compare-history'],
    queryFn: () => api.get('/ai/detect/compare/history').then(r => r.data),
    enabled: tab === 'history',
    retry: 1,
  })

  const kappaBadge = (k) => {
    if (k === null || k === undefined) return 'badge-neutral'
    if (k >= 0.8) return 'badge-success'
    if (k >= 0.4) return 'badge-warning'
    return 'badge-danger'
  }

  return (
    <div style={{ padding: 'var(--space-6)' }}>

      {/* En-tête */}
      <header style={{ marginBottom: 'var(--space-5)' }}>
        <h1 className="page-title">
          Comparaison des détecteurs
        </h1>
        <p className="page-subtitle">
          Quatre détecteurs appliqués aux mêmes pods, en direct (contribution C3).
        </p>
      </header>

      {/* Avertissement méthodologique — remplace l'affichage du F1 synthétique */}
      <div className="card" style={{ marginBottom: 'var(--space-5)', borderLeft: '3px solid var(--color-amber)' }}>
        <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          <strong style={{ color: 'var(--text-primary)' }}>Mesure en direct.</strong>{' '}
          Les valeurs ci-dessous sont calculées à l'instant de la consultation,
          sur les pods du cluster courant — et non sur un jeu de données
          d'entraînement.
        </div>
      </div>

      {/* Onglets */}
      <div className="tabs" style={{ marginBottom: 'var(--space-5)' }}>
        <button className={`tab ${tab === 'live' ? 'active' : ''}`} onClick={() => setTab('live')}>
          Mesure en direct
        </button>
        <button className={`tab ${tab === 'history' ? 'active' : ''}`} onClick={() => setTab('history')}>
          Série longitudinale
        </button>
        <button
          className="btn btn-ghost btn-sm"
          style={{ marginLeft: 'auto' }}
          onClick={() => refetch()}
          disabled={isFetching}
        >
          {isFetching ? 'Mesure…' : '↻ Relancer'}
        </button>
      </div>

      {isLoading && (
        <div className="card" style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
          Application des détecteurs au cluster…
        </div>
      )}

      {isError && (
        <div className="card" style={{ borderLeft: '3px solid var(--color-red)' }}>
          <div style={{ color: 'var(--color-red)', fontSize: 'var(--font-size-sm)' }}>
            Mesure impossible : {error?.response?.data?.detail || error?.message}
          </div>
          <div style={{ color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)', marginTop: 6 }}>
            Vérifier que le modèle est entraîné et que le cluster répond.
          </div>
        </div>
      )}

      {/* ── MESURE EN DIRECT ────────────────────────────────────────────── */}
      {tab === 'live' && data && (
        <>
          {/* Détections par détecteur */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 'var(--space-4)', marginBottom: 'var(--space-5)' }}>
            {DETECTORS.map(d => {
              const n = data.detections?.[d.key]
              const total = data.pods_analysed || 0
              const isAll = n === total && total > 0
              const isNone = n === 0
              return (
                <div key={d.key} className="card">
                  <div className="card-title" style={{ marginBottom: 2 }}>{d.label}</div>
                  <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginBottom: 10 }}>
                    {d.sub}
                  </div>
                  <div style={{
                    fontSize: 'var(--font-size-2xl)', fontWeight: 600, fontFamily: 'var(--font-mono)',
                    color: n === undefined ? 'var(--text-muted)'
                         : isAll ? 'var(--color-red)'
                         : isNone ? 'var(--text-secondary)'
                         : 'var(--color-amber)',
                  }}>
                    {n === undefined ? '—' : n}
                    <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)', fontWeight: 400 }}>
                      {' '}/ {total}
                    </span>
                  </div>
                  {isAll && (
                    <div className="badge badge-danger" style={{ marginTop: 8 }}>
                      signale tous les pods
                    </div>
                  )}
                  {isNone && (
                    <div className="badge badge-neutral" style={{ marginTop: 8 }}>
                      aucun signalement
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* Accords deux à deux */}
          <div className="card" style={{ marginBottom: 'var(--space-5)' }}>
            <div className="card-title">Accords deux à deux</div>
            <p style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: 0, marginBottom: 'var(--space-4)' }}>
              Le kappa de Cohen corrige l'accord observé de l'accord attendu par hasard.
              Il n'est pas défini lorsqu'un détecteur ne produit qu'une seule classe.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 'var(--space-3)' }}>
              {PAIRS.map(p => {
                const c = data.comparisons?.[p.key]
                if (!c) return null
                const k = c.cohen_kappa
                return (
                  <div key={p.key} style={{
                    background: 'var(--bg-elevated)', border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-md)', padding: 'var(--space-3)',
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--font-size-sm)', color: 'var(--text-primary)' }}>
                        {p.label}
                      </span>
                      <span className={`badge ${kappaBadge(k)}`}>
                        κ = {k === null || k === undefined ? 'n/d' : k.toFixed(3)}
                      </span>
                    </div>
                    <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)' }}>
                      Accord brut {c.raw_percent}&nbsp;%
                    </div>
                    <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: 3 }}>
                      {c.interpretation}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Désaccords */}
          <div className="card">
            <div className="card-title">
              Pods en désaccord
              <span className="badge badge-neutral" style={{ marginLeft: 8 }}>
                {data.disagreement_count ?? 0}
              </span>
            </div>
            {(!data.disagreements || data.disagreements.length === 0) ? (
              <div style={{ color: 'var(--text-muted)', fontSize: 'var(--font-size-sm)', padding: 'var(--space-4) 0' }}>
                Les quatre détecteurs sont unanimes sur tous les pods.
              </div>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--font-size-xs)' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      {['Pod', 'Statut', 'Redém.', 'Taux/h', 'Uptime', 'v1', 'v2', 'v3', 'ML', 'p(ML)'].map(h => (
                        <th key={h} style={{ textAlign: 'left', padding: '8px 10px', color: 'var(--text-muted)', fontWeight: 500, whiteSpace: 'nowrap' }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.disagreements.slice(0, 40).map((d, i) => (
                      <tr key={`${d.namespace}/${d.pod}-${i}`} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                          <div>{d.pod}</div>
                          <div style={{ color: 'var(--text-muted)', fontSize: 11.5 }}>{d.namespace}</div>
                        </td>
                        <td style={{ padding: '8px 10px', color: 'var(--text-secondary)' }}>{d.status}</td>
                        <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>{d.restarts}</td>
                        <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                          {d.restart_rate != null ? d.restart_rate.toFixed(4) : '—'}
                        </td>
                        <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                          {d.uptime_seconds != null ? `${Math.round(d.uptime_seconds)}s` : '—'}
                        </td>
                        {['rules_v1', 'rules_v2', 'rules_v3', 'ml'].map(k => (
                          <td key={k} style={{ padding: '8px 10px', textAlign: 'center' }}>
                            <span className={`badge ${d[k] === 1 ? 'badge-danger' : 'badge-neutral'}`}>
                              {d[k] === 1 ? '1' : '0'}
                            </span>
                          </td>
                        ))}
                        <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                          {d.ml_probability != null ? d.ml_probability.toFixed(3) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {data.disagreements.length > 40 && (
                  <div style={{ color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)', paddingTop: 10 }}>
                    {data.disagreements.length - 40} pods supplémentaires non affichés.
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}

      {/* ── SÉRIE LONGITUDINALE ─────────────────────────────────────────── */}
      {tab === 'history' && (
        history?.error ? (
          <div className="card" style={{ color: 'var(--text-muted)' }}>{history.error}</div>
        ) : history ? (
          <>
            <div className="card" style={{ marginBottom: 'var(--space-5)' }}>
              <div className="card-title">Série accumulée</div>
              <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>
                {history.observations_count} observations
                {history.period && (
                  <> · du {String(history.period.from).slice(0, 10)} au {String(history.period.to).slice(0, 10)}</>
                )}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 'var(--space-3)', marginTop: 'var(--space-4)' }}>
                {DETECTORS.map(d => {
                  const m = history.mean_detections?.[d.key]
                  if (!m) return null
                  return (
                    <div key={d.key} style={{
                      background: 'var(--bg-elevated)', border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-md)', padding: 'var(--space-3)',
                    }}>
                      <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>{d.label}</div>
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--font-size-lg)', color: 'var(--text-primary)' }}>
                        {m.mean}
                      </div>
                      <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
                        min {m.min} · max {m.max} · sur {m.observations} obs.
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            {history.recurrent_disagreements?.length > 0 && (
              <div className="card">
                <div className="card-title">Désaccords récurrents</div>
                <p style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: 0, marginBottom: 'var(--space-4)' }}>
                  Un désaccord qui se répète à chaque observation révèle une divergence
                  structurelle entre détecteurs, par opposition à un état transitoire.
                </p>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--font-size-xs)' }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border)' }}>
                        {['Pod', 'Occurrences', 'Dernier statut', 'Redém.', 'Motif dominant'].map(h => (
                          <th key={h} style={{ textAlign: 'left', padding: '8px 10px', color: 'var(--text-muted)', fontWeight: 500 }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {history.recurrent_disagreements.slice(0, 20).map(r => {
                        const dominant = Object.entries(r.pattern || {})
                          .sort((a, b) => b[1] - a[1])[0]
                        return (
                          <tr key={r.pod} style={{ borderBottom: '1px solid var(--border)' }}>
                            <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{r.pod}</td>
                            <td style={{ padding: '8px 10px' }}>
                              <span className="badge badge-warning">{r.occurrences}×</span>
                            </td>
                            <td style={{ padding: '8px 10px', color: 'var(--text-secondary)' }}>{r.last_status}</td>
                            <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>{r.last_restarts}</td>
                            <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                              {dominant ? `${dominant[0]} (${dominant[1]}×)` : '—'}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="card" style={{ color: 'var(--text-muted)' }}>Chargement de la série…</div>
        )
      )}
    </div>
  )
}

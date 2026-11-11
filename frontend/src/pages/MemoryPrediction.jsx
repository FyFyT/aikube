// src/pages/MemoryPrediction.jsx
// -----------------------------------------------------------------------------
// Prédiction d'épuisement mémoire.
//
// CE QUE CETTE PAGE ANNONCE
// -------------------------
// Une ÉCHÉANCE — « ce conteneur atteindra sa limite dans N minutes » — assortie
// d'un indice de confiance portant sur la fiabilité de l'extrapolation.
//
// L'indice n'est pas une probabilité de défaillance. L'interface l'énonce
// explicitement plutôt que d'afficher un pourcentage dont l'origine serait
// opaque : annoncer « 80 % de risque de crash » supposerait une base
// fréquentielle inexistante ici.
//
// Les trois composantes de la confiance sont détaillées pour chaque pod, afin
// qu'un lecteur puisse juger lui-même de la solidité de la prédiction plutôt
// que de se fier à un score agrégé.
//
// STYLES
// ------
// Classes du design system et variables CSS uniquement, sans objet de thème JS.
// -----------------------------------------------------------------------------

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

const formatEta = (seconds) => {
  if (seconds == null) return '—'
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  const r = s % 60
  if (m < 60) return r ? `${m} min ${r} s` : `${m} min`
  const h = Math.floor(m / 60)
  return `${h} h ${m % 60} min`
}

// L'urgence dépend de l'échéance, non de la confiance : une prédiction peu
// fiable mais imminente mérite davantage d'attention qu'une prédiction sûre
// à deux heures.
const urgency = (eta) => {
  if (eta == null) return { color: 'var(--text-muted)', label: '—' }
  if (eta < 120)  return { color: 'var(--color-red)',   label: 'imminent' }
  if (eta < 600)  return { color: 'var(--color-amber)', label: 'proche' }
  return { color: 'var(--color-blue)', label: 'à surveiller' }
}

const confidenceLabel = (c) => {
  if (c >= 0.85) return 'très fiable'
  if (c >= 0.6)  return 'fiable'
  if (c >= 0.4)  return 'incertaine'
  return 'faible'
}

function Bar({ value, color }) {
  return (
    <div style={{
      height: 5, borderRadius: 'var(--radius-full)',
      background: 'var(--bg-elevated)', overflow: 'hidden',
    }}>
      <div style={{
        width: `${Math.round((value || 0) * 100)}%`, height: '100%',
        background: color || 'var(--color-purple)',
        transition: 'width var(--transition-slow)',
      }} />
    </div>
  )
}

export default function MemoryPrediction() {
  const [showDiagnostics, setShowDiagnostics] = useState(false)

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['predict-memory'],
    queryFn: () => api.get('/ai/predict/memory').then(r => r.data),
    refetchInterval: 30000,
    retry: 1,
  })

  const preds = data?.predictions || []

  return (
    <div style={{ padding: 'var(--space-6)' }}>

      <header style={{ marginBottom: 'var(--space-5)' }}>
        <h1 className="page-title">
          Prédiction d'épuisement mémoire
        </h1>
        <p className="page-subtitle">
          Extrapolation de la consommation mémoire vers la limite déclarée de chaque conteneur.
        </p>
      </header>

      {/* Ce que la confiance signifie — placé avant les résultats, non en note
          de bas de page, pour qu'aucun pourcentage ne soit lu hors contexte. */}
      <div className="card" style={{ marginBottom: 'var(--space-5)', borderLeft: '3px solid var(--color-blue)' }}>
        <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          <strong style={{ color: 'var(--text-primary)' }}>Lecture de la confiance.</strong>{' '}
          Elle qualifie la fiabilité de l'<em>extrapolation</em> — qualité de l'ajustement
          linéaire, accord entre deux estimateurs de pente, stabilité des prédictions
          successives. Ce n'est <strong>pas</strong> une probabilité de défaillance :
          l'établir exigerait un historique d'incidents dont le système ne dispose pas.
          Une confiance élevée signifie que la trajectoire est régulière, non que la
          panne est certaine.
        </div>
      </div>

      {/* État de la collecte */}
      <div style={{ display: 'flex', gap: 'var(--space-4)', marginBottom: 'var(--space-5)', flexWrap: 'wrap' }}>
        {[
          { label: 'Pods suivis',        value: data?.pods_tracked ?? '—' },
          { label: 'Historique suffisant', value: data?.pods_with_history ?? '—' },
          { label: 'À risque',           value: preds.length,
            color: preds.length > 0 ? 'var(--color-amber)' : 'var(--color-green)' },
          { label: 'Échantillonnage',    value: data ? `${data.sample_interval_seconds} s` : '—' },
        ].map(k => (
          <div key={k.label} className="card" style={{ flex: '1 1 150px', minWidth: 150 }}>
            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>{k.label}</div>
            <div style={{
              fontVariantNumeric: 'tabular-nums', fontSize: 'var(--font-size-xl)',
              fontWeight: 600, color: k.color || 'var(--text-primary)',
            }}>{k.value}</div>
          </div>
        ))}
        <button className="btn btn-ghost btn-sm" onClick={() => refetch()} disabled={isFetching}
                style={{ alignSelf: 'center' }}>
          {isFetching ? 'Mesure…' : '↻ Actualiser'}
        </button>
      </div>

      {isLoading && (
        <div className="card" style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
          Interrogation du collecteur…
        </div>
      )}

      {isError && (
        <div className="card" style={{ borderLeft: '3px solid var(--color-red)' }}>
          <div style={{ color: 'var(--color-red)', fontSize: 'var(--font-size-sm)' }}>
            {error?.response?.data?.detail || error?.message}
          </div>
        </div>
      )}

      {data?.error && (
        <div className="card" style={{ marginBottom: 'var(--space-4)', borderLeft: '3px solid var(--color-amber)' }}>
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-amber)' }}>
            Collecte dégradée : {data.error}
          </div>
          <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: 4 }}>
            La télémétrie est fournie par metrics-server. Vérifier son activation.
          </div>
        </div>
      )}

      {data?.warmup && (
        <div className="card" style={{ marginBottom: 'var(--space-4)', borderLeft: '3px solid var(--color-blue)' }}>
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>
            <strong style={{ color: 'var(--text-primary)' }}>Historique en constitution.</strong>{' '}
            Trois relevés distincts sont nécessaires pour estimer une tendance, et
            metrics-server n'actualise ses agrégats qu'environ toutes les 60 secondes.
            Comptez deux à quatre minutes après le démarrage.
            <br />
            <span style={{ color: 'var(--text-muted)' }}>
              L'absence de prédiction ne vaut pas absence de risque tant que cet
              avertissement est affiché.
            </span>
          </div>
        </div>
      )}

      {/* Prédictions */}
      {!data?.warmup && preds.length === 0 && !isLoading && (
        <div className="card" style={{ textAlign: 'center', padding: 'var(--space-6)' }}>
          <div style={{ fontSize: 28, marginBottom: 8 }}>✓</div>
          <div style={{ color: 'var(--text-primary)', fontSize: 'var(--font-size-sm)' }}>
            Aucune trajectoire ne tend vers sa limite
          </div>
          <div style={{ color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)', marginTop: 6 }}>
            Les consommations sont stables ou décroissantes. Un pod occupant une part
            élevée de sa limite sans croître n'est pas signalé : c'est l'absence de
            tendance qui compte, non le niveau.
          </div>
        </div>
      )}

      {preds.map(p => {
        const u = urgency(p.eta_seconds)
        const c = p.components || {}
        return (
          <div key={`${p.namespace}/${p.pod}`} className="card"
               style={{ marginBottom: 'var(--space-4)', borderLeft: `3px solid ${u.color}` }}>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 'var(--space-3)' }}>
              <div style={{ minWidth: 200 }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--font-size-base)', color: 'var(--text-primary)', fontWeight: 600 }}>
                  {p.pod}
                </div>
                <div style={{ display: 'flex', gap: 6, marginTop: 5, flexWrap: 'wrap' }}>
                  <span className="badge badge-neutral">{p.namespace}</span>
                  <span className="badge badge-neutral">{p.usage_percent}% de la limite</span>
                  <span className="badge badge-neutral">+{p.slope_mib_per_min} Mio/min</span>
                </div>
              </div>

              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                  Limite atteinte dans
                </div>
                <div style={{ fontVariantNumeric: 'tabular-nums', fontSize: 'var(--font-size-2xl)', fontWeight: 600, color: u.color }}>
                  {formatEta(p.eta_seconds)}
                </div>
                <div style={{ fontSize: 'var(--font-size-xs)', color: u.color }}>{u.label}</div>
              </div>
            </div>

            {/* Occupation courante */}
            <div style={{ marginTop: 'var(--space-4)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginBottom: 4 }}>
                <span>{p.current_mib} Mio consommés</span>
                <span>limite {p.limit_mib} Mio</span>
              </div>
              <Bar value={p.usage_percent / 100} color={u.color} />
            </div>

            {/* Confiance décomposée */}
            <div style={{ marginTop: 'var(--space-4)', paddingTop: 'var(--space-4)', borderTop: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
                <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                  Confiance dans l'extrapolation
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--font-size-sm)', color: 'var(--text-primary)' }}>
                  {Math.round(p.confidence * 100)}% · {confidenceLabel(p.confidence)}
                </span>
              </div>
              <Bar value={p.confidence} color="var(--color-purple)" />

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 'var(--space-3)', marginTop: 'var(--space-4)' }}>
                {[
                  { k: 'r_squared', label: 'Ajustement (R²)',
                    help: 'la trajectoire est-elle linéaire ?' },
                  { k: 'agreement', label: 'Accord des estimateurs',
                    help: 'moindres carrés et Theil-Sen convergent-ils ?' },
                  { k: 'stability', label: 'Stabilité temporelle',
                    help: 'les prédictions successives coïncident-elles ?' },
                ].map(item => (
                  <div key={item.k}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--font-size-xs)', marginBottom: 3 }}>
                      <span style={{ color: 'var(--text-secondary)' }}>{item.label}</span>
                      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                        {c[item.k] != null ? c[item.k].toFixed(2) : '—'}
                      </span>
                    </div>
                    <Bar value={c[item.k]} />
                    <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 3 }}>{item.help}</div>
                  </div>
                ))}
              </div>

              {p.eta_seconds_robust != null && (
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: 'var(--space-3)' }}>
                  Estimateur robuste (Theil-Sen) : {formatEta(p.eta_seconds_robust)}
                  {' · '}{p.points_used} points utilisés
                </div>
              )}
            </div>
          </div>
        )
      })}

      {/* Diagnostics repliables */}
      {data?.diagnostics?.length > 0 && (
        <div className="card" style={{ marginTop: 'var(--space-5)' }}>
          <button
            onClick={() => setShowDiagnostics(v => !v)}
            className="btn btn-ghost btn-sm"
            style={{ padding: 0 }}
          >
            {showDiagnostics ? '▾' : '▸'} État de la collecte par pod
          </button>

          {showDiagnostics && (
            <>
              <p style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: 'var(--space-3)' }}>
                Seules les valeurs distinctes sont conservées : un pod à consommation
                constante accumule peu de points, ce qui est sans conséquence puisqu'il
                ne présente aucune tendance. Un conteneur sans limite déclarée ne peut
                pas être évalué — l'extrapolation exige une borne.
              </p>
              <div style={{ overflowX: 'auto', marginTop: 'var(--space-3)' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--font-size-xs)' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      {['Pod', 'Points', 'Dernière valeur', 'Limite'].map(h => (
                        <th key={h} style={{ textAlign: 'left', padding: '7px 10px', color: 'var(--text-muted)', fontWeight: 500 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.diagnostics.map(d => (
                      <tr key={d.pod} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '7px 10px', fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{d.pod}</td>
                        <td style={{ padding: '7px 10px' }}>
                          <span className={`badge ${d.points >= 3 ? 'badge-success' : 'badge-neutral'}`}>
                            {d.points}
                          </span>
                        </td>
                        <td style={{ padding: '7px 10px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                          {d.last_mib != null ? `${d.last_mib} Mio` : '—'}
                        </td>
                        <td style={{ padding: '7px 10px', fontFamily: 'var(--font-mono)', color: d.limit_mib ? 'var(--text-secondary)' : 'var(--color-amber)' }}>
                          {d.limit_mib != null ? `${d.limit_mib} Mio` : 'non déclarée'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

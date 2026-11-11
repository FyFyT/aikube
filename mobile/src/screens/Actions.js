// Actions : workloads, versions, retour arrière vérifié et redémarrage — toujours
// avec confirmation. Le retour arrière passe par la même chaîne que l'application web
// (dry-run serveur → retour à la version → surveillance des nouveaux pods).
import { useCallback, useEffect, useState } from 'react'
import { Alert, Pressable, RefreshControl, ScrollView, Text, View } from 'react-native'
import { api, waitJob } from '../api'
import { Badge, Button, Card, Code, ErrorBox, Muted, Title } from '../components'
import { C, mono } from '../theme'

// Fonction (et non constante) : les couleurs suivent le thème courant
const outcomes = () => ({ ok: ['réussi', C.green], regression: ['casse détectée', C.red], inconclusive: ['non concluant', C.amber],
  apply_failed: ['refusé', C.red], error: ['erreur', C.red] })

export default function Actions() {
  const [list, setList] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setList((await api('/devops/v2/workloads', { timeout: 60000 })).workloads) } catch (e) { setError(e.message) }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  return (
    <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 14 }}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={C.purple} />}>
      <ErrorBox text={error} />
      <Muted style={{ marginBottom: 10 }}>Touche un workload pour voir ses versions, revenir à une version précédente ou le redémarrer.</Muted>
      {(list || []).map(w => {
        const k = `${w.kind}/${w.namespace}/${w.name}`
        return (
          <Card key={k} style={{ padding: 0 }}>
            <Pressable onPress={() => setOpen(open === k ? null : k)} style={{ padding: 12 }}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                <Text style={{ color: C.text, fontWeight: '600', flex: 1 }} numberOfLines={1}>{w.name}</Text>
                <Badge label={`${w.ready}/${w.desired}`} color={w.healthy ? C.green : C.red} />
              </View>
              <Muted>{w.kind} · {w.namespace}{w.revision ? ` · révision ${w.revision}` : ''}</Muted>
              <Text style={[mono, { color: C.muted, fontSize: 11 }]} numberOfLines={1}>{w.images.join(', ')}</Text>
            </Pressable>
            {open === k && <Workload w={w} onChanged={load} />}
          </Card>
        )
      })}
      {list && list.length === 0 && <Muted>Aucun workload.</Muted>}
    </ScrollView>
  )
}

function Workload({ w, onChanged }) {
  const base = `/devops/v2/workloads/${w.kind}/${w.namespace}/${w.name}`
  const [revs, setRevs] = useState(null)
  const [sel, setSel] = useState(null)
  const [prev, setPrev] = useState(null)
  const [job, setJob] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(null)

  useEffect(() => { api(`${base}/revisions`).then(setRevs).catch(e => setErr(e.message)) }, [base])

  const preview = async (r) => {
    setSel(r.revision); setPrev(null); setJob(null); setErr(null); setBusy('preview')
    try { setPrev(await api(`${base}/rollback/preview`, { method: 'POST', body: { revision: r.revision }, timeout: 60000 })) }
    catch (e) { setErr(e.message) }
    setBusy(null)
  }
  const rollback = () => Alert.alert('Retour arrière', `Revenir à la révision ${sel} de ${w.name} ?\nLes pods vont redémarrer, puis être surveillés.`, [
    { text: 'Annuler', style: 'cancel' },
    { text: 'Revenir', style: 'destructive', onPress: async () => {
      setBusy('rollback'); setErr(null)
      try {
        const j = await api(`${base}/rollback`, { method: 'POST', body: { revision: sel } })
        setJob(j); setJob(await waitJob(j.id, setJob)); onChanged()
      } catch (e) { setErr(e.message) }
      setBusy(null)
    } },
  ])
  const restart = () => Alert.alert('Redémarrer', `Redémarrer ${w.name} (${w.namespace}) ?`, [
    { text: 'Annuler', style: 'cancel' },
    { text: 'Redémarrer', style: 'destructive', onPress: async () => {
      setBusy('restart'); setErr(null)
      try { const r = await api(`/cluster/deployments/${w.namespace}/${w.name}/restart`, { method: 'POST' }); setErr(null); setJob({ state: 'done', outcome: 'ok', reason: r.message }) }
      catch (e) { setErr(e.message) }
      setBusy(null)
    } },
  ])

  const scale = (n) => Alert.alert('Mettre à l\'échelle', `Passer ${w.name} de ${w.desired} à ${n} réplique(s) ?${n === 0 ? '\nAttention : 0 arrête complètement l\'application.' : ''}`, [
    { text: 'Annuler', style: 'cancel' },
    { text: 'Confirmer', style: n === 0 ? 'destructive' : 'default', onPress: async () => {
      setBusy('scale'); setErr(null)
      try { const r = await api(`/cluster/deployments/${w.namespace}/${w.name}/scale`, { method: 'PATCH', body: { replicas: n } }); setJob({ state: 'done', outcome: 'ok', reason: r.message }); onChanged() }
      catch (e) { setErr(e.message) }
      setBusy(null)
    } },
  ])

  const OUTCOME = outcomes()
  const o = job?.state === 'done' ? (OUTCOME[job.outcome] || OUTCOME.error) : null
  return (
    <View style={{ borderTopColor: C.border, borderTopWidth: 1, padding: 12 }}>
      <ErrorBox text={err || revs?.error} />
      <Title>Versions</Title>
      {(revs?.revisions || []).map(r => (
        <Pressable key={r.revision} disabled={r.current} onPress={() => preview(r)}
          style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 6 }}>
          <Text style={[mono, { color: C.text, width: 26 }]}>{r.revision}</Text>
          {r.current ? <Badge label="en cours" color={C.green} /> : <Badge label={sel === r.revision ? 'choisie' : 'comparer'} color={C.purpleLight} />}
          <Text style={[mono, { color: C.muted, fontSize: 11, flex: 1 }]} numberOfLines={1}>{r.images.join(', ')}</Text>
        </Pressable>
      ))}
      {busy === 'preview' && <Muted>Vérification par le serveur…</Muted>}
      {prev && (
        <View style={{ marginTop: 8 }}>
          <Muted>Changement en revenant à la révision {sel} :</Muted>
          <Code text={prev.diff || prev.error} maxHeight={220} />
          {prev.ok && !job && <View style={{ marginTop: 8 }}><Button label={`Revenir à la révision ${sel}`} color={C.amber} busy={busy === 'rollback'} onPress={rollback} /></View>}
          {!prev.ok && <ErrorBox text={prev.error} />}
        </View>
      )}
      {job && (
        <View style={{ marginTop: 10 }}>
          {(job.steps || []).map(s => <Muted key={s.name}>• {s.name} : {s.status} {s.detail ? `— ${s.detail.slice(0, 140)}` : ''}</Muted>)}
          {o && <View style={{ marginTop: 6 }}><Badge label={o[0]} color={o[1]} />{job.reason ? <Muted>{job.reason}</Muted> : null}</View>}
        </View>
      )}
      {w.kind === 'Deployment' && (
        <View style={{ marginTop: 12, gap: 8 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={{ color: C.text, flex: 1 }}>Répliques : {w.desired}</Text>
            <View style={{ width: 52 }}><Button label="−" small outline color={C.purpleLight} disabled={w.desired <= 0} busy={busy === 'scale'} onPress={() => scale(w.desired - 1)} /></View>
            <View style={{ width: 52 }}><Button label="+" small outline color={C.purpleLight} disabled={w.desired >= 20} busy={busy === 'scale'} onPress={() => scale(w.desired + 1)} /></View>
          </View>
          <Button label="Redémarrer" outline color={C.amber} small busy={busy === 'restart'} onPress={restart} />
        </View>
      )}
    </View>
  )
}

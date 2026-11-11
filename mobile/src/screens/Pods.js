// Pods : liste filtrable (comme la page Pods du web), puis détail d'un pod —
// conteneurs, événements, logs — avec les actions courantes, toujours confirmées.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Modal, Pressable, RefreshControl, ScrollView, Text, TextInput, View } from 'react-native'
import { api } from '../api'
import { Badge, Button, Card, Code, ErrorBox, Muted, Title } from '../components'
import { Feather } from '@expo/vector-icons'
import { C, mono } from '../theme'

export const fmtAge = (s) => s == null ? '—' : s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}min`
  : s < 86400 ? `${Math.floor(s / 3600)}h` : `${Math.floor(s / 86400)}j`
const statusColor = (p) => p.healthy ? C.green : ['Pending', 'ContainerCreating', 'Terminating'].includes(p.status) || p.status.startsWith('Init:') ? C.amber : C.red

function Chip({ label, active, onPress, color = C.purple }) {
  return (
    <Pressable onPress={onPress} style={{ marginRight: 6 }}>
      <Badge label={label} color={active ? color : C.muted} />
    </Pressable>
  )
}

export default function Pods({ askAgent }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [ns, setNs] = useState('*')
  const [onlyBad, setOnlyBad] = useState(false)
  const [system, setSystem] = useState(false)
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setData(await api('/mobile/pods', { timeout: 60000 })) } catch (e) { setError(e.message) }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  const list = useMemo(() => (data?.pods || []).filter(p =>
    (ns === '*' ? (system || !p.system) : p.namespace === ns)
    && (!onlyBad || !p.healthy)
    && (!q || p.name.includes(q.toLowerCase()))), [data, ns, onlyBad, system, q])
  const bad = (data?.pods || []).filter(p => !p.healthy && (system || !p.system)).length

  return (
    <View style={{ flex: 1 }}>
      <View style={{ padding: 12, paddingBottom: 6, borderBottomColor: C.border, borderBottomWidth: 1 }}>
        <TextInput value={q} onChangeText={setQ} placeholder="Rechercher un pod…" placeholderTextColor={C.muted}
          autoCapitalize="none" autoCorrect={false}
          style={{ color: C.text, backgroundColor: C.elevated, borderRadius: 6, paddingHorizontal: 12, paddingVertical: 8, marginBottom: 8 }} />
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 6 }}>
          <Chip label={`En difficulté (${bad})`} active={onlyBad} color={C.red} onPress={() => setOnlyBad(!onlyBad)} />
          <Chip label="Système" active={system} color={C.teal} onPress={() => setSystem(!system)} />
          <Chip label="Tous les namespaces" active={ns === '*'} onPress={() => setNs('*')} />
          {(data?.namespaces || []).filter(n => system || !['kube-system', 'kube-public', 'kube-node-lease'].includes(n))
            .map(n => <Chip key={n} label={n} active={ns === n} onPress={() => setNs(n)} />)}
        </ScrollView>
      </View>
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 12 }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={C.purple} />}>
        <ErrorBox text={error} />
        {data && <Muted style={{ marginBottom: 8 }}>{list.length} pod(s){data.metrics ? '' : ' · consommation indisponible (metrics-server)'}</Muted>}
        {list.map(p => (
          <Pressable key={`${p.namespace}/${p.name}`} onPress={() => setOpen(p)}
            style={({ pressed }) => [{ backgroundColor: C.card, borderColor: C.border, borderWidth: 1, borderLeftWidth: 3,
              borderLeftColor: statusColor(p), borderRadius: 6, padding: 10, marginBottom: 8 }, pressed && { opacity: 0.7 }]}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
              <Text style={{ color: C.text, fontWeight: '600', flex: 1 }} numberOfLines={1}>{p.name}</Text>
              <Badge label={p.status} color={statusColor(p)} />
            </View>
            <Muted>
              {p.namespace} · prêt {p.ready} · {p.restarts} redém. · {fmtAge(p.age_s)}
              {p.usage ? ` · ${p.usage.cpu} / ${p.usage.mem}` : ''}
            </Muted>
          </Pressable>
        ))}
        {data && list.length === 0 && <Muted>Aucun pod avec ces filtres.</Muted>}
      </ScrollView>
      <PodSheet pod={open} onClose={() => setOpen(null)} onChanged={load}
        askAgent={(t) => { setOpen(null); askAgent(t) }} />
    </View>
  )
}

function PodSheet({ pod, onClose, onChanged, askAgent }) {
  const [tab, setTab] = useState('info')
  const [d, setD] = useState(null)
  const [logs, setLogs] = useState(null)
  const [container, setContainer] = useState(null)
  const [previous, setPrevious] = useState(false)
  const [err, setErr] = useState(null)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(null)
  const base = pod ? `/mobile/pods/${pod.namespace}/${pod.name}` : null

  useEffect(() => {
    setTab('info'); setD(null); setLogs(null); setErr(null); setMsg(null); setPrevious(false); setContainer(null)
    if (!base) return
    api(base, { timeout: 40000 }).then(r => { setD(r); setContainer(r.containers?.[0]?.name || null) }).catch(e => setErr(e.message))
  }, [base])

  const loadLogs = useCallback(async (c = container, prev = previous) => {
    setBusy('logs'); setLogs(null)
    try {
      const r = await api(`${base}/logs?tail=300${c ? `&container=${c}` : ''}${prev ? '&previous=true' : ''}`, { timeout: 40000 })
      setLogs(r.error && !r.logs ? `(${r.error})` : r.logs || '(aucun log)')
    } catch (e) { setLogs(`(${e.message})`) }
    setBusy(null)
  }, [base, container, previous])
  useEffect(() => { if (tab === 'logs' && base && logs == null) loadLogs() }, [tab, base])

  if (!pod) return null
  const del = () => Alert.alert('Supprimer le pod', `Supprimer ${pod.name} ?${pod.workload ? `\nIl sera recréé par ${pod.workload_kind}/${pod.workload}.` : '\nAucun contrôleur : il ne sera PAS recréé.'}`, [
    { text: 'Annuler', style: 'cancel' },
    { text: 'Supprimer', style: 'destructive', onPress: async () => {
      setBusy('del')
      try { const r = await api(`/cluster/pods/${pod.namespace}/${pod.name}`, { method: 'DELETE' }); setMsg({ ok: true, text: r.message }); onChanged() }
      catch (e) { setMsg({ ok: false, text: e.message }) }
      setBusy(null)
    } },
  ])
  const restart = () => Alert.alert('Redémarrer', `Redémarrer le déploiement ${pod.workload} (${pod.namespace}) ?\nLes pods seront recréés un par un.`, [
    { text: 'Annuler', style: 'cancel' },
    { text: 'Redémarrer', style: 'destructive', onPress: async () => {
      setBusy('restart')
      try { const r = await api(`/cluster/deployments/${pod.namespace}/${pod.workload}/restart`, { method: 'POST' }); setMsg({ ok: true, text: r.message }); onChanged() }
      catch (e) { setMsg({ ok: false, text: e.message }) }
      setBusy(null)
    } },
  ])
  const question = `Dans le namespace ${pod.namespace}, le pod ${pod.name} est en ${pod.status} (${pod.restarts} redémarrages). Quelle est la cause exacte et la correction ?`
  const anyPrev = d?.containers?.some(c => c.last)

  return (
    <Modal visible animationType="slide" transparent onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: C.overlay, justifyContent: 'flex-end' }}>
        <View style={{ backgroundColor: C.card, borderTopLeftRadius: 18, borderTopRightRadius: 18, padding: 16, height: '88%' }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={{ color: C.text, fontSize: 16, fontWeight: '700', flex: 1 }} numberOfLines={2}>{pod.name}</Text>
            <Pressable onPress={onClose} hitSlop={12}><Feather name="x" size={22} color={C.muted} /></Pressable>
          </View>
          <Muted>{pod.namespace}{pod.workload ? ` · ${pod.workload_kind}/${pod.workload}` : ''}</Muted>
          <View style={{ flexDirection: 'row', marginVertical: 10 }}>
            {[['info', 'Détails'], ['events', `Événements${d ? ` (${d.events.length})` : ''}`], ['logs', 'Logs']].map(([id, l]) =>
              <Chip key={id} label={l} active={tab === id} onPress={() => setTab(id)} />)}
          </View>
          <ScrollView style={{ flex: 1 }}>
            <ErrorBox text={err} />
            {!d && !err && <Muted>Chargement…</Muted>}
            {d && tab === 'info' && (
              <>
                <Muted>
                  Statut : {d.status} · nœud {d.node || '—'} · IP {d.ip || '—'}{'\n'}
                  Âge : {fmtAge(d.age_s)} · QoS {d.qos || '—'}
                </Muted>
                {d.containers.map(c => (
                  <Card key={c.name} style={{ marginTop: 10, backgroundColor: C.elevated }}>
                    <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 6 }}>
                      <Text style={{ color: C.text, fontWeight: '600', flex: 1 }}>{c.name}</Text>
                      <Badge label={c.ready ? 'prêt' : (c.reason || c.state || '?')} color={c.ready ? C.green : C.red} />
                    </View>
                    <Text style={[mono, { color: C.muted, fontSize: 11, marginTop: 4 }]}>{c.image}</Text>
                    <Muted style={{ marginTop: 4 }}>
                      {c.restarts} redémarrage(s)
                      {c.last ? ` · dernier arrêt : ${c.last.reason || '?'} (code ${c.last.exit_code})` : ''}
                    </Muted>
                    {!!c.message && <Muted style={{ color: C.amber, marginTop: 4 }}>{c.message}</Muted>}
                    <Muted style={{ marginTop: 4 }}>
                      requests {fmtRes(c.resources.requests)} · limits {fmtRes(c.resources.limits)}
                    </Muted>
                  </Card>
                ))}
                <Muted style={{ marginTop: 6 }}>
                  {d.conditions.map(c => `${c.ok ? '✓' : '✗'} ${c.type}${c.reason ? ` (${c.reason})` : ''}`).join('   ')}
                </Muted>
              </>
            )}
            {d && tab === 'events' && (
              d.events.length === 0 ? <Muted>Aucun événement récent (Kubernetes les garde environ 1 h).</Muted>
                : d.events.map((e, i) => (
                  <View key={i} style={{ borderLeftWidth: 3, borderLeftColor: e.type === 'Warning' ? C.amber : C.border, paddingLeft: 8, marginBottom: 10 }}>
                    <Text style={{ color: e.type === 'Warning' ? C.amber : C.text, fontWeight: '600', fontSize: 13 }}>
                      {e.reason} {e.count > 1 ? `×${e.count}` : ''} <Text style={{ color: C.muted, fontWeight: '400' }}>· il y a {fmtAge(e.age_s)}</Text>
                    </Text>
                    <Muted>{e.message}</Muted>
                  </View>
                ))
            )}
            {tab === 'logs' && (
              <>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 8 }}>
                  {(d?.containers || []).length > 1 && d.containers.map(c =>
                    <Chip key={c.name} label={c.name} active={container === c.name} color={C.teal}
                      onPress={() => { setContainer(c.name); loadLogs(c.name, previous) }} />)}
                  {anyPrev && <Chip label="Avant le dernier crash" active={previous} color={C.amber}
                    onPress={() => { setPrevious(!previous); loadLogs(container, !previous) }} />}
                  <Chip label="Actualiser" active={false} onPress={() => loadLogs()} />
                </ScrollView>
                {busy === 'logs' ? <Muted>Chargement des logs…</Muted> : <Code text={logs} maxHeight={1200} />}
              </>
            )}
          </ScrollView>
          {msg && <Muted style={{ marginTop: 8, color: msg.ok ? C.green : C.red }}>{msg.text}</Muted>}
          <View style={{ gap: 8, marginTop: 10 }}>
            <Button label="Demander à l'agent" onPress={() => askAgent(question)} />
            <View style={{ flexDirection: 'row', gap: 8 }}>
              {pod.workload_kind === 'Deployment' && (
                <View style={{ flex: 1 }}><Button label="Redémarrer" small outline color={C.amber} busy={busy === 'restart'} onPress={restart} /></View>
              )}
              <View style={{ flex: 1 }}><Button label="Supprimer le pod" small outline color={C.red} busy={busy === 'del'} onPress={del} /></View>
            </View>
          </View>
        </View>
      </View>
    </Modal>
  )
}

const fmtRes = (r) => r && Object.keys(r).length ? Object.entries(r).map(([k, v]) => `${k}=${v}`).join(', ') : 'aucune'

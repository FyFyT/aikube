// Accueil : état du cluster et de la machine, alertes, et actions sur chaque alerte.
import { useCallback, useEffect, useState } from 'react'
import { Alert, Modal, Pressable, RefreshControl, ScrollView, Text, View } from 'react-native'
import { api } from '../api'
import { Badge, Button, Card, Code, ErrorBox, Metric, Muted, Title } from '../components'
import { C, SEV, mono } from '../theme'

const KIND_LABEL = { metrics: 'mesures', oom: 'OOM', pod: 'pod', reco: 'ressources', security: 'sécurité', deploy: 'déploiement' }
const STATUS = { oom_imminent: 'OOM imminent', sous_dimensionne: 'sous-dimensionnés', manquant: 'limites absentes',
  sur_dimensionne: 'sur-dimensionnés', adapte: 'adaptés', observation: 'en observation' }
const fmtMem = (m) => (m == null ? '—' : m >= 1024 ? `${(m / 1024).toFixed(1)} Gio` : `${m} Mio`)

export default function Home({ data, error, loading, refresh, askAgent }) {
  const [open, setOpen] = useState(null)
  const h = data?.host || {}, c = data?.cluster || {}
  const loadColor = h.load_ratio > 1.5 ? C.red : h.load_ratio > 0.8 ? C.amber : C.green
  return (
    <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 14 }}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={() => refresh(true)} tintColor={C.purple} />}>
      <ErrorBox text={error} />
      {data && (
        <>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', margin: -4, marginBottom: 8 }}>
            <Metric label="Nœuds prêts" value={`${c.nodes_ready}/${c.nodes}`} sub={`${c.context || ''} ${c.version || ''}`}
              color={c.nodes_ready === c.nodes ? C.green : C.red} />
            <Metric label="Pods en difficulté" value={String(data.pods_in_trouble)} color={data.pods_in_trouble ? C.amber : C.green}
              sub="hors namespaces système" />
            <Metric label="Charge de l'hôte" value={h.load != null ? `${h.load}` : '—'} color={loadColor}
              sub={`${h.cpu_count || '?'} cœurs · ${h.load_ratio ?? '—'} par cœur`} />
            <Metric label="RAM disponible" value={fmtMem(h.ram_available_mib)} sub={`sur ${fmtMem(h.ram_total_mib)}${c.local ? ' · cluster local' : ''}`} />
          </View>
          <Card>
            <Title right={<Badge label={data.metrics_ok ? 'mesures OK' : 'mesures en panne'} color={data.metrics_ok ? C.green : C.red} />}>
              Alertes ({data.alerts.length})
            </Title>
            {data.alerts.length === 0 && <Muted>Aucune alerte. Tire vers le bas pour actualiser.</Muted>}
            {data.alerts.map(a => (
              <Pressable key={a.id} onPress={() => setOpen(a)}
                style={({ pressed }) => [{ borderLeftWidth: 3, borderLeftColor: SEV[a.severity], backgroundColor: C.elevated,
                  borderRadius: 8, padding: 10, marginBottom: 8 }, pressed && { opacity: 0.7 }]}>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8 }}>
                  <Text style={{ color: C.text, fontWeight: '600', flex: 1 }} numberOfLines={2}>{a.title}</Text>
                  <Badge label={KIND_LABEL[a.kind] || a.kind} color={SEV[a.severity]} />
                </View>
                <Muted style={{ marginTop: 3 }}>{a.detail}</Muted>
              </Pressable>
            ))}
          </Card>
          <Card>
            <Title>Ressources et sécurité</Title>
            <Muted>
              OOM annoncés : {data.oom?.warmup ? 'collecte en cours' : data.oom?.count}{'\n'}
              Recommandations : {Object.entries(data.resources || {}).filter(([, v]) => v).map(([k, v]) => `${v} ${STATUS[k] || k}`).join(' · ') || '—'}{'\n'}
              Sécurité : {data.security ? `${data.security.critical} critique(s), note ${data.security.risk_grade}` : 'aucun scan récent (lancer depuis le web)'}
            </Muted>
          </Card>
          {data.recent_deploys?.length > 0 && (
            <Card>
              <Title>Derniers déploiements</Title>
              {data.recent_deploys.map((d, i) => (
                <View key={i} style={{ flexDirection: 'row', gap: 8, marginBottom: 6, alignItems: 'center' }}>
                  <Badge label={d.outcome || '?'} color={d.outcome === 'ok' ? C.green : d.outcome === 'regression' ? C.red : C.amber} />
                  <Text style={{ color: C.text, fontSize: 12, flex: 1 }} numberOfLines={1}>{(d.objects || []).join(', ')}</Text>
                </View>
              ))}
            </Card>
          )}
          <Muted style={{ textAlign: 'center', marginBottom: 20 }}>
            Mis à jour {new Date(data.generated_at * 1000).toLocaleTimeString('fr-FR')}
          </Muted>
        </>
      )}
      <AlertSheet alert={open} onClose={() => setOpen(null)} askAgent={(q) => { setOpen(null); askAgent(q) }} onDone={() => refresh(true)} />
    </ScrollView>
  )
}

function AlertSheet({ alert, onClose, askAgent, onDone }) {
  const [busy, setBusy] = useState(null)
  const [msg, setMsg] = useState(null)
  const [check, setCheck] = useState(null)
  useEffect(() => { setMsg(null); setCheck(null); setBusy(null) }, [alert?.id])
  const t = alert?.target || {}

  const restart = useCallback(() => {
    const dep = t.owner_kind === 'ReplicaSet' ? t.owner.replace(/-[a-z0-9]+$/, '') : t.owner
    Alert.alert('Redémarrer', `Redémarrer le déploiement ${dep} (${t.namespace}) ?\nLes pods seront recréés un par un.`, [
      { text: 'Annuler', style: 'cancel' },
      { text: 'Redémarrer', style: 'destructive', onPress: async () => {
        setBusy('restart'); setMsg(null)
        try { const r = await api(`/cluster/deployments/${t.namespace}/${dep}/restart`, { method: 'POST' }); setMsg({ ok: true, text: r.message }); onDone() }
        catch (e) { setMsg({ ok: false, text: e.message }) }
        setBusy(null)
      } },
    ])
  }, [t])

  const recoBody = (dry) => ({ kind: t.kind, namespace: t.namespace, name: t.workload, container: t.container,
    recommended: t.recommended, dry_run: dry, before: t.current, usage: t.usage, status: t.status })
  const verify = async () => {
    setBusy('verify'); setMsg(null)
    try { setCheck(await api('/resources/apply', { method: 'POST', body: recoBody(true), timeout: 60000 })) }
    catch (e) { setMsg({ ok: false, text: e.message }) }
    setBusy(null)
  }
  const applyReco = () => Alert.alert('Appliquer', `Appliquer les nouvelles ressources à ${t.kind?.toLowerCase()}/${t.workload} ?\nLes pods vont redémarrer.`, [
    { text: 'Annuler', style: 'cancel' },
    { text: 'Appliquer', style: 'destructive', onPress: async () => {
      setBusy('apply'); setMsg(null)
      try { const r = await api('/resources/apply', { method: 'POST', body: recoBody(false), timeout: 60000 }); setMsg({ ok: true, text: r.note || 'Appliqué' }); onDone() }
      catch (e) { setMsg({ ok: false, text: e.message }) }
      setBusy(null)
    } },
  ])

  if (!alert) return null
  const question = alert.kind === 'pod' ? `Dans le namespace ${t.namespace}, pourquoi le pod ${t.pod} est-il en ${t.reason} ? Quelle est la cause exacte et la correction ?`
    : alert.kind === 'oom' ? `Le pod ${t.pod} du namespace ${t.namespace} va manquer de mémoire. Pourquoi, et que faire ?`
    : alert.kind === 'reco' ? `Pourquoi ${t.kind?.toLowerCase()}/${t.workload} (namespace ${t.namespace}) est-il mal dimensionné ?`
    : `Explique cette alerte et ce qu'il faut faire : ${alert.title} — ${alert.detail}`
  return (
    <Modal visible animationType="slide" transparent onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: C.overlay, justifyContent: 'flex-end' }}>
        <View style={{ backgroundColor: C.card, borderTopLeftRadius: 18, borderTopRightRadius: 18, padding: 18, maxHeight: '85%' }}>
          <ScrollView>
            <Badge label={alert.severity === 'critical' ? 'critique' : 'attention'} color={SEV[alert.severity]} />
            <Text style={{ color: C.text, fontSize: 17, fontWeight: '700', marginVertical: 8 }}>{alert.title}</Text>
            <Muted>{alert.detail}</Muted>
            {alert.kind === 'metrics' && t.fix && (
              <><Muted style={{ marginTop: 10 }}>Réparer, dans le terminal Ubuntu (WSL) du PC :</Muted><Code text={t.fix} /></>
            )}
            {alert.kind === 'reco' && t.recommended && (
              <View style={{ marginTop: 10 }}>
                <Text style={[mono, { color: C.text, fontSize: 12 }]}>
                  CPU request : {t.current?.cpu_request_m ?? '—'}m → {t.recommended.cpu_request_m}m{'\n'}
                  Mémoire request : {t.current?.mem_request_mib ?? '—'}Mi → {t.recommended.mem_request_mib}Mi{'\n'}
                  Mémoire limit : {t.current?.mem_limit_mib ?? '—'}Mi → {t.recommended.mem_limit_mib}Mi
                </Text>
                {check && <Muted style={{ marginTop: 6, color: C.green }}>Vérifié par le serveur (dry-run) : {check.output || 'OK'}</Muted>}
              </View>
            )}
            {msg && <Muted style={{ marginTop: 10, color: msg.ok ? C.green : C.red }}>{msg.text}</Muted>}
            <View style={{ gap: 8, marginTop: 16 }}>
              <Button label="Demander à l'agent" onPress={() => askAgent(question)} />
              {alert.kind === 'pod' && t.owner_kind === 'ReplicaSet' && t.owner && (
                <Button label="Redémarrer le déploiement" color={C.amber} outline busy={busy === 'restart'} onPress={restart} />
              )}
              {alert.kind === 'reco' && !check && <Button label="Vérifier (dry-run)" outline color={C.teal} busy={busy === 'verify'} onPress={verify} />}
              {alert.kind === 'reco' && check && <Button label="Appliquer" color={C.green} busy={busy === 'apply'} onPress={applyReco} />}
              <Button label="Fermer" outline color={C.muted} onPress={onClose} />
            </View>
          </ScrollView>
        </View>
      </View>
    </Modal>
  )
}

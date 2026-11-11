// Composants d'interface communs.
import { ActivityIndicator, Pressable, ScrollView, Text, View } from 'react-native'
import { C, mono } from './theme'

export function Card({ children, style }) {
  return <View style={[st().card, style]}>{children}</View>
}

export function Title({ children, right }) {
  return (
    <View style={st().titleRow}>
      <Text style={st().title}>{children}</Text>
      {right}
    </View>
  )
}

export function Badge({ label, color = C.purpleLight }) {
  return (
    <View style={[st().badge, { borderColor: color + '66', backgroundColor: color + '1F' }]}>
      <Text style={[st().badgeText, { color }]}>{label}</Text>
    </View>
  )
}

export function Button({ label, onPress, color = C.purple, disabled, busy, small, outline }) {
  return (
    <Pressable onPress={onPress} disabled={disabled || busy}
      style={({ pressed }) => [st().btn, small && st().btnSmall,
        outline ? { borderColor: color, borderWidth: 1, backgroundColor: 'transparent' } : { backgroundColor: color },
        (disabled || busy) && { opacity: 0.45 }, pressed && { opacity: 0.75 }]}>
      {busy ? <ActivityIndicator color={outline ? color : C.onAccent} size="small" />
        : <Text style={[st().btnText, small && { fontSize: 12 }, outline && { color }]}>{label}</Text>}
    </Pressable>
  )
}

export function Metric({ label, value, sub, color = C.text }) {
  return (
    <View style={st().metric}>
      <Text style={st().metricLabel}>{label}</Text>
      <Text style={[st().metricValue, { color }]} numberOfLines={1}>{value}</Text>
      {sub ? <Text style={st().metricSub} numberOfLines={2}>{sub}</Text> : null}
    </View>
  )
}

export function Code({ text, maxHeight = 320 }) {
  return (
    <ScrollView style={[st().code, { maxHeight }]} nestedScrollEnabled>
      <ScrollView horizontal nestedScrollEnabled>
        <Text style={[mono, { color: C.text, fontSize: 11.5, lineHeight: 16 }]}>
          {(text || '').split('\n').map((l, i) => {
            const col = l.startsWith('+') && !l.startsWith('+++') ? C.green
              : l.startsWith('-') && !l.startsWith('---') ? C.red : l.startsWith('@@') ? C.purpleLight
              : l.startsWith('#') ? C.amber : C.text
            return <Text key={i} style={{ color: col }}>{l + '\n'}</Text>
          })}
        </Text>
      </ScrollView>
    </ScrollView>
  )
}

export function ErrorBox({ text }) {
  if (!text) return null
  return <View style={st().error}><Text style={{ color: C.red, fontSize: 13 }}>{text}</Text></View>
}

export function Muted({ children, style }) {
  return <Text style={[{ color: C.muted, fontSize: 12.5, lineHeight: 18 }, style]}>{children}</Text>
}

// Styles recalculés à chaque affichage pour suivre le thème (clair / sombre)
const st = () => ({
  card: { backgroundColor: C.card, borderColor: C.border, borderWidth: 1, borderRadius: 8, padding: 14, marginBottom: 12 },
  titleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  title: { color: C.text, fontSize: 15.5, fontWeight: '600' },
  badge: { borderWidth: 1, borderRadius: 4, paddingHorizontal: 6, paddingVertical: 1, alignSelf: 'flex-start' },
  badgeText: { fontSize: 12, fontWeight: '500' },
  btn: { borderRadius: 6, paddingVertical: 11, paddingHorizontal: 16, alignItems: 'center', justifyContent: 'center', minHeight: 42 },
  btnSmall: { paddingVertical: 6, paddingHorizontal: 10, minHeight: 32 },
  btnText: { color: C.onAccent, fontWeight: '600', fontSize: 14 },
  metric: { flex: 1, minWidth: '45%', backgroundColor: C.card, borderColor: C.border, borderWidth: 1, borderRadius: 8, padding: 12, margin: 4 },
  metricLabel: { color: C.muted, fontSize: 13 },
  metricValue: { fontSize: 24, fontWeight: '600', marginTop: 2 },
  metricSub: { color: C.muted, fontSize: 12.5, marginTop: 2 },
  code: { backgroundColor: C.codeBg, borderRadius: 8, padding: 10, borderColor: C.border, borderWidth: 1 },
  error: { backgroundColor: C.red + '12', borderLeftColor: C.red, borderLeftWidth: 3, borderRadius: 4, padding: 10, marginVertical: 8 },
})

// Mini-rendu Markdown pour les réponses de l'agent : titres, listes, **gras**,
// `code` et blocs ```…```. Volontairement simple (pas de dépendance externe).
function inline(text, key, base) {
  const parts = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0, m, i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const t = m[0]
    parts.push(t.startsWith('**')
      ? <Text key={`${key}-${i++}`} style={{ fontWeight: '700' }}>{inline(t.slice(2, -2), `${key}-b${i}`, base)}</Text>
      : <Text key={`${key}-${i++}`} style={[mono, { color: C.purpleLight, fontSize: (base.fontSize || 14) - 1.5 }]}>{t.slice(1, -1)}</Text>)
    last = m.index + t.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

export function Markdown({ text, color = C.text }) {
  const base = { color, fontSize: 14, lineHeight: 20 }
  const lines = (text || '').split('\n')
  const out = []
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i]
    if (l.trim().startsWith('```')) {
      const buf = []
      for (i++; i < lines.length && !lines[i].trim().startsWith('```'); i++) buf.push(lines[i])
      out.push(<View key={i} style={{ marginVertical: 4 }}><Code text={buf.join('\n')} maxHeight={260} /></View>)
      continue
    }
    const h = l.match(/^(#{1,4})\s+(.*)/)
    const li = l.match(/^(\s*)([-*•]|\d+[.)])\s+(.*)/)
    if (h) out.push(<Text key={i} style={[base, { fontWeight: '700', fontSize: 15, marginTop: 6 }]}>{inline(h[2], i, base)}</Text>)
    else if (li) {
      const indent = Math.min(3, Math.floor(li[1].length / 2))
      const bullet = /\d/.test(li[2]) ? li[2] : '•'
      out.push(
        <View key={i} style={{ flexDirection: 'row', paddingLeft: 4 + indent * 14, marginTop: 2 }}>
          <Text style={[base, { width: 18, color: C.purpleLight }]}>{bullet}</Text>
          <Text style={[base, { flex: 1 }]}>{inline(li[3], i, base)}</Text>
        </View>)
    } else if (!l.trim()) out.push(<View key={i} style={{ height: 8 }} />)
    else out.push(<Text key={i} style={base}>{inline(l, i, base)}</Text>)
  }
  return <View>{out}</View>
}

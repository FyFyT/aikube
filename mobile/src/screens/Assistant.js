// Assistant : l'agent de dépannage enquête sur le cluster (outils en lecture seule)
// avant de répondre ; la liste de ses vérifications est consultable.
import { useEffect, useRef, useState } from 'react'
import { Keyboard, Platform, Pressable, ScrollView, StatusBar, Text, TextInput, View } from 'react-native'
import { api } from '../api'
import { Badge, Button, Markdown, Muted } from '../components'
import { C, mono } from '../theme'

const EXAMPLES = ['Quels pods posent problème en ce moment ?', 'La machine hôte est-elle surchargée ?',
  'Un conteneur risque-t-il de manquer de mémoire ?']

export default function Assistant({ prefill, clearPrefill }) {
  const [msgs, setMsgs] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const scroll = useRef(null)
  const box = useRef(null)
  const [kb, setKb] = useState(0)

  // Clavier : on remonte la zone de saisie de la hauteur exacte qui serait cachée.
  // (KeyboardAvoidingView ne suffit pas sous Android récent, en affichage plein écran.)
  useEffect(() => {
    const show = Keyboard.addListener('keyboardDidShow', (e) => {
      box.current?.measureInWindow((x, y, w, h) => {
        // Sous Android, les coordonnées de la vue commencent SOUS la barre d'état alors que
        // celles du clavier partent du haut de l'écran : on ajoute la barre d'état, et
        // quelques points de marge. Jamais plus que la hauteur du clavier lui-même.
        const offset = Platform.OS === 'android' ? (StatusBar.currentHeight || 0) : 0
        setKb(Math.min(e.endCoordinates.height, Math.max(0, y + h - e.endCoordinates.screenY + offset + 6)))
        setTimeout(() => scroll.current?.scrollToEnd({ animated: true }), 50)
      })
    })
    const hide = Keyboard.addListener('keyboardDidHide', () => setKb(0))
    return () => { show.remove(); hide.remove() }
  }, [])

  useEffect(() => { if (prefill) { setInput(prefill); clearPrefill() } }, [prefill])

  const send = async (text) => {
    const q = (text ?? input).trim()
    if (!q || busy) return
    const history = msgs.filter(m => !m.error).map(m => ({ role: m.role, content: m.content })).slice(-8)
    setMsgs(m => [...m, { role: 'user', content: q }])
    setInput(''); setBusy(true)
    try {
      const r = await api('/ai/agent', { method: 'POST', body: { message: q, history }, timeout: 600000 })
      setMsgs(m => [...m, { role: 'assistant', content: r.response, trace: r.trace || [], mode: r.mode }])
    } catch (e) {
      setMsgs(m => [...m, { role: 'assistant', content: e.message, error: true }])
    }
    setBusy(false)
    setTimeout(() => scroll.current?.scrollToEnd({ animated: true }), 100)
  }

  return (
    <View ref={box} style={{ flex: 1, paddingBottom: kb }}>
      <ScrollView ref={scroll} style={{ flex: 1 }} contentContainerStyle={{ padding: 14 }}
        onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: true })}>
        {msgs.length === 0 && (
          <View>
            <Muted>L'agent vérifie le cluster lui-même (pods, événements, logs…) avant de répondre. Il ne modifie rien.</Muted>
            {EXAMPLES.map(e => (
              <Pressable key={e} onPress={() => send(e)} style={{ backgroundColor: C.elevated, borderRadius: 6, padding: 12, marginTop: 8 }}>
                <Text style={{ color: C.purpleLight }}>{e}</Text>
              </Pressable>
            ))}
          </View>
        )}
        {msgs.map((m, i) => <Bubble key={i} m={m} />)}
        {busy && <Muted style={{ marginTop: 8 }}>L'agent enquête… (30 s à 2 min)</Muted>}
      </ScrollView>
      <View style={{ flexDirection: 'row', gap: 8, padding: 10, borderTopColor: C.border, borderTopWidth: 1, backgroundColor: C.card }}>
        <TextInput value={input} onChangeText={setInput} placeholder="Pose ta question…" placeholderTextColor={C.muted}
          multiline style={{ flex: 1, color: C.text, backgroundColor: C.elevated, borderRadius: 6, paddingHorizontal: 12,
            paddingVertical: 8, maxHeight: 110 }} />
        <Button label="Envoyer" onPress={() => send()} busy={busy} disabled={!input.trim()} small />
      </View>
    </View>
  )
}

function Bubble({ m }) {
  const [open, setOpen] = useState(false)
  const user = m.role === 'user'
  return (
    <View style={{ alignSelf: user ? 'flex-end' : 'flex-start', maxWidth: '92%', marginTop: 10,
      backgroundColor: user ? C.purple + '33' : m.error ? C.red + '18' : C.card, borderColor: C.border,
      borderWidth: user ? 0 : 1, borderRadius: 6, padding: 12 }}>
      {user || m.error
        ? <Text style={{ color: m.error ? C.red : C.text, fontSize: 14, lineHeight: 20 }}>{m.content}</Text>
        : <Markdown text={m.content} />}
      {m.trace?.length > 0 && (
        <Pressable onPress={() => setOpen(!open)} style={{ marginTop: 8 }}>
          <Badge label={`${m.trace.length} vérification(s), ${open ? 'masquer' : 'voir'}`} color={C.teal} />
        </Pressable>
      )}
      {open && m.trace.map((t, i) => (
        <Text key={i} style={[mono, { color: C.muted, fontSize: 11, marginTop: 4 }]}>
          {i + 1}. {t.tool} {JSON.stringify(t.input || {})}
        </Text>
      ))}
    </View>
  )
}

// Réglages : adresse du backend (IP du PC sur le Wi-Fi) et jeton d'accès.
import { useState } from 'react'
import { Pressable, ScrollView, Switch, Text, TextInput, View } from 'react-native'
import { api, setApiConfig } from '../api'
import { Button, Card, Muted, Title } from '../components'
import { askPermission, notificationsAvailable } from '../notify'
import { saveSettings } from '../storage'
import { C, mono } from '../theme'

const THEMES = [['system', 'Automatique'], ['light', 'Clair'], ['dark', 'Sombre']]

export default function Settings({ settings, onSaved, themeChoice, onTheme }) {
  const [url, setUrl] = useState(settings.url || 'http://192.168.1.10:8000')
  const [token, setToken] = useState(settings.token)
  const [notify, setNotify] = useState(settings.notify)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)

  const save = async () => {
    setBusy(true); setMsg(null)
    const clean = { url: url.trim().replace(/\/+$/, ''), token: token.trim(), notify }
    setApiConfig(clean)
    try {
      const r = await api('/mobile/ping', { timeout: 8000 })
      await saveSettings(clean)
      if (notify) await askPermission()
      setMsg({ ok: true, text: `Connecté à ${r.app} ${r.version} (vu par le serveur comme ${r.client})` })
      onSaved(clean)
    } catch (e) { setMsg({ ok: false, text: e.message }) }
    setBusy(false)
  }

  const input = { color: C.text, backgroundColor: C.elevated, borderRadius: 6, padding: 12, marginTop: 6, marginBottom: 12, ...mono }
  return (
    <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 14 }} keyboardShouldPersistTaps="handled">
      <Card>
        <Title>Connexion au backend</Title>
        <Muted>Adresse du PC sur le Wi-Fi (commande « ipconfig » sous Windows), port 8000.</Muted>
        <TextInput value={url} onChangeText={setUrl} autoCapitalize="none" autoCorrect={false} keyboardType="url"
          placeholder="http://192.168.1.10:8000" placeholderTextColor={C.muted} style={input} />
        <Muted>Jeton : la valeur de MOBILE_TOKEN dans ~/aikube/.env</Muted>
        <TextInput value={token} onChangeText={setToken} autoCapitalize="none" autoCorrect={false} secureTextEntry
          placeholder="jeton" placeholderTextColor={C.muted} style={input} />
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <Text style={{ color: C.text }}>Notifier les nouvelles alertes critiques</Text>
          <Switch value={notify} onValueChange={setNotify} trackColor={{ true: C.purple }} />
        </View>
        {notify && !notificationsAvailable() && (
          <Muted style={{ marginBottom: 12, color: C.amber }}>
            Expo Go ne peut pas afficher de notifications : elles arrivent par l'application ntfy (abonne-toi au canal NTFY_TOPIC du fichier .env). Les alertes restent aussi visibles dans l'accueil.
          </Muted>
        )}
        <Button label="Tester et enregistrer" onPress={save} busy={busy} disabled={!url || !token} />
        {msg && <Muted style={{ marginTop: 10, color: msg.ok ? C.green : C.red }}>{msg.text}</Muted>}
      </Card>
      <Card>
        <Title>Apparence</Title>
        <View style={{ flexDirection: 'row', borderWidth: 1, borderColor: C.border, borderRadius: 6, overflow: 'hidden' }}>
          {THEMES.map(([id, label], i) => (
            <Pressable key={id} onPress={() => onTheme?.(id)} accessibilityRole="button" accessibilityState={{ selected: themeChoice === id }}
              style={{ flex: 1, paddingVertical: 10, alignItems: 'center', backgroundColor: themeChoice === id ? C.purple : 'transparent',
                borderLeftWidth: i ? 1 : 0, borderLeftColor: C.border }}>
              <Text style={{ color: themeChoice === id ? C.onAccent : C.text, fontWeight: themeChoice === id ? '600' : '400' }}>{label}</Text>
            </Pressable>
          ))}
        </View>
        <Muted style={{ marginTop: 8 }}>« Automatique » suit le réglage clair ou sombre du téléphone.</Muted>
      </Card>
      <Card>
        <Title>À propos</Title>
        <Muted>
          AI-Kube mobile — consultation du cluster, agent de dépannage, génération vérifiée, retour arrière et
          ressources avec confirmation. Le jeton est conservé dans le stockage chiffré du téléphone.
          Les notifications fonctionnent tant que l'application est ouverte ou récemment utilisée.
        </Muted>
      </Card>
    </ScrollView>
  )
}

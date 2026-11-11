// Générateur : même pipeline que l'application web (lecture de l'hôte, génération,
// dry-run serveur, contrôle de capacité, correction). Consultation seulement : le
// déploiement se fait depuis le poste, avec l'aperçu des différences.
import { useState } from 'react'
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native'
import * as Clipboard from 'expo-clipboard'
import { api } from '../api'
import { Badge, Button, Card, Code, ErrorBox, Muted, Title } from '../components'
import { C } from '../theme'

const MODELS = [['auto', 'Auto'], ['claude', 'Claude'], ['groq', 'gpt-oss'], ['kubellm-v3', 'KubeLLM v3'], ['kubellm-v4', 'KubeLLM v4']]

export default function Yaml() {
  const [desc, setDesc] = useState('')
  const [model, setModel] = useState('auto')
  const [res, setRes] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  const gen = async () => {
    setBusy(true); setErr(null); setRes(null); setCopied(false)
    try { setRes(await api('/ai/generate-yaml', { method: 'POST', body: { description: desc, model, repair: true }, timeout: 900000 })) }
    catch (e) { setErr(e.message) }
    setBusy(false)
  }
  const copy = async () => { await Clipboard.setStringAsync(res.yaml || ''); setCopied(true) }

  return (
    <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 14 }} keyboardShouldPersistTaps="handled">
      <Card>
        <Title>Décris ce que tu veux déployer</Title>
        <TextInput value={desc} onChangeText={setDesc} multiline placeholder="ex. Redis avec 2 répliques et un Service"
          placeholderTextColor={C.muted} style={{ color: C.text, backgroundColor: C.elevated, borderRadius: 6, padding: 12, minHeight: 80, textAlignVertical: 'top' }} />
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginVertical: 10 }}>
          {MODELS.map(([id, label]) => (
            <Pressable key={id} onPress={() => setModel(id)}>
              <Badge label={label} color={model === id ? C.purple : C.muted} />
            </Pressable>
          ))}
        </View>
        <Button label="Générer et vérifier" onPress={gen} busy={busy} disabled={!desc.trim()} />
        {busy && <Muted style={{ marginTop: 8 }}>Génération puis vérification par le serveur Kubernetes… {model.startsWith('kubellm') ? '(modèle local : 1 à 3 min)' : ''}</Muted>}
      </Card>
      <ErrorBox text={err} />
      {res && (
        <Card>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
            <Badge label={res.valid ? 'valide' : res.server_validated === false ? 'rejeté' : 'non vérifié'}
              color={res.valid ? C.green : C.red} />
            {res.attempts_used != null && <Badge label={`${res.attempts_used} tentative(s)`} color={C.purpleLight} />}
            {res.model_used && <Badge label={res.model_used} color={C.muted} />}
          </View>
          {(res.errors || []).map((e, i) => <Muted key={i} style={{ color: C.red }}>• {e.category} : {String(e.message).slice(0, 200)}</Muted>)}
          {(res.warnings || []).slice(0, 4).map((w, i) => <Muted key={i} style={{ color: C.amber }}>• {w}</Muted>)}
          <View style={{ marginTop: 8 }}><Code text={res.yaml} maxHeight={420} /></View>
          <View style={{ marginTop: 10 }}><Button label={copied ? 'Copié' : 'Copier le YAML'} outline color={C.teal} small onPress={copy} /></View>
          <Muted style={{ marginTop: 8 }}>Pour déployer : page « Déploiement & CI/CD » de l'application web (aperçu, surveillance, retour arrière automatique).</Muted>
        </Card>
      )}
    </ScrollView>
  )
}

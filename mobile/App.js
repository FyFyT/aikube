// AI-Kube mobile — point d'entrée : onglets, chargement périodique du résumé,
// notifications des nouvelles alertes critiques.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Appearance, AppState, Pressable, StatusBar, Text, View } from 'react-native'
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context'
import { api, apiConfigured, setApiConfig } from './src/api'
import { notifyNew } from './src/notify'
import { loadSettings, saveTheme } from './src/storage'
import { Feather } from '@expo/vector-icons'
import { C, applyTheme } from './src/theme'
import Actions from './src/screens/Actions'
import Assistant from './src/screens/Assistant'
import Home from './src/screens/Home'
import Pods from './src/screens/Pods'
import Settings from './src/screens/Settings'
import Yaml from './src/screens/Yaml'

const TABS = [['home', 'Accueil', 'grid'], ['pods', 'Pods', 'box'], ['agent', 'Agent', 'message-square'], ['actions', 'Actions', 'rotate-ccw'], ['yaml', 'YAML', 'file-text'], ['settings', 'Réglages', 'settings']]
const POLL_MS = 60000

export default function App() {
  const [settings, setSettings] = useState(null)
  const [tab, setTab] = useState('home')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [prefill, setPrefill] = useState(null)
  const appState = useRef(AppState.currentState)
  const [visited, setVisited] = useState(new Set(['home']))
  // Thème : choix de l'utilisateur ('system' | 'light' | 'dark') et thème effectif
  const [themeChoice, setThemeChoice] = useState('system')
  const [, setThemeName] = useState(C.name)
  const changeTheme = useCallback((choice) => {
    setThemeChoice(choice); setThemeName(applyTheme(choice)); saveTheme(choice).catch(() => {})
  }, [])
  // En mode automatique, suivre le téléphone quand il change de thème
  useEffect(() => {
    if (themeChoice !== 'system') return
    const sub = Appearance.addChangeListener(() => setThemeName(applyTheme('system')))
    return () => sub.remove()
  }, [themeChoice])
  useEffect(() => { setVisited(v => v.has(tab) ? v : new Set([...v, tab])) }, [tab])
  const askAgent = useCallback((q) => { setPrefill(q); setTab('agent') }, [])

  useEffect(() => {
    loadSettings().then(s => {
      setThemeChoice(s.theme); setThemeName(applyTheme(s.theme))
      setApiConfig(s); setSettings(s)
      if (!s.url || !s.token) setTab('settings')
    })
  }, [])

  const refresh = useCallback(async (force = false) => {
    if (!apiConfigured()) return
    setLoading(true)
    try {
      const d = await api(`/mobile/summary${force ? '?refresh=true' : ''}`, { timeout: 90000 })
      setData(d); setError(null)
      // une notification impossible (permission refusée, plateforme) ne doit jamais masquer les données
      if (settings?.notify) notifyNew(d.alerts || []).catch(() => {})
    } catch (e) { setError(e.message) }
    setLoading(false)
  }, [settings])

  useEffect(() => {
    if (!settings?.url) return
    refresh()
    const t = setInterval(() => { if (appState.current === 'active') refresh() }, POLL_MS)
    const sub = AppState.addEventListener('change', s => { appState.current = s; if (s === 'active') refresh() })
    return () => { clearInterval(t); sub.remove() }
  }, [settings, refresh])

  if (!settings) return <View style={{ flex: 1, backgroundColor: C.bg }} />
  const crit = (data?.alerts || []).filter(a => a.severity === 'critical').length

  return (
    <SafeAreaProvider>
      <SafeAreaView style={{ flex: 1, backgroundColor: C.deep }} edges={['top']}>
        <View style={{ flex: 1, backgroundColor: C.bg }}>
        <StatusBar barStyle="light-content" backgroundColor={C.deep} />
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 16, paddingVertical: 12, backgroundColor: C.deep }}>
          <Feather name="hexagon" size={20} color={C.deepAccent} />
          <Text style={{ color: C.deepText, fontSize: 18, fontWeight: '600', flex: 1 }}>AI-Kube</Text>
          {data && <>
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: data.metrics_ok ? '#5CB585' : '#F07167' }} />
            <Text style={{ color: C.deepMuted, fontSize: 13 }}>{data.cluster?.context || 'cluster'}</Text>
          </>}
        </View>
        <View style={{ flex: 1 }}>
          {/* Les écrans déjà ouverts restent montés (masqués) : la discussion avec
              l'agent, les filtres et les résultats ne sont pas perdus en changeant d'onglet. */}
          {Object.entries({
            home: () => <Home data={data} error={error} loading={loading} refresh={refresh} askAgent={askAgent} />,
            pods: () => <Pods askAgent={askAgent} />,
            agent: () => <Assistant prefill={prefill} clearPrefill={() => setPrefill(null)} />,
            actions: () => <Actions />,
            yaml: () => <Yaml />,
            settings: () => <Settings settings={settings} themeChoice={themeChoice} onTheme={changeTheme} onSaved={(s) => { setSettings(s); setTab('home') }} />,
          }).filter(([id]) => visited.has(id)).map(([id, render]) => (
            <View key={id} style={{ flex: 1, display: tab === id ? 'flex' : 'none' }}>{render()}</View>
          ))}
        </View>
        <View style={{ flexDirection: 'row', borderTopColor: C.border, borderTopWidth: 1, backgroundColor: C.card }}>
          {TABS.map(([id, label, icon]) => (
            <Pressable key={id} onPress={() => setTab(id)} style={{ flex: 1, alignItems: 'center', paddingVertical: 8, borderTopWidth: 2, borderTopColor: tab === id ? C.purple : 'transparent', marginTop: -1 }}>
              <Feather name={icon} size={19} color={tab === id ? C.purple : C.muted} />
              <Text numberOfLines={1} style={{ fontSize: 11, color: tab === id ? C.text : C.muted, fontWeight: tab === id ? '600' : '400', marginTop: 3 }}>{label}</Text>
              {id === 'home' && crit > 0 && (
                <View style={{ position: 'absolute', top: 4, right: '26%', backgroundColor: C.red, borderRadius: 4, minWidth: 16, paddingHorizontal: 4 }}>
                  <Text style={{ color: C.onAccent, fontSize: 10.5, fontWeight: '600', textAlign: 'center' }}>{crit}</Text>
                </View>
              )}
            </Pressable>
          ))}
        </View>
        </View>
        <SafeAreaView edges={['bottom']} style={{ backgroundColor: C.card }} />
      </SafeAreaView>
    </SafeAreaProvider>
  )
}

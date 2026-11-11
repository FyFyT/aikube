import { useState, useCallback, memo } from 'react'
import { useNavigate } from 'react-router-dom'
import { X, Check } from 'lucide-react'

// Formulaire → manifeste YAML (Deployment, et Service si demandé)
function formToYaml(f) {
  const port = parseInt(f.port) || null
  const replicas = parseInt(f.replicas) || 1
  let y = `apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${f.name}
  namespace: ${f.namespace || 'default'}
  labels:
    app: ${f.name}
spec:
  replicas: ${replicas}
  selector:
    matchLabels:
      app: ${f.name}
  template:
    metadata:
      labels:
        app: ${f.name}
    spec:
      containers:
        - name: ${f.name}
          image: ${f.image}
${port ? `          ports:
            - containerPort: ${port}
` : ''}          resources:
            requests:
              cpu: ${f.cpu_request}
              memory: ${f.memory_request}
            limits:
              cpu: ${f.cpu_limit}
              memory: ${f.memory_limit}
`
  if (f.create_service && port) {
    y += `---
apiVersion: v1
kind: Service
metadata:
  name: ${f.name}
  namespace: ${f.namespace || 'default'}
spec:
  selector:
    app: ${f.name}
  ports:
    - port: ${port}
      targetPort: ${port}
`
  }
  return y
}

// Composant Input mémorisé pour éviter le re-render qui perd le focus
const Input = memo(({ value, onChange, placeholder, type='text', min, max }) => (
  <input
    type={type} value={value} onChange={onChange} placeholder={placeholder}
    min={min} max={max}
    style={{
      width:'100%', background:'var(--surface-2)', border:'1px solid color-mix(in srgb, var(--ink) 10%, transparent)',
      borderRadius:6, padding:'9px 12px', fontSize:14, color:'var(--ink)',
      outline:'none', fontFamily:'inherit', boxSizing:'border-box'
    }}
  />
))

const Select = memo(({ value, onChange, children }) => (
  <select value={value} onChange={onChange} style={{
    width:'100%', background:'var(--surface-2)', border:'1px solid color-mix(in srgb, var(--ink) 10%, transparent)',
    borderRadius:6, padding:'9px 12px', fontSize:14, color:'var(--ink)', boxSizing:'border-box'
  }}>{children}</select>
))

const Field = ({ label, children }) => (
  <div style={{ marginBottom:12 }}>
    <div style={{ fontSize:12.5, color:'var(--ink-3)', marginBottom:5 }}>{label}</div>
    {children}
  </div>
)

// ── Tab 1: Formulaire ────────────────────────────────────────
const FormTab = memo(({ form, setForm, namespaces }) => {
  const set = useCallback((k) => (e) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value
    setForm(f => ({ ...f, [k]: v }))
  }, [setForm])

  return (
    <div>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
        <Field label="Nom du deployment">
          <Input value={form.name} onChange={set('name')} placeholder="mon-app" />
        </Field>
        <Field label="Namespace">
          <Select value={form.namespace} onChange={set('namespace')}>
            {(namespaces||[]).map(ns => <option key={ns} value={ns}>{ns}</option>)}
          </Select>
        </Field>
      </div>
      <Field label="Image Docker">
        <Input value={form.image} onChange={set('image')} placeholder="nginx:latest, python:3.11..." />
      </Field>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
        <Field label="Replicas">
          <Input type="number" min="0" max="10" value={form.replicas} onChange={set('replicas')} />
        </Field>
        <Field label="Port exposé (optionnel)">
          <Input type="number" value={form.port} onChange={set('port')} placeholder="8080" />
        </Field>
      </div>
      <div style={{ fontSize:12.5, color:'var(--ink-3)', marginBottom:8 }}>Ressources CPU / RAM</div>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr 1fr', gap:8, marginBottom:14 }}>
        {[
          ['CPU Req','cpu_request','100m'],
          ['RAM Req','memory_request','128Mi'],
          ['CPU Limit','cpu_limit','500m'],
          ['RAM Limit','memory_limit','512Mi'],
        ].map(([l,k,p]) => (
          <div key={k}>
            <div style={{ fontSize:11.5, color:'var(--ink-3)', marginBottom:4 }}>{l}</div>
            <Input value={form[k]} onChange={set(k)} placeholder={p} />
          </div>
        ))}
      </div>
      <div
        onClick={() => setForm(f => ({ ...f, create_service: !f.create_service }))}
        style={{ display:'flex', alignItems:'center', gap:8, padding:'10px 12px', background:'color-mix(in srgb, var(--ink) 3%, transparent)', borderRadius:6, border:'1px solid color-mix(in srgb, var(--ink) 7%, transparent)', cursor:'pointer' }}
      >
        <div style={{
          width:16, height:16, borderRadius:3, flexShrink:0,
          border:`1px solid ${form.create_service ? 'var(--accent)' : 'var(--rule-strong)'}`,
          background: form.create_service ? 'var(--accent)' : 'transparent',
          display:'flex', alignItems:'center', justifyContent:'center', color:'var(--on-accent)'
        }}>{form.create_service ? <Check size={12} strokeWidth={3} /> : null}</div>
        <span style={{ fontSize:13, color:'var(--ink-2)' }}>Créer aussi un Service ClusterIP (si port défini)</span>
      </div>
    </div>
  )
})

// ── Tab 2: YAML direct ───────────────────────────────────────
const YAMLTab = memo(({ yaml, setYaml, namespace, setNamespace, namespaces }) => (
  <div>
    <Field label="Namespace cible">
      <Select value={namespace} onChange={e => setNamespace(e.target.value)}>
        {(namespaces||[]).map(ns => <option key={ns} value={ns}>{ns}</option>)}
      </Select>
    </Field>
    <Field label="Contenu YAML">
      <textarea
        value={yaml}
        onChange={e => setYaml(e.target.value)}
        placeholder={`apiVersion: apps/v1
kind: Deployment
metadata:
  name: mon-app
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mon-app
  template:
    metadata:
      labels:
        app: mon-app
    spec:
      containers:
      - name: mon-app
        image: nginx:latest
        resources:
          requests:
            cpu: 100m
            memory: 128Mi`}
        style={{
          width:'100%', height:280, background:'var(--code-bg)',
          border:'1px solid color-mix(in srgb, var(--ink) 10%, transparent)', borderRadius:6,
          padding:'12px', fontSize:13, color:'var(--ink)',
          fontFamily:'var(--font-mono)', lineHeight:1.7,
          outline:'none', resize:'vertical', boxSizing:'border-box'
        }}
      />
    </Field>
    <div style={{ fontSize:13, color:'var(--ink-3)' }}>
      Coller un manifeste Kubernetes, par exemple celui produit par la page Génération YAML.
    </div>
  </div>
))

// ── Modal principal ──────────────────────────────────────────
export default function CreateDeploymentModal({ isOpen, onClose, onSuccess, namespaces }) {
  const navigate = useNavigate()
  const [tab, setTab] = useState('form')
  const [error, setError] = useState('')

  const [form, setForm] = useState({
    name:'', namespace:'default', image:'nginx:1.27',
    replicas:1, port:'', cpu_request:'100m', memory_request:'128Mi',
    cpu_limit:'500m', memory_limit:'512Mi', create_service:false,
  })

  const [yaml, setYaml] = useState('')
  const [yamlNs, setYamlNs] = useState('default')

  if (!isOpen) return null

  // Les deux onglets passent par le déploiement surveillé (page Déploiement) :
  // aperçu, application après confirmation, surveillance, retour arrière.
  const submitForm = () => {
    if (!form.name.match(/^[a-z0-9-]+$/)) { setError('Nom invalide — utilisez a-z, 0-9, -'); return }
    if (!form.image) { setError('Image requise'); return }
    onClose()
    navigate('/devops', { state: { yaml: formToYaml(form) } })
  }

  const submitYaml = () => {
    if (!yaml.trim()) { setError('YAML vide'); return }
    onClose()
    navigate('/devops', { state: { yaml: yaml.trim(), namespace: yamlNs } })
  }

  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(17,24,32,.45)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:1000 }}>
      <div style={{ background:'var(--surface)', border:'1px solid var(--rule)', borderRadius:6, padding:22, width:540, maxHeight:'92vh', overflowY:'auto', boxShadow:'var(--shadow-lg)' }}>

        {/* Header */}
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:20 }}>
          <div style={{ fontSize:16, fontWeight:600, color:'var(--ink)' }}>Créer un deployment</div>
          <button onClick={onClose} title="Fermer" aria-label="Fermer" style={{ background:'transparent', border:'none', color:'var(--ink-3)', cursor:'pointer', display:'flex' }}><X size={18} /></button>
        </div>

        {/* Tabs */}
        <div className="tabs" style={{ marginBottom:18 }}>
          {[
            { id:'form', label:'Formulaire' },
            { id:'yaml', label:'YAML direct' },
          ].map(t => (
            <button key={t.id} onClick={() => setTab(t.id)} className={'tab' + (tab===t.id ? ' active' : '')}>{t.label}</button>
          ))}
        </div>

        {/* Content */}
        {tab === 'form'
          ? <FormTab form={form} setForm={setForm} namespaces={namespaces} />
          : <YAMLTab yaml={yaml} setYaml={setYaml} namespace={yamlNs} setNamespace={setYamlNs} namespaces={namespaces} />
        }

        {/* Error */}
        {error && (
          <div style={{ color:'var(--crit)', fontSize:13, margin:'12px 0', padding:'8px 12px', background:'color-mix(in srgb, var(--crit) 8%, transparent)', borderRadius:6, border:'1px solid color-mix(in srgb, var(--crit) 20%, transparent)' }}>
            {error}
          </div>
        )}

        {/* Buttons */}
        <div style={{ display:'flex', gap:10, marginTop:16 }}>
          <button onClick={onClose} className="btn btn-ghost" style={{ marginLeft:'auto' }}>
            Annuler
          </button>
          <button onClick={tab==='form' ? submitForm : submitYaml} className="btn btn-primary">
            Prévisualiser et déployer →
          </button>
        </div>
      </div>
    </div>
  )
}

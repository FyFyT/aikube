#!/bin/bash
# ============================================================
# AI-Kube — Script création fichiers backend complets
# Lancer dans Ubuntu WSL : bash create_backend.sh
# ============================================================

set -e
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

PROJECT="$HOME/aikube"
echo -e "${BLUE}🚀 Création des fichiers backend AI-Kube...${NC}"

# ── security_router.py ──────────────────────────────────────
cat > $PROJECT/backend/api/security_router.py << 'ENDSEC'
from fastapi import APIRouter, HTTPException
from core.kubernetes_manager import k8s_manager
from kubernetes.client.rest import ApiException
import json

router = APIRouter()

@router.get("/rbac/roles")
async def get_roles():
    try:
        roles = k8s_manager.rbac_v1.list_role_for_all_namespaces()
        cluster_roles = k8s_manager.rbac_v1.list_cluster_role()
        result = []
        for r in roles.items:
            result.append({
                "name": r.metadata.name,
                "namespace": r.metadata.namespace,
                "type": "Role",
                "rules_count": len(r.rules or []),
            })
        for r in cluster_roles.items:
            result.append({
                "name": r.metadata.name,
                "namespace": "cluster-wide",
                "type": "ClusterRole",
                "rules_count": len(r.rules or []),
            })
        return {"roles": result, "count": len(result)}
    except ApiException as e:
        raise HTTPException(status_code=e.status, detail=str(e.reason))

@router.get("/rbac/bindings")
async def get_bindings():
    try:
        bindings = k8s_manager.rbac_v1.list_role_binding_for_all_namespaces()
        result = []
        for b in bindings.items:
            result.append({
                "name": b.metadata.name,
                "namespace": b.metadata.namespace,
                "role": b.role_ref.name,
                "role_kind": b.role_ref.kind,
                "subjects": [
                    {"name": s.name, "kind": s.kind}
                    for s in (b.subjects or [])
                ],
            })
        return {"bindings": result, "count": len(result)}
    except ApiException as e:
        raise HTTPException(status_code=e.status, detail=str(e.reason))

@router.get("/network-policies")
async def get_network_policies():
    try:
        nps = k8s_manager.networking_v1.list_network_policy_for_all_namespaces()
        result = []
        for np in nps.items:
            result.append({
                "name": np.metadata.name,
                "namespace": np.metadata.namespace,
                "pod_selector": np.spec.pod_selector.match_labels or {},
                "policy_types": np.spec.policy_types or [],
            })
        return {"policies": result, "count": len(result)}
    except ApiException as e:
        raise HTTPException(status_code=e.status, detail=str(e.reason))

@router.get("/audit/scan")
async def security_scan():
    """Scan de sécurité IA sur les configurations du cluster"""
    issues = []
    score = 100

    try:
        # Vérifier les pods sans SecurityContext
        pods = k8s_manager.v1.list_pod_for_all_namespaces()
        for pod in pods.items:
            for container in (pod.spec.containers or []):
                sc = container.security_context
                if not sc:
                    issues.append({
                        "type": "MissingSecurityContext",
                        "severity": "warning",
                        "resource": f"pod/{pod.metadata.name}",
                        "namespace": pod.metadata.namespace,
                        "description": f"Container '{container.name}' sans SecurityContext",
                        "fix": "Ajouter runAsNonRoot: true, readOnlyRootFilesystem: true",
                        "cis_control": "CIS-5.2.6",
                    })
                    score -= 5
                elif sc.run_as_root is True or sc.privileged is True:
                    issues.append({
                        "type": "PrivilegedContainer",
                        "severity": "critical",
                        "resource": f"pod/{pod.metadata.name}",
                        "namespace": pod.metadata.namespace,
                        "description": f"Container '{container.name}' tourne en mode privilégié",
                        "fix": "Désactiver privileged: false et runAsNonRoot: true",
                        "cis_control": "CIS-5.2.1",
                    })
                    score -= 15

        # Vérifier les namespaces sans NetworkPolicy
        namespaces = k8s_manager.v1.list_namespace()
        nps = k8s_manager.networking_v1.list_network_policy_for_all_namespaces()
        ns_with_policy = {np.metadata.namespace for np in nps.items}

        for ns in namespaces.items:
            ns_name = ns.metadata.name
            if ns_name not in ["kube-system", "kube-public", "kube-node-lease"]:
                if ns_name not in ns_with_policy:
                    issues.append({
                        "type": "NoNetworkPolicy",
                        "severity": "warning",
                        "resource": f"namespace/{ns_name}",
                        "namespace": ns_name,
                        "description": f"Namespace '{ns_name}' sans NetworkPolicy — trafic non filtré",
                        "fix": "Créer une NetworkPolicy deny-all par défaut",
                        "cis_control": "CIS-5.3.2",
                    })
                    score -= 8

        # Vérifier les secrets exposés en variables d'env
        for pod in pods.items:
            for container in (pod.spec.containers or []):
                for env in (container.env or []):
                    if env.name and any(
                        kw in env.name.upper()
                        for kw in ["PASSWORD", "SECRET", "TOKEN", "KEY", "API"]
                    ):
                        if env.value and not env.value_from:
                            issues.append({
                                "type": "SecretInEnv",
                                "severity": "critical",
                                "resource": f"pod/{pod.metadata.name}",
                                "namespace": pod.metadata.namespace,
                                "description": f"Variable sensible '{env.name}' en clair dans env",
                                "fix": "Utiliser un Secret Kubernetes ou valueFrom.secretKeyRef",
                                "cis_control": "CIS-5.4.1",
                            })
                            score -= 20

        score = max(0, score)
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        issues.sort(key=lambda x: severity_order.get(x.get("severity", "info"), 2))

        return {
            "cis_score": score,
            "grade": "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D",
            "issues": issues,
            "critical_count": len([i for i in issues if i["severity"] == "critical"]),
            "warning_count": len([i for i in issues if i["severity"] == "warning"]),
            "total_issues": len(issues),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/secrets")
async def get_secrets():
    """Liste les secrets (sans exposer les valeurs)"""
    try:
        secrets = k8s_manager.v1.list_secret_for_all_namespaces()
        return {
            "secrets": [
                {
                    "name": s.metadata.name,
                    "namespace": s.metadata.namespace,
                    "type": s.type,
                    "keys": list((s.data or {}).keys()),
                    "age": str(s.metadata.creation_timestamp),
                }
                for s in secrets.items
                if s.metadata.name != "default-token"
            ]
        }
    except ApiException as e:
        raise HTTPException(status_code=e.status, detail=str(e.reason))
ENDSEC

echo -e "${GREEN}✅ security_router.py créé${NC}"

# ── devops_router.py ─────────────────────────────────────────
cat > $PROJECT/backend/api/devops_router.py << 'ENDDEV'
from fastapi import APIRouter, HTTPException
from core.kubernetes_manager import k8s_manager
from core.config import settings
import httpx
import json

router = APIRouter()

@router.get("/health")
async def devops_health():
    """Vérifie la santé des outils DevOps intégrés"""
    status = {}

    # Prometheus
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{settings.PROMETHEUS_URL}/-/healthy")
            status["prometheus"] = "up" if r.status_code == 200 else "down"
    except:
        status["prometheus"] = "unreachable"

    return {"services": status, "k8s": k8s_manager.connected}

@router.get("/prometheus/query")
async def prometheus_query(query: str = "up"):
    """Exécute une query PromQL"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{settings.PROMETHEUS_URL}/api/v1/query",
                params={"query": query}
            )
            return r.json()
    except Exception as e:
        return {"error": str(e), "message": "Prometheus non disponible — installe-le avec: minikube addons enable metrics-server"}

@router.get("/metrics/cluster")
async def get_cluster_metrics():
    """Métriques générales du cluster"""
    try:
        pods = k8s_manager.v1.list_pod_for_all_namespaces()
        nodes = k8s_manager.v1.list_node()
        deployments = k8s_manager.apps_v1.list_deployment_for_all_namespaces()

        return {
            "pods": {
                "total": len(pods.items),
                "running": sum(1 for p in pods.items if p.status.phase == "Running"),
                "pending": sum(1 for p in pods.items if p.status.phase == "Pending"),
                "failed": sum(1 for p in pods.items if p.status.phase == "Failed"),
                "succeeded": sum(1 for p in pods.items if p.status.phase == "Succeeded"),
            },
            "nodes": {
                "total": len(nodes.items),
                "ready": sum(
                    1 for n in nodes.items
                    if any(c.type == "Ready" and c.status == "True"
                           for c in (n.status.conditions or []))
                ),
            },
            "deployments": {
                "total": len(deployments.items),
                "healthy": sum(
                    1 for d in deployments.items
                    if (d.status.ready_replicas or 0) == (d.spec.replicas or 0)
                ),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/events")
async def get_events(namespace: str = None):
    """Récupère les événements Kubernetes récents"""
    try:
        if namespace:
            events = k8s_manager.v1.list_namespaced_event(namespace)
        else:
            events = k8s_manager.v1.list_event_for_all_namespaces()

        result = []
        for e in sorted(
            events.items,
            key=lambda x: x.last_timestamp or x.event_time,
            reverse=True
        )[:50]:
            result.append({
                "name": e.metadata.name,
                "namespace": e.metadata.namespace,
                "reason": e.reason,
                "message": e.message,
                "type": e.type,
                "count": e.count,
                "involved_object": f"{e.involved_object.kind}/{e.involved_object.name}",
                "last_seen": str(e.last_timestamp or e.event_time),
            })
        return {"events": result, "count": len(result)}
    except ApiException as e:
        raise HTTPException(status_code=e.status, detail=str(e.reason))

@router.post("/helm/install")
async def helm_install_info():
    """Info sur l'installation Helm (pour la recherche)"""
    return {
        "message": "Helm integration — utilise kubectl pour appliquer les charts",
        "commands": [
            "helm repo add stable https://charts.helm.sh/stable",
            "helm install prometheus prometheus-community/kube-prometheus-stack",
            "helm list -A",
        ]
    }
ENDDEV

echo -e "${GREEN}✅ devops_router.py créé${NC}"

# ── Mettre à jour main.py avec tous les routers ──────────────
cat > $PROJECT/backend/main.py << 'ENDMAIN'
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from core.config import settings
from core.kubernetes_manager import k8s_manager
from api.cluster_router import router as cluster_router
from api.ai_router import router as ai_router
from api.security_router import router as security_router
from api.devops_router import router as devops_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 AI-Kube v{settings.APP_VERSION} démarrage...")
    await k8s_manager.connect()
    print(f"✅ Kubernetes: {'connecté' if k8s_manager.connected else 'non connecté'}")
    yield
    print("👋 Arrêt AI-Kube")

app = FastAPI(
    title="AI-Kube API",
    description="Intelligent Kubernetes Manager — Master Recherche 2025",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cluster_router, prefix="/api/cluster", tags=["Cluster"])
app.include_router(ai_router,      prefix="/api/ai",      tags=["Intelligence IA"])
app.include_router(security_router,prefix="/api/security",tags=["Cybersécurité"])
app.include_router(devops_router,  prefix="/api/devops",  tags=["DevOps"])

@app.get("/")
async def root():
    return {
        "app": "AI-Kube",
        "version": settings.APP_VERSION,
        "status": "running",
        "k8s_connected": k8s_manager.connected,
        "endpoints": {
            "cluster": "/api/cluster",
            "ia":      "/api/ai",
            "security":"/api/security",
            "devops":  "/api/devops",
            "docs":    "/docs",
        }
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "k8s": k8s_manager.connected}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
ENDMAIN

echo -e "${GREEN}✅ main.py mis à jour${NC}"

# ── requirements.txt final ───────────────────────────────────
cat > $PROJECT/requirements.txt << 'ENDREQ'
fastapi==0.111.0
uvicorn[standard]==0.30.0
kubernetes==30.1.0
pyyaml==6.0.1
openai==1.35.0
google-generativeai==0.7.1
python-dotenv==1.0.1
pydantic==2.7.4
pydantic-settings==2.3.4
httpx==0.27.0
aiofiles==23.2.1
scikit-learn==1.5.0
numpy==1.26.4
pandas==2.2.2
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
ENDREQ

echo -e "${GREEN}✅ requirements.txt créé${NC}"

# ── README.md ────────────────────────────────────────────────
cat > $PROJECT/README.md << 'ENDREADME'
# 🤖 AI-Kube — Intelligent Kubernetes Manager
## Master Recherche 2025

### Démarrage rapide
```bash
cd ~/aikube
source venv/bin/activate
cd backend && python main.py
```

API disponible sur : http://localhost:8000
Documentation Swagger : http://localhost:8000/docs

### Endpoints principaux
- GET  /api/cluster/pods         → Liste des pods
- GET  /api/cluster/nodes        → Liste des nœuds
- GET  /api/cluster/deployments  → Liste des deployments
- GET  /api/cluster/services     → Liste des services
- GET  /api/cluster/namespaces   → Liste des namespaces
- POST /api/ai/generate-yaml     → Génération YAML intelligente
- GET  /api/ai/detect            → Détection anomalies
- POST /api/ai/chat              → Assistant IA
- GET  /api/security/audit/scan  → Scan sécurité CIS
- GET  /api/security/rbac/roles  → Rôles RBAC
- GET  /api/devops/metrics/cluster → Métriques cluster
- GET  /api/devops/events        → Événements K8s

### Contributions scientifiques
- C1: Fine-tuning KubeLLM (phi-2 + LoRA)
- C2: XAI — Raisonnement tracé
- C3: Détection hybride (ML + règles + LLM)
- C4: KubeSecAI — Cybersécurité IA
- C5: DevSecOps intelligent
- C6: KubeBench — Framework évaluation
ENDREADME

echo -e "${GREEN}✅ README.md créé${NC}"

# ── .gitignore ───────────────────────────────────────────────
cat > $PROJECT/.gitignore << 'ENDGIT'
venv/
__pycache__/
*.pyc
*.pyo
.pytest_cache/
models/fine_tuned/
models/checkpoints/
.env
node_modules/
dist/
.DS_Store
ENDGIT

echo -e "${GREEN}✅ .gitignore créé${NC}"

# ── package.json frontend ────────────────────────────────────
cat > $PROJECT/frontend/package.json << 'ENDPKG'
{
  "name": "aikube-frontend",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.24.0",
    "axios": "^1.7.2",
    "@tanstack/react-query": "^5.49.2",
    "recharts": "^2.12.7",
    "@monaco-editor/react": "^4.6.0",
    "zustand": "^4.5.4",
    "clsx": "^2.1.1",
    "date-fns": "^3.6.0",
    "lucide-react": "^0.400.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.1",
    "vite": "^5.3.3",
    "tailwindcss": "^3.4.4",
    "autoprefixer": "^10.4.19",
    "postcss": "^8.4.39"
  }
}
ENDPKG

echo -e "${GREEN}✅ package.json frontend créé${NC}"

# ── vite.config.js ───────────────────────────────────────────
cat > $PROJECT/frontend/vite.config.js << 'ENDVITE'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
ENDVITE

# ── tailwind.config.js ───────────────────────────────────────
cat > $PROJECT/frontend/tailwind.config.js << 'ENDTW'
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: '#0A0C10',
          secondary: '#0F1117',
          tertiary: '#161A23',
        },
        brand: {
          purple: '#7C6AF7',
          teal: '#22D3A5',
          red: '#FF5E6D',
          amber: '#F4A64A',
          green: '#4ADE80',
        }
      },
      fontFamily: {
        sans: ['Space Grotesk', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      }
    },
  },
  plugins: [],
}
ENDTW

# ── postcss.config.js ────────────────────────────────────────
cat > $PROJECT/frontend/postcss.config.js << 'ENDPOST'
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
ENDPOST

# ── index.html ───────────────────────────────────────────────
cat > $PROJECT/frontend/index.html << 'ENDHTML'
<!DOCTYPE html>
<html lang="fr">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>AI-Kube — Intelligent Kubernetes Manager</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
ENDHTML

# ── src/main.jsx ─────────────────────────────────────────────
cat > $PROJECT/frontend/src/main.jsx << 'ENDJSX'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './styles/index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchInterval: 30000, retry: 1 }
  }
})

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
)
ENDJSX

# ── src/styles/index.css ─────────────────────────────────────
cat > $PROJECT/frontend/src/styles/index.css << 'ENDCSS'
@tailwind base;
@tailwind components;
@tailwind utilities;

* { box-sizing: border-box; }

body {
  background: #0A0C10;
  color: #F0F2F7;
  font-family: 'Space Grotesk', sans-serif;
  margin: 0;
}

::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #0F1117; }
::-webkit-scrollbar-thumb { background: rgba(124,106,247,.3); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #7C6AF7; }

code, pre, .font-mono { font-family: 'JetBrains Mono', monospace; }
ENDCSS

# ── src/utils/api.js ─────────────────────────────────────────
cat > $PROJECT/frontend/src/utils/api.js << 'ENDAPI'
import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' }
})

// Cluster
export const clusterAPI = {
  getPods:        (ns)  => api.get(`/cluster/pods${ns ? `?namespace=${ns}` : ''}`),
  getNodes:       ()    => api.get('/cluster/nodes'),
  getDeployments: (ns)  => api.get(`/cluster/deployments${ns ? `?namespace=${ns}` : ''}`),
  getServices:    (ns)  => api.get(`/cluster/services${ns ? `?namespace=${ns}` : ''}`),
  getNamespaces:  ()    => api.get('/cluster/namespaces'),
  getPodLogs:     (ns, name, tail=100) => api.get(`/cluster/pods/${ns}/${name}/logs?tail=${tail}`),
  deletePod:      (ns, name) => api.delete(`/cluster/pods/${ns}/${name}`),
}

// IA
export const aiAPI = {
  generateYAML: (description, model='gpt4o') =>
    api.post('/ai/generate-yaml', { description, model }),
  detect:       () => api.get('/ai/detect'),
  chat:         (message, history=[], model='gpt4o') =>
    api.post('/ai/chat', { message, history, model }),
}

// Sécurité
export const securityAPI = {
  scan:              () => api.get('/security/audit/scan'),
  getRoles:          () => api.get('/security/rbac/roles'),
  getBindings:       () => api.get('/security/rbac/bindings'),
  getNetworkPolicies:() => api.get('/security/network-policies'),
  getSecrets:        () => api.get('/security/secrets'),
}

// DevOps
export const devopsAPI = {
  getMetrics: () => api.get('/devops/metrics/cluster'),
  getEvents:  (ns) => api.get(`/devops/events${ns ? `?namespace=${ns}` : ''}`),
  getHealth:  () => api.get('/devops/health'),
}

export default api
ENDAPI

# ── src/App.jsx ──────────────────────────────────────────────
cat > $PROJECT/frontend/src/App.jsx << 'ENDAPP'
import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/layout/Layout'
import Dashboard from './pages/Dashboard'
import Pods from './pages/Pods'
import YAMLGenerator from './pages/YAMLGenerator'
import Detection from './pages/Detection'
import Security from './pages/Security'
import DevOps from './pages/DevOps'
import Assistant from './pages/Assistant'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard"  element={<Dashboard />} />
        <Route path="pods"       element={<Pods />} />
        <Route path="yaml"       element={<YAMLGenerator />} />
        <Route path="detection"  element={<Detection />} />
        <Route path="security"   element={<Security />} />
        <Route path="devops"     element={<DevOps />} />
        <Route path="assistant"  element={<Assistant />} />
      </Route>
    </Routes>
  )
}
ENDAPP

# ── Layout.jsx ───────────────────────────────────────────────
cat > $PROJECT/frontend/src/components/layout/Layout.jsx << 'ENDLAYOUT'
import { Outlet, NavLink, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { clusterAPI, aiAPI } from '../../utils/api'

const navItems = [
  { path: '/dashboard', icon: '⬡', label: 'Dashboard', section: 'cluster' },
  { path: '/pods',      icon: '◻', label: 'Pods & Ressources', section: 'cluster' },
  { path: '/yaml',      icon: '✦', label: 'Génération YAML', section: 'ia' },
  { path: '/detection', icon: '◎', label: 'Détection IA', section: 'ia' },
  { path: '/assistant', icon: '◈', label: 'Assistant', section: 'ia' },
  { path: '/security',  icon: '⬡', label: 'Cybersécurité', section: 'security' },
  { path: '/devops',    icon: '⟳', label: 'DevOps & CI/CD', section: 'devops' },
]

export default function Layout() {
  const { data: detect } = useQuery({
    queryKey: ['detect'],
    queryFn: () => aiAPI.detect().then(r => r.data),
    refetchInterval: 30000,
  })

  const criticals = detect?.critical || 0

  return (
    <div style={{ display:'flex', height:'100vh', overflow:'hidden' }}>
      {/* SIDEBAR */}
      <aside style={{
        width: 220, background: '#0F1117',
        borderRight: '1px solid rgba(255,255,255,0.07)',
        display: 'flex', flexDirection: 'column', flexShrink: 0
      }}>
        {/* Logo */}
        <div style={{ padding:'18px 16px 14px', borderBottom:'1px solid rgba(255,255,255,0.07)' }}>
          <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:14 }}>
            <div style={{
              width:32, height:32, background:'linear-gradient(135deg,#7C6AF7,#5B4FD4)',
              borderRadius:8, display:'flex', alignItems:'center', justifyContent:'center',
              fontSize:16, color:'#fff', fontWeight:700
            }}>K</div>
            <div>
              <div style={{ fontSize:15, fontWeight:700 }}>AI-Kube</div>
              <div style={{ fontSize:10, color:'#555870', fontFamily:'JetBrains Mono,monospace' }}>v1.0-research</div>
            </div>
          </div>
          {/* Cluster status */}
          <div style={{
            display:'flex', alignItems:'center', gap:8,
            background:'#161A23', border:'1px solid rgba(255,255,255,0.07)',
            borderRadius:8, padding:'8px 10px'
          }}>
            <div style={{ width:7, height:7, borderRadius:'50%', background:'#4ADE80', boxShadow:'0 0 6px rgba(74,222,128,.5)', flexShrink:0 }}></div>
            <div>
              <div style={{ fontSize:11, fontWeight:500 }}>minikube</div>
              <div style={{ fontSize:10, color:'#555870', fontFamily:'JetBrains Mono,monospace' }}>local · 3 nodes</div>
            </div>
            {criticals > 0 && (
              <div style={{ marginLeft:'auto', background:'#FF5E6D', color:'#fff', fontSize:10, padding:'1px 7px', borderRadius:8, fontWeight:600 }}>
                {criticals}
              </div>
            )}
          </div>
        </div>

        {/* Nav */}
        <nav style={{ padding:'10px 8px', flex:1, overflowY:'auto' }}>
          {['cluster','ia','security','devops'].map(section => (
            <div key={section} style={{ marginBottom:6 }}>
              <div style={{ fontSize:10, color:'#555870', padding:'6px 10px 3px', textTransform:'uppercase', letterSpacing:'.08em' }}>
                {section === 'ia' ? 'Intelligence IA' : section === 'cluster' ? 'Cluster' : section === 'security' ? 'Cybersécurité' : 'DevOps'}
              </div>
              {navItems.filter(n => n.section === section).map(item => (
                <NavLink key={item.path} to={item.path} style={({ isActive }) => ({
                  display:'flex', alignItems:'center', gap:9, padding:'8px 10px',
                  borderRadius:7, fontSize:13, marginBottom:1, textDecoration:'none',
                  background: isActive ? 'rgba(124,106,247,.12)' : 'transparent',
                  color: isActive ? '#A78BFA' : '#8B8FA8',
                  fontWeight: isActive ? 600 : 400,
                })}>
                  <span style={{ fontSize:14 }}>{item.icon}</span>
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        {/* Footer */}
        <div style={{ padding:'12px 16px', borderTop:'1px solid rgba(255,255,255,0.07)', display:'flex', alignItems:'center', gap:10 }}>
          <div style={{
            width:30, height:30, borderRadius:'50%',
            background:'rgba(124,106,247,.2)', display:'flex',
            alignItems:'center', justifyContent:'center',
            fontSize:11, fontWeight:700, color:'#A78BFA', flexShrink:0
          }}>T</div>
          <div>
            <div style={{ fontSize:12, fontWeight:500 }}>Tamft</div>
            <div style={{ fontSize:10, color:'#555870' }}>Master Recherche</div>
          </div>
        </div>
      </aside>

      {/* MAIN */}
      <main style={{ flex:1, overflow:'auto', background:'#0A0C10' }}>
        <Outlet />
      </main>
    </div>
  )
}
ENDLAYOUT

# ── Dashboard.jsx ────────────────────────────────────────────
cat > $PROJECT/frontend/src/pages/Dashboard.jsx << 'ENDDASH'
import { useQuery } from '@tanstack/react-query'
import { clusterAPI, aiAPI, devopsAPI } from '../utils/api'

export default function Dashboard() {
  const { data: pods } = useQuery({ queryKey: ['pods'], queryFn: () => clusterAPI.getPods().then(r => r.data) })
  const { data: nodes } = useQuery({ queryKey: ['nodes'], queryFn: () => clusterAPI.getNodes().then(r => r.data) })
  const { data: detect } = useQuery({ queryKey: ['detect'], queryFn: () => aiAPI.detect().then(r => r.data) })
  const { data: metrics } = useQuery({ queryKey: ['metrics'], queryFn: () => devopsAPI.getMetrics().then(r => r.data) })

  const running = pods?.pods?.filter(p => p.status === 'Running').length || 0
  const pending = pods?.pods?.filter(p => p.status === 'Pending').length || 0
  const failed  = pods?.pods?.filter(p => p.status === 'Failed').length || 0
  const total   = pods?.count || 0

  return (
    <div style={{ padding:20 }}>
      {/* Header */}
      <div style={{ marginBottom:20 }}>
        <h1 style={{ fontSize:20, fontWeight:700, marginBottom:4 }}>Dashboard</h1>
        <p style={{ fontSize:13, color:'#555870' }}>Vue d'ensemble du cluster Kubernetes en temps réel</p>
      </div>

      {/* Metrics */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:12, marginBottom:16 }}>
        {[
          { label:'Pods Running', value: running, color:'#22D3A5', sub:`${total} total` },
          { label:'Pods Pending', value: pending, color: pending > 0 ? '#F4A64A' : '#4ADE80', sub:'à planifier' },
          { label:'Pods Failed',  value: failed,  color: failed > 0 ? '#FF5E6D' : '#4ADE80', sub:'en erreur' },
          { label:'Alertes IA',   value: detect?.critical || 0, color: (detect?.critical||0) > 0 ? '#FF5E6D' : '#4ADE80', sub:`${detect?.total||0} total` },
        ].map(m => (
          <div key={m.label} style={{ background:'#0F1117', border:'1px solid rgba(255,255,255,0.07)', borderRadius:12, padding:16 }}>
            <div style={{ fontSize:10, color:'#555870', marginBottom:8, textTransform:'uppercase', letterSpacing:'.06em' }}>{m.label}</div>
            <div style={{ fontSize:28, fontWeight:700, color: m.color, lineHeight:1 }}>{m.value}</div>
            <div style={{ fontSize:11, color:'#555870', marginTop:6 }}>{m.sub}</div>
          </div>
        ))}
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
        {/* Nodes */}
        <div style={{ background:'#0F1117', border:'1px solid rgba(255,255,255,0.07)', borderRadius:12, padding:16 }}>
          <div style={{ fontSize:13, fontWeight:600, marginBottom:14, display:'flex', alignItems:'center', gap:7 }}>
            <span style={{ color:'#7C6AF7' }}>◫</span> Nœuds du cluster
          </div>
          {nodes?.nodes?.map(node => (
            <div key={node.name} style={{ display:'flex', alignItems:'center', gap:10, padding:'8px 0', borderBottom:'1px solid rgba(255,255,255,0.05)' }}>
              <div style={{ width:8, height:8, borderRadius:'50%', background: node.status === 'Ready' ? '#4ADE80' : '#FF5E6D', flexShrink:0 }}></div>
              <div style={{ flex:1 }}>
                <div style={{ fontSize:12, fontWeight:500, fontFamily:'JetBrains Mono,monospace' }}>{node.name}</div>
                <div style={{ fontSize:10, color:'#555870' }}>CPU: {node.cpu} · RAM: {node.memory}</div>
              </div>
              <span style={{
                fontSize:10, padding:'2px 8px', borderRadius:7, fontWeight:500,
                background: node.status === 'Ready' ? 'rgba(74,222,128,.12)' : 'rgba(255,94,109,.12)',
                color: node.status === 'Ready' ? '#4ADE80' : '#FF5E6D',
              }}>{node.status}</span>
            </div>
          ))}
        </div>

        {/* Alertes IA */}
        <div style={{ background:'#0F1117', border:'1px solid rgba(255,255,255,0.07)', borderRadius:12, padding:16 }}>
          <div style={{ fontSize:13, fontWeight:600, marginBottom:14, display:'flex', alignItems:'center', gap:7 }}>
            <span style={{ color:'#FF5E6D' }}>◎</span> Détections IA récentes
          </div>
          {(detect?.problems || []).slice(0,5).map(p => (
            <div key={p.id} style={{ display:'flex', alignItems:'flex-start', gap:10, padding:'8px 0', borderBottom:'1px solid rgba(255,255,255,0.05)' }}>
              <div style={{
                width:28, height:28, borderRadius:7, display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0,
                background: p.severity === 'critical' ? 'rgba(255,94,109,.15)' : 'rgba(244,166,74,.15)',
                color: p.severity === 'critical' ? '#FF5E6D' : '#F4A64A',
                fontSize:14
              }}>!</div>
              <div style={{ flex:1 }}>
                <div style={{ fontSize:12, fontWeight:500, marginBottom:2 }}>{p.title}</div>
                <div style={{ fontSize:10, color:'#555870' }}>{p.description?.substring(0,60)}...</div>
              </div>
            </div>
          ))}
          {(!detect?.problems || detect.problems.length === 0) && (
            <div style={{ textAlign:'center', color:'#4ADE80', fontSize:13, padding:'20px 0' }}>✓ Aucun problème détecté</div>
          )}
        </div>
      </div>

      {/* Pods table */}
      <div style={{ background:'#0F1117', border:'1px solid rgba(255,255,255,0.07)', borderRadius:12, padding:16, marginTop:12 }}>
        <div style={{ fontSize:13, fontWeight:600, marginBottom:14 }}>Pods récents</div>
        <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
          <thead>
            <tr>
              {['Nom','Namespace','Nœud','Restarts','Statut'].map(h => (
                <th key={h} style={{ textAlign:'left', padding:'5px 10px', borderBottom:'1px solid rgba(255,255,255,0.07)', fontSize:10, color:'#555870', textTransform:'uppercase', letterSpacing:'.05em' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(pods?.pods || []).slice(0,8).map(pod => (
              <tr key={pod.name} style={{ borderBottom:'1px solid rgba(255,255,255,0.04)' }}>
                <td style={{ padding:'9px 10px', fontFamily:'JetBrains Mono,monospace', fontSize:11 }}>{pod.name}</td>
                <td style={{ padding:'9px 10px', color:'#8B8FA8' }}>{pod.namespace}</td>
                <td style={{ padding:'9px 10px', color:'#8B8FA8', fontFamily:'JetBrains Mono,monospace', fontSize:11 }}>{pod.node || '—'}</td>
                <td style={{ padding:'9px 10px', color: pod.restarts > 0 ? '#F4A64A' : '#8B8FA8' }}>{pod.restarts}</td>
                <td style={{ padding:'9px 10px' }}>
                  <span style={{
                    fontSize:10, padding:'2px 8px', borderRadius:7, fontWeight:500,
                    background: pod.status==='Running' ? 'rgba(74,222,128,.12)' : pod.status==='Pending' ? 'rgba(244,166,74,.12)' : 'rgba(255,94,109,.12)',
                    color: pod.status==='Running' ? '#4ADE80' : pod.status==='Pending' ? '#F4A64A' : '#FF5E6D',
                  }}>{pod.status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
ENDDASH

# ── Pages simplifiées (stubs) ────────────────────────────────
for page in Pods YAMLGenerator Detection Security DevOps Assistant; do
cat > $PROJECT/frontend/src/pages/${page}.jsx << ENDSTUB
import { useEffect, useState } from 'react'
import { $(echo $page | tr '[:upper:]' '[:lower:]' | sed 's/generator//'| sed 's/yaml/ai/')API } from '../utils/api'

export default function ${page}() {
  return (
    <div style={{ padding:20 }}>
      <h1 style={{ fontSize:20, fontWeight:700, marginBottom:4 }}>${page}</h1>
      <p style={{ fontSize:13, color:'#555870', marginBottom:20 }}>Page en cours de développement</p>
      <div style={{ background:'#0F1117', border:'1px solid rgba(124,106,247,.2)', borderRadius:12, padding:20 }}>
        <p style={{ color:'#A78BFA', fontSize:13 }}>
          ✦ Cette section sera connectée à l'API backend (/api/$(echo $page | tr '[:upper:]' '[:lower:]')).
          Le backend est déjà prêt — il faut maintenant construire l'interface.
        </p>
      </div>
    </div>
  )
}
ENDSTUB
done

echo -e "${GREEN}✅ Pages frontend créées${NC}"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  ✅ Tous les fichiers AI-Kube sont créés !   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}📋 Prochaines étapes:${NC}"
echo ""
echo -e "  ${BLUE}1. Tester le backend complet:${NC}"
echo "     cd ~/aikube/backend && python main.py"
echo "     curl http://localhost:8000/docs"
echo ""
echo -e "  ${BLUE}2. Installer et lancer le frontend:${NC}"
echo "     cd ~/aikube/frontend"
echo "     npm install"
echo "     npm run dev"
echo ""
echo -e "  ${BLUE}3. Ouvrir l'application:${NC}"
echo "     http://localhost:5173"
echo ""
echo -e "  ${BLUE}4. Ajouter ta clé OpenAI (optionnel):${NC}"
echo "     nano ~/aikube/.env"
echo "     → OPENAI_API_KEY=sk-..."
echo ""

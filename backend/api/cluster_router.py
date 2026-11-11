from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
from core.k8s_cache import cache
from core.kubectl_client import (
    get_summary_parallel,
    kubectl, kubectl_action, kubectl_action_async,
    get_pods, get_nodes, get_deployments, get_namespaces
)
from kubernetes import client
from core.kubernetes_manager import k8s_manager
import subprocess, tempfile, os, json

router = APIRouter()

# ── SUMMARY ──────────────────────────────────────────────────

@router.get("/summary")
async def get_cluster_summary(force: bool = False):
    cached = cache.get("summary")
    if cached and not force:
        return cached
    try:
        result = await get_summary_parallel()
        cache.set("summary", result)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── PODS ─────────────────────────────────────────────────────

@router.get("/pods")
async def api_get_pods(namespace: Optional[str] = None):
    cached = cache.get("summary")
    if cached and not namespace:
        return cached["pods"]
    try:
        pods = get_pods(namespace)
        return {"pods": pods, "count": len(pods)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/pods/{namespace}/{pod_name}/logs")
async def get_pod_logs(namespace: str, pod_name: str, tail: int = 100):
    try:
        result = subprocess.run(
            ['kubectl', 'logs', pod_name, '-n', namespace, f'--tail={tail}'],
            capture_output=True, text=True, timeout=10
        )
        return {"logs": result.stdout, "pod": pod_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/pods/{namespace}/{pod_name}")
async def delete_pod(namespace: str, pod_name: str):
    try:
        # --wait=false : la suppression est demandée, Kubernetes la termine en arrière-plan.
        # Sans cela, kubectl attend la fin de l'arrêt du conteneur (jusqu'à 30 s) et la
        # requête bloquait tout le backend, la boîte de confirmation restant figée.
        await kubectl_action_async(['delete', 'pod', pod_name, '-n', namespace, '--wait=false'])
        cache.invalidate("summary")
        return {"message": f"Pod {pod_name} supprimé", "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── NODES ─────────────────────────────────────────────────────

@router.get("/nodes")
async def api_get_nodes():
    cached = cache.get("summary")
    if cached:
        return cached["nodes"]
    try:
        nodes = get_nodes()
        return {"nodes": nodes, "count": len(nodes)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── NAMESPACES ───────────────────────────────────────────────

@router.get("/namespaces")
async def api_get_namespaces():
    cached = cache.get("summary")
    if cached:
        return cached["namespaces"]
    try:
        return {"namespaces": get_namespaces()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class NamespaceCreate(BaseModel):
    name: str
    labels: Optional[Dict[str, str]] = {}

@router.post("/namespaces")
async def create_namespace(req: NamespaceCreate):
    try:
        await kubectl_action_async(['create', 'namespace', req.name])
        cache.invalidate("summary")
        return {"message": f"Namespace '{req.name}' créé", "name": req.name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/namespaces/{name}")
async def delete_namespace(name: str):
    protected = ["kube-system","kube-public","kube-node-lease","default"]
    if name in protected:
        raise HTTPException(status_code=403, detail=f"Namespace '{name}' protégé")
    try:
        await kubectl_action_async(['delete', 'namespace', name, '--wait=false'])
        cache.invalidate("summary")
        return {"message": f"Namespace '{name}' supprimé", "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── DEPLOYMENTS ──────────────────────────────────────────────

@router.get("/deployments")
async def api_get_deployments(namespace: Optional[str] = None):
    cached = cache.get("summary")
    if cached and not namespace:
        return cached["deployments"]
    try:
        deps = get_deployments(namespace)
        return {"deployments": deps, "count": len(deps)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class DeploymentScale(BaseModel):
    replicas: int

@router.patch("/deployments/{namespace}/{name}/scale")
async def scale_deployment(namespace: str, name: str, req: DeploymentScale):
    if not 0 <= req.replicas <= 20:
        raise HTTPException(status_code=400, detail="Replicas entre 0 et 20")
    try:
        await kubectl_action_async(['scale', 'deployment', name, f'--replicas={req.replicas}', '-n', namespace])
        cache.invalidate("summary")
        return {"message": f"'{name}' scalé à {req.replicas} replicas", "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/deployments/{namespace}/{name}/restart")
async def restart_deployment(namespace: str, name: str):
    try:
        await kubectl_action_async(['rollout', 'restart', 'deployment', name, '-n', namespace])
        cache.invalidate("summary")
        return {"message": f"Deployment '{name}' restart lancé", "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/deployments/{namespace}/{name}")
async def delete_deployment(namespace: str, name: str):
    try:
        await kubectl_action_async(['delete', 'deployment', name, '-n', namespace, '--wait=false'])
        cache.invalidate("summary")
        return {"message": f"Deployment '{name}' supprimé", "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class DeploymentCreate(BaseModel):
    name: str
    namespace: str
    image: str
    replicas: int = 1
    port: Optional[int] = None
    cpu_request: Optional[str] = "100m"
    memory_request: Optional[str] = "128Mi"
    cpu_limit: Optional[str] = "500m"
    memory_limit: Optional[str] = "512Mi"
    env_vars: Optional[Dict[str, str]] = {}
    create_service: Optional[bool] = False

@router.post("/deployments")
async def create_deployment(req: DeploymentCreate):
    try:
        # Construire le manifest YAML
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": req.name, "namespace": req.namespace, "labels": {"app": req.name, "created-by": "ai-kube"}},
            "spec": {
                "replicas": req.replicas,
                "selector": {"matchLabels": {"app": req.name}},
                "template": {
                    "metadata": {"labels": {"app": req.name}},
                    "spec": {
                        "containers": [{
                            "name": req.name,
                            "image": req.image,
                            "ports": [{"containerPort": req.port}] if req.port else [],
                            "env": [{"name": k, "value": v} for k,v in (req.env_vars or {}).items()],
                            "resources": {
                                "requests": {"cpu": req.cpu_request, "memory": req.memory_request},
                                "limits":   {"cpu": req.cpu_limit,   "memory": req.memory_limit},
                            }
                        }]
                    }
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(manifest, f)
            tmp = f.name

        result = subprocess.run(['kubectl', 'apply', '-f', tmp], capture_output=True, text=True, timeout=15)
        os.unlink(tmp)

        if result.returncode != 0:
            raise HTTPException(status_code=400, detail=result.stderr)

        svc_msg = ""
        if req.create_service and req.port:
            subprocess.run([
                'kubectl', 'expose', 'deployment', req.name,
                f'--port={req.port}', '--type=ClusterIP', '-n', req.namespace
            ], capture_output=True, timeout=10)
            svc_msg = f" + Service:{req.port}"

        cache.invalidate("summary")
        return {"message": f"Deployment '{req.name}' créé dans '{req.namespace}'{svc_msg}", "name": req.name, "success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── SERVICES ─────────────────────────────────────────────────

@router.get("/services")
async def api_get_services(namespace: Optional[str] = None):
    try:
        if namespace:
            data = kubectl(['get', 'services', '-n', namespace])
        else:
            data = kubectl(['get', 'services', '-A'])
        return {
            "services": [{
                "name":       s['metadata']['name'],
                "namespace":  s['metadata']['namespace'],
                "type":       s['spec'].get('type','ClusterIP'),
                "cluster_ip": s['spec'].get('clusterIP',''),
                "ports":      [{"port": p['port'], "protocol": p.get('protocol','TCP')} for p in s['spec'].get('ports', [])],
            } for s in data.get('items', [])]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── APPLY YAML ───────────────────────────────────────────────

class YAMLApply(BaseModel):
    yaml: str
    namespace: str = "default"

@router.post("/apply-yaml")
async def apply_yaml(req: YAMLApply):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(req.yaml)
        tmp = f.name
    try:
        result = subprocess.run(['kubectl', 'apply', '-f', tmp], capture_output=True, text=True, timeout=30)
        os.unlink(tmp)
        if result.returncode != 0:
            raise HTTPException(status_code=400, detail=result.stderr)
        cache.invalidate("summary")
        return {"message": result.stdout.strip() or "YAML appliqué", "success": True}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="kubectl apply timeout")

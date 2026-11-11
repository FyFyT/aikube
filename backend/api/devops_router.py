from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import subprocess, json, time
from datetime import datetime, timedelta
import random

router = APIRouter()

def kubectl(args: list, timeout: int = 15) -> dict:
    r = subprocess.run(['kubectl'] + args + ['-o', 'json'],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise Exception(r.stderr.strip())
    return json.loads(r.stdout)

def kubectl_plain(args: list, timeout: int = 15) -> str:
    r = subprocess.run(['kubectl'] + args,
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()

# ── MÉTRIQUES CLUSTER ─────────────────────────────────────────

@router.get("/metrics/cluster")
async def get_cluster_metrics():
    try:
        pods_data  = kubectl(['get', 'pods', '-A'])
        nodes_data = kubectl(['get', 'nodes'])
        deps_data  = kubectl(['get', 'deployments', '-A'])
        pods  = pods_data.get('items', [])
        nodes = nodes_data.get('items', [])
        deps  = deps_data.get('items', [])
        running  = sum(1 for p in pods if p.get('status',{}).get('phase')=='Running')
        pending  = sum(1 for p in pods if p.get('status',{}).get('phase')=='Pending')
        failed   = sum(1 for p in pods if p.get('status',{}).get('phase')=='Failed')
        nodes_ready = sum(1 for n in nodes
            if any(c.get('type')=='Ready' and c.get('status')=='True'
                   for c in n.get('status',{}).get('conditions',[])))
        deps_healthy = sum(1 for d in deps
            if d.get('status',{}).get('readyReplicas',0)==d.get('spec',{}).get('replicas',0))
        return {
            "pods":        {"total":len(pods),"running":running,"pending":pending,"failed":failed},
            "nodes":       {"total":len(nodes),"ready":nodes_ready},
            "deployments": {"total":len(deps),"healthy":deps_healthy},
            "timestamp":   datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── MÉTRIQUES NODES (Prometheus) ──────────────────────────────

@router.get("/metrics/nodes")
async def get_node_metrics():
    try:
        result = subprocess.run(
            ['kubectl', 'top', 'nodes', '--no-headers'],
            capture_output=True, text=True, timeout=10
        )
        nodes = []
        for line in result.stdout.strip().split('\n'):
            if not line: continue
            parts = line.split()
            if len(parts) >= 5:
                nodes.append({
                    "name":        parts[0],
                    "cpu_cores":   parts[1],
                    "cpu_percent": int(parts[2].replace('%','')),
                    "ram_bytes":   parts[3],
                    "ram_percent": int(parts[4].replace('%','')),
                })
        return {"nodes": nodes, "count": len(nodes)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/metrics/pods")
async def get_pod_metrics():
    try:
        result = subprocess.run(
            ['kubectl', 'top', 'pods', '-A', '--no-headers'],
            capture_output=True, text=True, timeout=10
        )
        pods = []
        for line in result.stdout.strip().split('\n'):
            if not line: continue
            parts = line.split()
            if len(parts) >= 4:
                pods.append({"namespace":parts[0],"name":parts[1],"cpu":parts[2],"memory":parts[3]})
        pods.sort(key=lambda x: int(x['cpu'].replace('m','')) if 'm' in x['cpu'] else 0, reverse=True)
        return {"pods": pods[:20], "count": len(pods)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/metrics/history")
async def get_metrics_history():
    try:
        result = subprocess.run(
            ['kubectl', 'top', 'nodes', '--no-headers'],
            capture_output=True, text=True, timeout=10
        )
        nodes_cpu, nodes_ram = [], []
        for line in result.stdout.strip().split('\n'):
            if not line: continue
            parts = line.split()
            if len(parts) >= 5:
                nodes_cpu.append(int(parts[2].replace('%','')))
                nodes_ram.append(int(parts[4].replace('%','')))
        avg_cpu = round(sum(nodes_cpu)/len(nodes_cpu)) if nodes_cpu else 0
        avg_ram = round(sum(nodes_ram)/len(nodes_ram)) if nodes_ram else 0
        now = datetime.now()
        history = [{"time":(now-timedelta(minutes=9-i)).strftime("%H:%M"),"cpu":avg_cpu,"ram":avg_ram} for i in range(10)]
        return {"history":history,"current":{"cpu":avg_cpu,"ram":avg_ram}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── ÉVÉNEMENTS K8s ────────────────────────────────────────────

@router.get("/events")
async def get_events(namespace: Optional[str] = None):
    try:
        if namespace:
            data = kubectl(['get','events','-n',namespace,'--sort-by=.lastTimestamp'])
        else:
            data = kubectl(['get','events','-A','--sort-by=.lastTimestamp'])
        events = []
        for e in data.get('items',[])[-30:]:
            events.append({
                "type":           e.get('type','Normal'),
                "reason":         e.get('reason',''),
                "message":        e.get('message','')[:120],
                "namespace":      e['metadata'].get('namespace',''),
                "involved_object":e.get('involvedObject',{}).get('name',''),
                "count":          e.get('count',1),
                "last_seen":      e.get('lastTimestamp',''),
            })
        events.reverse()
        return {"events":events,"count":len(events)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Retirés le 01/11/2026 (aucun écran ne les appelait plus) : le pipeline CI/CD
# simulé (/pipeline/run, /pipeline/history), l'ancien score de risque
# (/risk-score) et un retour arrière sans vérification (/rollback). Le
# déploiement surveillé et le retour arrière vérifié sont dans devops_v2_router.

@router.get("/health")
async def get_health():
    try:
        nodes = kubectl(['get','nodes'])
        pods  = kubectl(['get','pods','-A'])
        total_nodes  = len(nodes.get('items',[]))
        ready_nodes  = sum(1 for n in nodes.get('items',[]) if any(c.get('type')=='Ready' and c.get('status')=='True' for c in n.get('status',{}).get('conditions',[])))
        total_pods   = len(pods.get('items',[]))
        running_pods = sum(1 for p in pods.get('items',[]) if p.get('status',{}).get('phase')=='Running')
        health_pct   = round((ready_nodes/total_nodes*0.4 + running_pods/total_pods*0.6)*100) if total_nodes>0 and total_pods>0 else 0
        return {
            "health_score":health_pct,
            "nodes":{"total":total_nodes,"ready":ready_nodes},
            "pods":{"total":total_pods,"running":running_pods},
            "status":"healthy" if health_pct>=80 else "degraded" if health_pct>=60 else "critical",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── NOTIFICATIONS ─────────────────────────────────────────────

@router.get("/notifications")
async def get_notifications():
    """
    Retourne les alertes temps réel du cluster.
    Utilisé par le badge de notification en sidebar.
    """
    try:
        notifications = []
        # Même critère que /ai/detect (règles v3) : un pod n'est signalé que
        # s'il redémarre encore, est bloqué ou en échec — pas pour un compteur
        # de redémarrages accumulé depuis sa création.
        from ai_module.detection_comparator import fetch_real_pods, rules_v3_predict
        for pod in fetch_real_pods(exclude_namespaces=set()):
            v = rules_v3_predict(pod)
            if not v["is_anomaly"]:
                continue
            ns, name, phase = pod["namespace"], pod["name"], pod["status"]
            base = {"resource": name, "namespace": ns, "timestamp": datetime.now().isoformat()}
            if phase == 'Failed':
                notifications.append({"id":f"failed-{ns}-{name}","type":"error","severity":"critical","title":"Pod en échec","message":f"{name} ({ns})", **base})
            elif phase == 'Pending':
                notifications.append({"id":f"pending-{ns}-{name}","type":"info","severity":"warning","title":"Pod en attente","message":f"{name} non planifié", **base})
            elif v.get("stabilised") is False:
                notifications.append({"id":f"crash-{ns}-{name}","type":"warning","severity":"critical","title":"Redémarrages en cours","message":f"{name} redémarre encore ({pod.get('restarts',0)} au total)", **base})
            else:
                notifications.append({"id":f"notready-{ns}-{name}","type":"info","severity":"warning","title":"Pod non disponible","message":f"{name} ({ns})", **base})

        # Prédictions de crash mémoire (OOM) : seule panne annoncée AVANT qu'elle arrive.
        # Placées en tête pour ne pas être coupées par la limite de 10 alertes.
        try:
            from ai_module import oom_predictor
            oom_alerts = []
            for p in oom_predictor.get_predictions().get("predictions", [])[:5]:
                ns, name = p["namespace"], p["pod"]
                mins = max(1, round(p["eta_seconds"] / 60))
                oom_alerts.append({
                    "id": f"oom-{ns}-{name}", "type": "oom",
                    "severity": "critical" if mins < 15 else "warning",
                    "title": "Crash mémoire annoncé",
                    "message": f"{name} atteindra sa limite mémoire dans ~{mins} min "
                               f"({p['usage_percent']} % utilisés, confiance {p['confidence']})",
                    "resource": name, "namespace": ns, "timestamp": datetime.now().isoformat(),
                })
            notifications = oom_alerts + notifications
        except Exception:  # noqa: BLE001 — la prédiction ne doit jamais casser les alertes
            pass

        return {
            "notifications": notifications[:10],
            "count":         len(notifications),
            "critical":      sum(1 for n in notifications if n['severity']=='critical'),
            "warning":       sum(1 for n in notifications if n['severity']=='warning'),
        }
    except Exception as e:
        return {"notifications":[],"count":0,"critical":0,"warning":0}

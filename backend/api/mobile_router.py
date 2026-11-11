"""
API de l'application mobile — /api/mobile.

L'application mobile réutilise les routes existantes (agent, générateur, retour
arrière, recommandations). Ce module ajoute seulement :
  GET /ping      test de connexion (et du jeton)
  GET /summary   état condensé pour l'écran d'accueil + liste d'alertes

Sécurité : toute requête qui ne vient PAS de la machine elle-même doit porter
« Authorization: Bearer <MOBILE_TOKEN> » (voir le middleware dans main.py).
"""
import asyncio
import hashlib
import json
import subprocess
import time

from fastapi import APIRouter, Request

router = APIRouter()
_cache: dict = {"t": 0.0, "data": None}
SYSTEM_NS = {"kube-system", "kube-public", "kube-node-lease"}


def _problem_pods(include_system: bool = False) -> list[dict]:
    try:
        r = subprocess.run(["kubectl", "get", "pods", "-A", "-o", "json"],
                           capture_output=True, text=True, timeout=25)
        items = json.loads(r.stdout).get("items", []) if r.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []
    out = []
    for p in items:
        ns, name = p["metadata"]["namespace"], p["metadata"]["name"]
        if ns in SYSTEM_NS and not include_system:
            continue
        st = p.get("status", {})
        phase = st.get("phase")
        if phase == "Succeeded":
            continue
        reason, restarts = None, 0
        for cs in st.get("containerStatuses") or []:
            restarts += cs.get("restartCount", 0)
            w = ((cs.get("state") or {}).get("waiting") or {}).get("reason")
            last = ((cs.get("lastState") or {}).get("terminated") or {}).get("reason")
            if w:
                reason = w
            elif not cs.get("ready") and phase == "Running":
                reason = reason or "pas prêt"
            if last == "OOMKilled":
                reason = reason or "OOMKilled"
        if phase == "Pending" and not st.get("containerStatuses"):
            reason = "Pending"
        if phase == "Failed":
            reason = "Failed"
        if reason:
            owner = (p["metadata"].get("ownerReferences") or [{}])[0]
            out.append({"namespace": ns, "pod": name, "reason": reason, "restarts": restarts,
                        "owner_kind": owner.get("kind"), "owner": owner.get("name")})
    return out


def _alert(sev: str, kind: str, title: str, detail: str, target: dict | None = None) -> dict:
    aid = hashlib.sha1(f"{kind}|{title}".encode()).hexdigest()[:12]
    return {"id": aid, "severity": sev, "kind": kind, "title": title, "detail": detail,
            "target": target or {}}


def _summary() -> dict:
    from ai_module import oom_predictor, platform_health, rightsizing, safe_deploy, security_scanner
    from ai_module import host_context
    t0 = time.time()
    ctx = asyncio.run(host_context.collect())
    host, cluster = ctx.get("host") or {}, ctx.get("cluster") or {}
    nodes = cluster.get("nodes", [])
    health = platform_health.check()
    pods = _problem_pods()
    try:
        oom = oom_predictor.get_predictions()
    except Exception:  # noqa: BLE001
        oom = {"predictions": [], "warmup": True}
    try:
        reco = rightsizing.analyse(include_system=False)
        risky = [r for r in reco["recommendations"] if r["status"] in ("oom_imminent", "sous_dimensionne")]
        reco_counts = reco["summary"]["by_status"]
    except Exception:  # noqa: BLE001
        risky, reco_counts = [], {}
    scans = [v for v in security_scanner._LAST_SCAN.values() if v]
    scan = max(scans, key=lambda s: s.get("generated_at", 0)) if scans else None
    deploys = safe_deploy.stats().get("recent", [])[:5]

    alerts = []
    if health.get("degraded"):
        alerts.append(_alert("critical", "metrics", "Mesures du cluster indisponibles",
                             f"{(health.get('metrics_server') or {}).get('reason', '')} — {health.get('impact', '')}",
                             {"fix": health.get("fix")}))
    for p in oom.get("predictions", [])[:5]:
        mins = round(p["eta_seconds"] / 60, 1)
        alerts.append(_alert("critical" if mins < 15 else "warning", "oom",
                             f"OOM annoncé : {p['namespace']}/{p['pod']}",
                             f"dans ~{mins} min · {p['usage_percent']} % de la limite · confiance {p['confidence']}",
                             {"namespace": p["namespace"], "pod": p["pod"]}))
    for p in pods[:10]:
        alerts.append(_alert("critical" if p["reason"] in ("CrashLoopBackOff", "OOMKilled", "Failed",
                                                          "ImagePullBackOff", "ErrImagePull") else "warning",
                             "pod", f"{p['namespace']}/{p['pod']} : {p['reason']}",
                             f"{p['restarts']} redémarrage(s) · {p['owner_kind'] or ''} {p['owner'] or ''}".strip(),
                             p))
    for r in risky[:5]:
        alerts.append(_alert("warning", "reco", f"Ressources : {r['kind'].lower()}/{r['workload']}",
                             (r.get("reasons") or [""])[0][:200],
                             {k: r.get(k) for k in ("kind", "namespace", "workload", "container",
                                                    "recommended", "current", "usage", "status")}))
    if scan and scan["summary"]["critical"]:
        alerts.append(_alert("warning", "security", f"{scan['summary']['critical']} faille(s) critique(s)",
                             f"note de risque {scan['summary']['risk_grade']} · dernier scan "
                             f"{time.strftime('%H:%M', time.localtime(scan['generated_at']))}"))
    for d in deploys:
        if d.get("outcome") in ("regression", "apply_failed"):
            alerts.append(_alert("warning", "deploy", f"Déploiement en échec : {', '.join(d.get('objects', [])[:2])}",
                                 f"{d.get('reason', '')[:160]} · retour arrière : "
                                 f"{'oui' if d.get('rolled_back') else 'non'}"))
    # Une même alerte peut revenir plusieurs fois (ex. deux déploiements ratés du même
    # objet) : même identifiant, donc doublon à l'écran. On garde la première.
    seen_ids = set()
    alerts = [a for a in alerts if not (a["id"] in seen_ids or seen_ids.add(a["id"]))]
    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: order[a["severity"]])

    load = host.get("load_avg_1m")
    return {
        "generated_at": time.time(), "compute_ms": round((time.time() - t0) * 1000),
        "host": {"cpu_count": host.get("cpu_count"), "load": load,
                 "load_ratio": round(load / host["cpu_count"], 2) if load and host.get("cpu_count") else None,
                 "ram_available_mib": host.get("ram_available_mib"), "ram_total_mib": host.get("ram_total_mib")},
        "cluster": {"context": cluster.get("context"), "version": cluster.get("server_version"),
                    "nodes": len(nodes), "nodes_ready": sum(1 for n in nodes if n.get("ready")),
                    "cpu_free_m": cluster.get("total_cpu_free_m"), "mem_free_mib": cluster.get("total_mem_free_mib"),
                    "local": cluster.get("local_cluster")},
        "pods_in_trouble": len(pods),
        "oom": {"warmup": oom.get("warmup"), "count": len(oom.get("predictions", []))},
        "resources": reco_counts,
        "security": scan["summary"] if scan else None,
        "metrics_ok": not health.get("degraded"),
        "recent_deploys": deploys,
        "alerts": alerts,
    }


@router.get("/ping")
async def ping(request: Request):
    from core.config import settings
    return {"ok": True, "app": "AI-Kube", "version": settings.APP_VERSION,
            "client": request.client.host if request.client else None}


@router.get("/summary")
async def summary(refresh: bool = False):
    if not refresh and _cache["data"] and time.time() - _cache["t"] < 15:
        return _cache["data"]
    data = await asyncio.get_running_loop().run_in_executor(None, _summary)
    _cache.update(t=time.time(), data=data)
    return data


# ── Pods (liste et détail, comme la page Pods du web) ─────────────────────────
import re
from datetime import datetime, timezone

from fastapi import HTTPException

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")


def _kjson(args: list[str], timeout: int = 25) -> dict:
    r = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise HTTPException(502, r.stderr.strip()[:300] or "kubectl a échoué")
    return json.loads(r.stdout)


def _age(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        return int((datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds())
    except ValueError:
        return None


def _display_status(p: dict) -> str:
    """Même logique que la colonne STATUS de « kubectl get pods »."""
    st = p.get("status", {})
    if p["metadata"].get("deletionTimestamp"):
        return "Terminating"
    for cs in st.get("initContainerStatuses") or []:
        w = ((cs.get("state") or {}).get("waiting") or {}).get("reason")
        if w:
            return f"Init:{w}"
    reason = st.get("reason") or st.get("phase", "Unknown")
    for cs in st.get("containerStatuses") or []:
        s = cs.get("state") or {}
        if s.get("waiting", {}).get("reason"):
            return s["waiting"]["reason"]
        if s.get("terminated", {}).get("reason"):
            reason = s["terminated"]["reason"]
    return reason


def _workload(meta: dict) -> tuple[str | None, str | None]:
    o = (meta.get("ownerReferences") or [{}])[0]
    kind, name = o.get("kind"), o.get("name")
    if kind == "ReplicaSet" and name:
        h = (meta.get("labels") or {}).get("pod-template-hash")
        if h and name.endswith("-" + h):
            return "Deployment", name[: -len(h) - 1]
    return kind, name


def _top() -> dict:
    try:
        r = subprocess.run(["kubectl", "top", "pods", "-A", "--no-headers"], capture_output=True, text=True, timeout=12)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out = {}
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) >= 4:
            out[(f[0], f[1])] = {"cpu": f[2], "mem": f[3]}
    return out


def _pods(namespace: str | None) -> dict:
    data = _kjson(["get", "pods", "-n", namespace] if namespace else ["get", "pods", "-A"])
    top = _top()
    pods = []
    for p in data.get("items", []):
        m, st, spec = p["metadata"], p.get("status", {}), p.get("spec", {})
        css = st.get("containerStatuses") or []
        status = _display_status(p)
        ready = sum(1 for c in css if c.get("ready"))
        total = len(spec.get("containers", []))
        healthy = status in ("Running", "Completed", "Succeeded") and (ready == total or status != "Running")
        wk, wn = _workload(m)
        pods.append({
            "namespace": m["namespace"], "name": m["name"], "status": status,
            "ready": f"{ready}/{total}", "healthy": healthy,
            "restarts": sum(c.get("restartCount", 0) for c in css),
            "age_s": _age(m.get("creationTimestamp")), "node": spec.get("nodeName"),
            "workload_kind": wk, "workload": wn, "system": m["namespace"] in SYSTEM_NS,
            "usage": top.get((m["namespace"], m["name"])),
        })
    pods.sort(key=lambda x: (x["healthy"], x["system"], x["namespace"], x["name"]))
    return {"pods": pods, "namespaces": sorted({p["namespace"] for p in pods}), "metrics": bool(top)}


@router.get("/pods")
async def pods(namespace: str | None = None):
    if namespace and not NAME_RE.match(namespace):
        raise HTTPException(400, "namespace invalide")
    return await asyncio.get_running_loop().run_in_executor(None, _pods, namespace)


def _pod_detail(ns: str, name: str) -> dict:
    p = _kjson(["get", "pod", name, "-n", ns])
    m, st, spec = p["metadata"], p.get("status", {}), p.get("spec", {})
    by_name = {c["name"]: c for c in st.get("containerStatuses") or []}
    containers = []
    for c in spec.get("containers", []):
        cs = by_name.get(c["name"], {})
        state = cs.get("state") or {}
        k = next(iter(state), None)
        last = (cs.get("lastState") or {}).get("terminated")
        containers.append({
            "name": c["name"], "image": c.get("image"), "ready": cs.get("ready", False),
            "restarts": cs.get("restartCount", 0), "state": k,
            "reason": (state.get(k) or {}).get("reason") if k else None,
            "message": ((state.get(k) or {}).get("message") or "")[:400] if k else "",
            "last": {"reason": last.get("reason"), "exit_code": last.get("exitCode"),
                     "finished": last.get("finishedAt")} if last else None,
            "resources": c.get("resources") or {},
        })
    ev = _kjson(["get", "events", "-n", ns, "--field-selector", f"involvedObject.name={name}"])
    events = sorted(ev.get("items", []), key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "", reverse=True)
    wk, wn = _workload(m)
    return {
        "namespace": ns, "name": name, "status": _display_status(p), "node": spec.get("nodeName"),
        "ip": st.get("podIP"), "age_s": _age(m.get("creationTimestamp")), "qos": st.get("qosClass"),
        "workload_kind": wk, "workload": wn, "containers": containers,
        "conditions": [{"type": c["type"], "ok": c["status"] == "True", "reason": c.get("reason")}
                       for c in st.get("conditions", [])],
        "events": [{"type": e.get("type"), "reason": e.get("reason"), "count": e.get("count") or 1,
                    "message": (e.get("message") or "")[:300], "age_s": _age(e.get("lastTimestamp") or e.get("eventTime"))}
                   for e in events[:15]],
    }


@router.get("/pods/{namespace}/{name}")
async def pod_detail(namespace: str, name: str):
    if not (NAME_RE.match(namespace) and NAME_RE.match(name)):
        raise HTTPException(400, "nom invalide")
    return await asyncio.get_running_loop().run_in_executor(None, _pod_detail, namespace, name)


@router.get("/pods/{namespace}/{name}/logs")
async def pod_logs(namespace: str, name: str, container: str | None = None, previous: bool = False, tail: int = 200):
    if not (NAME_RE.match(namespace) and NAME_RE.match(name)) or (container and not NAME_RE.match(container)):
        raise HTTPException(400, "nom invalide")
    args = ["kubectl", "logs", name, "-n", namespace, f"--tail={max(10, min(tail, 1000))}", "--timestamps=false"]
    if container:
        args += ["-c", container]
    if previous:
        args.append("--previous")

    def run():
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            return {"logs": "", "error": "délai dépassé"}
        return {"logs": r.stdout[-60000:], "error": r.stderr.strip()[:300] if r.returncode else None}
    return await asyncio.get_running_loop().run_in_executor(None, run)

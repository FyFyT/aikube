"""
Contexte réel de la machine hôte et du cluster, injecté avant toute génération.

Objectif fondateur du projet : « générer un YAML en lisant la configuration de
la machine hôte d'abord ». Ce module est cette lecture. Il produit deux choses :

1. un résumé lisible par le modèle (`to_prompt`) : versions d'API exactes du
   serveur, capacité libre par nœud, namespaces et classes de stockage
   existants, conteneurs proches d'un OOM ;
2. des données structurées que la boucle de validation réutilise pour vérifier
   que les `requests` d'un manifeste tiennent réellement sur un nœud.

La capacité libre est calculée comme `allocatable - somme des requests` des
pods non terminés : c'est le critère qu'utilise l'ordonnanceur de Kubernetes,
pas l'usage instantané mesuré par metrics-server. L'usage instantané est
rapporté à part, à titre indicatif.
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

_executor = ThreadPoolExecutor(max_workers=8)
_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
CACHE_TTL = 20  # secondes

# ── Historique de charge de la machine hôte ─────────────────────────────────
# Sans historique, on explique un incident PASSÉ avec l'état PRÉSENT de la
# machine (erreur constatée le 19/10 : « pas de surcharge » conclu sur une
# charge actuelle de 0,74, alors que la panne avait eu lieu sous forte charge).
# Un relevé toutes les 30 s, conservé 6 h en mémoire et ajouté à un journal.
from collections import deque  # noqa: E402
_HOST_HIST: deque = deque(maxlen=720)
_HOST_LOG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                         "data", "observations", "host_load.jsonl"))
_hist_restored = False


def _restore_host_history() -> None:
    global _hist_restored
    if _hist_restored:
        return
    _hist_restored = True
    try:
        if not os.path.exists(_HOST_LOG):
            return
        cutoff = time.time() - 6 * 3600
        with open(_HOST_LOG, "rb") as f:
            f.seek(0, 2)
            start = max(0, f.tell() - 200_000)
            f.seek(start)
            lines = f.read().decode("utf-8", "ignore").splitlines()
            if start > 0:
                lines = lines[1:]      # première ligne possiblement coupée
        for line in lines:
            try:
                d = json.loads(line)
                if d["t"] >= cutoff:
                    _HOST_HIST.append((d["t"], d["load1"], d["ram_avail_mib"]))
            except (ValueError, KeyError):
                continue
    except OSError:
        pass


def record_host_sample() -> None:
    """Appelé par le collecteur toutes les 30 s."""
    _restore_host_history()
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        return
    avail = int(_read_meminfo().get("MemAvailable", 0) / 1024**2)
    t = time.time()
    _HOST_HIST.append((t, round(load1, 2), avail))
    try:
        os.makedirs(os.path.dirname(_HOST_LOG), exist_ok=True)
        with open(_HOST_LOG, "a") as f:
            f.write(json.dumps({"t": round(t, 1), "load1": round(load1, 2),
                                "ram_avail_mib": avail, "cpu": os.cpu_count()}) + "\n")
    except OSError:
        pass


def host_history(minutes: int = 120, bucket_min: int = 5) -> dict:
    """Charge et RAM de l'hôte par tranches, horodatées en UTC (comme les logs)."""
    _restore_host_history()
    since = time.time() - minutes * 60
    pts = [p for p in _HOST_HIST if p[0] >= since]
    cpus = os.cpu_count() or 1
    if not pts:
        return {"available": False, "cpu_count": cpus, "buckets": []}
    buckets: dict[int, list] = {}
    for t, load, avail in pts:
        buckets.setdefault(int(t // (bucket_min * 60)), []).append((load, avail))
    out = []
    for k in sorted(buckets):
        vals = buckets[k]
        start = k * bucket_min * 60
        out.append({
            "utc": time.strftime("%H:%M", time.gmtime(start)),
            "local": time.strftime("%H:%M", time.localtime(start)),
            "load_avg": round(sum(v[0] for v in vals) / len(vals), 2),
            "load_max": max(v[0] for v in vals),
            "ram_avail_min_mib": min(v[1] for v in vals),
        })
    peak = max(pts, key=lambda p: p[1])
    return {"available": True, "cpu_count": cpus, "samples": len(pts),
            "covered_min": round((pts[-1][0] - pts[0][0]) / 60),
            "peak": {"utc": time.strftime("%H:%M:%S", time.gmtime(peak[0])),
                     "load": peak[1], "ratio": round(peak[1] / cpus, 2)},
            "buckets": out}

LOCAL_CONTEXTS = ("minikube", "kind-", "k3d-", "docker-desktop", "rancher-desktop",
                  "colima", "orbstack")

# Ressources dont la version d'API est rappelée au modèle en priorité :
# ce sont celles pour lesquelles le modèle fine-tuné se trompait le plus.
KEY_KINDS = [
    "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob",
    "Pod", "Service", "ConfigMap", "Secret", "PersistentVolumeClaim",
    "PersistentVolume", "Namespace", "ServiceAccount", "Ingress",
    "IngressClass", "NetworkPolicy", "HorizontalPodAutoscaler",
    "PodDisruptionBudget", "Role", "RoleBinding", "ClusterRole",
    "ClusterRoleBinding", "StorageClass", "LimitRange", "ResourceQuota",
    "PriorityClass",
]


# ── Conversion des quantités Kubernetes ─────────────────────────────────────

_MEM_SUFFIX = {
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5,
    "k": 1000, "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4, "P": 1000**5,
    "m": 0.001,
}


def parse_cpu(q: Any) -> float:
    """Quantité CPU → millicœurs. '2' → 2000, '500m' → 500, '0.5' → 500."""
    if q is None or q == "":
        return 0.0
    s = str(q).strip()
    try:
        if s.endswith("m"):
            return float(s[:-1])
        if s.endswith("u"):
            return float(s[:-1]) / 1000.0
        if s.endswith("n"):
            return float(s[:-1]) / 1_000_000.0
        return float(s) * 1000.0
    except ValueError:
        return 0.0


def parse_mem(q: Any) -> float:
    """Quantité mémoire → octets. '512Mi', '1G', '8167836Ki', '1e9'."""
    if q is None or q == "":
        return 0.0
    s = str(q).strip()
    for suf in ("Ki", "Mi", "Gi", "Ti", "Pi"):
        if s.endswith(suf):
            try:
                return float(s[:-2]) * _MEM_SUFFIX[suf]
            except ValueError:
                return 0.0
    if s and s[-1] in "kKMGTPm":
        try:
            return float(s[:-1]) * _MEM_SUFFIX[s[-1]]
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def mib(b: float) -> int:
    return int(round(b / 1024**2))


# ── Appels kubectl ──────────────────────────────────────────────────────────

def _kubectl(args: list[str], timeout: int = 15, as_json: bool = True):
    cmd = ["kubectl"] + args + (["-o", "json"] if as_json else [])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"kubectl {' '.join(args)} a échoué")
    return json.loads(r.stdout) if as_json else r.stdout


async def _safe(fn, *args, **kwargs):
    """Exécute dans un thread ; renvoie (résultat, erreur) sans lever."""
    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(_executor, lambda: fn(*args, **kwargs))
        return res, None
    except Exception as e:  # noqa: BLE001 — chaque source est optionnelle
        return None, str(e)[:300]


# ── Machine hôte (celle qui exécute le backend) ─────────────────────────────

def _read_meminfo() -> dict:
    out = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                parts = v.split()
                if parts:
                    out[k.strip()] = int(parts[0]) * 1024  # kB → octets
    except OSError:
        pass
    return out


def collect_host_machine() -> dict:
    mem = _read_meminfo()
    try:
        load = os.getloadavg()
    except OSError:
        load = (None, None, None)
    disk = shutil.disk_usage("/")
    wsl = "microsoft" in platform.release().lower()
    return {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "wsl": wsl,
        "cpu_count": os.cpu_count(),
        "load_avg_1m": round(load[0], 2) if load[0] is not None else None,
        "ram_total_mib": mib(mem.get("MemTotal", 0)),
        "ram_available_mib": mib(mem.get("MemAvailable", 0)),
        "disk_free_gib": round(disk.free / 1024**3, 1),
        "disk_total_gib": round(disk.total / 1024**3, 1),
    }


# ── Cluster ─────────────────────────────────────────────────────────────────

def _api_resources() -> dict[str, dict]:
    """Kind → {apiVersion, namespaced}. Source de vérité : le serveur lui-même."""
    out = _kubectl(["api-resources", "--no-headers"], as_json=False, timeout=20)
    kinds: dict[str, dict] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        kind, namespaced, api_version = parts[-1], parts[-2], parts[-3]
        # En cas de doublon (ex. Event v1 et events.k8s.io/v1) on garde le premier
        kinds.setdefault(kind, {"apiVersion": api_version,
                                "namespaced": namespaced == "true"})
    return kinds


def _top_nodes() -> dict[str, dict]:
    out = _kubectl(["top", "nodes", "--no-headers"], as_json=False, timeout=10)
    res = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 5:
            try:
                res[p[0]] = {"cpu_used_m": parse_cpu(p[1]),
                             "cpu_used_pct": int(p[2].rstrip("%")),
                             "mem_used_mib": mib(parse_mem(p[3])),
                             "mem_used_pct": int(p[4].rstrip("%"))}
            except ValueError:
                continue
    return res


def _pod_requests_by_node(pods_json: dict) -> dict[str, dict]:
    per_node: dict[str, dict] = {}
    for item in pods_json.get("items", []):
        status = item.get("status", {})
        if status.get("phase") in ("Succeeded", "Failed"):
            continue
        spec = item.get("spec", {})
        node = spec.get("nodeName")
        if not node:
            continue
        cpu = mem = 0.0
        for c in spec.get("containers", []):
            req = (c.get("resources") or {}).get("requests") or {}
            cpu += parse_cpu(req.get("cpu"))
            mem += parse_mem(req.get("memory"))
        # Les initContainers comptent pour leur maximum, pas leur somme
        for c in spec.get("initContainers", []) or []:
            req = (c.get("resources") or {}).get("requests") or {}
            cpu = max(cpu, parse_cpu(req.get("cpu")))
            mem = max(mem, parse_mem(req.get("memory")))
        d = per_node.setdefault(node, {"cpu_req_m": 0.0, "mem_req_b": 0.0, "pods": 0})
        d["cpu_req_m"] += cpu
        d["mem_req_b"] += mem
        d["pods"] += 1
    return per_node


def _oom_risks(limit: int = 5) -> list[dict]:
    try:
        from ai_module.oom_predictor import get_predictions
        preds = get_predictions(min_confidence=0.4).get("predictions", [])
    except Exception:  # noqa: BLE001
        return []
    return [{"namespace": p.get("namespace"), "pod": p.get("pod"),
             "eta_seconds": round(p.get("eta_seconds", 0)),
             "confidence": round(p.get("confidence", 0), 2)}
            for p in preds[:limit]]


async def collect(force: bool = False) -> dict:
    """Instantané complet hôte + cluster. Mis en cache CACHE_TTL secondes."""
    if not force and _CACHE["data"] and time.time() - _CACHE["at"] < CACHE_TTL:
        return _CACHE["data"]

    t0 = time.time()
    (host, host_err), (version, v_err), (kinds, k_err), (nodes_j, n_err), \
        (pods_j, p_err), (ns_j, ns_err), (sc_j, sc_err), (ic_j, _ic_err), \
        (top, top_err) = await asyncio.gather(
            _safe(collect_host_machine),
            _safe(_kubectl, ["version"]),
            _safe(_api_resources),
            _safe(_kubectl, ["get", "nodes"]),
            _safe(_kubectl, ["get", "pods", "-A"]),
            _safe(_kubectl, ["get", "namespaces"]),
            _safe(_kubectl, ["get", "storageclasses"]),
            _safe(_kubectl, ["get", "ingressclasses"]),
            _safe(_top_nodes),
        )
    kctx, _ = await _safe(_kubectl, ["config", "current-context"], as_json=False)
    kctx = (kctx or "").strip()

    errors = {k: v for k, v in {
        "host": host_err, "version": v_err, "api_resources": k_err,
        "nodes": n_err, "pods": p_err, "namespaces": ns_err,
        "storageclasses": sc_err, "metrics": top_err,
    }.items() if v}

    requests_by_node = _pod_requests_by_node(pods_j or {})
    top = top or {}

    nodes = []
    for item in (nodes_j or {}).get("items", []):
        name = item["metadata"]["name"]
        st = item.get("status", {})
        alloc = st.get("allocatable", {})
        cond = {c["type"]: c["status"] for c in st.get("conditions", [])}
        taints = item.get("spec", {}).get("taints", []) or []
        cpu_alloc = parse_cpu(alloc.get("cpu"))
        mem_alloc = parse_mem(alloc.get("memory"))
        req = requests_by_node.get(name, {"cpu_req_m": 0, "mem_req_b": 0, "pods": 0})
        pods_alloc = int(alloc.get("pods", "110") or 110)
        nodes.append({
            "name": name,
            "ready": cond.get("Ready") == "True",
            "schedulable": not item.get("spec", {}).get("unschedulable", False)
                           and not any(t.get("effect") == "NoSchedule" for t in taints),
            "cpu_allocatable_m": round(cpu_alloc),
            "cpu_requested_m": round(req["cpu_req_m"]),
            "cpu_free_m": round(max(0, cpu_alloc - req["cpu_req_m"])),
            "mem_allocatable_mib": mib(mem_alloc),
            "mem_requested_mib": mib(req["mem_req_b"]),
            "mem_free_mib": mib(max(0, mem_alloc - req["mem_req_b"])),
            "pods": req["pods"],
            "pods_allocatable": pods_alloc,
            "usage": top.get(name),
            "kubelet": st.get("nodeInfo", {}).get("kubeletVersion"),
        })

    schedulable = [n for n in nodes if n["ready"] and n["schedulable"]]

    # Cluster local : tous les nœuds partagent la RAM de la machine hôte.
    # La capacité « allouable » annoncée par Kubernetes est alors fictive :
    # 3 nœuds de 11 Gi sur une machine de 12 Gi n'offrent pas 33 Gi.
    labels_local = any(k.startswith(("minikube.k8s.io", "node.kubernetes.io/kind"))
                       for item in (nodes_j or {}).get("items", [])
                       for k in (item["metadata"].get("labels") or {}))
    local = labels_local or kctx.startswith(LOCAL_CONTEXTS)
    sum_free_mem = sum(n["mem_free_mib"] for n in schedulable)
    sum_free_cpu = sum(n["cpu_free_m"] for n in schedulable)
    max_free_mem = max((n["mem_free_mib"] for n in schedulable), default=0)
    host_avail = (host or {}).get("ram_available_mib")
    shared = bool(local and host_avail and len(schedulable) >= 1
                  and sum(n["mem_allocatable_mib"] for n in schedulable) > (host or {}).get("ram_total_mib", 0))
    eff_mem = min(sum_free_mem, host_avail) if (local and host_avail) else sum_free_mem
    eff_max_mem = min(max_free_mem, host_avail) if (local and host_avail) else max_free_mem
    storage_classes = []
    for sc in (sc_j or {}).get("items", []):
        ann = sc["metadata"].get("annotations", {}) or {}
        storage_classes.append({
            "name": sc["metadata"]["name"],
            "default": ann.get("storageclass.kubernetes.io/is-default-class") == "true",
            "provisioner": sc.get("provisioner"),
        })

    server = (version or {}).get("serverVersion", {}) if version else {}
    data = {
        "collected_at": time.time(),
        "collect_ms": round((time.time() - t0) * 1000),
        "host": host,
        "cluster": {
            "server_version": server.get("gitVersion"),
            "nodes": nodes,
            "schedulable_nodes": len(schedulable),
            "total_cpu_free_m": sum(n["cpu_free_m"] for n in schedulable),
            "total_mem_free_mib": eff_mem,
            "declared_mem_free_mib": sum_free_mem,
            "local_cluster": local,
            "shared_host_memory": shared,
            "context": kctx,
            "max_node_cpu_free_m": max((n["cpu_free_m"] for n in schedulable), default=0),
            "max_node_mem_free_mib": eff_max_mem,
            "declared_max_node_mem_free_mib": max_free_mem,
            "namespaces": sorted(i["metadata"]["name"] for i in (ns_j or {}).get("items", [])),
            "storage_classes": storage_classes,
            "ingress_classes": [i["metadata"]["name"] for i in (ic_j or {}).get("items", [])],
            "metrics_available": bool(top),
        },
        "api_kinds": kinds or {},
        "oom_risks": _oom_risks(),
        "errors": errors,
    }
    _CACHE.update(at=time.time(), data=data)
    return data


def to_prompt(ctx: dict) -> str:
    """Résumé compact du contexte, destiné au modèle de langage."""
    c = ctx.get("cluster", {})
    h = ctx.get("host") or {}
    lines = ["## Machine hôte (lue à l'instant)"]
    if h:
        load = h.get("load_avg_1m")
        if load is not None and h.get("cpu_count"):
            ratio = load / h["cpu_count"]
            etat = "SURCHARGÉE" if ratio > 1.5 else "chargée" if ratio > 0.8 else "normale"
            lines.append(f"- Charge de la machine hôte : {load} sur {h['cpu_count']} cœurs ({etat})")
        hh = host_history(60)
        if hh["available"]:
            lines.append(f"- Pic de charge sur la dernière heure : {hh['peak']['load']} à "
                         f"{hh['peak']['utc']} UTC ({hh['peak']['ratio']} par cœur). "
                         f"Détail par tranche : outil host_load_history.")
        lines.append(f"- {h.get('os')} · {h.get('cpu_count')} CPU · RAM disponible "
                     f"{h.get('ram_available_mib')} Mio / {h.get('ram_total_mib')} Mio · "
                     f"disque libre {h.get('disk_free_gib')} Gio")
    lines.append("\n## Cluster Kubernetes")
    lines.append(f"- Version du serveur : {c.get('server_version') or 'inconnue'}")
    lines.append(f"- Nœuds planifiables : {c.get('schedulable_nodes', 0)}")
    lines.append("- Capacité LIBRE par nœud (allocatable − requests déjà réservées) :")
    for n in c.get("nodes", []):
        if not (n["ready"] and n["schedulable"]):
            lines.append(f"  - {n['name']} : non planifiable")
            continue
        use = n.get("usage")
        use_s = (f" · usage réel CPU {use['cpu_used_pct']} %, RAM {use['mem_used_pct']} %"
                 if use else "")
        lines.append(f"  - {n['name']} : CPU libre {n['cpu_free_m']}m sur "
                     f"{n['cpu_allocatable_m']}m · RAM libre {n['mem_free_mib']}Mi sur "
                     f"{n['mem_allocatable_mib']}Mi{use_s}")
    if c.get("local_cluster") and h.get("ram_available_mib"):
        lines.append(f"- ATTENTION : cluster LOCAL ({c.get('context') or 'minikube'}). Tous les nœuds "
                     f"partagent la RAM de la machine hôte : la mémoire réellement disponible est "
                     f"{h['ram_available_mib']} Mio au total, quelle que soit la somme annoncée par les "
                     f"nœuds ({c.get('declared_mem_free_mib')} Mio). La somme des requests mémoire de "
                     f"toutes les répliques doit tenir dans {c.get('total_mem_free_mib')} Mio.")
    lines.append(f"- Le plus gros pod plaçable doit demander au plus "
                 f"{c.get('max_node_cpu_free_m', 0)}m CPU et "
                 f"{c.get('max_node_mem_free_mib', 0)}Mi RAM (un pod tient sur UN nœud).")
    if c.get("namespaces"):
        lines.append(f"- Namespaces existants : {', '.join(c['namespaces'])}")
    if c.get("storage_classes"):
        sc = ", ".join(f"{s['name']}{' (défaut)' if s['default'] else ''}"
                       for s in c["storage_classes"])
        lines.append(f"- StorageClasses : {sc}")
    else:
        lines.append("- Aucune StorageClass : un PVC restera Pending sans PV manuel.")
    lines.append(f"- IngressClasses : {', '.join(c.get('ingress_classes') or []) or 'aucune'}")

    kinds = ctx.get("api_kinds") or {}
    if kinds:
        lines.append("\n## Versions d'API EXACTES servies par ce cluster (à respecter)")
        lines.append(", ".join(f"{k}={kinds[k]['apiVersion']}"
                               for k in KEY_KINDS if k in kinds))
        extra = [k for k in kinds if k not in KEY_KINDS]
        if extra:
            lines.append("Autres kinds disponibles : " + ", ".join(sorted(extra)[:60]))

    if ctx.get("oom_risks"):
        lines.append("\n## Conteneurs proches de leur limite mémoire (prédiction)")
        for r in ctx["oom_risks"]:
            lines.append(f"- {r['namespace']}/{r['pod']} : OOM estimé dans "
                         f"~{r['eta_seconds']} s (confiance {r['confidence']})")
    return "\n".join(lines)

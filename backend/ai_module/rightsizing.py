"""
Analyse des ressources en temps réel et recommandations de dimensionnement.

Pour chaque conteneur de chaque workload (Deployment, StatefulSet, DaemonSet),
on compare ce qui est RÉSERVÉ (requests / limits) à ce qui est CONSOMMÉ
(échantillons `kubectl top`, toutes les 30 s), puis on recommande :

  CPU request     = p95 de l'usage × 1,15        (plancher 10m)
  Mémoire         = maximum observé × 1,25        (request = limit, plancher 32Mi)

C'est la règle des outils de right-sizing existants (KRR de Robusta : p95 CPU,
maximum mémoire + marge). Elle a une faiblesse connue : elle regarde le passé.
Un conteneur dont la mémoire croît régulièrement sera sous-dimensionné dès la
recommandation appliquée — KRR n'augmente la mémoire qu'APRÈS un OOMKill.

L'apport d'AI-Kube : la recommandation intègre la prédiction d'OOM
(oom_predictor). Si une croissance est détectée, la limite recommandée couvre
la mémoire projetée à un horizon donné, ET la recommandation est marquée
« palliatif » : augmenter la limite retarde le crash d'une fuite, il ne la
corrige pas.

Les recommandations sont déterministes et explicables : chaque chiffre est
accompagné de sa source (p95, max, projection, nombre d'échantillons).
L'application passe par `kubectl set resources`, validée d'abord par le serveur
(`--dry-run=server`), et chaque changement appliqué est journalisé.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
from collections import deque
from typing import Optional

from ai_module.host_context import parse_cpu, parse_mem

SAMPLE_INTERVAL = 30          # s
HISTORY_SIZE = 240            # ≈ 2 h par conteneur
MIN_SAMPLES = 6               # ≈ 3 min avant toute recommandation
CPU_MARGIN = 1.15
MEM_MARGIN = 1.25
CPU_FLOOR_M = 10
MEM_FLOOR_MIB = 32
OVER_FACTOR = 2.0             # réservé ≥ 2× le recommandé → sur-dimensionné
MIN_GAIN_CPU_M = 50
MIN_GAIN_MEM_MIB = 64
PROJECTION_HORIZON_S = 3600   # la limite couvre la mémoire projetée à 1 h
MIN_WINDOW_REDUCE_MIN = 15    # pas de réduction proposée avant 15 min d'observation
MAX_REDUCTION = 0.5           # une réduction ne retire pas plus de 50 % par étape
SYSTEM_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease",
                     "kubernetes-dashboard", "ingress-nginx", "metallb-system"}
WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet")

_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                    "data", "observations"))
_STATE_PATH = os.path.join(_DIR, "usage_history.json")
_ACTIONS_PATH = os.path.join(_DIR, "rightsizing_actions.jsonl")

_lock = threading.Lock()
_history: dict[str, deque] = {}    # "ns/pod/container" → deque[(t, cpu_m, mem_mib)]
_started = False
_last_error: Optional[str] = None
_last_sample: Optional[float] = None


# ── Collecte ────────────────────────────────────────────────────────────────

def _sample_once() -> None:
    global _last_error, _last_sample
    r = subprocess.run(["kubectl", "top", "pods", "-A", "--containers", "--no-headers"],
                       capture_output=True, text=True, timeout=25)
    if r.returncode != 0:
        _last_error = (r.stderr or "kubectl top a échoué").strip()[:200]
        return
    _last_error = None
    t = time.time()
    with _lock:
        for line in r.stdout.splitlines():
            p = line.split()
            if len(p) < 5:
                continue
            ns, pod, cont, cpu, mem = p[0], p[1], p[2], p[3], p[4]
            key = f"{ns}/{pod}/{cont}"
            h = _history.setdefault(key, deque(maxlen=HISTORY_SIZE))
            if h and t - h[-1][0] > 600:          # trou de collecte : on repart
                h.clear()
            h.append((t, parse_cpu(cpu), parse_mem(mem) / 1024**2))
    _last_sample = t


def _persist() -> None:
    try:
        os.makedirs(_DIR, exist_ok=True)
        with _lock:
            data = {k: list(v) for k, v in _history.items()}
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, _STATE_PATH)
    except OSError:
        pass


def _restore() -> None:
    try:
        if not os.path.exists(_STATE_PATH):
            return
        if time.time() - os.path.getmtime(_STATE_PATH) > 3 * 3600:
            return                                   # trop ancien pour être pertinent
        with open(_STATE_PATH) as f:
            data = json.load(f)
        with _lock:
            for k, pts in data.items():
                _history[k] = deque([tuple(x) for x in pts], maxlen=HISTORY_SIZE)
    except (OSError, ValueError):
        pass


def _loop() -> None:
    _restore()
    n = 0
    while True:
        try:
            from ai_module.host_context import record_host_sample
            record_host_sample()
        except Exception:  # noqa: BLE001
            pass
        try:
            _sample_once()
            n += 1
            if n % 4 == 0:
                _persist()
        except Exception as e:  # noqa: BLE001
            globals()["_last_error"] = str(e)[:200]
        time.sleep(SAMPLE_INTERVAL)


def ensure_collector() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="rightsizing-sampler").start()


# ── Inventaire des workloads ────────────────────────────────────────────────

def _kubectl_json(args: list[str]) -> dict:
    r = subprocess.run(["kubectl"] + args + ["-o", "json"],
                       capture_output=True, text=True, timeout=25)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return json.loads(r.stdout)


def _inventory() -> tuple[list[dict], dict[str, tuple]]:
    """Workloads avec leurs conteneurs, et pod → (kind, ns, name)."""
    workloads = []
    for kind, res in (("Deployment", "deployments"), ("StatefulSet", "statefulsets"),
                      ("DaemonSet", "daemonsets")):
        for item in _kubectl_json(["get", res, "-A"]).get("items", []):
            meta, spec = item["metadata"], item.get("spec", {})
            containers = (spec.get("template", {}).get("spec", {}) or {}).get("containers", [])
            workloads.append({
                "kind": kind, "namespace": meta["namespace"], "name": meta["name"],
                "replicas": spec.get("replicas", 1) if kind != "DaemonSet"
                            else item.get("status", {}).get("desiredNumberScheduled", 1),
                "containers": [{
                    "name": c["name"], "image": c.get("image"),
                    "requests": (c.get("resources") or {}).get("requests") or {},
                    "limits": (c.get("resources") or {}).get("limits") or {},
                } for c in containers],
            })

    rs_owner = {}
    for rs in _kubectl_json(["get", "replicasets", "-A"]).get("items", []):
        for o in rs["metadata"].get("ownerReferences", []) or []:
            if o.get("kind") == "Deployment":
                rs_owner[f"{rs['metadata']['namespace']}/{rs['metadata']['name']}"] = o["name"]

    pod_owner = {}
    pod_restarts = {}
    for pod in _kubectl_json(["get", "pods", "-A"]).get("items", []):
        ns, name = pod["metadata"]["namespace"], pod["metadata"]["name"]
        for o in pod["metadata"].get("ownerReferences", []) or []:
            if o.get("kind") == "ReplicaSet" and f"{ns}/{o['name']}" in rs_owner:
                pod_owner[f"{ns}/{name}"] = ("Deployment", ns, rs_owner[f"{ns}/{o['name']}"])
            elif o.get("kind") in ("StatefulSet", "DaemonSet"):
                pod_owner[f"{ns}/{name}"] = (o["kind"], ns, o["name"])
        for cs in pod.get("status", {}).get("containerStatuses", []) or []:
            term = (cs.get("lastState", {}) or {}).get("terminated", {}) or {}
            pod_restarts[f"{ns}/{name}/{cs['name']}"] = {
                "restarts": cs.get("restartCount", 0),
                "last_reason": term.get("reason"),
            }
    return workloads, pod_owner, pod_restarts


# ── Calcul ──────────────────────────────────────────────────────────────────

def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def _round_cpu(m: float) -> int:
    return max(CPU_FLOOR_M, int(math.ceil(m / 5.0) * 5))


def _round_mem(mib: float) -> int:
    return max(MEM_FLOOR_MIB, int(math.ceil(mib / 16.0) * 16))


def fmt_cpu(m: Optional[float]) -> Optional[str]:
    return None if m is None else f"{int(round(m))}m"


def fmt_mem(mib: Optional[float]) -> Optional[str]:
    return None if mib is None else f"{int(round(mib))}Mi"


def _oom_by_pod() -> dict[str, dict]:
    try:
        from ai_module.oom_predictor import get_predictions
        preds = get_predictions(min_confidence=0.3).get("predictions", [])
    except Exception:  # noqa: BLE001
        return {}
    return {f"{p['namespace']}/{p['pod']}": p for p in preds}


def analyse(include_system: bool = False) -> dict:
    ensure_collector()
    t0 = time.time()
    workloads, pod_owner, pod_restarts = _inventory()
    oom = _oom_by_pod()

    with _lock:
        hist = {k: list(v) for k, v in _history.items()}

    # Regroupement des échantillons par (workload, conteneur)
    samples: dict[tuple, dict] = {}
    for key, pts in hist.items():
        ns, pod, cont = key.split("/", 2)
        owner = pod_owner.get(f"{ns}/{pod}")
        if not owner:
            continue
        g = samples.setdefault((owner, cont), {"cpu": [], "mem": [], "pods": set(),
                                               "first": None, "oom": None, "restarts": 0,
                                               "oomkilled": False})
        g["cpu"] += [p[1] for p in pts]
        g["mem"] += [p[2] for p in pts]
        g["pods"].add(pod)
        g["first"] = min(g["first"] or pts[0][0], pts[0][0]) if pts else g["first"]
        if f"{ns}/{pod}" in oom:
            cur = oom[f"{ns}/{pod}"]
            if not g["oom"] or cur["eta_seconds"] < g["oom"]["eta_seconds"]:
                g["oom"] = {**cur, "pod": pod}
        rs = pod_restarts.get(key, {})
        g["restarts"] += rs.get("restarts", 0)
        g["oomkilled"] |= rs.get("last_reason") == "OOMKilled"

    recos = []
    for w in workloads:
        system = w["namespace"] in SYSTEM_NAMESPACES
        if system and not include_system:
            continue
        for c in w["containers"]:
            owner = (w["kind"], w["namespace"], w["name"])
            g = samples.get((owner, c["name"]))
            recos.append(_recommend(w, c, g, system))

    order = {"oom_imminent": 0, "sous_dimensionne": 1, "manquant": 2,
             "sur_dimensionne": 3, "adapte": 4, "observation": 5}
    recos.sort(key=lambda r: (order.get(r["status"], 9), -(r.get("gain_mem_mib") or 0)))

    freed_cpu = sum(r["gain_cpu_m"] for r in recos if r["status"] == "sur_dimensionne")
    freed_mem = sum(r["gain_mem_mib"] for r in recos if r["status"] == "sur_dimensionne")
    reserved_cpu = sum((r["current"]["cpu_request_m"] or 0) * r["replicas"] for r in recos)
    reserved_mem = sum((r["current"]["mem_request_mib"] or 0) * r["replicas"] for r in recos)
    used_cpu = sum((r["usage"]["cpu_p95_m"] or 0) * r["replicas"] for r in recos if r["usage"])
    used_mem = sum((r["usage"]["mem_max_mib"] or 0) * r["replicas"] for r in recos if r["usage"])

    return {
        "generated_at": time.time(),
        "compute_ms": round((time.time() - t0) * 1000),
        "collector": {"interval_s": SAMPLE_INTERVAL, "last_sample": _last_sample,
                      "error": _last_error, "series": len(hist),
                      "min_samples": MIN_SAMPLES},
        "summary": {
            "containers": len(recos),
            "by_status": {s: sum(1 for r in recos if r["status"] == s) for s in order},
            "reserved_cpu_m": round(reserved_cpu), "reserved_mem_mib": round(reserved_mem),
            "used_cpu_p95_m": round(used_cpu), "used_mem_max_mib": round(used_mem),
            "freeable_cpu_m": round(freed_cpu), "freeable_mem_mib": round(freed_mem),
        },
        "method": {
            "cpu": f"request = p95 × {CPU_MARGIN}",
            "memory": f"request = limit = max × {MEM_MARGIN}, ou projection à "
                      f"{PROJECTION_HORIZON_S // 60} min si croissance détectée",
            "reference": "KRR (Robusta) pour la règle de base ; projection OOM : AI-Kube",
        },
        "recommendations": recos,
    }


def _recommend(w: dict, c: dict, g: Optional[dict], system: bool) -> dict:
    req, lim = c["requests"], c["limits"]
    cur = {
        "cpu_request_m": parse_cpu(req.get("cpu")) if "cpu" in req else None,
        "cpu_limit_m": parse_cpu(lim.get("cpu")) if "cpu" in lim else None,
        "mem_request_mib": parse_mem(req.get("memory")) / 1024**2 if "memory" in req else None,
        "mem_limit_mib": parse_mem(lim.get("memory")) / 1024**2 if "memory" in lim else None,
    }
    base = {
        "kind": w["kind"], "namespace": w["namespace"], "workload": w["name"],
        "container": c["name"], "image": c["image"], "replicas": w["replicas"],
        "system": system, "current": {k: (round(v) if v is not None else None)
                                      for k, v in cur.items()},
        "usage": None, "recommended": None, "reasons": [],
        "gain_cpu_m": 0, "gain_mem_mib": 0, "oom": None,
    }
    n = len(g["cpu"]) if g else 0
    if n < MIN_SAMPLES:
        base["status"] = "observation"
        base["reasons"].append(f"{n} échantillon(s) sur {MIN_SAMPLES} requis "
                               f"(≈ {MIN_SAMPLES * SAMPLE_INTERVAL // 60} min d'observation)")
        if cur["cpu_request_m"] is None or cur["mem_limit_mib"] is None:
            base["reasons"].append("requests/limits absents : à définir dès que l'usage est connu")
        return base

    cpu_p95 = _percentile(g["cpu"], 0.95)
    cpu_max = max(g["cpu"])
    mem_max = max(g["mem"])
    mem_p95 = _percentile(g["mem"], 0.95)
    window_s = time.time() - (g["first"] or time.time())
    base["usage"] = {"samples": n, "pods": len(g["pods"]), "window_min": round(window_s / 60),
                     "cpu_p95_m": round(cpu_p95), "cpu_max_m": round(cpu_max),
                     "mem_p95_mib": round(mem_p95), "mem_max_mib": round(mem_max),
                     "restarts": g["restarts"], "oomkilled": g["oomkilled"]}

    rec_cpu = _round_cpu(cpu_p95 * CPU_MARGIN)
    rec_mem = _round_mem(mem_max * MEM_MARGIN)
    reasons = [f"CPU : p95 {round(cpu_p95)}m sur {n} échantillons → request {rec_cpu}m",
               f"Mémoire : max {round(mem_max)}Mi → request = limit {rec_mem}Mi (marge 25 %)"]

    status = "adapte"
    oom = g.get("oom")
    if oom:
        projected = oom["current_mib"] + oom["slope_mib_per_min"] * PROJECTION_HORIZON_S / 60
        rec_mem = max(rec_mem, _round_mem(projected * 1.1))
        status = "oom_imminent"
        base["oom"] = {"eta_seconds": round(oom["eta_seconds"]),
                       "confidence": oom["confidence"],
                       "slope_mib_per_min": oom["slope_mib_per_min"],
                       "pod": oom.get("pod")}
        reasons.insert(0, f"Croissance mémoire de {oom['slope_mib_per_min']} Mi/min : limite "
                          f"atteinte dans ~{round(oom['eta_seconds'] / 60)} min "
                          f"(confiance {oom['confidence']}).")
        reasons.append(f"Limite recommandée {rec_mem}Mi = mémoire projetée à "
                       f"{PROJECTION_HORIZON_S // 60} min + 10 %. PALLIATIF : une croissance "
                       f"continue évoque une fuite mémoire, à corriger dans l'application.")
    elif g["oomkilled"]:
        rec_mem = max(rec_mem, _round_mem((cur["mem_limit_mib"] or mem_max) * 1.5))
        status = "sous_dimensionne"
        reasons.insert(0, "Dernier arrêt du conteneur : OOMKilled. La limite actuelle est trop basse.")

    if status == "adapte":
        if cur["cpu_request_m"] is None or cur["mem_request_mib"] is None \
                or cur["mem_limit_mib"] is None:
            status = "manquant"
            reasons.insert(0, "requests/limits incomplets : l'ordonnanceur place ce pod à l'aveugle "
                              "et il est le premier évincé en cas de pression mémoire.")
        elif (cur["mem_limit_mib"] and mem_max > 0.9 * cur["mem_limit_mib"]) \
                or (cur["cpu_limit_m"] and cpu_p95 > 0.9 * cur["cpu_limit_m"]):
            status = "sous_dimensionne"
            reasons.insert(0, "Usage au-delà de 90 % de la limite : risque d'OOMKill (mémoire) "
                              "ou de bridage (CPU).")
        elif cur["mem_request_mib"] and mem_max > cur["mem_request_mib"]:
            status = "sous_dimensionne"
            reasons.insert(0, "Usage mémoire au-dessus de la request : réservation insuffisante, "
                              "risque d'éviction.")
        else:
            # Réduction progressive : jamais plus de MAX_REDUCTION par étape
            cap_cpu = _round_cpu((cur["cpu_request_m"] or 0) * (1 - MAX_REDUCTION))
            cap_mem = _round_mem((cur["mem_request_mib"] or 0) * (1 - MAX_REDUCTION))
            gain_cpu = (cur["cpu_request_m"] or 0) - max(rec_cpu, cap_cpu)
            gain_mem = (cur["mem_request_mib"] or 0) - max(rec_mem, cap_mem)
            over_cpu = cur["cpu_request_m"] and cur["cpu_request_m"] >= OVER_FACTOR * rec_cpu \
                and gain_cpu >= MIN_GAIN_CPU_M
            over_mem = cur["mem_request_mib"] and cur["mem_request_mib"] >= OVER_FACTOR * rec_mem \
                and gain_mem >= MIN_GAIN_MEM_MIB
            if over_cpu or over_mem:
                status = "sur_dimensionne"
                if over_cpu and rec_cpu < cap_cpu:
                    reasons.append(f"CPU : cible calculée {rec_cpu}m, réduction plafonnée à 50 % par étape → {cap_cpu}m")
                    rec_cpu = cap_cpu
                if over_mem and rec_mem < cap_mem:
                    reasons.append(f"Mémoire : cible calculée {rec_mem}Mi, réduction plafonnée à 50 % par étape → {cap_mem}Mi")
                    rec_mem = cap_mem
                base["gain_cpu_m"] = round(max(0, gain_cpu)) * w["replicas"] if over_cpu else 0
                base["gain_mem_mib"] = round(max(0, gain_mem)) * w["replicas"] if over_mem else 0
                reasons.insert(0, f"Réserve {fmt_cpu(cur['cpu_request_m'])} / "
                                  f"{fmt_mem(cur['mem_request_mib'])} pour un usage de "
                                  f"{round(cpu_p95)}m / {round(mem_max)}Mi : place bloquée pour "
                                  f"les autres pods.")
            else:
                reasons.insert(0, "Dimensionnement cohérent avec l'usage observé.")

    # Une alerte (OOM, sous-dimensionnement) ne doit jamais RÉDUIRE une ressource
    if status in ("oom_imminent", "sous_dimensionne"):
        if cur["cpu_request_m"] and rec_cpu < cur["cpu_request_m"]:
            reasons.append(f"CPU conservé à {round(cur['cpu_request_m'])}m : une alerte ne déclenche "
                           f"jamais de réduction (la réduction se fera une fois le risque traité).")
            rec_cpu = round(cur["cpu_request_m"])
        rec_mem = max(rec_mem, round(cur["mem_limit_mib"] or 0), round(cur["mem_request_mib"] or 0))

    # Si on ne change que d'un côté, on conserve les valeurs actuelles de l'autre
    if status == "sur_dimensionne":
        if not base["gain_cpu_m"] and cur["cpu_request_m"]:
            rec_cpu = round(cur["cpu_request_m"])
        if not base["gain_mem_mib"] and cur["mem_request_mib"]:
            rec_mem = round(max(cur["mem_request_mib"], cur["mem_limit_mib"] or 0))
    rec_cpu_limit = cur["cpu_limit_m"]
    if rec_cpu_limit is not None and rec_cpu_limit < rec_cpu:
        rec_cpu_limit = rec_cpu * 2
    base["recommended"] = {"cpu_request_m": rec_cpu, "cpu_limit_m":
                           round(rec_cpu_limit) if rec_cpu_limit else None,
                           "mem_request_mib": rec_mem, "mem_limit_mib": rec_mem}
    base["status"] = status
    base["reasons"] = reasons
    base["applicable"] = status in ("oom_imminent", "sous_dimensionne", "manquant",
                                    "sur_dimensionne") and not system
    if status == "sur_dimensionne" and base["usage"]["window_min"] < MIN_WINDOW_REDUCE_MIN:
        base["applicable"] = False
        reasons.append(f"Observation de {base['usage']['window_min']} min seulement : la réduction "
                       f"sera applicable après {MIN_WINDOW_REDUCE_MIN} min, pour ne pas dimensionner "
                       f"sur un creux d'activité.")
    return base


# ── Application ─────────────────────────────────────────────────────────────

def _set_resources_args(kind: str, ns: str, name: str, container: str,
                        rec: dict) -> list[str]:
    reqs = [f"cpu={rec['cpu_request_m']}m", f"memory={rec['mem_request_mib']}Mi"]
    lims = [f"memory={rec['mem_limit_mib']}Mi"]
    if rec.get("cpu_limit_m"):
        lims.append(f"cpu={rec['cpu_limit_m']}m")
    return ["kubectl", "set", "resources", f"{kind.lower()}/{name}", "-n", ns,
            "-c", container, f"--requests={','.join(reqs)}", f"--limits={','.join(lims)}"]


def apply(kind: str, namespace: str, name: str, container: str, rec: dict,
          dry_run: bool = True, before: Optional[dict] = None,
          usage: Optional[dict] = None, status: Optional[str] = None) -> dict:
    if kind not in WORKLOAD_KINDS:
        return {"ok": False, "error": f"Type non pris en charge : {kind}"}
    if namespace in SYSTEM_NAMESPACES:
        return {"ok": False, "error": "Namespace système : modification refusée par sécurité"}
    args = _set_resources_args(kind, namespace, name, container, rec)
    check = subprocess.run(args + ["--dry-run=server"], capture_output=True, text=True, timeout=30)
    if check.returncode != 0:
        return {"ok": False, "stage": "dry-run", "error": check.stderr.strip()[:800],
                "command": " ".join(args)}
    if dry_run:
        return {"ok": True, "stage": "dry-run", "output": check.stdout.strip(),
                "command": " ".join(args)}
    r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    ok = r.returncode == 0
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(_ACTIONS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                "kind": kind, "namespace": namespace, "name": name,
                                "container": container, "status": status,
                                "before": before, "after": rec, "usage": usage,
                                "ok": ok, "error": None if ok else r.stderr.strip()[:300]},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass
    if not ok:
        return {"ok": False, "stage": "apply", "error": r.stderr.strip()[:800],
                "command": " ".join(args)}
    return {"ok": True, "stage": "apply", "output": r.stdout.strip(), "command": " ".join(args),
            "note": "Les pods vont redémarrer progressivement (rolling update)."}

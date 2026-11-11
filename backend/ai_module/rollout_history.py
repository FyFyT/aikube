"""
Historique des versions des workloads et retour arrière — partie DevOps.

Kubernetes garde lui-même les versions précédentes d'un workload :
  Deployment              un ReplicaSet par version (annotation deployment.kubernetes.io/revision)
  StatefulSet, DaemonSet  une ControllerRevision par version (champ revision)
On les relit pour afficher : quelle version tourne, ce qui a changé entre deux
versions (différence du gabarit de pod), et revenir à une version précise.

Retour arrière : dry-run serveur, puis `kubectl rollout undo --to-revision=N`,
puis la même surveillance que le déploiement sûr (preuve de casse, lent ≠ cassé).
Journalisé dans data/observations/deployments_log.jsonl (type « rollback »).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Optional

import yaml

from ai_module import remediation as R
from ai_module import safe_deploy as D

KINDS = ("Deployment", "StatefulSet", "DaemonSet")


def _images(tpl: Optional[dict]) -> list[str]:
    spec = (tpl or {}).get("spec") or {}
    return [c.get("image", "?") for c in spec.get("containers") or []]


def _owned(items: list[dict], kind: str, name: str) -> list[dict]:
    return [i for i in items if any(o.get("kind") == kind and o.get("name") == name
                                    for o in i["metadata"].get("ownerReferences") or [])]


def list_workloads(namespace: Optional[str] = None) -> dict:
    out, errors = [], []
    for kind in KINDS:
        args = ["get", kind.lower() + "s", "-o", "json"] + (["-n", namespace] if namespace else ["-A"])
        ok, raw = R._k(args, timeout=40)
        if not ok:
            errors.append(f"{kind} : {raw[:150]}")
            continue
        for w in json.loads(raw).get("items", []):
            m, st, sp = w["metadata"], w.get("status", {}), w.get("spec", {})
            ns = m.get("namespace")
            if not namespace and ns in ("kube-system", "kube-public", "kube-node-lease"):
                continue
            if kind == "Deployment":
                rev = (m.get("annotations") or {}).get("deployment.kubernetes.io/revision")
                desired, ready = sp.get("replicas", 1), st.get("readyReplicas", 0)
            elif kind == "StatefulSet":
                rev = st.get("currentRevision")
                desired, ready = sp.get("replicas", 1), st.get("readyReplicas", 0)
            else:
                rev = None
                desired, ready = st.get("desiredNumberScheduled", 0), st.get("numberReady", 0)
            out.append({"kind": kind, "namespace": ns, "name": m["name"], "revision": rev,
                        "images": _images(sp.get("template")), "desired": desired, "ready": ready,
                        "healthy": ready >= desired, "created": m.get("creationTimestamp")})
    out.sort(key=lambda w: (w["namespace"], w["kind"], w["name"]))
    return {"workloads": out, "errors": errors}


def revisions(kind: str, ns: str, name: str) -> dict:
    """Versions connues d'un workload, de la plus récente à la plus ancienne."""
    if kind not in KINDS:
        return {"error": f"type non pris en charge : {kind}"}
    w = R._get_obj(kind, ns, name)
    if not w:
        return {"error": f"{kind}/{name} introuvable dans {ns}"}
    revs = []
    if kind == "Deployment":
        ok, raw = R._k(["get", "replicasets", "-n", ns, "-o", "json"], timeout=40)
        if not ok:
            return {"error": raw[:300]}
        current = (w["metadata"].get("annotations") or {}).get("deployment.kubernetes.io/revision")
        for rs in _owned(json.loads(raw).get("items", []), "Deployment", name):
            ann = rs["metadata"].get("annotations") or {}
            tpl = D._strip_template(rs["spec"].get("template"))
            revs.append({"revision": int(ann.get("deployment.kubernetes.io/revision", 0)),
                         "created": rs["metadata"].get("creationTimestamp"),
                         "cause": ann.get("kubernetes.io/change-cause", ""),
                         "replicas": rs.get("status", {}).get("replicas", 0),
                         "images": _images(tpl), "template": tpl})
        current = int(current) if current else None
    else:
        ok, raw = R._k(["get", "controllerrevisions", "-n", ns, "-o", "json"], timeout=40)
        if not ok:
            return {"error": raw[:300]}
        cur_name = (w.get("status") or {}).get("currentRevision") or (w.get("status") or {}).get("updateRevision")
        current = None
        for cr in _owned(json.loads(raw).get("items", []), kind, name):
            tpl = D._strip_template(((cr.get("data") or {}).get("spec") or {}).get("template"))
            if cr["metadata"]["name"] == cur_name:
                current = cr.get("revision")
            revs.append({"revision": cr.get("revision", 0),
                         "created": cr["metadata"].get("creationTimestamp"),
                         "cause": (cr["metadata"].get("annotations") or {}).get("kubernetes.io/change-cause", ""),
                         "replicas": None, "images": _images(tpl), "template": tpl})
        if current is None and revs:
            current = max(r["revision"] for r in revs)
    revs.sort(key=lambda r: -r["revision"])
    for r in revs:
        r["current"] = r["revision"] == current
    return {"kind": kind, "namespace": ns, "name": name, "current": current, "revisions": revs}


def diff(kind: str, ns: str, name: str, rev_a: int, rev_b: int) -> dict:
    h = revisions(kind, ns, name)
    if "error" in h:
        return h
    by = {r["revision"]: r for r in h["revisions"]}
    if rev_a not in by or rev_b not in by:
        return {"error": "révision inconnue (Kubernetes ne garde que les dernières : revisionHistoryLimit)"}
    a = yaml.safe_dump(by[rev_a]["template"], sort_keys=True, allow_unicode=True).splitlines()
    b = yaml.safe_dump(by[rev_b]["template"], sort_keys=True, allow_unicode=True).splitlines()
    import difflib
    d = "\n".join(difflib.unified_diff(a, b, f"révision {rev_a}", f"révision {rev_b}", n=3, lineterm=""))
    return {"diff": d or "(gabarits identiques)", "from": rev_a, "to": rev_b}


# ── Retour arrière vers une révision ────────────────────────────────────────

def rollback_preview(kind: str, ns: str, name: str, revision: int) -> dict:
    h = revisions(kind, ns, name)
    if "error" in h:
        return {"ok": False, "error": h["error"]}
    if revision == h["current"]:
        return {"ok": False, "error": f"la révision {revision} est déjà celle qui tourne"}
    if revision not in {r["revision"] for r in h["revisions"]}:
        return {"ok": False, "error": f"révision {revision} introuvable"}
    ok, out = R._k(["rollout", "undo", f"{kind.lower()}/{name}", "-n", ns,
                    f"--to-revision={revision}", "--dry-run=server"])
    d = diff(kind, ns, name, h["current"], revision) if h["current"] else {"diff": ""}
    return {"ok": ok, "server_check": out[:500], "current": h["current"], "target": revision,
            "diff": d.get("diff", ""), "error": None if ok else out[:500]}


def rollback(kind: str, ns: str, name: str, revision: int) -> dict:
    job = {"id": uuid.uuid4().hex[:12], "type": "rollback", "state": "running", "created": time.time(),
           "t0": time.time(), "steps": [], "target": {"kind": kind, "namespace": ns, "name": name,
                                                       "revision": revision}}
    with D._lock:
        D._jobs[job["id"]] = job

    def run():
        D._step(job, "Retour à la révision", "running", f"révision {revision}")
        since = time.time()
        ok, out = R._k(["rollout", "undo", f"{kind.lower()}/{name}", "-n", ns, f"--to-revision={revision}"])
        if not ok:
            D._step(job, "Retour à la révision", "failed", out[:400])
            res = {"outcome": "apply_failed", "reason": out[:400]}
        else:
            D._step(job, "Retour à la révision", "ok", out[:300])
            D._step(job, "Surveillance", "running")
            res = D.watch_workloads(job, [(kind, ns, name)], since,
                                    expect_new={f"{kind}/{name} ({ns})"})
            D._step(job, "Surveillance", {"ok": "ok", "regression": "failed"}.get(res["outcome"], "warning"),
                    res.get("reason", "")[:500] or "pods prêts et stables")
        with D._lock:
            job.update(state="done", outcome=res["outcome"], reason=res.get("reason", ""),
                       duration_s=round(time.time() - job["t0"], 1))
        D._log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "type": "rollback", "id": job["id"],
                "objects": [f"{kind}/{name} ({ns})"], "to_revision": revision,
                "outcome": res["outcome"], "reason": res.get("reason", "")[:500],
                "duration_s": job["duration_s"]})

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, **D._job_view(job)}

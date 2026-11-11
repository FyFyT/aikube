"""
Déploiement sûr — partie DevOps.

Question : peut-on mettre un manifeste en production sur un cluster sans casser ce
qui tourne, et revenir automatiquement en arrière quand ça casse ?

  APERÇU       lecture du YAML → namespace par défaut complété → contrôle de capacité
               réelle et dry-run serveur (mêmes arbitres que le générateur) → pour
               chaque objet : état actuel du cluster face au résultat du dry-run →
               plan (création / modification / inchangé), différence, pods redémarrés ?
               → contrôle de sécurité du manifeste (mêmes règles que le scanner)
  APPLICATION  (après confirmation) photo de l'état actuel → kubectl apply →
               surveillance des nouveaux pods (même critère de casse que la
               remédiation : preuve de casse, lent ≠ cassé) → Services sans endpoint
               → si régression et retour arrière automatique : objets créés
               supprimés, contrôleurs remis à leur révision précédente, autres objets
               restaurés → explication de l'échec par l'IA, à partir des événements
               et des logs réels.

Journal : data/observations/deployments_log.jsonl (même format pour les retours
arrière manuels lancés depuis l'historique).
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from typing import Any, Optional

import yaml

from ai_module import remediation as R
from ai_module import security_scanner as S
from ai_module import yaml_pipeline as Y

LOG_PATH = os.path.join(R.LOG_DIR, "deployments_log.jsonl")
CLUSTER_SCOPED = {"Namespace", "ClusterRole", "ClusterRoleBinding", "PersistentVolume",
                  "StorageClass", "CustomResourceDefinition", "PriorityClass", "IngressClass",
                  "Node", "APIService", "MutatingWebhookConfiguration",
                  "ValidatingWebhookConfiguration", "RuntimeClass", "CSIDriver"}
WATCHED = set(S.CONTROLLER_KINDS) | {"Pod"}
PREVIEW_TTL = 1800

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


# ── Utilitaires ─────────────────────────────────────────────────────────────

def _log(entry: dict) -> None:
    try:
        os.makedirs(R.LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _ref(d: dict) -> str:
    ns = d["metadata"].get("namespace")
    return f"{d['kind']}/{d['metadata']['name']}" + (f" ({ns})" if ns else "")


def set_namespace(docs: list[dict], default_ns: str) -> list[dict]:
    """Complète le namespace des objets qui en ont besoin (sans toucher aux autres)."""
    out = []
    for d in docs:
        d = json.loads(json.dumps(d))
        if d["kind"] not in CLUSTER_SCOPED:
            d.setdefault("metadata", {})
            d["metadata"]["namespace"] = d["metadata"].get("namespace") or default_ns
        out.append(d)
    return out


def _dump(docs: list[dict]) -> str:
    return "---\n".join(yaml.safe_dump(d, sort_keys=False, allow_unicode=True) for d in docs)


def _strip_template(t: Any) -> Any:
    """Gabarit de pod comparable entre révisions (sans le hachage ajouté par Kubernetes)."""
    if not isinstance(t, dict):
        return t
    t = json.loads(json.dumps(t))
    labels = (t.get("metadata") or {}).get("labels") or {}
    labels.pop("pod-template-hash", None)
    labels.pop("controller-revision-hash", None)
    return t


def classify_change(live: Optional[dict], after: Optional[dict]) -> dict:
    """Nature d'un changement : création, modification (avec ou sans redémarrage des pods), inchangé."""
    if live is None:
        return {"action": "création", "restarts_pods": after is not None and after.get("kind") in WATCHED}
    a, b = R._clean(live), R._clean(after) if after else None
    if b is None:
        return {"action": "modification", "restarts_pods": False}
    if a == b:
        return {"action": "inchangé", "restarts_pods": False}
    tpl_changed = _strip_template(R._pod_template(a)) != _strip_template(R._pod_template(b))
    return {"action": "modification",
            "restarts_pods": live.get("kind") in WATCHED and tpl_changed}


def _security(docs: list[dict]) -> list[dict]:
    """Constats de sécurité du manifeste lui-même (avant tout déploiement)."""
    out = []
    for d in docs:
        spec = S._pod_spec_of(d["kind"], d)
        if not spec:
            continue
        for f in S._check_pod_spec(spec, {}, d["metadata"].get("namespace", "")):
            out.append({"object": _ref(d), "check": f["check"],
                        "title": S.CHECKS.get(f["check"], {}).get("title", f["check"]),
                        "severity": f["severity"], "container": f.get("container"),
                        "detail": f.get("detail", "")})
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda f: order.get(f["severity"], 3))


def _job_view(job: dict) -> dict:
    hidden = {"docs", "before", "_thread"}
    return {k: v for k, v in job.items() if k not in hidden}


def _step(job: dict, name: str, status: str, detail: str = "") -> None:
    with _lock:
        for s in job["steps"]:
            if s["name"] == name:
                s.update(status=status, detail=detail, t=round(time.time() - job["t0"], 1))
                return
        job["steps"].append({"name": name, "status": status, "detail": detail,
                             "t": round(time.time() - job["t0"], 1)})


# ── Aperçu ──────────────────────────────────────────────────────────────────

def preview(yaml_text: str, default_ns: str, ctx: dict) -> dict:
    docs, err = Y.parse_docs(yaml_text)
    if err:
        return {"ok": False, "error": err}
    docs = set_namespace(docs, default_ns or "default")
    final_text = _dump(docs)
    check = Y.validate_text(final_text, ctx)          # capacité + dry-run serveur de l'ensemble

    plan = []
    created_ns = {d["metadata"]["name"] for d in docs if d["kind"] == "Namespace"}
    for d in docs:
        kind, name = d["kind"], d["metadata"]["name"]
        ns = d["metadata"].get("namespace", "")
        live = R._get_obj(kind, ns, name)
        after = None
        note = ""
        if ns in created_ns and live is None:
            note = "namespace créé par ce déploiement : objet vérifié avec l'ensemble"
        else:
            ok, out = R._k(["apply", "--dry-run=server", "-o", "json", "-f", "-"],
                           stdin=yaml.safe_dump(d, sort_keys=False, allow_unicode=True))
            if ok and out.startswith("{"):
                try:
                    after = json.loads(out)
                except json.JSONDecodeError:
                    after = None
            elif not ok:
                note = out[:300]
        change = classify_change(live, after)
        if live is None:
            diff = "\n".join("+ " + l for l in
                             yaml.safe_dump(d, sort_keys=False, allow_unicode=True).splitlines())
        elif after is not None:
            diff = R._diff(R._clean(live), R._clean(after))
        else:
            diff = ""
        rev = ((live or {}).get("metadata", {}).get("annotations") or {}).get(
            "deployment.kubernetes.io/revision")
        plan.append({"object": _ref(d), "kind": kind, "name": name, "namespace": ns,
                     **change, "diff": diff[:12000], "note": note, "current_revision": rev})

    sec = _security(docs)
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "type": "deploy", "state": "preview", "created": time.time(),
           "namespace_default": default_ns, "yaml": final_text, "docs": docs,
           "valid": check["valid"], "server_validated": check.get("server_validated"),
           "errors": check["errors"], "warnings": check["warnings"],
           "capacity": check.get("capacity"), "plan": plan, "security": sec,
           "summary": {
               "création": sum(p["action"] == "création" for p in plan),
               "modification": sum(p["action"] == "modification" for p in plan),
               "inchangé": sum(p["action"] == "inchangé" for p in plan),
               "pods_redémarrés": sum(bool(p["restarts_pods"]) for p in plan),
               "sécurité_critique": sum(f["severity"] == "critical" for f in sec),
           }}
    with _lock:
        for k in [k for k, j in _jobs.items() if time.time() - j["created"] > PREVIEW_TTL
                  and j["state"] in ("preview", "done")]:
            _jobs.pop(k, None)
        _jobs[job_id] = job
    return {"ok": True, **_job_view(job)}


# ── Application ─────────────────────────────────────────────────────────────

def apply(job_id: str, auto_rollback: bool = True) -> dict:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "aperçu introuvable ou expiré : refaire l'aperçu"}
        if job["state"] != "preview":
            return {"ok": False, "error": f"déjà {job['state']}"}
        if job["server_validated"] is False:
            return {"ok": False, "error": "le serveur Kubernetes a refusé ce manifeste : corrige-le d'abord"}
        job.update(state="running", auto_rollback=auto_rollback, steps=[], t0=time.time())
    th = threading.Thread(target=_execute, args=(job,), daemon=True)
    th.start()
    return {"ok": True, **_job_view(job)}


def status(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        return _job_view(job) if job else None


def _service_endpoints(docs: list[dict]) -> list[str]:
    """Services du déploiement qui ne trouvent aucun pod prêt (sélecteur ou sondes)."""
    empty = []
    for d in docs:
        if d["kind"] != "Service" or not (d.get("spec") or {}).get("selector"):
            continue
        ns, name = d["metadata"].get("namespace", "default"), d["metadata"]["name"]
        ok, out = R._k(["get", "endpoints", name, "-n", ns, "-o", "json"])
        if not ok:
            continue
        subsets = json.loads(out).get("subsets") or []
        if not any(s.get("addresses") for s in subsets):
            empty.append(f"Service {name} ({ns}) : aucun pod prêt derrière le sélecteur "
                         f"{(d.get('spec') or {}).get('selector')}")
    return empty


def watch_workloads(job: dict, workloads: list[tuple[str, str, str]], since: float,
                    step: str = "Surveillance", expect_new: Optional[set] = None) -> dict:
    """Verdict global : regression dès qu'un workload casse ; ok quand tous sont prêts et
    stables ; inconclusive si toujours en attente au bout de R.MAX_WAIT_S.

    expect_new : workloads dont le gabarit change. Pour eux, « pods existants sains »
    ne suffit pas : on attend que les NOUVEAUX pods existent (sur une machine chargée,
    le contrôleur peut mettre plus de STABILIZE_S secondes à les créer)."""
    expect_new = expect_new or set()
    if not workloads:
        return {"outcome": "ok", "reason": "aucun workload à surveiller", "details": {}}
    deadline = since + R.MAX_WAIT_S
    ready_since = None
    details: dict[str, dict] = {}
    while time.time() < deadline:
        for kind, ns, name in workloads:
            key = f"{kind}/{name} ({ns})"
            h = R._health(kind, ns, name, since)
            if h["verdict"] == "ready" and key in expect_new and \
                    not any(p.get("new") for p in h.get("pods", [])):
                h = {**h, "verdict": "progressing", "reason": "nouveaux pods pas encore créés"}
            details[key] = h
        failed = {k: v for k, v in details.items() if v["verdict"] == "failed"}
        if failed:
            return {"outcome": "regression", "details": details,
                    "reason": " ; ".join(f"{k} : {v['reason']}" for k, v in failed.items())[:1500]}
        if all(v["verdict"] == "ready" for v in details.values()):
            ready_since = ready_since or time.time()
            if time.time() - ready_since >= R.STABILIZE_S:
                return {"outcome": "ok", "reason": "", "details": details}
            _step(job, step, "running", f"pods prêts, vérification de stabilité ({R.STABILIZE_S} s)")
        else:
            ready_since = None
            waiting = [f"{k} : {v['reason'][:120]}" for k, v in details.items() if v["verdict"] != "ready"]
            _step(job, step, "running",
                  f"en attente ({round(time.time() - since)} s) — " + " ; ".join(waiting)[:300])
        time.sleep(R.POLL_S)
    waiting = [f"{k} : {v['reason'][:150]}" for k, v in details.items() if v["verdict"] != "ready"]
    return {"outcome": "inconclusive", "details": details,
            "reason": f"toujours en attente après {R.MAX_WAIT_S // 60} min, sans preuve de casse : "
                      + " ; ".join(waiting)[:600]}


def _rollback_all(job: dict) -> tuple[bool, list[str]]:
    """Remet le cluster dans l'état photographié avant l'application."""
    msgs, ok_all = [], True
    for d in reversed(job["docs"]):
        kind, name = d["kind"], d["metadata"]["name"]
        ns = d["metadata"].get("namespace", "")
        before = job["before"].get(_ref(d))
        if before is None:                               # créé par ce déploiement → supprimé
            args = ["delete", kind.lower(), name, "--ignore-not-found", "--wait=false"]
            if kind not in CLUSTER_SCOPED:
                args += ["-n", ns]
            ok, out = R._k(args)
            msgs.append(f"{_ref(d)} supprimé" if ok else f"{_ref(d)} : {out[:150]}")
        elif kind in S.CONTROLLER_KINDS and job["changes"].get(_ref(d), {}).get("restarts_pods"):
            ok, out = R._k(["rollout", "undo", f"{kind.lower()}/{name}", "-n", ns])
            msgs.append(f"{_ref(d)} remis à la révision précédente" if ok else f"{_ref(d)} : {out[:150]}")
        elif job["changes"].get(_ref(d), {}).get("action") == "modification":
            ok, out = R._k(["replace", "-f", "-"],
                           stdin=yaml.safe_dump(R._clean(before), sort_keys=False, allow_unicode=True))
            msgs.append(f"{_ref(d)} restauré" if ok else f"{_ref(d)} : {out[:150]}")
        else:
            continue
        ok_all = ok_all and ok
    return ok_all, msgs


EXPLAIN_SYSTEM = """Tu es l'ingénieur DevOps de la plateforme AI-Kube. Un déploiement vient d'échouer
et a été annulé. Explique en français simple, en 5 à 10 lignes :
1. ce qui a cassé, en citant les preuves fournies (événements, logs, états des pods) ;
2. la cause la plus probable ;
3. la correction à apporter au manifeste.
N'invente rien : si les preuves ne suffisent pas, dis-le et indique quoi vérifier."""


def _evidence(details: dict) -> str:
    parts = []
    for wl, h in details.items():
        if h.get("verdict") != "failed":
            continue
        parts.append(f"## {wl}\nVerdict : {h['reason']}")
        ns = wl.rsplit("(", 1)[-1].rstrip(")")
        for p in [p for p in h.get("pods", []) if p.get("new")][:2]:
            pod = p["pod"]
            ok, ev = R._k(["get", "events", "-n", ns, "--field-selector",
                           f"involvedObject.name={pod}", "--sort-by=.lastTimestamp",
                           "-o", "custom-columns=TYPE:.type,REASON:.reason,MESSAGE:.message",
                           "--no-headers"], timeout=30)
            parts.append(f"### Événements de {pod}\n{ev[-1500:] if ok else '(indisponibles)'}")
            ok, logs = R._k(["logs", pod, "-n", ns, "--all-containers", "--tail=25", "--previous"], timeout=30)
            if not ok:
                ok, logs = R._k(["logs", pod, "-n", ns, "--all-containers", "--tail=25"], timeout=30)
            parts.append(f"### Logs de {pod}\n{logs[-1500:] if ok and logs else '(aucun log)'}")
    return "\n\n".join(parts)[:8000]


def explain_failure(job: dict, details: dict) -> Optional[dict]:
    from ai_module import llm_client
    if not llm_client.available_providers():
        return None
    evidence = _evidence(details)
    if not evidence:
        return None
    msg = (f"Manifeste déployé :\n```yaml\n{job['yaml'][:5000]}\n```\n\n"
           f"Preuves relevées sur le cluster :\n{evidence}")
    try:
        out = asyncio.run(llm_client.chat(EXPLAIN_SYSTEM, [{"role": "user", "content": msg}]))
        return {"text": out["text"], "model": out["model"], "evidence": evidence}
    except Exception as e:  # noqa: BLE001
        return {"text": f"Explication indisponible : {e}", "model": None, "evidence": evidence}


def _execute(job: dict) -> None:
    docs = job["docs"]
    try:
        # 1. Photo de l'état actuel (au moment de l'application, pas de l'aperçu)
        _step(job, "Photo de l'état actuel", "running")
        before, changes = {}, {}
        for p, d in zip(job["plan"], docs):
            live = R._get_obj(d["kind"], d["metadata"].get("namespace", ""), d["metadata"]["name"])
            before[_ref(d)] = live
            changes[_ref(d)] = classify_change(live, None) if live is None else \
                {"action": p["action"], "restarts_pods": p["restarts_pods"]}
        job["before"], job["changes"] = before, changes
        _step(job, "Photo de l'état actuel", "ok",
              f"{sum(v is not None for v in before.values())} objet(s) existant(s) sauvegardé(s)")

        # 2. Application
        _step(job, "Application", "running")
        since = time.time()
        ok, out = R._k(["apply", "-f", "-"], stdin=job["yaml"], timeout=120)
        if not ok:
            _step(job, "Application", "failed", out[:500])
            result = {"outcome": "apply_failed", "reason": out[:800], "details": {}}
        else:
            _step(job, "Application", "ok", out[:500])
            # 3. Surveillance
            workloads = [(d["kind"], d["metadata"].get("namespace", ""), d["metadata"]["name"])
                         for d in docs if d["kind"] in WATCHED]
            _step(job, "Surveillance", "running")
            expect = {_ref(d) for d in docs                     # même format que watch_workloads
                      if d["kind"] in WATCHED and job["changes"][_ref(d)].get("restarts_pods")}
            result = watch_workloads(job, workloads, since, expect_new=expect)
            if result["outcome"] == "ok":
                empty = _service_endpoints(docs)
                result["service_warnings"] = empty
                _step(job, "Surveillance", "ok",
                      "pods prêts et stables" + (" — ⚠ " + " ; ".join(empty) if empty else ""))
            elif result["outcome"] == "regression":
                _step(job, "Surveillance", "failed", result["reason"][:600])
            else:
                _step(job, "Surveillance", "warning", result["reason"][:600])

        # 4. Retour arrière
        rolled = None
        if result["outcome"] in ("regression", "apply_failed") and job["auto_rollback"]:
            _step(job, "Retour arrière", "running")
            rb_ok, msgs = _rollback_all(job)
            rolled = {"ok": rb_ok, "actions": msgs}
            _step(job, "Retour arrière", "ok" if rb_ok else "failed", " ; ".join(msgs)[:600])

        # 5. Explication de l'échec
        explanation = None
        if result["outcome"] == "regression":
            _step(job, "Explication", "running", "lecture des événements et des logs")
            explanation = explain_failure(job, result["details"])
            _step(job, "Explication", "ok" if explanation else "warning",
                  "" if explanation else "aucun modèle configuré")

        with _lock:
            job.update(state="done", outcome=result["outcome"], reason=result.get("reason", ""),
                       service_warnings=result.get("service_warnings", []), rollback=rolled,
                       explanation=explanation, duration_s=round(time.time() - job["t0"], 1),
                       health={k: {"verdict": v["verdict"], "reason": v["reason"]}
                               for k, v in result.get("details", {}).items()})
    except Exception as e:  # noqa: BLE001
        with _lock:
            job.update(state="done", outcome="error", reason=str(e)[:500],
                       duration_s=round(time.time() - job["t0"], 1))
    _log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "type": "deploy", "id": job["id"],
          "objects": [p["object"] for p in job["plan"]], "summary": job["summary"],
          "security_critical": job["summary"]["sécurité_critique"],
          "auto_rollback": job.get("auto_rollback"), "outcome": job.get("outcome"),
          "reason": (job.get("reason") or "")[:500],
          "rolled_back": bool(job.get("rollback")), "rollback_ok": (job.get("rollback") or {}).get("ok"),
          "service_warnings": job.get("service_warnings"),
          "explained": bool(job.get("explanation")), "duration_s": job.get("duration_s")})


# ── Statistiques ────────────────────────────────────────────────────────────

def stats() -> dict:
    rows = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    deploys = [r for r in rows if r.get("type") == "deploy"]
    out = {"deployments": len(deploys),
           "by_outcome": {}, "rollbacks": sum(1 for r in deploys if r.get("rolled_back")),
           "rollbacks_ok": sum(1 for r in deploys if r.get("rollback_ok")),
           "manual_rollbacks": sum(1 for r in rows if r.get("type") == "rollback"),
           "recent": rows[-15:][::-1]}
    for r in deploys:
        out["by_outcome"][r.get("outcome")] = out["by_outcome"].get(r.get("outcome"), 0) + 1
    return out

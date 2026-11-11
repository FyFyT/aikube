"""
Remédiation vérifiée des défauts de configuration — contribution du chapitre 7.

Question mesurée : « la remédiation automatique des défauts de configuration
Kubernetes est-elle réalisable sans régression fonctionnelle, et à quel taux ? »

Déroulé d'une remédiation, en deux temps :

  APERÇU     correctif (déterministe ou proposé par l'IA) → validation par le
             serveur (dry-run) → différence avant/après montrée à l'utilisateur
  APPLICATION (après confirmation)
             application → attente du déploiement (rollout) → stabilisation →
             CONTRÔLE DE NON-RÉGRESSION : les nouveaux pods sont-ils prêts, sans
             redémarrage ni CrashLoop ? → sinon RETOUR ARRIÈRE automatique
             (rollout undo) → nouveau scan : le constat a-t-il disparu ?

Une remédiation est comptée RÉUSSIE seulement si le constat a disparu ET
qu'aucune régression n'a été observée. Chaque tentative est journalisée dans
data/observations/remediation_log.jsonl.

Limite assumée du contrôle de non-régression : il observe la SANTÉ des pods
(prêts, stables), pas le comportement métier. Une NetworkPolicy qui coupe un
flux applicatif sans faire échouer de sonde ne sera pas détectée.
"""
from __future__ import annotations

import calendar
import copy
import difflib
import json
import os
import subprocess
import threading
import time
import uuid
from typing import Any, Optional

import yaml

from ai_module import security_scanner as S

LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                       "data", "observations"))
LOG_PATH = os.path.join(LOG_DIR, "remediation_log.jsonl")
ROLLOUT_TIMEOUT = 120
STABILIZE_S = 20
MAX_WAIT_S = 420          # attente maximale d'un verdict (machine lente : 7 min)
READY_GRACE_S = 90        # un conteneur DÉMARRÉ mais jamais prêt après ce délai = cassé
POLL_S = 5
FAIL_TERMINATED = {"Error", "OOMKilled", "ContainerCannotRun", "StartError"}
BAD_WAITING = {"CrashLoopBackOff", "CreateContainerConfigError", "CreateContainerError",
               "ErrImagePull", "ImagePullBackOff", "InvalidImageName", "RunContainerError"}

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


# ── Utilitaires kubectl ─────────────────────────────────────────────────────

def _k(args: list[str], stdin: Optional[str] = None, timeout: int = 60) -> tuple[bool, str]:
    try:
        r = subprocess.run(["kubectl"] + args, input=stdin, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"délai dépassé ({timeout} s)"
    return r.returncode == 0, (r.stdout if r.returncode == 0 else r.stderr).strip()


def _get_obj(kind: str, ns: str, name: str) -> Optional[dict]:
    args = ["get", kind.lower(), name, "-o", "json"]
    if kind != "Namespace":
        args[3:3] = ["-n", ns]
    ok, out = _k(args)
    return json.loads(out) if ok else None


def _clean(obj: dict) -> dict:
    """Supprime l'état et les champs gérés par le serveur, pour l'affichage et l'IA."""
    o = copy.deepcopy(obj)
    o.pop("status", None)
    m = o.get("metadata", {})
    for k in ("uid", "resourceVersion", "generation", "creationTimestamp", "managedFields",
              "selfLink", "ownerReferences"):
        m.pop(k, None)
    ann = m.get("annotations") or {}
    for k in list(ann):
        if k in ("kubectl.kubernetes.io/last-applied-configuration",
                 "deployment.kubernetes.io/revision"):
            ann.pop(k)
    if not ann:
        m.pop("annotations", None)
    if o.get("kind") == "Pod":
        spec = o.get("spec", {})
        # Volume du jeton injecté automatiquement : ne pas le recopier
        spec["volumes"] = [v for v in spec.get("volumes") or []
                           if not v.get("name", "").startswith("kube-api-access-")]
        for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
            c["volumeMounts"] = [vm for vm in c.get("volumeMounts") or []
                                 if not vm.get("name", "").startswith("kube-api-access-")]
            if not c["volumeMounts"]:
                c.pop("volumeMounts")
        if not spec["volumes"]:
            spec.pop("volumes")
    return o


def _pod_template(obj: Optional[dict]) -> Any:
    if not obj:
        return None
    if obj.get("kind") == "Pod":
        return obj.get("spec")
    return (obj.get("spec") or {}).get("template")


def _diff(before: Any, after: Any) -> str:
    a = yaml.safe_dump(before, sort_keys=True, allow_unicode=True).splitlines()
    b = yaml.safe_dump(after, sort_keys=True, allow_unicode=True).splitlines()
    return "\n".join(difflib.unified_diff(a, b, "avant", "après", n=2, lineterm=""))


def _run_op(op: dict, dry: bool) -> tuple[bool, str, Optional[dict]]:
    """Exécute une opération ; en dry-run, renvoie aussi l'objet résultant."""
    extra = ["--dry-run=server", "-o", "json"] if dry else []
    if op["op"] == "apply":
        ok, out = _k(["apply", "-f", "-"] + extra,
                     stdin=yaml.safe_dump(op["manifest"], sort_keys=False, allow_unicode=True))
    elif op["op"] == "patch":
        ns = op.get("namespace") or op["target_ns"]
        ok, out = _k(["patch", op["target"], "-n", ns, f"--type={op['type']}",
                      "-p", json.dumps(op["patch"])] + extra)
    elif op["op"] == "recreate":              # pod autonome : supprimer puis recréer
        if dry:
            ok, out = _k(["apply", "-f", "-", "--dry-run=client", "-o", "json"],
                         stdin=yaml.safe_dump(op["manifest"], sort_keys=False))
        else:
            m = op["manifest"]["metadata"]
            _k(["delete", "pod", m["name"], "-n", m["namespace"], "--wait=true"], timeout=90)
            ok, out = _k(["apply", "-f", "-"], stdin=yaml.safe_dump(op["manifest"], sort_keys=False))
    elif op["op"] == "delete":
        ok, out = _k(["delete"] + op["args"] + (["--dry-run=server"] if dry else []))
    else:
        return False, f"opération inconnue {op['op']}", None
    obj = None
    if ok and dry and out.startswith("{"):
        try:
            obj = json.loads(out)
        except json.JSONDecodeError:
            obj = None
    return ok, out[:1500], obj


# ── Contrôle de non-régression ──────────────────────────────────────────────

def _iso(ts: str) -> float:
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0.0


def _health(kind: str, ns: str, name: str, since: float) -> dict:
    """
    État des pods créés après le correctif, en trois verdicts :

      failed       preuve de casse : CrashLoopBackOff, redémarrage, arrêt en erreur,
                   image introuvable, ou conteneur DÉMARRÉ mais jamais prêt
                   après READY_GRACE_S secondes ;
      progressing  pas encore démarré (Pending, ContainerCreating) : c'est LENT,
                   ce n'est pas CASSÉ — sur une machine chargée, cela peut durer ;
      ready        tous les nouveaux pods sont prêts.

    Première version (19/10) : un pod encore « Pending » au bout du délai était
    compté comme régression. Deux fausses alertes l'ont montré : un délai fixe
    mesure la vitesse de la machine, pas la santé de l'application.
    """
    if kind == "Pod":
        obj = _get_obj("Pod", ns, name)
        pods = [obj] if obj else []
    else:
        w = _get_obj(kind, ns, name)
        sel = ((w or {}).get("spec", {}).get("selector") or {}).get("matchLabels") or {}
        if not sel:
            return {"verdict": "progressing", "reason": "sélecteur introuvable", "pods": []}
        ok, out = _k(["get", "pods", "-n", ns, "-l",
                      ",".join(f"{k}={v}" for k, v in sel.items()), "-o", "json"])
        if not ok:
            return {"verdict": "progressing", "reason": f"API injoignable : {out[:120]}", "pods": []}
        pods = json.loads(out).get("items", [])
    now = time.time()
    report, failures, waiting_msgs = [], [], []
    for p in pods:
        if p["metadata"].get("deletionTimestamp"):
            continue
        new = _iso(p["metadata"].get("creationTimestamp", "")) >= since - 5
        st = p.get("status", {})
        css = st.get("containerStatuses") or []
        ready = bool(css) and all(c.get("ready") for c in css)
        restarts = sum(c.get("restartCount", 0) for c in css)
        waiting = [((c.get("state") or {}).get("waiting") or {}).get("reason") for c in css]
        waiting = [w for w in waiting if w]
        last = [((c.get("lastState") or {}).get("terminated") or {}).get("reason") for c in css]
        last = [x for x in last if x]
        started = [_iso(((c.get("state") or {}).get("running") or {}).get("startedAt"))
                   for c in css if (c.get("state") or {}).get("running")]
        unsched = [c.get("message", "") for c in st.get("conditions", [])
                   if c.get("type") == "PodScheduled" and c.get("status") == "False"]
        entry = {"pod": p["metadata"]["name"], "new": new, "ready": ready, "restarts": restarts,
                 "waiting": waiting, "last_terminated": last, "phase": st.get("phase")}
        report.append(entry)
        if not new:
            continue
        pod = entry["pod"]
        if any(w in BAD_WAITING for w in waiting):
            failures.append(f"{pod} : {', '.join(waiting)}")
        elif restarts > 0 or any(x in FAIL_TERMINATED for x in last):
            failures.append(f"{pod} : {restarts} redémarrage(s) ({', '.join(last) or 'erreur'})")
        elif st.get("phase") == "Failed":
            failures.append(f"{pod} : arrêté en échec")
        elif not ready and started and now - min(started) > READY_GRACE_S:
            failures.append(f"{pod} : démarré depuis {round(now - min(started))} s mais jamais prêt "
                            f"(sonde de disponibilité en échec)")
        elif not ready:
            waiting_msgs.append(f"{pod} : {'non planifié — ' + unsched[0][:80] if unsched else (', '.join(waiting) or st.get('phase') or 'en attente')}")
    if failures:
        return {"verdict": "failed", "reason": "; ".join(failures), "pods": report}
    new_pods = [r for r in report if r["new"]]
    if new_pods and all(r["ready"] for r in new_pods):
        return {"verdict": "ready", "reason": "", "pods": report}
    if not new_pods and report and all(r["ready"] for r in report) and kind != "Pod":
        return {"verdict": "ready", "reason": "gabarit inchangé, pods existants sains", "pods": report}
    return {"verdict": "progressing", "reason": "; ".join(waiting_msgs) or "nouveaux pods pas encore créés",
            "pods": report}


def _watch(job: dict, kind: str, ns: str, name: str, since: float) -> dict:
    """Surveille jusqu'à un verdict : régression (preuve de casse), succès (prêts et stables
    STABILIZE_S secondes), ou non concluant (toujours en attente après MAX_WAIT_S)."""
    deadline = since + MAX_WAIT_S
    ready_since = None
    last = {"verdict": "progressing", "reason": "", "pods": []}
    while time.time() < deadline:
        h = _health(kind, ns, name, since)
        last = h
        if h["verdict"] == "failed":
            return {**h, "outcome": "regression"}
        if h["verdict"] == "ready":
            ready_since = ready_since or time.time()
            if time.time() - ready_since >= STABILIZE_S:
                return {**h, "outcome": "ok"}
            _step(job, "Contrôle de non-régression", "running",
                  f"pods prêts, vérification de stabilité ({STABILIZE_S} s)")
        else:
            ready_since = None
            _step(job, "Contrôle de non-régression", "running",
                  f"en attente ({round(time.time() - since)} s) — {h['reason'][:160]}")
        time.sleep(POLL_S)
    return {**last, "outcome": "inconclusive",
            "reason": f"toujours en attente après {MAX_WAIT_S // 60} min, sans signe de casse : "
                      f"{last['reason'][:200]}"}


def _rollback(job: dict) -> tuple[bool, str]:
    kind, ns, name = job["kind"], job["namespace"], job["name"]
    if kind in S.CONTROLLER_KINDS:
        ok, out = _k(["rollout", "undo", f"{kind.lower()}/{name}", "-n", ns])
        if ok:
            _k(["rollout", "status", f"{kind.lower()}/{name}", "-n", ns,
                f"--timeout={ROLLOUT_TIMEOUT}s"], timeout=ROLLOUT_TIMEOUT + 10)
        return ok, out
    if kind == "Pod" and job.get("original"):
        _k(["delete", "pod", name, "-n", ns, "--wait=true"], timeout=90)
        return _k(["apply", "-f", "-"], stdin=yaml.safe_dump(job["original"], sort_keys=False))
    msgs = []
    for u in job.get("undo") or []:
        msgs.append(_run_op(u, False)[1])
    return True, " / ".join(msgs) or "rien à annuler"


# ── Correctif par IA ────────────────────────────────────────────────────────

AI_SYSTEM = """Tu es l'ingénieur sécurité Kubernetes de la plateforme AI-Kube.
On te donne un workload RÉEL et la liste de ses défauts de sécurité. Produis le manifeste
corrigé, COMPLET et prêt à appliquer, qui supprime ces défauts SANS CASSER l'application.

Règles :
1. Conserve le nom, le namespace, les labels, le sélecteur, les ports exposés, la commande et les
   variables d'environnement (sauf correction explicite). Le sélecteur d'un Deployment est immuable.
2. Si l'image officielle exige root (nginx, httpd…), utilise sa variante non privilégiée
   (nginxinc/nginx-unprivileged écoute sur 8080 : adapte containerPort et sondes, et signale-le).
3. readOnlyRootFilesystem : ajoute des volumes emptyDir pour les répertoires écrits
   (/tmp, /var/cache/nginx, /var/run…).
4. hostPath : remplace par emptyDir (données temporaires) ou explique dans warnings pourquoi
   un PVC est nécessaire.
5. Les valeurs « __AIKUBE_MASQUE_n__ » sont des secrets masqués : recopie-les à l'identique.
6. Dans choices, explique chaque modification et son risque pour le fonctionnement."""


def _mask_secrets(obj: dict) -> tuple[dict, dict]:
    o = copy.deepcopy(obj)
    masks = {}
    spec = S._pod_spec_of(o.get("kind"), o) or {}
    for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
        for e in c.get("env") or []:
            if "value" in e and S.is_secret_env(e.get("name", ""), e.get("value")):
                token = f"__AIKUBE_MASQUE_{len(masks)}__"
                masks[token] = e["value"]
                e["value"] = token
    return o, masks


def _unmask(text: str, masks: dict) -> str:
    """Remet les vraies valeurs après analyse YAML (pas de remplacement textuel fragile)."""
    if not masks:
        return text
    try:
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError:
        return text

    def walk(x):
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, str) and x in masks:
            return masks[x]
        return x
    return yaml.safe_dump_all([walk(d) for d in docs], sort_keys=False, allow_unicode=True)


async def _ai_proposal(finding: dict, all_findings: list[dict]) -> dict:
    from ai_module import host_context, llm_client, yaml_pipeline
    obj = _get_obj(finding["kind"], finding["namespace"], finding["name"])
    if not obj:
        return {"ok": False, "error": "workload introuvable"}
    clean = _clean(obj)
    masked, masks = _mask_secrets(clean)
    same = [f for f in all_findings if (f["kind"], f["namespace"], f["name"]) ==
            (finding["kind"], finding["namespace"], finding["name"]) and f["severity"] != "info"]
    issues = "\n".join(f"- [{f['severity']}] {f['title']} ({f.get('container') or 'pod'}) : "
                       f"{f.get('detail', '')}" for f in same)
    user = (f"## Workload actuel\n```yaml\n{yaml.safe_dump(masked, sort_keys=False, allow_unicode=True)}```\n\n"
            f"## Défauts à corriger\n{issues}\n\nCorrige en priorité : {finding['title']}.")
    ctx = await host_context.collect()
    attempts = []
    for _ in range(2):
        out = await llm_client.generate_manifest(AI_SYSTEM, user, "claude"
                                                 if "claude" in llm_client.available_providers() else None)
        text = _unmask(out.get("yaml", ""), masks)
        check = yaml_pipeline.validate_text(text, ctx)
        attempts.append({"valid": check["valid"], "errors": check["errors"]})
        if check["valid"]:
            break
        user = user + yaml_pipeline._feedback(out.get("yaml", ""), check)
    if not check.get("valid"):
        return {"ok": False, "error": "le correctif proposé par l'IA est refusé par le serveur",
                "attempts": attempts, "yaml": out.get("yaml", "")}
    docs = [d for d in yaml.safe_load_all(text) if d]
    main = next((d for d in docs if d.get("kind") == finding["kind"]
                 and d["metadata"]["name"] == finding["name"]), None)
    ops = []
    for d in docs:
        if finding["kind"] == "Pod" and d is main:
            ops.append({"op": "recreate", "manifest": d})
        else:
            ops.append({"op": "apply", "manifest": d})
    return {"ok": True, "ops": ops, "rollout": finding["kind"] in S.CONTROLLER_KINDS,
            "summary": "Correctif proposé par l'IA, validé par le serveur",
            "choices": out.get("reasoning", []), "warnings": out.get("warnings", []),
            "model": out.get("model"), "after": main, "original": clean, "attempts": attempts,
            "yaml": _mask_secrets_text(text, masks)}


def _mask_secrets_text(text: str, masks: dict) -> str:
    for token, val in masks.items():
        text = text.replace(val, "••••••")
    return text


# ── Tâches ──────────────────────────────────────────────────────────────────

def _step(job: dict, name: str, status: str, detail: str = "") -> None:
    with _lock:
        for s in job["steps"]:
            if s["name"] == name:
                s.update(status=status, detail=detail[:800], t=round(time.time() - job["t0"], 1))
                return
        job["steps"].append({"name": name, "status": status, "detail": detail[:800],
                             "t": round(time.time() - job["t0"], 1)})


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        j = _jobs.get(job_id)
        return None if j is None else {k: v for k, v in j.items()
                                       if k not in ("ops", "original", "undo", "_secret")}


async def preview(finding_id: str, method: str = "auto") -> dict:
    finding, all_findings = S.find_finding(finding_id)
    if not finding:
        return {"ok": False, "error": "constat introuvable (déjà corrigé ?) — relance le scan"}
    job = {"id": uuid.uuid4().hex[:10], "t0": time.time(), "state": "preview",
           "finding": {k: finding.get(k) for k in ("id", "check", "title", "severity", "kind",
                                                    "namespace", "name", "container", "detail",
                                                    "technique")},
           "kind": finding["kind"], "namespace": finding["namespace"], "name": finding["name"],
           "method": "ia" if method == "ai" else "deterministe", "steps": []}
    _step(job, "Préparation du correctif", "running")
    before_obj = _get_obj(finding["kind"], finding["namespace"], finding["name"]) \
        if finding["kind"] != "Namespace" else None

    if method == "ai":
        prop = await _ai_proposal(finding, all_findings)
        if not prop["ok"]:
            _step(job, "Préparation du correctif", "failed", prop["error"])
            job.update(state="failed", error=prop["error"], ai_attempts=prop.get("attempts"))
            with _lock:
                _jobs[job["id"]] = job
            return get_job(job["id"])
        job.update(ops=prop["ops"], rollout=prop["rollout"], summary=prop["summary"],
                   choices=prop["choices"], warnings=prop["warnings"], model=prop["model"],
                   original=prop["original"] if finding["kind"] == "Pod" else None,
                   proposal_yaml=prop["yaml"])
        after_tpl = _pod_template(prop["after"])
    else:
        fx = S.build_fix(finding, before_obj)
        if not fx["ok"]:
            _step(job, "Préparation du correctif", "failed", fx["error"])
            job.update(state="failed", error=fx["error"])
            with _lock:
                _jobs[job["id"]] = job
            return get_job(job["id"])
        for op in fx["ops"]:
            op.setdefault("target_ns", finding["namespace"])
        job.update(ops=fx["ops"], rollout=fx.get("rollout", False), summary=fx["summary"],
                   undo=fx.get("undo"))
        after_tpl = None
    _step(job, "Préparation du correctif", "done", job["summary"])

    _step(job, "Validation par le serveur (dry-run)", "running")
    msgs = []
    for op in job["ops"]:
        ok, out, obj = _run_op(op, dry=True)
        if not ok:
            _step(job, "Validation par le serveur (dry-run)", "failed", out)
            job.update(state="failed", error=f"refusé par le serveur : {out}")
            with _lock:
                _jobs[job["id"]] = job
            return get_job(job["id"])
        if obj and obj.get("kind") == finding["kind"] and after_tpl is None:
            after_tpl = _pod_template(obj)
        msgs.append(f"{op['op']} accepté")
    _step(job, "Validation par le serveur (dry-run)", "done", " · ".join(msgs))
    if before_obj is not None and after_tpl is not None:
        job["diff"] = _diff(_pod_template(_clean(before_obj)), after_tpl)
    elif finding["kind"] == "Namespace":
        job["diff"] = yaml.safe_dump(job["ops"][0]["manifest"], sort_keys=False)
    job["state"] = "ready"
    with _lock:
        _jobs[job["id"]] = job
    return get_job(job["id"])


def apply(job_id: str) -> dict:
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        return {"ok": False, "error": "tâche inconnue"}
    if job["state"] != "ready":
        return {"ok": False, "error": f"état {job['state']} : rien à appliquer"}
    job["state"] = "running"
    threading.Thread(target=_execute, args=(job,), daemon=True).start()
    return {"ok": True, "id": job_id}


def _execute(job: dict) -> None:
    kind, ns, name = job["kind"], job["namespace"], job["name"]
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "job": job["id"],
              "check": job["finding"]["check"], "severity": job["finding"]["severity"],
              "kind": kind, "namespace": ns, "name": name, "method": job["method"],
              "applied": False, "rollout_ok": None, "regression": None, "rolled_back": False,
              "fixed": False}
    t_apply = time.time()
    try:
        _step(job, "Application", "running")
        for op in job["ops"]:
            ok, out, _ = _run_op(op, dry=False)
            if not ok:
                raise RuntimeError(f"application refusée : {out}")
        record["applied"] = True
        _step(job, "Application", "done", job["summary"])

        if kind != "Namespace":
            _step(job, "Contrôle de non-régression", "running", "surveillance des nouveaux pods")
            h = _watch(job, kind, ns, name, t_apply)
            job["health"] = h
            record["outcome"] = h["outcome"]
            record["rollout_ok"] = h["outcome"] == "ok"
            if h["outcome"] == "ok":
                record["regression"] = False
                _step(job, "Contrôle de non-régression", "done", "pods prêts et stables, aucun redémarrage")
            else:
                inconclusive = h["outcome"] == "inconclusive"
                record["regression"] = None if inconclusive else True
                record["inconclusive"] = inconclusive
                record["regression_reason"] = h["reason"]
                _step(job, "Contrôle de non-régression", "failed",
                      ("NON CONCLUANT — " if inconclusive else "") + h["reason"])
                # Par prudence, un résultat non concluant est aussi annulé ; il n'est pas
                # compté comme régression dans les statistiques.
                _step(job, "Retour arrière automatique", "running")
                ok, out = _rollback(job)
                record["rolled_back"] = ok
                _step(job, "Retour arrière automatique", "done" if ok else "failed", out)
        else:
            record["regression"] = False
            record["outcome"] = "ok"

        _step(job, "Nouveau scan", "running")
        scan = S.scan(include_system=False, include_info=True)
        still = any(f["id"] == job["finding"]["id"] for f in scan["findings"])
        record["fixed"] = (not still) and record["regression"] is False
        _step(job, "Nouveau scan", "done",
              "constat disparu" if not still else "le constat est toujours présent")
        record["fixed"] = record["fixed"] and record.get("outcome") == "ok"
        job["state"] = "success" if record["fixed"] else \
            ("inconclusive" if record.get("inconclusive") else
             "rolled_back" if record["rolled_back"] else "failed")
    except Exception as e:  # noqa: BLE001
        job["state"] = "failed"
        job["error"] = str(e)[:500]
        _step(job, "Erreur", "failed", str(e)[:500])
    record["duration_s"] = round(time.time() - job["t0"], 1)
    job["record"] = record
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def stats() -> dict:
    """Taux de remédiation sans régression, par méthode et par type de défaut."""
    if not os.path.exists(LOG_PATH):
        return {"attempts": 0}
    rows = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows = [r for r in rows if r.get("applied")]
    # Requalification documentée : avant le 21/10, un pod LENT (« rollout non terminé »,
    # « Pending ») était compté comme régression. Ces lignes sont reclassées non concluantes.
    requalified = 0
    for r in rows:
        reason = r.get("regression_reason") or ""
        if r.get("regression") and "outcome" not in r and (
                reason.startswith("rollout non terminé") or "pas prêt (phase Pending)" in reason):
            r["inconclusive"], r["regression"] = True, None
            requalified += 1
    inconclusive = [r for r in rows if r.get("inconclusive")]
    rows = [r for r in rows if not r.get("inconclusive")]
    if not rows:
        return {"attempts": 0, "inconclusive": len(inconclusive), "requalified": requalified}

    def agg(rs):
        n = len(rs)
        return {"attempts": n, "fixed_no_regression": sum(r["fixed"] for r in rs),
                "regressions": sum(bool(r.get("regression")) for r in rs),
                "rolled_back": sum(r.get("rolled_back", False) for r in rs),
                "rate": round(sum(r["fixed"] for r in rs) / n, 3) if n else None}
    by_method = {m: agg([r for r in rows if r["method"] == m]) for m in {r["method"] for r in rows}}
    by_check = {c: agg([r for r in rows if r["check"] == c]) for c in {r["check"] for r in rows}}
    # Vue « un résultat par cas » : dernière tentative concluante par (workload, défaut, méthode)
    last: dict = {}
    for r in rows:
        last[(r["name"], r["check"], r["method"])] = r
    unique = list(last.values())
    return {**agg(rows), "inconclusive": len(inconclusive), "requalified": requalified,
            "unique_cases": agg(unique),
            "by_method": by_method, "by_check": by_check,
            "by_method_unique": {m: agg([r for r in unique if r["method"] == m])
                                 for m in {r["method"] for r in unique}},
            "recent": rows[-15:][::-1]}

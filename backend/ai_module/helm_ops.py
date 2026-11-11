"""
Releases Helm — partie DevOps.

Lecture : `helm list -A`, `helm history`, valeurs saisies par l'utilisateur
(valeurs sensibles masquées), différence des manifestes entre deux révisions.

Actions (toujours précédées d'un essai à blanc `--dry-run`) :
  rollback  `helm rollback NOM REV --wait`
  upgrade   `helm upgrade NOM CHART --reuse-values [--version V] [--set …] --atomic --wait`
            --atomic : Helm revient lui-même à la révision précédente si la mise à
            jour n'aboutit pas (pods non prêts dans le délai).
Journal : data/observations/deployments_log.jsonl (types helm_rollback, helm_upgrade).
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from typing import Optional

import yaml

from ai_module import safe_deploy as D
from ai_module.security_scanner import is_secret_env

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
CHART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$")
SET_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\[\]-]*=[^\n]*$")   # jamais d'option déguisée
TIMEOUT = "5m0s"


def available() -> bool:
    return shutil.which("helm") is not None


def _h(args: list[str], timeout: int = 60) -> tuple[bool, str]:
    if not available():
        return False, "helm n'est pas installé sur la machine du backend"
    try:
        r = subprocess.run(["helm"] + args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"délai dépassé ({timeout} s)"
    return r.returncode == 0, (r.stdout if r.returncode == 0 else r.stderr).strip()


def _check(name: str, ns: str) -> Optional[str]:
    if not NAME_RE.match(name or "") or not NAME_RE.match(ns or ""):
        return "nom de release ou de namespace invalide"
    return None


def releases() -> dict:
    if not available():
        return {"available": False, "releases": [],
                "hint": "Installer helm sur la machine du backend : https://helm.sh/docs/intro/install/"}
    ok, out = _h(["list", "-A", "-o", "json"])
    if not ok:
        return {"available": True, "releases": [], "error": out[:300]}
    rows = json.loads(out or "[]")
    return {"available": True, "releases": [
        {"name": r.get("name"), "namespace": r.get("namespace"), "revision": int(r.get("revision", 0)),
         "updated": r.get("updated"), "status": r.get("status"), "chart": r.get("chart"),
         "app_version": r.get("app_version")} for r in rows]}


def history(name: str, ns: str) -> dict:
    if (e := _check(name, ns)):
        return {"error": e}
    ok, out = _h(["history", name, "-n", ns, "-o", "json", "--max", "20"])
    if not ok:
        return {"error": out[:300]}
    rows = json.loads(out or "[]")
    return {"history": sorted(rows, key=lambda r: -int(r.get("revision", 0)))}


def _mask(obj):
    if isinstance(obj, dict):
        return {k: ("••••••" if isinstance(v, (str, int, float)) and is_secret_env(str(k), str(v))
                    else _mask(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask(v) for v in obj]
    return obj


def values(name: str, ns: str, revision: Optional[int] = None) -> dict:
    if (e := _check(name, ns)):
        return {"error": e}
    args = ["get", "values", name, "-n", ns, "-o", "json"] + (["--revision", str(revision)] if revision else [])
    ok, out = _h(args)
    if not ok:
        return {"error": out[:300]}
    vals = json.loads(out or "null") or {}
    return {"values": yaml.safe_dump(_mask(vals), sort_keys=False, allow_unicode=True) if vals else "(valeurs par défaut du chart)"}


def manifest_diff(name: str, ns: str, rev_a: int, rev_b: int) -> dict:
    if (e := _check(name, ns)):
        return {"error": e}
    ok_a, a = _h(["get", "manifest", name, "-n", ns, "--revision", str(rev_a)])
    ok_b, b = _h(["get", "manifest", name, "-n", ns, "--revision", str(rev_b)])
    if not (ok_a and ok_b):
        return {"error": (a if not ok_a else b)[:300]}
    d = "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), f"révision {rev_a}",
                                       f"révision {rev_b}", n=3, lineterm=""))
    return {"diff": (d or "(manifestes identiques)")[:20000]}


# ── Actions ─────────────────────────────────────────────────────────────────

def _upgrade_args(name, ns, chart, version, sets) -> tuple[Optional[list[str]], Optional[str]]:
    if (e := _check(name, ns)):
        return None, e
    if not CHART_RE.match(chart or ""):
        return None, "référence de chart invalide (ex. bitnami/nginx)"
    if version and not re.match(r"^[A-Za-z0-9.+-]+$", version):
        return None, "version invalide"
    args = ["upgrade", name, chart, "-n", ns, "--reuse-values"]
    if version:
        args += ["--version", version]
    for s in sets or []:
        if not SET_RE.match(s):
            return None, f"--set invalide : {s[:60]}"
        args.append(f"--set={s}")             # valeur liée à --set, pas d'option injectable
    return args, None


def upgrade_preview(name: str, ns: str, chart: str, version: Optional[str], sets: list[str]) -> dict:
    args, err = _upgrade_args(name, ns, chart, version, sets)
    if err:
        return {"ok": False, "error": err}
    ok, out = _h(args + ["--dry-run=server"], timeout=120)
    if not ok:
        ok, out = _h(args + ["--dry-run"], timeout=120)      # anciennes versions de helm
    if not ok:
        return {"ok": False, "error": out[:800]}
    new_manifest = out.split("MANIFEST:", 1)[-1].split("NOTES:", 1)[0]
    ok_c, cur = _h(["get", "manifest", name, "-n", ns])
    d = "\n".join(difflib.unified_diff((cur if ok_c else "").splitlines(), new_manifest.splitlines(),
                                       "actuel", "après mise à jour", n=3, lineterm=""))
    return {"ok": True, "diff": (d or "(aucun changement de manifeste)")[:20000]}


def rollback_preview(name: str, ns: str, revision: int) -> dict:
    if (e := _check(name, ns)):
        return {"ok": False, "error": e}
    ok, out = _h(["rollback", name, str(revision), "-n", ns, "--dry-run"])
    h = history(name, ns)
    cur = next((int(r["revision"]) for r in h.get("history", []) if r.get("status") == "deployed"), None)
    d = manifest_diff(name, ns, cur, revision) if cur else {"diff": ""}
    return {"ok": ok, "error": None if ok else out[:500], "current": cur, "target": revision,
            "diff": d.get("diff", d.get("error", ""))}


def _run_job(kind: str, title: str, args: list[str], target: dict) -> dict:
    job = {"id": uuid.uuid4().hex[:12], "type": kind, "state": "running", "created": time.time(),
           "t0": time.time(), "steps": [], "target": target}
    with D._lock:
        D._jobs[job["id"]] = job

    def run():
        D._step(job, title, "running", " ".join(args[:3]) + " …")
        ok, out = _h(args, timeout=420)
        D._step(job, title, "ok" if ok else "failed", out[-800:])
        status = (history(target["name"], target["namespace"]).get("history") or [{}])[0]
        outcome = "ok" if ok else ("rolled_back" if "--atomic" in args and
                                   "rollback" in out.lower() else "failed")
        with D._lock:
            job.update(state="done", outcome=outcome, reason="" if ok else out[-800:],
                       release_status=status, duration_s=round(time.time() - job["t0"], 1))
        D._log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "type": kind, "id": job["id"],
                "objects": [f"helm/{target['name']} ({target['namespace']})"], "target": target,
                "outcome": outcome, "reason": ("" if ok else out[-500:]), "duration_s": job["duration_s"]})

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, **D._job_view(job)}


def rollback(name: str, ns: str, revision: int) -> dict:
    if (e := _check(name, ns)):
        return {"ok": False, "error": e}
    return _run_job("helm_rollback", "Retour arrière Helm",
                    ["rollback", name, str(revision), "-n", ns, "--wait", "--timeout", TIMEOUT],
                    {"name": name, "namespace": ns, "revision": revision})


def upgrade(name: str, ns: str, chart: str, version: Optional[str], sets: list[str]) -> dict:
    args, err = _upgrade_args(name, ns, chart, version, sets)
    if err:
        return {"ok": False, "error": err}
    return _run_job("helm_upgrade", "Mise à jour Helm",
                    args + ["--atomic", "--wait", "--timeout", TIMEOUT],
                    {"name": name, "namespace": ns, "chart": chart, "version": version, "set": sets})

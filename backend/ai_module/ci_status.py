"""
État de l'intégration continue (GitHub Actions) — partie DevOps.

Lit les dernières exécutions du workflow .github/workflows/ci.yml du dépôt
GITHUB_REPO (fichier .env), et le détail des jobs de la plus récente. Aucune
donnée n'est inventée : sans dépôt configuré, la page l'indique.
Cache de 60 s (l'API GitHub limite à 60 requêtes/heure sans jeton).
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Optional

import httpx

from core.config import settings

API = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_cache: dict = {"t": 0.0, "data": None}


def _headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
    return h


def _dur(a: Optional[str], b: Optional[str]) -> Optional[int]:
    try:
        fa = datetime.fromisoformat(a.replace("Z", "+00:00"))
        fb = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return int((fb - fa).total_seconds())
    except (AttributeError, ValueError):
        return None


def runs(force: bool = False) -> dict:
    repo = settings.GITHUB_REPO
    if not repo or not REPO_RE.match(repo):
        return {"configured": False,
                "hint": "Ajouter GITHUB_REPO=proprietaire/aikube (et GITHUB_TOKEN si le dépôt est privé) "
                        "dans ~/aikube/.env, puis pousser le dépôt avec .github/workflows/ci.yml."}
    if not force and _cache["data"] and time.time() - _cache["t"] < 60:
        return _cache["data"]
    try:
        with httpx.Client(timeout=15, headers=_headers()) as c:
            r = c.get(f"{API}/repos/{repo}/actions/runs", params={"per_page": 10})
            if r.status_code != 200:
                return {"configured": True, "repo": repo,
                        "error": f"GitHub a répondu {r.status_code} : {r.text[:200]}"}
            items = r.json().get("workflow_runs", [])
            out = [{"id": w["id"], "name": w.get("name"), "branch": w.get("head_branch"),
                    "commit": (w.get("head_sha") or "")[:7],
                    "message": ((w.get("head_commit") or {}).get("message") or "").split("\n")[0][:90],
                    "event": w.get("event"), "status": w.get("status"), "conclusion": w.get("conclusion"),
                    "created": w.get("created_at"), "duration_s": _dur(w.get("run_started_at"), w.get("updated_at")),
                    "url": w.get("html_url")} for w in items]
            jobs = []
            if items:
                j = c.get(items[0]["jobs_url"])
                if j.status_code == 200:
                    for job in j.json().get("jobs", []):
                        jobs.append({"name": job.get("name"), "status": job.get("status"),
                                     "conclusion": job.get("conclusion"),
                                     "duration_s": _dur(job.get("started_at"), job.get("completed_at")),
                                     "steps": [{"name": s.get("name"), "conclusion": s.get("conclusion")}
                                               for s in job.get("steps", [])],
                                     "url": job.get("html_url")})
    except httpx.HTTPError as e:
        return {"configured": True, "repo": repo, "error": f"GitHub injoignable : {e}"}
    done = [w for w in out if w["status"] == "completed"]
    data = {"configured": True, "repo": repo, "runs": out, "latest_jobs": jobs,
            "success_rate": round(sum(w["conclusion"] == "success" for w in done) / len(done), 2) if done else None,
            "fetched_at": time.time()}
    _cache.update(t=time.time(), data=data)
    return data

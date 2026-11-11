"""
API DevOps v2 — déploiement sûr, historique et retour arrière, Helm, CI/CD, santé
des mesures. Monté sur /api/devops/v2 ; l'ancienne API /api/devops est conservée.
"""
import asyncio
import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
KINDS = {"Deployment", "StatefulSet", "DaemonSet"}


def _ok(*names: str) -> None:
    for n in names:
        if not NAME.match(n or ""):
            raise HTTPException(400, f"nom invalide : {n!r}")


async def _bg(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


# ── Déploiement sûr ─────────────────────────────────────────────────────────

class PreviewReq(BaseModel):
    yaml: str
    namespace: Optional[str] = "default"


class ApplyReq(BaseModel):
    auto_rollback: bool = True


@router.post("/deploy/preview")
async def deploy_preview(req: PreviewReq):
    from ai_module import host_context, safe_deploy
    ns = req.namespace or "default"
    _ok(ns)
    ctx = await host_context.collect(force=True)
    return await _bg(safe_deploy.preview, req.yaml, ns, ctx)


@router.post("/deploy/{job_id}/apply")
async def deploy_apply(job_id: str, req: ApplyReq):
    from ai_module import safe_deploy
    res = safe_deploy.apply(job_id, req.auto_rollback)
    if not res.get("ok"):
        raise HTTPException(409, res.get("error"))
    return res


@router.get("/jobs/{job_id}")
async def job_status(job_id: str):
    from ai_module import safe_deploy
    res = safe_deploy.status(job_id)
    if not res:
        raise HTTPException(404, "opération inconnue (le backend a peut-être redémarré)")
    return res


@router.get("/deploy/stats")
async def deploy_stats():
    from ai_module import safe_deploy
    return await _bg(safe_deploy.stats)


# ── Historique et retour arrière ────────────────────────────────────────────

class RevisionReq(BaseModel):
    revision: int


@router.get("/workloads")
async def workloads(namespace: Optional[str] = None):
    from ai_module import rollout_history
    if namespace:
        _ok(namespace)
    return await _bg(rollout_history.list_workloads, namespace)


def _wl(kind: str, ns: str, name: str):
    if kind not in KINDS:
        raise HTTPException(400, f"type non pris en charge : {kind}")
    _ok(ns, name)


@router.get("/workloads/{kind}/{ns}/{name}/revisions")
async def wl_revisions(kind: str, ns: str, name: str):
    from ai_module import rollout_history
    _wl(kind, ns, name)
    res = await _bg(rollout_history.revisions, kind, ns, name)
    for r in res.get("revisions", []):
        r.pop("template", None)
    return res


@router.get("/workloads/{kind}/{ns}/{name}/diff")
async def wl_diff(kind: str, ns: str, name: str, a: int, b: int):
    from ai_module import rollout_history
    _wl(kind, ns, name)
    return await _bg(rollout_history.diff, kind, ns, name, a, b)


@router.post("/workloads/{kind}/{ns}/{name}/rollback/preview")
async def wl_rollback_preview(kind: str, ns: str, name: str, req: RevisionReq):
    from ai_module import rollout_history
    _wl(kind, ns, name)
    return await _bg(rollout_history.rollback_preview, kind, ns, name, req.revision)


@router.post("/workloads/{kind}/{ns}/{name}/rollback")
async def wl_rollback(kind: str, ns: str, name: str, req: RevisionReq):
    from ai_module import rollout_history
    _wl(kind, ns, name)
    return await _bg(rollout_history.rollback, kind, ns, name, req.revision)


# ── Helm ────────────────────────────────────────────────────────────────────

class UpgradeReq(BaseModel):
    chart: str
    version: Optional[str] = None
    set: list[str] = []


@router.get("/helm/releases")
async def helm_releases():
    from ai_module import helm_ops
    return await _bg(helm_ops.releases)


@router.get("/helm/{ns}/{name}/history")
async def helm_history(ns: str, name: str):
    from ai_module import helm_ops
    return await _bg(helm_ops.history, name, ns)


@router.get("/helm/{ns}/{name}/values")
async def helm_values(ns: str, name: str, revision: Optional[int] = None):
    from ai_module import helm_ops
    return await _bg(helm_ops.values, name, ns, revision)


@router.get("/helm/{ns}/{name}/diff")
async def helm_diff(ns: str, name: str, a: int, b: int):
    from ai_module import helm_ops
    return await _bg(helm_ops.manifest_diff, name, ns, a, b)


@router.post("/helm/{ns}/{name}/rollback/preview")
async def helm_rb_preview(ns: str, name: str, req: RevisionReq):
    from ai_module import helm_ops
    return await _bg(helm_ops.rollback_preview, name, ns, req.revision)


@router.post("/helm/{ns}/{name}/rollback")
async def helm_rb(ns: str, name: str, req: RevisionReq):
    from ai_module import helm_ops
    return await _bg(helm_ops.rollback, name, ns, req.revision)


@router.post("/helm/{ns}/{name}/upgrade/preview")
async def helm_up_preview(ns: str, name: str, req: UpgradeReq):
    from ai_module import helm_ops
    return await _bg(helm_ops.upgrade_preview, name, ns, req.chart, req.version, req.set)


@router.post("/helm/{ns}/{name}/upgrade")
async def helm_up(ns: str, name: str, req: UpgradeReq):
    from ai_module import helm_ops
    return await _bg(helm_ops.upgrade, name, ns, req.chart, req.version, req.set)


# ── CI/CD et santé des mesures ──────────────────────────────────────────────

@router.get("/ci/runs")
async def ci_runs(refresh: bool = False):
    from ai_module import ci_status
    return await _bg(ci_status.runs, refresh)


@router.get("/platform-health")
async def platform_health(refresh: bool = False):
    from ai_module import platform_health as ph
    return await _bg(ph.check, refresh)

"""
Analyse des ressources en temps réel et recommandations de dimensionnement.

GET  /api/resources/recommendations      état réservé vs consommé, recommandations
POST /api/resources/apply                 dry-run serveur, puis application
GET  /api/resources/actions               historique des changements appliqués
"""
import asyncio
import json
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class ApplyRequest(BaseModel):
    kind: str
    namespace: str
    name: str
    container: str
    recommended: dict
    dry_run: bool = True
    before: Optional[dict] = None
    usage: Optional[dict] = None
    status: Optional[str] = None


@router.get("/recommendations")
async def recommendations(include_system: bool = False):
    from ai_module import rightsizing
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, rightsizing.analyse, include_system)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/apply")
async def apply_recommendation(req: ApplyRequest):
    from ai_module import rightsizing
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(
        None, lambda: rightsizing.apply(req.kind, req.namespace, req.name, req.container,
                                        req.recommended, req.dry_run, req.before,
                                        req.usage, req.status))
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res)
    return res


@router.get("/actions")
async def actions(limit: int = 50):
    from ai_module import rightsizing
    path = rightsizing._ACTIONS_PATH
    if not os.path.exists(path):
        return {"actions": []}
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return {"actions": rows[-limit:][::-1]}

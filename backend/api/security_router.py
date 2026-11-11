from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import subprocess, json, io
from datetime import datetime

router = APIRouter()

def kubectl(args: list, timeout: int = 15) -> dict:
    r = subprocess.run(['kubectl'] + args + ['-o', 'json'],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise Exception(r.stderr.strip())
    return json.loads(r.stdout)

# ── RBAC ─────────────────────────────────────────────────────

@router.get("/rbac/roles")
async def get_roles():
    try:
        roles_data = kubectl(['get', 'roles', '-A'])
        cr_data    = kubectl(['get', 'clusterroles'])
        result = []
        for r in roles_data.get('items', []):
            result.append({
                "name":        r['metadata']['name'],
                "namespace":   r['metadata']['namespace'],
                "type":        "Role",
                "rules_count": len(r.get('rules') or []),
            })
        for r in cr_data.get('items', []):
            result.append({
                "name":        r['metadata']['name'],
                "namespace":   "cluster-wide",
                "type":        "ClusterRole",
                "rules_count": len(r.get('rules') or []),
            })
        return {"roles": result, "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/rbac/bindings")
async def get_bindings():
    try:
        rb  = kubectl(['get', 'rolebindings', '-A'])
        crb = kubectl(['get', 'clusterrolebindings'])
        result = []
        for b in rb.get('items', []):
            result.append({
                "name":      b['metadata']['name'],
                "namespace": b['metadata']['namespace'],
                "type":      "RoleBinding",
                "role":      b.get('roleRef', {}).get('name', ''),
                "subjects":  len(b.get('subjects') or []),
            })
        for b in crb.get('items', []):
            result.append({
                "name":      b['metadata']['name'],
                "namespace": "cluster-wide",
                "type":      "ClusterRoleBinding",
                "role":      b.get('roleRef', {}).get('name', ''),
                "subjects":  len(b.get('subjects') or []),
            })
        return {"bindings": result, "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/network-policies")
async def get_network_policies():
    try:
        data = kubectl(['get', 'networkpolicies', '-A'])
        result = []
        for np in data.get('items', []):
            result.append({
                "name":      np['metadata']['name'],
                "namespace": np['metadata']['namespace'],
                "pod_selector": np.get('spec', {}).get('podSelector', {}),
            })
        return {"network_policies": result, "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/secrets")
async def get_secrets():
    try:
        data = kubectl(['get', 'secrets', '-A'])
        result = []
        for s in data.get('items', []):
            result.append({
                "name":      s['metadata']['name'],
                "namespace": s['metadata']['namespace'],
                "type":      s.get('type', 'Opaque'),
            })
        return {"secrets": result, "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── CIS BENCHMARK ─────────────────────────────────────────────

@router.get("/audit/scan")
async def cis_scan():
    try:
        pods_data = kubectl(['get', 'pods', '-A'])
        ns_data   = kubectl(['get', 'namespaces'])
        np_data   = kubectl(['get', 'networkpolicies', '-A'])

        pods = pods_data.get('items', [])
        namespaces = [n['metadata']['name'] for n in ns_data.get('items', [])]
        ns_with_policy = set(np['metadata']['namespace'] for np in np_data.get('items', []))
        system_ns = {'kube-system', 'kube-public', 'kube-node-lease'}

        issues = []
        score  = 100

        for pod in pods:
            meta = pod['metadata']
            spec = pod.get('spec', {})
            ns   = meta['namespace']
            name = meta['name']

            for container in spec.get('containers', []):
                sc = container.get('securityContext', {})
                if not sc:
                    issues.append({
                        "cis_control": "CIS-5.2.6",
                        "severity":    "warning",
                        "description": f"Container '{container['name']}' sans SecurityContext",
                        "fix":         "Ajouter runAsNonRoot: true, readOnlyRootFilesystem: true",
                        "resource":    f"pod/{name}",
                    })
                    score -= 3

        for ns in namespaces:
            if ns not in system_ns and ns not in ns_with_policy:
                issues.append({
                    "cis_control": "CIS-5.3.2",
                    "severity":    "warning",
                    "description": f"Namespace '{ns}' sans NetworkPolicy",
                    "fix":         "Créer NetworkPolicy deny-all par défaut",
                    "resource":    f"namespace/{ns}",
                })
                score -= 5

        score = max(0, score)
        grade = "A" if score>=90 else "B" if score>=80 else "C" if score>=70 else "D"

        return {
            "cis_score":     score,
            "grade":         grade,
            "total_issues":  len(issues),
            "critical_count":sum(1 for i in issues if i['severity']=='critical'),
            "issues":        issues,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Retirés le 01/11/2026 (aucun écran ne les appelait plus) : l'ancien scan
# MITRE (/mitre/scan) et l'export HTML (/export/pdf). Le rattachement à MITRE
# ATT&CK est fait par le scanner v2 (ai_module/security_scanner.py).


# ══════════════════════════════════════════════════════════════════════════════
# V2 — scanner au niveau des workloads + remédiation vérifiée (chapitre 7)
# Les endpoints ci-dessus sont conservés pour la traçabilité des anciennes mesures.
# ══════════════════════════════════════════════════════════════════════════════
import asyncio as _asyncio
from pydantic import BaseModel as _BaseModel


class _PreviewRequest(_BaseModel):
    finding_id: str
    method: str = "auto"      # auto = correctif déterministe ; ai = correctif proposé par l'IA


@router.get("/v2/scan")
async def scan_v2(include_system: bool = False, include_info: bool = True):
    from ai_module import security_scanner
    loop = _asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, security_scanner.scan, include_system, include_info)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v2/benchmark")
async def benchmark_v2():
    """Précision / rappel sur le banc étiqueté k8s/benchmark/vuln-benchmark.yaml."""
    from ai_module import security_scanner
    loop = _asyncio.get_running_loop()
    return await loop.run_in_executor(None, security_scanner.evaluate_benchmark)


@router.post("/v2/remediation/preview")
async def remediation_preview(req: _PreviewRequest):
    from ai_module import remediation
    loop = _asyncio.get_running_loop()
    try:
        # L'aperçu lance kubectl et attend l'IA : on l'exécute dans un fil à part,
        # avec sa propre boucle, pour ne jamais bloquer les autres requêtes.
        return await loop.run_in_executor(
            None, lambda: _asyncio.run(remediation.preview(req.finding_id, req.method)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v2/remediation/{job_id}/apply")
async def remediation_apply(job_id: str):
    from ai_module import remediation
    res = remediation.apply(job_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res


@router.get("/v2/remediation/{job_id}")
async def remediation_status(job_id: str):
    from ai_module import remediation
    job = remediation.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="tâche inconnue")
    return job


@router.get("/v2/remediation-stats")
async def remediation_stats():
    from ai_module import remediation
    return remediation.stats()

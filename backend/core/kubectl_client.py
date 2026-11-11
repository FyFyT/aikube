import subprocess
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=8)

def _run(args: list, timeout: int = 8) -> dict:
    cmd = ['kubectl'] + args + ['-o', 'json']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise Exception(r.stderr.strip())
    return json.loads(r.stdout)

def _run_action(args: list, timeout: int = 30) -> str:
    r = subprocess.run(['kubectl'] + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise Exception(r.stderr.strip())
    return r.stdout.strip()

async def kubectl_async(args: list) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, _run, args)

async def kubectl_action_async(args: list) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, _run_action, args)

def kubectl(args: list) -> dict:
    return _run(args)

def kubectl_action(args: list) -> str:
    return _run_action(args)

def _parse_pods(data):
    result = []
    for item in data.get('items', []):
        meta = item['metadata']
        spec = item.get('spec', {})
        status = item.get('status', {})
        restarts = sum(cs.get('restartCount', 0) for cs in status.get('containerStatuses', []))
        result.append({
            "name":       meta['name'],
            "namespace":  meta['namespace'],
            "status":     status.get('phase', 'Unknown'),
            "node":       spec.get('nodeName'),
            "ip":         status.get('podIP'),
            "restarts":   restarts,
            "age":        meta.get('creationTimestamp', ''),
            "labels":     meta.get('labels', {}),
            "containers": [c['name'] for c in spec.get('containers', [])],
        })
    return result

def _parse_nodes(data):
    result = []
    for item in data.get('items', []):
        meta = item['metadata']
        status = item.get('status', {})
        conditions = {c['type']: c['status'] for c in status.get('conditions', [])}
        capacity = status.get('capacity', {})
        info = status.get('nodeInfo', {})
        result.append({
            "name":    meta['name'],
            "status":  "Ready" if conditions.get("Ready") == "True" else "NotReady",
            "cpu":     capacity.get("cpu"),
            "memory":  capacity.get("memory"),
            "kubelet": info.get("kubeletVersion"),
            "os":      info.get("osImage"),
        })
    return result

def _parse_deployments(data):
    result = []
    for item in data.get('items', []):
        meta = item['metadata']
        spec = item.get('spec', {})
        status = item.get('status', {})
        containers = spec.get('template', {}).get('spec', {}).get('containers', [])
        result.append({
            "name":      meta['name'],
            "namespace": meta['namespace'],
            "replicas":  spec.get('replicas', 0),
            "ready":     status.get('readyReplicas', 0),
            "available": status.get('availableReplicas', 0),
            "image":     containers[0]['image'] if containers else 'unknown',
            "age":       meta.get('creationTimestamp', ''),
            "labels":    meta.get('labels', {}),
            "strategy":  spec.get('strategy', {}).get('type', 'RollingUpdate'),
        })
    return result

def _parse_namespaces(data):
    return [
        {
            "name":   item['metadata']['name'],
            "status": item.get('status', {}).get('phase', 'Active'),
            "age":    item['metadata'].get('creationTimestamp', ''),
        }
        for item in data.get('items', [])
    ]

async def get_summary_parallel():
    """Fait les 4 appels kubectl EN PARALLÈLE — 4x plus rapide"""
    pods_task = kubectl_async(['get', 'pods', '-A'])
    nodes_task = kubectl_async(['get', 'nodes'])
    deps_task  = kubectl_async(['get', 'deployments', '-A'])
    ns_task    = kubectl_async(['get', 'namespaces'])

    pods_raw, nodes_raw, deps_raw, ns_raw = await asyncio.gather(
        pods_task, nodes_task, deps_task, ns_task
    )

    pods = _parse_pods(pods_raw)
    nodes = _parse_nodes(nodes_raw)
    deps = _parse_deployments(deps_raw)
    ns = _parse_namespaces(ns_raw)

    return {
        "pods":        {"pods": pods, "count": len(pods)},
        "nodes":       {"nodes": nodes, "count": len(nodes)},
        "deployments": {"deployments": deps, "count": len(deps)},
        "namespaces":  {"namespaces": ns},
    }

def get_pods(namespace=None):
    args = ['get', 'pods', '-n', namespace] if namespace else ['get', 'pods', '-A']
    return _parse_pods(_run(args))

def get_nodes():
    return _parse_nodes(_run(['get', 'nodes']))

def get_deployments(namespace=None):
    args = ['get', 'deployments', '-n', namespace] if namespace else ['get', 'deployments', '-A']
    return _parse_deployments(_run(args))

def get_namespaces():
    return _parse_namespaces(_run(['get', 'namespaces']))

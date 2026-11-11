"""
Réparation DÉTERMINISTE d'un manifeste rejeté (option « repair » du générateur).

Constat (banc C1, KubeLLM v3, 108 générations) : les 18 échecs répètent la même
erreur à chacune des trois tentatives — le modèle lit le message d'erreur sans savoir
quoi changer. Or ces erreurs se CALCULENT :
  - version d'API : le serveur publie la bonne (contexte « Versions d'API EXACTES ») ;
  - champ mal placé : sondes au niveau du pod, fsGroup dans le conteneur… ;
  - nom invalide : caractère parasite dans un nom ;
  - capacité : la place libre par nœud, dans le cluster et dans la RAM de l'hôte est connue ;
  - requests/limits absents, namespace non créé.
Le modèle propose, la réparation corrige ce qui se calcule, les arbitres (dry-run
serveur + contrôle de capacité) restent seuls juges. Chaque modification est
journalisée et signalée en tête du manifeste par « # Avertissement : réparation… ».

Ce qui n'est PAS réparé : une ressource oubliée, un mauvais choix de conception,
une intention mal comprise. Un champ inconnu du serveur est supprimé (et signalé)
s'il ne correspond à aucun déplacement connu.
"""
from __future__ import annotations

import copy
import re
from typing import Any

import yaml

from ai_module.host_context import mib, parse_cpu, parse_mem

SAFETY = 0.9                 # marge sous la capacité libre lors d'une réduction
MIN_CPU_M, MIN_MEM_MIB = 10, 32
DEFAULT_RES = {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "500m", "memory": "256Mi"}}

# champs de CONTENEUR écrits par erreur au niveau du pod (jamais valides au niveau du pod)
CONTAINER_ONLY = ("readinessProbe", "livenessProbe", "startupProbe", "ports", "env", "envFrom",
                  "volumeMounts", "image", "imagePullPolicy", "command", "args", "workingDir", "lifecycle")
# securityContext : champs propres au POD / propres au CONTENEUR
POD_SC_ONLY = ("fsGroup", "fsGroupChangePolicy", "supplementalGroups", "sysctls")
CONTAINER_SC_ONLY = ("allowPrivilegeEscalation", "capabilities", "privileged", "readOnlyRootFilesystem", "procMount")
DNS1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


# ── Accès aux pods des objets ───────────────────────────────────────────────
def pod_spec(d: dict) -> dict | None:
    k, s = d.get("kind"), d.get("spec") or {}
    if k == "Pod":
        return s
    if k == "CronJob":
        return (((s.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec")
    if k in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "ReplicationController"):
        return (s.get("template") or {}).get("spec")
    return None


def _replicas_key(d: dict):
    """(objet portant le nombre de pods, clé) — None pour un DaemonSet ou un Pod."""
    k, s = d.get("kind"), d.get("spec") or {}
    if k in ("Deployment", "StatefulSet", "ReplicaSet", "ReplicationController"):
        return s, "replicas"
    if k == "Job":
        return s, "parallelism"
    if k == "CronJob":
        return (s.get("jobTemplate") or {}).get("spec") or {}, "parallelism"
    return None, None


def _replicas(d: dict, n_nodes: int) -> int:
    if d.get("kind") == "DaemonSet":
        return max(1, n_nodes)
    obj, key = _replicas_key(d)
    if obj is None:
        return 1
    try:
        return int(obj.get(key, 1) or 1)
    except (TypeError, ValueError):
        return 1


def fmt_cpu(m: float) -> str:
    return f"{max(MIN_CPU_M, int(m))}m"


def fmt_mem(b: float) -> str:
    return f"{max(MIN_MEM_MIB, int(b / 1024**2))}Mi"


# ── Règles de structure ─────────────────────────────────────────────────────
def fix_api_versions(docs, ctx, changes):
    kinds = ctx.get("api_kinds") or {}
    for d in docs:
        served = (kinds.get(d.get("kind")) or {}).get("apiVersion")
        if served and d.get("apiVersion") != served:
            changes.append(f"{d['kind']}/{d['metadata']['name']} : apiVersion « {d.get('apiVersion')} » "
                           f"remplacée par « {served} » (version servie par ce cluster)")
            d["apiVersion"] = served


def fix_misplaced(docs, changes):
    for d in docs:
        ps = pod_spec(d)
        if not isinstance(ps, dict):
            continue
        name = f"{d['kind']}/{d['metadata']['name']}"
        cs = [c for c in ps.get("containers") or [] if isinstance(c, dict)]
        if len(cs) == 1:
            for k in CONTAINER_ONLY:
                if k in ps and k not in cs[0]:
                    cs[0][k] = ps.pop(k)
                    changes.append(f"{name} : « {k} » déplacé du pod vers le conteneur « {cs[0].get('name')} »")
        pod_sc = ps.get("securityContext") if isinstance(ps.get("securityContext"), dict) else None
        for c in cs:
            sc = c.get("securityContext")
            if isinstance(sc, dict):
                for k in POD_SC_ONLY:
                    if k in sc:
                        ps.setdefault("securityContext", {}).setdefault(k, sc.pop(k))
                        changes.append(f"{name} : securityContext.{k} déplacé du conteneur vers le pod")
            for p in c.get("ports") or []:
                if isinstance(p, dict) and "port" in p and "containerPort" not in p:
                    p["containerPort"] = p.pop("port")
                    changes.append(f"{name} : ports.port renommé containerPort")
        if pod_sc:
            for k in CONTAINER_SC_ONLY:
                if k in pod_sc:
                    v = pod_sc.pop(k)
                    for c in cs:
                        c.setdefault("securityContext", {}).setdefault(k, v)
                    changes.append(f"{name} : securityContext.{k} déplacé du pod vers les conteneurs")


def _slug(s: str, limit: int = 63) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", str(s).lower()).strip("-")[:limit].strip("-")
    return s or "app"


def fix_names(docs, changes):
    for d in docs:
        md = d.get("metadata") or {}
        n = str(md.get("name", ""))
        if n and not re.match(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$", n):
            md["name"] = _slug(n, 253)
            changes.append(f"{d['kind']} : nom « {n} » corrigé en « {md['name']} »")
        ps = pod_spec(d)
        for c in (ps or {}).get("containers") or []:
            cn = str(c.get("name", ""))
            if cn and not DNS1123.match(cn):
                c["name"] = _slug(cn)
                changes.append(f"{d['kind']}/{md.get('name')} : nom de conteneur « {cn} » corrigé en « {c['name']} »")


def fix_namespaces(docs, ctx, changes):
    known = set((ctx.get("cluster") or {}).get("namespaces") or []) | {
        d["metadata"]["name"] for d in docs if d.get("kind") == "Namespace"}
    missing = sorted({(d.get("metadata") or {}).get("namespace") for d in docs} - known - {None})
    for ns in missing:
        docs.insert(0, {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns}})
        changes.append(f"namespace « {ns} » absent du cluster : document Namespace ajouté")


def fix_missing_resources(docs, changes):
    for d in docs:
        for c in (pod_spec(d) or {}).get("containers") or []:
            res = c.setdefault("resources", {})
            for key in ("requests", "limits"):
                blk = res.setdefault(key, {})
                for r in ("cpu", "memory"):
                    if r not in blk:
                        blk[r] = DEFAULT_RES[key][r]
                        changes.append(f"{d['kind']}/{d['metadata']['name']}, conteneur « {c.get('name')} » : "
                                       f"{key}.{r} absent, fixé à {blk[r]}")
            # une limite inférieure à la request est refusée par le serveur
            for r, parse, fmt in (("cpu", parse_cpu, fmt_cpu), ("memory", parse_mem, fmt_mem)):
                if parse(res["limits"][r]) < parse(res["requests"][r]):
                    res["limits"][r] = res["requests"][r]


def drop_unknown_fields(docs, check, changes):
    """Supprime les champs que le serveur déclare inconnus (après les déplacements connus)."""
    err = ((check or {}).get("dry_run") or {}).get("error") or ""
    for m in re.finditer(r"cannot be handled as an? (\w+): strict decoding error: ([^\n]+)", err):
        kind, rest = m.group(1), m.group(2)
        for path in re.findall(r'unknown field "([^"]+)"', rest):
            for d in docs:
                if d.get("kind") == kind and _delete_path(d, path):
                    changes.append(f"{kind}/{d['metadata']['name']} : champ inconnu « {path} » supprimé")


def _delete_path(obj, path: str) -> bool:
    parts = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    cur = obj
    for p in parts[:-1]:
        if p.startswith("["):
            i = int(p[1:-1])
            if not isinstance(cur, list) or i >= len(cur):
                return False
            cur = cur[i]
        else:
            if not isinstance(cur, dict) or p not in cur:
                return False
            cur = cur[p]
    last = parts[-1] if parts else None
    if isinstance(cur, dict) and last in cur:
        cur.pop(last)
        return True
    return False


# ── Capacité ────────────────────────────────────────────────────────────────
def _pod_req(ps: dict) -> tuple[float, float]:
    cpu = mem = 0.0
    for c in ps.get("containers") or []:
        req = ((c.get("resources") or {}).get("requests")) or {}
        cpu += parse_cpu(req.get("cpu"))
        mem += parse_mem(req.get("memory"))
    return cpu, mem


def _scale_pod(ps: dict, fcpu: float, fmem: float):
    for c in ps.get("containers") or []:
        res = c.get("resources") or {}
        for key in ("requests", "limits"):
            blk = res.get(key) or {}
            if fcpu < 1 and "cpu" in blk:
                blk["cpu"] = fmt_cpu(parse_cpu(blk["cpu"]) * fcpu)
            if fmem < 1 and "memory" in blk:
                blk["memory"] = fmt_mem(parse_mem(blk["memory"]) * fmem)
        req, lim = res.get("requests") or {}, res.get("limits") or {}
        for r, parse in (("cpu", parse_cpu), ("memory", parse_mem)):
            if r in req and r in lim and parse(lim[r]) < parse(req[r]):
                lim[r] = req[r]


def fit_capacity(docs, ctx, changes):
    cl = ctx.get("cluster") or {}
    nodes = [n for n in cl.get("nodes") or [] if n.get("ready") and n.get("schedulable")]
    if not nodes:
        return
    local = cl.get("local_cluster")
    node_cap = cl.get("max_node_mem_free_mib") if local else None
    host_avail = (ctx.get("host") or {}).get("ram_available_mib")

    def mem_cap(n):
        return min(n["mem_free_mib"], node_cap or n["mem_free_mib"]) * 1024**2

    work = [(d, pod_spec(d)) for d in docs if isinstance(pod_spec(d), dict)]
    # 1. chaque pod doit tenir sur UN nœud (le plus favorable)
    for d, ps in work:
        cpu, mem = _pod_req(ps)
        if cpu <= 0 and mem <= 0:
            continue
        best = max(nodes, key=lambda n: min(n["cpu_free_m"] / cpu if cpu else 9e9, mem_cap(n) / mem if mem else 9e9))
        fc = min(1.0, SAFETY * best["cpu_free_m"] / cpu) if cpu and cpu > best["cpu_free_m"] else 1.0
        fm = min(1.0, SAFETY * mem_cap(best) / mem) if mem and mem > mem_cap(best) else 1.0
        if fc < 1 or fm < 1:
            _scale_pod(ps, fc, fm)
            c2, m2 = _pod_req(ps)
            changes.append(f"{d['kind']}/{d['metadata']['name']} : un pod demandait {round(cpu)}m CPU et "
                           f"{mib(mem)}Mi ; le nœud le plus favorable ({best['name']}) n'offre que "
                           f"{best['cpu_free_m']}m et {mib(mem_cap(best))}Mi → ramené à {round(c2)}m et {mib(m2)}Mi par pod")
    # 2. la somme des répliques doit tenir dans le cluster (et dans la RAM de l'hôte si local)
    free_cpu = cl.get("total_cpu_free_m") or 0
    free_mem = (cl.get("total_mem_free_mib") or 0) * 1024**2
    if local and host_avail:
        free_mem = min(free_mem, host_avail * 1024**2)
    tot_cpu = sum(_pod_req(ps)[0] * _replicas(d, len(nodes)) for d, ps in work)
    tot_mem = sum(_pod_req(ps)[1] * _replicas(d, len(nodes)) for d, ps in work)
    fc = min(1.0, SAFETY * free_cpu / tot_cpu) if tot_cpu > free_cpu > 0 else 1.0
    fm = min(1.0, SAFETY * free_mem / tot_mem) if tot_mem > free_mem > 0 else 1.0
    if fc < 1 or fm < 1:
        for d, ps in work:
            _scale_pod(ps, fc, fm)
        changes.append(f"l'ensemble demandait {round(tot_cpu)}m CPU et {mib(tot_mem)}Mi ; il reste "
                       f"{free_cpu}m et {mib(free_mem)}Mi{' (RAM de l’hôte comprise)' if local and host_avail else ''} "
                       f"→ requests et limits réduites de {round((1 - fc) * 100)} % (CPU) et {round((1 - fm) * 100)} % (mémoire)")
    # 3. au plancher (10m / 32Mi) et toujours trop : on réduit le nombre de répliques
    tot_mem = sum(_pod_req(ps)[1] * _replicas(d, len(nodes)) for d, ps in work)
    tot_cpu = sum(_pod_req(ps)[0] * _replicas(d, len(nodes)) for d, ps in work)
    if (free_mem and tot_mem > free_mem) or (free_cpu and tot_cpu > free_cpu):
        for d, ps in work:
            obj, key = _replicas_key(d)
            if obj is None:
                continue
            cpu, mem = _pod_req(ps)
            n_old = _replicas(d, len(nodes))
            n_new = max(1, min(n_old, int(SAFETY * free_mem / mem) if mem else n_old,
                               int(SAFETY * free_cpu / cpu) if cpu else n_old))
            if n_new < n_old:
                obj[key] = n_new
                changes.append(f"{d['kind']}/{d['metadata']['name']} : {key} réduit de {n_old} à {n_new} "
                               f"(même au minimum par pod, {n_old} pods ne tiennent pas)")


# ── Point d'entrée ──────────────────────────────────────────────────────────
def repair(yaml_text: str, check: dict | None, ctx: dict) -> dict:
    """Renvoie {"yaml", "changes"} ; changes vide = rien n'a pu être réparé."""
    try:
        docs = [d for d in yaml.safe_load_all(re.sub(r"^```(?:ya?ml)?|```$", "", yaml_text.strip(), flags=re.M))
                if isinstance(d, dict)]
    except yaml.YAMLError:
        return {"yaml": yaml_text, "changes": []}
    if not docs or any(not (d.get("metadata") or {}).get("name") or "kind" not in d for d in docs):
        return {"yaml": yaml_text, "changes": []}
    docs = copy.deepcopy(docs)
    changes: list[str] = []
    fix_api_versions(docs, ctx, changes)
    fix_misplaced(docs, changes)
    drop_unknown_fields(docs, check, changes)
    fix_names(docs, changes)
    fix_namespaces(docs, ctx, changes)
    fix_missing_resources(docs, changes)
    fit_capacity(docs, ctx, changes)
    if not changes:
        return {"yaml": yaml_text, "changes": []}
    head = [l for l in yaml_text.splitlines() if l.startswith("# Avertissement")]
    head += [f"# Avertissement : réparation automatique — {c}" for c in changes]
    body = yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True)
    return {"yaml": "\n".join(head) + "\n" + body, "changes": changes}

"""
Génération de manifestes Kubernetes vérifiés.

Boucle :   lire l'hôte  →  générer  →  contrôler  →  dry-run serveur
                               ↑                            │
                               └──── erreur renvoyée ───────┘   (≤ N tentatives)

Deux arbitres indépendants du modèle décident si un manifeste est valide :

1. Le serveur Kubernetes lui-même (`kubectl apply --dry-run=server`) : schéma,
   versions d'API, champs inconnus, valeurs invalides. C'est le même critère
   que celui du chapitre C1 (0/12 applicables pour le modèle fine-tuné).
2. Un contrôle de capacité que le dry-run ne fait PAS : les `requests`
   demandées tiennent-elles sur un nœud réel ? Un manifeste peut passer le
   dry-run et laisser ensuite ses pods en Pending indéfiniment.

Chaque génération est journalisée dans data/observations/yaml_generations.jsonl
(taux de réussite à la 1re tentative, après correction, catégories d'erreur).
Ces données servent directement le chapitre C1 du mémoire.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import subprocess
import time
import uuid
from typing import Any, Optional

import yaml

from ai_module import host_context, llm_client
from ai_module.host_context import mib, parse_cpu, parse_mem

LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                       "..", "..", "data", "observations"))
LOG_PATH = os.path.join(LOG_DIR, "yaml_generations.jsonl")
MAX_ATTEMPTS = 3

SYSTEM_PROMPT = """Tu es l'assistant de génération de la plateforme AI-Kube, expert Kubernetes.
Tu reçois l'état RÉEL de la machine hôte et du cluster, lu à l'instant, puis une demande.
Tu produis un manifeste prêt à appliquer sur CE cluster, pas un exemple générique.

Règles impératives :
1. apiVersion : utilise exactement les versions listées dans « Versions d'API EXACTES ».
2. Chaque conteneur a resources.requests ET resources.limits (cpu et memory).
   Dimensionne-les pour tenir dans la capacité LIBRE indiquée : un pod doit tenir sur UN nœud,
   et la somme des répliques doit tenir dans le cluster. Si la demande dépasse ce qui est libre,
   réduis et explique-le dans warnings.
3. Namespace : « default », sauf si l'utilisateur en nomme un autre. Si le namespace nommé
   n'existe pas encore, ajoute d'abord un document Namespace.
4. Images : tag précis, jamais :latest. Sondes readiness/liveness quand le service les permet.
   securityContext : allowPrivilegeEscalation: false ; n'impose runAsNonRoot que si l'image
   le supporte (ex. nginxinc/nginx-unprivileged plutôt que nginx).
5. Stockage : storageClassName existant, ou rien si une classe par défaut existe.
6. Ne génère que les ressources demandées (plus un Service si l'exposition est demandée).
7. Dans « choices » (en français, 3 à 5 éléments), justifie chaque choix de configuration par
   les chiffres réels du contexte (capacité libre, nœuds, versions d'API)."""


# ── Lecture et inspection des documents ─────────────────────────────────────

def parse_docs(text: str) -> tuple[list[dict], Optional[str]]:
    try:
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as e:
        return [], f"YAML illisible : {str(e)[:300]}"
    if not docs:
        return [], "Aucun document YAML"
    for i, d in enumerate(docs, 1):
        if not isinstance(d, dict):
            return [], f"Document {i} : ce n'est pas un objet Kubernetes"
        missing = [k for k in ("apiVersion", "kind") if k not in d]
        if missing or not (d.get("metadata") or {}).get("name"):
            return [], (f"Document {i} ({d.get('kind', '?')}) : champ requis manquant "
                        f"({', '.join(missing) or 'metadata.name'})")
    return docs, None


def _pod_spec(doc: dict, schedulable_nodes: int) -> tuple[Optional[dict], int]:
    kind, spec = doc.get("kind"), doc.get("spec") or {}
    if kind == "Pod":
        return spec, 1
    if kind in ("Deployment", "ReplicaSet", "StatefulSet", "ReplicationController"):
        return (spec.get("template") or {}).get("spec"), int(spec.get("replicas", 1) or 0)
    if kind == "DaemonSet":
        return (spec.get("template") or {}).get("spec"), max(1, schedulable_nodes)
    if kind == "Job":
        return (spec.get("template") or {}).get("spec"), int(spec.get("parallelism", 1) or 1)
    if kind == "CronJob":
        job = (spec.get("jobTemplate") or {}).get("spec") or {}
        return (job.get("template") or {}).get("spec"), int(job.get("parallelism", 1) or 1)
    return None, 0


def _sum_resources(pod_spec: dict) -> dict:
    cpu_r = mem_r = cpu_l = mem_l = 0.0
    missing = []
    for c in pod_spec.get("containers", []) or []:
        res = c.get("resources") or {}
        req, lim = res.get("requests") or {}, res.get("limits") or {}
        for key, block in (("requests", req), ("limits", lim)):
            lacks = [r for r in ("cpu", "memory") if r not in block]
            if lacks:
                missing.append(f"conteneur « {c.get('name', '?')} » : {key} "
                               f"{'/'.join(lacks)} absent")
        cpu_r += parse_cpu(req.get("cpu"))
        mem_r += parse_mem(req.get("memory"))
        cpu_l += parse_cpu(lim.get("cpu"))
        mem_l += parse_mem(lim.get("memory"))
    for c in pod_spec.get("initContainers", []) or []:
        req = (c.get("resources") or {}).get("requests") or {}
        cpu_r = max(cpu_r, parse_cpu(req.get("cpu")))
        mem_r = max(mem_r, parse_mem(req.get("memory")))
    return {"cpu_req_m": cpu_r, "mem_req_b": mem_r,
            "cpu_lim_m": cpu_l, "mem_lim_b": mem_l, "missing": missing}


def static_checks(docs: list[dict], ctx: dict) -> dict:
    """Contrôles que le dry-run serveur ne fait pas : capacité réelle et bonnes pratiques."""
    errors: list[dict] = []
    warnings: list[str] = []
    cluster = ctx.get("cluster", {})
    kinds = ctx.get("api_kinds") or {}
    nodes = [n for n in cluster.get("nodes", []) if n["ready"] and n["schedulable"]]
    capacity_known = bool(nodes) and "pods" not in ctx.get("errors", {})
    sc_names = {s["name"] for s in cluster.get("storage_classes", [])}
    has_default_sc = any(s["default"] for s in cluster.get("storage_classes", []))

    workloads = []
    total_cpu = total_mem = 0.0
    for d in docs:
        kind, name = d.get("kind"), d["metadata"]["name"]
        served = kinds.get(kind, {}).get("apiVersion")
        if served and d.get("apiVersion") != served:
            warnings.append(f"{kind}/{name} : apiVersion « {d.get('apiVersion')} », "
                            f"le serveur préfère « {served} »")

        if kind == "PersistentVolumeClaim":
            scn = (d.get("spec") or {}).get("storageClassName")
            if scn and sc_names and scn not in sc_names:
                errors.append({"source": "capacité", "category": "StorageClass inexistante",
                               "message": f"PVC {name} : storageClassName « {scn} » n'existe pas "
                                          f"(classes : {', '.join(sorted(sc_names))})"})
            elif not scn and not has_default_sc:
                warnings.append(f"PVC {name} : aucune StorageClass par défaut, il restera Pending")

        pod_spec, replicas = _pod_spec(d, len(nodes))
        if pod_spec is None:
            continue
        r = _sum_resources(pod_spec)
        for m in r["missing"]:
            errors.append({"source": "politique", "category": "requests/limits manquants",
                           "message": f"{kind}/{name}, {m}"})
        for c in pod_spec.get("containers", []) or []:
            img = str(c.get("image", ""))
            if img.endswith(":latest") or (":" not in img.split("/")[-1] and "@" not in img):
                warnings.append(f"{kind}/{name} : image « {img} » sans tag précis")

        fits_node = None
        if capacity_known:
            node_mem_cap = cluster.get("max_node_mem_free_mib") if cluster.get("local_cluster") else None
            fits_node = [n["name"] for n in nodes
                         if r["cpu_req_m"] <= n["cpu_free_m"]
                         and r["mem_req_b"] <= min(n["mem_free_mib"], node_mem_cap or n["mem_free_mib"]) * 1024**2]
            if not fits_node:
                errors.append({"source": "capacité", "category": "ne tient sur aucun nœud",
                               "message": (f"{kind}/{name} : un pod demande "
                                           f"{round(r['cpu_req_m'])}m CPU et {mib(r['mem_req_b'])}Mi, "
                                           f"mais le nœud le plus libre n'offre que "
                                           f"{cluster.get('max_node_cpu_free_m')}m et "
                                           f"{cluster.get('max_node_mem_free_mib')}Mi. "
                                           f"Les pods resteraient Pending.")})
            elif kind == "DaemonSet" and len(fits_node) < len(nodes):
                warnings.append(f"DaemonSet/{name} : ne tient que sur {len(fits_node)} "
                                f"nœud(s) sur {len(nodes)}")
        total_cpu += r["cpu_req_m"] * replicas
        total_mem += r["mem_req_b"] * replicas
        workloads.append({
            "kind": kind, "name": name, "replicas": replicas,
            "cpu_req_m": round(r["cpu_req_m"]), "mem_req_mib": mib(r["mem_req_b"]),
            "cpu_lim_m": round(r["cpu_lim_m"]), "mem_lim_mib": mib(r["mem_lim_b"]),
            "fits_nodes": fits_node,
        })

    if capacity_known and workloads:
        free_cpu, free_mem = cluster.get("total_cpu_free_m", 0), cluster.get("total_mem_free_mib", 0)
        host_avail = (ctx.get("host") or {}).get("ram_available_mib")
        if cluster.get("local_cluster") and host_avail and mib(total_mem) > host_avail:
            errors.append({"source": "capacité", "category": "dépasse la RAM physique de l'hôte",
                           "message": (f"L'ensemble des répliques réserve {mib(total_mem)}Mi. Le cluster "
                                       f"local annonce {cluster.get('declared_mem_free_mib')}Mi libres, "
                                       f"mais ses nœuds partagent la même machine, qui n'a que "
                                       f"{host_avail}Mi de RAM disponible.")})
        elif total_cpu > free_cpu or mib(total_mem) > free_mem:
            errors.append({"source": "capacité", "category": "dépasse la capacité du cluster",
                           "message": (f"L'ensemble des répliques demande {round(total_cpu)}m CPU "
                                       f"et {mib(total_mem)}Mi, le cluster n'a que {free_cpu}m "
                                       f"et {free_mem}Mi de libre.")})
    if not capacity_known:
        warnings.append("Capacité des nœuds inconnue : contrôle de placement non effectué")

    return {
        "errors": errors, "warnings": warnings,
        "capacity": {
            "known": capacity_known, "workloads": workloads,
            "total_cpu_req_m": round(total_cpu), "total_mem_req_mib": mib(total_mem),
            "cluster_free_cpu_m": cluster.get("total_cpu_free_m"),
            "cluster_free_mem_mib": cluster.get("total_mem_free_mib"),
            "declared_free_mem_mib": cluster.get("declared_mem_free_mib"),
            "local_cluster": cluster.get("local_cluster"),
            "max_node_cpu_free_m": cluster.get("max_node_cpu_free_m"),
            "max_node_mem_free_mib": cluster.get("max_node_mem_free_mib"),
        },
    }


# ── Arbitre : le serveur Kubernetes ─────────────────────────────────────────

_ERR_PATTERNS = [  # l'ordre compte : du plus spécifique au plus général
    (r"no matches for kind|could not find the requested resource|"
     r"the server could not find", "groupe d'API incorrect"),
    (r"unknown field|strict decoding error", "champ inconnu"),
    (r"Required value", "champ requis manquant"),
    (r"Invalid value|Unsupported value|must be", "valeur invalide"),
    (r"namespaces? \"[^\"]+\" not found", "namespace absent"),
    (r"error converting YAML|yaml:|json:", "YAML illisible"),
    (r"forbidden|Forbidden", "refusé par le serveur"),
]


def classify(stderr: str) -> str:
    for pat, label in _ERR_PATTERNS:
        if re.search(pat, stderr):
            return label
    return "autre"


def _run_dry(text: str, strict: bool) -> subprocess.CompletedProcess:
    args = ["kubectl", "apply", "--dry-run=server", "-f", "-"]
    if strict:
        args.insert(3, "--validate=strict")
    return subprocess.run(args, input=text, capture_output=True, text=True, timeout=40)


def server_dry_run(docs: list[dict], ctx: dict) -> dict:
    """`kubectl apply --dry-run=server`. Gère les namespaces créés par le lot lui-même."""
    existing = set(ctx.get("cluster", {}).get("namespaces", []))
    kinds = ctx.get("api_kinds") or {}
    new_ns = {d["metadata"]["name"] for d in docs if d.get("kind") == "Namespace"} - existing
    notes = []
    test_docs = []
    for d in docs:
        dd = copy.deepcopy(d)
        ns = (dd.get("metadata") or {}).get("namespace")
        namespaced = kinds.get(dd.get("kind"), {}).get("namespaced", True)
        # Un namespace créé par ce même lot n'existe pas encore côté serveur :
        # on valide le schéma de ces objets dans 'default'.
        if namespaced and ns in new_ns:
            dd["metadata"]["namespace"] = "default"
            notes.append(f"{dd['kind']}/{dd['metadata']['name']} validé dans « default » "
                         f"(le namespace « {ns} » sera créé par ce manifeste)")
        test_docs.append(dd)
    text = yaml.safe_dump_all(test_docs, sort_keys=False, allow_unicode=True)

    try:
        r = _run_dry(text, strict=True)
        if r.returncode != 0 and "validate" in r.stderr and "strict" in r.stderr:
            r = _run_dry(text, strict=False)   # kubectl trop ancien pour --validate=strict
    except FileNotFoundError:
        return {"ok": None, "error": "kubectl introuvable", "category": "indisponible",
                "actions": [], "notes": notes}
    except subprocess.TimeoutExpired:
        return {"ok": None, "error": "le serveur n'a pas répondu (40 s)",
                "category": "indisponible", "actions": [], "notes": notes}

    actions = []
    for line in r.stdout.splitlines():
        m = re.match(r"(\S+)\s+(created|configured|unchanged)", line.strip())
        if m:
            actions.append({"resource": m.group(1), "action": m.group(2)})
    if r.returncode == 0:
        return {"ok": True, "error": None, "category": None, "actions": actions, "notes": notes}
    unreachable = re.search(r"connection refused|Unable to connect|no such host|"
                            r"i/o timeout|dial tcp", r.stderr)
    return {"ok": None if unreachable else False,
            "error": r.stderr.strip()[:1500],
            "category": "cluster injoignable" if unreachable else classify(r.stderr),
            "actions": actions, "notes": notes}


def validate_text(text: str, ctx: dict) -> dict:
    """Validation complète d'un texte YAML (utilisée aussi après édition manuelle)."""
    docs, parse_err = parse_docs(text)
    if parse_err:
        return {"valid": False, "server_validated": False,
                "errors": [{"source": "lecture", "category": "YAML illisible", "message": parse_err}],
                "warnings": [], "capacity": None, "dry_run": None}
    checks = static_checks(docs, ctx)
    dry = server_dry_run(docs, ctx)
    errors = list(checks["errors"])
    if dry["ok"] is False:
        errors.insert(0, {"source": "serveur Kubernetes", "category": dry["category"],
                          "message": dry["error"]})
    warnings = checks["warnings"] + dry["notes"]
    if dry["ok"] is None:
        warnings.insert(0, f"Validation serveur impossible : {dry['error']}")
    return {
        "valid": dry["ok"] is True and not errors,
        "server_validated": dry["ok"],
        "errors": errors, "warnings": warnings,
        "capacity": checks["capacity"], "dry_run": dry,
        "kinds": [f"{d['kind']}/{d['metadata']['name']}" for d in docs],
    }


# ── Boucle de génération ────────────────────────────────────────────────────

def _feedback(prev_yaml: str, check: dict) -> str:
    lines = ["\n\n## Ta tentative précédente a été REJETÉE", "```yaml", prev_yaml.strip(), "```",
             "Erreurs constatées par les arbitres (serveur Kubernetes et contrôle de capacité) :"]
    for e in check["errors"]:
        lines.append(f"- [{e['source']} · {e['category']}] {e['message']}")
    lines.append("Corrige ces erreurs précisément et soumets le manifeste complet corrigé.")
    return "\n".join(lines)


def _log(entry: dict) -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _context_summary(ctx: dict) -> dict:
    c, h = ctx.get("cluster", {}), ctx.get("host") or {}
    return {
        "host": h,
        "server_version": c.get("server_version"),
        "schedulable_nodes": c.get("schedulable_nodes"),
        "cluster_free_cpu_m": c.get("total_cpu_free_m"),
        "cluster_free_mem_mib": c.get("total_mem_free_mib"),
        "max_node_cpu_free_m": c.get("max_node_cpu_free_m"),
        "max_node_mem_free_mib": c.get("max_node_mem_free_mib"),
        "namespaces": len(c.get("namespaces", [])),
        "storage_classes": [s["name"] for s in c.get("storage_classes", [])],
        "api_kinds_known": len(ctx.get("api_kinds") or {}),
        "oom_risks": len(ctx.get("oom_risks") or []),
        "errors": ctx.get("errors"),
    }


async def generate(description: str, provider: Optional[str] = None,
                   max_attempts: int = MAX_ATTEMPTS, use_context: bool = True,
                   campaign: Optional[str] = None, repair: bool = False) -> dict:
    """repair=True : avant de renvoyer un manifeste rejeté au modèle, la réparation
    déterministe (manifest_repair) corrige ce qui se calcule ; les arbitres jugent le
    résultat. Le manifeste d'origine du modèle reste dans « yaml_model » de la tentative."""
    t0 = time.time()
    gen_id = uuid.uuid4().hex[:12]
    ctx = await host_context.collect(force=True)
    # Ablation (mesure C1) : sans le contexte, le modèle ne reçoit que la demande.
    # Les arbitres (dry-run serveur, capacité réelle) restent identiques.
    context_block = host_context.to_prompt(ctx) if use_context else \
        "## Contexte\nAucune information sur le cluster n'est fournie."
    base_user = (f"{context_block}\n\n## Demande de l'utilisateur\n"
                 f"{description.strip()}")
    user = base_user
    attempts: list[dict[str, Any]] = []
    last_out: dict = {}
    last_check: dict = {}
    loop = asyncio.get_running_loop()

    for n in range(1, max(1, min(max_attempts, 5)) + 1):
        out = await llm_client.generate_manifest(SYSTEM_PROMPT, user, provider)
        if not out["yaml"].strip():
            refused = out.get("stop_reason") == "refusal"
            check = {"valid": False, "server_validated": False, "warnings": [],
                     "capacity": None, "dry_run": None, "kinds": [],
                     "errors": [{"source": "modèle",
                                 "category": "refus du modèle" if refused else "réponse vide",
                                 "message": (f"Aucun manifeste reçu (stop_reason={out.get('stop_reason')}, "
                                             f"blocs={out.get('block_types')})")}]}
        else:
            check = await loop.run_in_executor(None, validate_text, out["yaml"], ctx)
        repair_info = None
        if repair and out["yaml"].strip() and not check["valid"] and check.get("server_validated") is not None:
            from ai_module import manifest_repair
            r_yaml, r_check, r_changes = out["yaml"], check, []
            for _ in range(2):          # 2e passage : champs inconnus révélés par le 1er dry-run
                r = manifest_repair.repair(r_yaml, r_check, ctx)
                if not r["changes"]:
                    break
                r_changes += r["changes"]
                r_yaml = r["yaml"]
                r_check = await loop.run_in_executor(None, validate_text, r_yaml, ctx)
                if r_check["valid"]:
                    break
            if r_changes:
                better = r_check["valid"] or len(r_check["errors"]) <= len(check["errors"])
                repair_info = {"changes": r_changes, "valid_after": r_check["valid"], "kept": better,
                               "errors_before": [e["category"] for e in check["errors"]]}
                if better:
                    out = {**out, "yaml_model": out["yaml"], "yaml": r_yaml}
                    check = r_check
        attempts.append({
            "n": n, "valid": check["valid"],
            "server_validated": check.get("server_validated"),
            "errors": check["errors"], "warnings": check["warnings"],
            "latency_ms": out.get("latency_ms"), "usage": out.get("usage"),
            "stop_reason": out.get("stop_reason"), "yaml": out["yaml"],
            "repair": repair_info, "yaml_model": out.get("yaml_model"),
        })
        last_out, last_check = out, check
        unreachable = bool(check.get("dry_run")) and check["dry_run"].get("ok") is None
        if check["valid"] or unreachable:
            break   # validé, ou serveur Kubernetes injoignable : inutile d'insister
        if not out["yaml"].strip():
            user = base_user + ("\n\n## Ta réponse précédente ne contenait aucun manifeste.\n"
                                "Appelle l'outil submit_manifest avec le YAML complet.")
        else:
            user = base_user + _feedback(out["yaml"], check)

    result = {
        "generation_id": gen_id,
        "yaml": last_out.get("yaml", ""),
        "valid": last_check.get("valid", False),
        "server_validated": last_check.get("server_validated"),
        "attempts_used": len(attempts),
        "max_attempts": max_attempts,
        "attempts": attempts,
        "reasoning": [{"step": i, **r} for i, r in enumerate(last_out.get("reasoning", []), 1)
                      if isinstance(r, dict)],
        "warnings": list(dict.fromkeys((last_out.get("warnings") or [])
                                       + last_check.get("warnings", []))),
        "errors": last_check.get("errors", []),
        "capacity": last_check.get("capacity"),
        "dry_run": last_check.get("dry_run"),
        "kinds": last_check.get("kinds", []),
        "provider": last_out.get("provider"),
        "model_used": last_out.get("model"),
        "context": _context_summary(ctx),
        "duration_ms": round((time.time() - t0) * 1000),
        "repair_enabled": repair,
        "repaired": bool(attempts and attempts[-1].get("repair") and attempts[-1]["repair"]["kept"]),
        "repair_changes": (attempts[-1].get("repair") or {}).get("changes", []) if attempts else [],
    }
    # Écarts possibles avec la demande (conteneur non demandé, port, objet absent) :
    # avertissements seulement, le verdict « valide » n'est pas modifié.
    try:
        from ai_module.intent_checks import intent_warnings
        final_docs, _ = parse_docs(result["yaml"])
        result["intent_warnings"] = intent_warnings(description, final_docs) if final_docs else []
    except Exception as e:  # noqa: BLE001 — un avertissement ne doit jamais casser la génération
        result["intent_warnings"] = []
        result["warnings"].append(f"Contrôle de l'intention non effectué : {str(e)[:120]}")

    _log({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "id": gen_id,
        "use_context": use_context, "campaign": campaign, "max_attempts": max_attempts,
        "description": description, "provider": result["provider"], "model": result["model_used"],
        "attempts": len(attempts), "valid": result["valid"],
        "valid_first_try": bool(attempts and attempts[0]["valid"]),
        "server_validated": result["server_validated"],
        "error_categories": [[e["category"] for e in a["errors"]] for a in attempts],
        "kinds": result["kinds"], "duration_ms": result["duration_ms"],
        "tokens": [a.get("usage") for a in attempts],
        "repair_enabled": repair,
        "repairs": [a.get("repair") for a in attempts],
        "intent_warnings": result.get("intent_warnings", []),
    })
    return result


def stats() -> dict:
    """Agrégat du journal : taux de réussite au 1er essai et après correction."""
    if not os.path.exists(LOG_PATH):
        return {"generations": 0}
    rows = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows = [r for r in rows if r.get("server_validated") is not None]
    if not rows:
        return {"generations": 0}
    cats: dict[str, int] = {}
    for r in rows:
        for att in r.get("error_categories", []):
            for c in att:
                cats[c] = cats.get(c, 0) + 1
    n = len(rows)
    return {
        "generations": n,
        "valid_first_try": sum(r["valid_first_try"] for r in rows),
        "valid_final": sum(r["valid"] for r in rows),
        "rate_first_try": round(sum(r["valid_first_try"] for r in rows) / n, 3),
        "rate_final": round(sum(r["valid"] for r in rows) / n, 3),
        "mean_attempts": round(sum(r["attempts"] for r in rows) / n, 2),
        "error_categories": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
        "by_model": {m: sum(1 for r in rows if r.get("model") == m)
                     for m in {r.get("model") for r in rows}},
    }

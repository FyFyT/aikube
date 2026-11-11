#!/usr/bin/env python3
"""
scripts/build_corpus_v3.py — Corpus d'entraînement de KubeLLM v2 (fine-tuning)
================================================================================
Pourquoi un nouveau corpus (le v1 et le v2 restent intacts) :
  1. FORMAT : le modèle doit apprendre la tâche de l'application — état de l'hôte
     et du cluster + demande → manifeste qui tient sur CETTE machine. Le corpus v1
     associait une instruction seule à une ressource isolée.
  2. VÉRIFICATION : chaque exemple n'entre au corpus qu'après les deux arbitres de
     l'application (dry-run du serveur Kubernetes réel + contrôle de capacité
     contre le contexte montré). 14/112 paires du v1 étaient inapplicables.
  3. ENSEIGNANT : llama-3.3-70b (enseignant du v1) a été retiré par Groq le
     16/08/2026 ; l'enseignant est openai/gpt-oss-120b (poids ouverts, Apache 2.0).
     Aucune sortie de Claude n'entre dans le corpus.
  4. ÉVALUATION PROPRE : les 36 demandes du banc C1 (scripts/campaign_yaml.py) sont
     EXCLUES ; toute demande générée trop proche de l'une d'elles est écartée.

Contextes : profils SYNTHÉTIQUES variés (minikube 1–3 nœuds, kind, cloud 3–6 nœuds,
RAM 8–32 Gio, classes de stockage…) pour que le modèle apprenne à LIRE le contexte
et non à mémoriser une machine. Les versions d'API sont celles du cluster réel.

Mesure secondaire (mémoire) : fiabilité de l'enseignant au 1er essai et après
correction, par catégorie → data/observations/corpus_v3_log.jsonl.

USAGE (terminal Ubuntu WSL, dans ~/aikube, cluster démarré, GROQ_API_KEY ou CEREBRAS_API_KEY dans .env)
  python3 scripts/build_corpus_v3.py --plan-only          # voir le plan, sans appel
  python3 scripts/build_corpus_v3.py --n 20               # essai
  python3 scripts/build_corpus_v3.py --n 500              # corpus complet
  Offre gratuite Groq (200 000 jetons/jour) : le script s'arrête à la limite du jour
  Cerebras (1 000 000 jetons/jour, 5 requêtes/min) : --provider cerebras
  OpenRouter (payant à l'usage, sans plafond quotidien) : --provider openrouter --workers 4
  et reprend où il en était au lancement suivant. Offre payante : --tpm 0.

COMPLÉMENT v4 (KubeLLM v3) — exemples de CAPACITÉ supplémentaires, dans des fichiers NOUVEAUX
(le corpus v3 n'est pas modifié) :
  python3 scripts/build_corpus_v3.py --provider cerebras --tag v4cap --seed 43 --n 250 --categories capacite_plus
  → data/datasets/corpus_v4cap.jsonl + data/observations/corpus_v4cap_log.jsonl
  Dans ce mode, l'enseignant doit écrire le CALCUL dans chaque avertissement de réduction,
  et un quart des demandes TIENNENT dans la machine (le modèle ne doit pas réduire par réflexe).
================================================================================
"""
import argparse
import asyncio
import copy
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

OUT = os.path.join(ROOT, "data", "datasets", "corpus_v3.jsonl")
LOG = os.path.join(ROOT, "data", "observations", "corpus_v3_log.jsonl")
NS_POOL = ["production", "staging", "monitoring", "data", "web"]

# ── Applications (images à tag précis) ──────────────────────────────────────
APPS = [
    ("web", "nginxinc/nginx-unprivileged:1.27", 8080), ("nginx", "nginx:1.27", 80),
    ("api", "python:3.12-slim", 8000), ("flask-api", "python:3.12-slim", 5000),
    ("node-app", "node:20-alpine", 3000), ("redis", "redis:7.2", 6379),
    ("postgres", "postgres:16", 5432), ("mongodb", "mongo:7.0", 27017),
    ("rabbitmq", "rabbitmq:3.13-management", 5672), ("grafana", "grafana/grafana:11.0.0", 3000),
    ("prometheus", "prom/prometheus:v2.53.0", 9090), ("memcached", "memcached:1.6", 11211),
    ("worker", "python:3.12-slim", None), ("httpd", "httpd:2.4", 80),
    ("mysql", "mysql:8.4", 3306), ("minio", "minio/minio:RELEASE.2024-06-13T22-53-53Z", 9000),
]


def pick_app(rng, need_port=False):
    while True:
        a = rng.choice(APPS)
        if a[2] or not need_port:
            return a


# ── Demandes (catégorie, fabrique) ──────────────────────────────────────────
def T(rng, prof):
    """Renvoie (catégorie, demande)."""
    name, img, port = pick_app(rng, need_port=True)
    ns = rng.choice(["default"] * 3 + NS_POOL)
    in_ns = "" if ns == "default" else rng.choice([f" dans le namespace {ns}", f" (namespace {ns})", f" dans {ns}"])
    n = rng.choice([1, 2, 2, 3, 3, 4, 5])
    cat = rng.choices(
        ["workload", "stateful", "batch", "network", "autoscaling", "config", "rbac",
         "daemonset", "namespace", "security", "capacite", "english"],
        weights=[22, 9, 9, 9, 7, 9, 6, 4, 4, 5, 13, 3])[0]
    c = prof["cluster"]
    big_mem = max(1, round(c["max_node_mem_free_mib"] / 1024 * rng.choice([1.5, 2, 3, 4])))
    big_cpu = max(2, round(c["max_node_cpu_free_m"] / 1000 * rng.choice([1.5, 2, 4])))
    choices = {
        "workload": [
            f"Déployer {name} ({img}) avec {n} répliques, exposé sur le port {port}{in_ns}",
            f"Je veux {name} en {n} exemplaires derrière un Service ClusterIP{in_ns}",
            f"Deployment {name} avec sondes readiness et liveness sur le port {port}{in_ns}",
            f"Mettre en ligne {name} avec un Service NodePort{in_ns}",
            f"Déployer {name} avec les variables APP_ENV=prod et LOG_LEVEL=info{in_ns}",
            f"Application {name}, image {img}, {n} replicas, stratégie RollingUpdate maxUnavailable 0",
        ],
        "stateful": [
            f"StatefulSet {name} avec un volume persistant de {rng.choice([1, 2, 5, 10])}Gi{in_ns}",
            f"Base {name} en StatefulSet de {rng.choice([1, 3])} répliques avec Service headless{in_ns}",
            f"PVC de {rng.choice([1, 2, 5])}Gi monté dans un pod {name} sur /data{in_ns}",
        ],
        "batch": [
            f"CronJob qui lance une sauvegarde de {name} tous les jours à {rng.choice([1, 2, 3, 4])}h{in_ns}",
            f"Job unique de migration pour {name} avec 2 tentatives maximum{in_ns}",
            f"CronJob toutes les {rng.choice([5, 10, 30])} minutes, sans exécutions concurrentes, image busybox:1.36",
            f"Job parallèle : {rng.choice([3, 4, 6])} exécutions, {rng.choice([2, 3])} en même temps, image busybox:1.36",
        ],
        "network": [
            f"NetworkPolicy qui bloque tout le trafic entrant{in_ns or ' dans default'}",
            f"Autoriser seulement app=frontend à joindre {name} sur le port {port}{in_ns}",
            f"Ingress pour {name} sur l'hôte {name}.local, chemin /{rng.choice(['', 'api', 'v1'])}{in_ns}",
            f"Exposer {name} à l'extérieur via un Ingress{in_ns}",
            f"Autoriser le trafic sortant de {name} uniquement vers le DNS du cluster{in_ns}",
            f"Bloquer tout le trafic sortant{in_ns or ' de default'} sauf vers app=db sur le port 5432",
        ],
        "autoscaling": [
            f"HPA pour le deployment {name} : de {rng.choice([1, 2])} à {rng.choice([5, 8, 10])} répliques à {rng.choice([60, 70, 80])} % de CPU{in_ns}",
            f"Deployment {name} et son autoscaler sur la mémoire{in_ns}",
            f"PodDisruptionBudget : au moins {rng.choice([1, 2])} pods app={name} toujours disponibles{in_ns}",
        ],
        "config": [
            f"ConfigMap de configuration pour {name} montée en fichier dans son Deployment{in_ns}",
            f"Secret avec un login et un mot de passe pour {name}, injecté en variables d'environnement{in_ns}",
            f"ResourceQuota pour {ns} : {rng.choice([2, 4, 8])} CPU, {rng.choice([4, 8, 16])}Gi, {rng.choice([10, 20])} pods",
            f"LimitRange avec des valeurs par défaut de {rng.choice([100, 250, 500])}m CPU et {rng.choice([128, 256, 512])}Mi{in_ns}",
        ],
        "rbac": [
            f"ServiceAccount {name}-sa avec un Role en lecture sur les pods et le RoleBinding{in_ns}",
            f"Rôle permettant de gérer les ConfigMaps{in_ns}, lié à l'utilisateur dev",
            "ClusterRole de lecture des nœuds liée au ServiceAccount monitoring du namespace default",
            f"Donner au compte de service ci-bot le droit de redémarrer les deployments{in_ns}",
            f"Compte de service pour {name} sans montage automatique du jeton{in_ns}",
            f"Permettre au compte backup de lire les secrets{in_ns}, rien d'autre",
        ],
        "daemonset": [
            "DaemonSet de collecte de logs fluent-bit (fluent/fluent-bit:3.0) sur chaque nœud",
            "Agent de supervision node-exporter (prom/node-exporter:v1.8.1) en DaemonSet",
            f"Un agent {name}-agent par machine du cluster, image busybox:1.36, qui écrit l'heure chaque minute",
            "Un pod par machine, y compris le control-plane (tolérance), image busybox:1.36",
            "Collecteur qui lit /var/log de chaque machine en lecture seule, image busybox:1.36",
        ],
        "namespace": [
            f"Créer le namespace {rng.choice(['qa', 'demo', 'team-a', 'sandbox'])} et y déployer {name} avec {n} répliques",
        ],
        "security": [
            f"Deployment {name} durci : non root, système de fichiers en lecture seule, aucune capacité Linux",
            f"Pod {name} avec seccomp RuntimeDefault et allowPrivilegeEscalation à false{in_ns}",
            f"Exécuter {name} avec runAsUser 1000 et fsGroup 2000{in_ns}",
            f"Conteneur {name} sans aucune capacité Linux sauf NET_BIND_SERVICE{in_ns}",
        ],
        "capacite": [
            f"{name} avec {big_mem}Gi de RAM{in_ns}",
            f"{n + 3} répliques de {name} avec {max(1, big_mem // 2)}Gi chacune",
            f"Job de calcul qui demande {big_cpu} CPU et {big_mem}Gi",
            f"{name} avec le maximum de mémoire possible sur ce cluster",
            f"DaemonSet demandant {big_cpu} CPU par nœud",
        ],
        "english": [
            f"Deploy {name} with {n} replicas and a ClusterIP service on port {port}",
            f"Create a CronJob that runs every hour with busybox:1.36",
            f"NetworkPolicy allowing only app=frontend to reach {name}",
        ],
    }
    return cat, rng.choice(choices[cat])


def T_cap(rng, prof):
    """Demandes de capacité (complément v4). Trois quarts DÉPASSENT la machine (au niveau du
    nœud ou du cluster), un quart TIENT de justesse : l'élève doit apprendre à calculer,
    pas à réduire systématiquement. Renvoie (catégorie, demande)."""
    name, img, port = pick_app(rng, need_port=False)
    ns = rng.choice(["default"] * 3 + NS_POOL)
    in_ns = "" if ns == "default" else rng.choice([f" dans le namespace {ns}", f" (namespace {ns})", f" dans {ns}"])
    c = prof["cluster"]
    node_mem, tot_mem = c["max_node_mem_free_mib"], c["total_mem_free_mib"]
    node_cpu, tot_cpu = c["max_node_cpu_free_m"], c["total_cpu_free_m"]
    gi = lambda mib_: max(1, round(mib_ / 1024))
    if rng.random() < 0.75:
        reps = rng.choice([3, 4, 5, 6, 8, 10])
        per_gi = max(1, int(tot_mem * rng.uniform(1.3, 2.5) / reps / 1024)) if gi(node_mem) > 1 else 1
        big = gi(node_mem * rng.choice([1.5, 2, 3, 4]))
        req = rng.choice([
            f"{name} avec {big}Gi de RAM{in_ns}",
            f"{reps} répliques de {name} avec {per_gi}Gi de mémoire chacune{in_ns}",
            f"StatefulSet {name} de {rng.choice([3, 5])} répliques, {big}Gi et {rng.choice([1, 2])} CPU par réplique{in_ns}",
            f"Job de calcul de {rng.choice([2, 4, 8, 16])} CPU et {big}Gi de RAM{in_ns}",
            f"DaemonSet {name}-agent qui réserve {gi(node_mem * rng.uniform(1.2, 2))}Gi sur chaque nœud",
            f"{name} avec une request de {int(node_cpu * rng.uniform(1.3, 3)) // 100 * 100}m CPU{in_ns}",
            f"Deploy {name} with {reps} replicas of {per_gi}Gi memory each",
            f"{name} : {reps} pods de {per_gi}Gi, il faut qu'ils démarrent tous{in_ns}",
        ])
        return "capacite", req
    reps = rng.choice([2, 3, 4])
    fit_mib = max(64, int(min(node_mem, tot_mem / reps) * rng.uniform(0.3, 0.6)) // 64 * 64)
    req = rng.choice([
        f"{name} avec {fit_mib}Mi de RAM{in_ns}",
        f"{reps} répliques de {name} à {fit_mib}Mi chacune{in_ns}",
        f"{name} avec {int(node_cpu * rng.uniform(0.3, 0.6)) // 50 * 50}m de CPU et {fit_mib}Mi{in_ns}",
        f"Le plus gros {name} qui tienne sur un seul nœud{in_ns}",
    ])
    return "capacite_ok", req


def build_plan(seed, n, real, holdout, thr, mode="all", tag="v3"):
    """Plan déterministe (même graine → mêmes profils, mêmes demandes, mêmes identifiants).
    Le mode « all » reproduit EXACTEMENT le plan du corpus v3 : build_corrections_v4.py
    s'en sert pour retrouver le profil de chaque exemple."""
    rng = random.Random(seed)
    plan, rejected_close, seen, i = [], 0, set(), 0
    while len(plan) < n:
        prof = make_profile(rng, real, i)
        cat, req = (T_cap if mode == "capacite_plus" else T)(rng, prof)
        i += 1
        if req in seen:
            continue
        if too_close(req, holdout, thr):
            rejected_close += 1
            continue
        seen.add(req)
        plan.append({"id": f"{tag}-{seed}-{len(plan):04d}", "category": cat, "request": req, "profile": prof})
    return plan, rejected_close


# ── Profils de machine synthétiques ─────────────────────────────────────────
# Verbes de RÉDUCTION seulement : « capacité » ou « tient » apparaissent aussi dans de simples
# justifications (« les requests tiennent dans la capacité libre »), qui ne sont pas des réductions.
REDUCTION_WORDS = ("rédui", "réduction", "abaiss", "ramené", "ramenée", "diminu", "au lieu de",
                   "plafonn", "reduc", "lowered", "instead of", "limité à", "limitée à")


def reduction_warnings(warnings):
    """Seuls les avertissements de RÉDUCTION (capacité) sont appris. Le 1er essai réel
    (26/10) : 18 exemples sur 18 portaient un avertissement, souvent sans rapport ;
    le modèle aurait appris à en écrire un systématiquement."""
    return [w for w in warnings or [] if any(k in w.lower() for k in REDUCTION_WORDS)]


def make_profile(rng, real, i):
    kind = rng.choices(["minikube", "kind", "cloud"], weights=[50, 15, 35])[0]
    local = kind != "cloud"
    cpu_count = rng.choice([2, 4, 4, 8, 12, 16])
    ram_total = rng.choice([8192, 12288, 16384, 16384, 32768])
    ram_avail = int(ram_total * rng.uniform(0.3, 0.8))
    host = {"os": "Linux (WSL2)" if rng.random() < .5 else "Linux", "cpu_count": cpu_count,
            "ram_total_mib": ram_total, "ram_available_mib": ram_avail,
            "disk_free_gib": rng.choice([20, 45, 80, 200]),
            "load_avg_1m": round(cpu_count * rng.uniform(0.1, 2.2), 2)}
    n_nodes = rng.choice([1, 2, 3]) if local else rng.choice([3, 3, 4, 5, 6])
    nodes = []
    for k in range(n_nodes):
        if local:
            ca, ma = cpu_count * 1000, ram_total - rng.choice([200, 400])
        else:
            ca = rng.choice([2000, 4000, 4000, 8000]) - 100
            ma = rng.choice([4096, 8192, 8192, 16384, 32768]) - 600
        cf = int(ca * rng.uniform(0.25, 0.9)) // 10 * 10
        mf = int(ma * rng.uniform(0.25, 0.9))
        nm = (f"{kind}" if k == 0 else f"{kind}-m0{k + 1}") if local else f"node-{chr(97 + k)}"
        nodes.append({"name": nm, "ready": True, "schedulable": True, "cpu_allocatable_m": ca,
                      "cpu_free_m": cf, "mem_allocatable_mib": ma, "mem_free_mib": mf,
                      "usage": {"cpu_used_pct": rng.randint(3, 70), "mem_used_pct": rng.randint(5, 70)}})
    sum_free = sum(n["mem_free_mib"] for n in nodes)
    max_free = max(n["mem_free_mib"] for n in nodes)
    eff, eff_max = (min(sum_free, ram_avail), min(max_free, ram_avail)) if local else (sum_free, max_free)
    if local:
        sc = [{"name": "standard", "default": True}]
    else:
        sc = rng.choice([[{"name": "standard", "default": True}],
                         [{"name": "gp3", "default": True}, {"name": "io2", "default": False}],
                         [{"name": "fast-ssd", "default": False}, {"name": "standard", "default": True}],
                         []])
    namespaces = sorted(["default", "kube-system", "kube-public", "kube-node-lease"]
                        + rng.sample(NS_POOL, rng.randint(0, len(NS_POOL))))
    return {"profile_id": f"p{i:04d}-{kind}",
            "host": host,
            "cluster": {"server_version": real["cluster"].get("server_version") or "v1.28.0",
                        "nodes": nodes, "schedulable_nodes": n_nodes,
                        "total_cpu_free_m": sum(n["cpu_free_m"] for n in nodes),
                        "total_mem_free_mib": eff, "declared_mem_free_mib": sum_free,
                        "local_cluster": local, "context": kind if local else f"prod-{rng.choice(['eu', 'us', 'af'])}",
                        "max_node_cpu_free_m": max(n["cpu_free_m"] for n in nodes),
                        "max_node_mem_free_mib": eff_max, "declared_max_node_mem_free_mib": max_free,
                        "namespaces": namespaces, "storage_classes": sc,
                        "ingress_classes": rng.choice([[], ["nginx"]]), "metrics_available": True},
            "api_kinds": real.get("api_kinds") or {}, "oom_risks": [], "errors": {}}


# ── Proximité avec le banc d'évaluation C1 ──────────────────────────────────
def words(s):
    return set(re.findall(r"[a-zà-ü0-9]+", s.lower())) - {"de", "la", "le", "les", "un", "une", "avec", "et", "sur", "dans", "des", "du", "en", "à"}


def too_close(req, holdout, thr):
    w = words(req)
    return any(len(w & h) / max(1, len(w | h)) >= thr for h in holdout)


def namespace_errors(yaml_text, prof):
    """Le dry-run valide contre le cluster RÉEL (où tous les namespaces du jeu existent).
    Un exemple doit rester cohérent avec le contexte MONTRÉ : un namespace absent du
    profil doit être créé par le manifeste."""
    import yaml as _y
    try:
        docs = [d for d in _y.safe_load_all(yaml_text) if isinstance(d, dict)]
    except _y.YAMLError:
        return []
    known = set(prof["cluster"]["namespaces"]) | {d.get("metadata", {}).get("name")
                                                  for d in docs if d.get("kind") == "Namespace"}
    errs = []
    for d in docs:
        ns = (d.get("metadata") or {}).get("namespace")
        if ns and ns not in known:
            errs.append({"source": "contexte", "category": "namespace absent",
                         "message": f"{d.get('kind')}/{d.get('metadata', {}).get('name')} : le namespace "
                                    f"« {ns} » n'existe pas sur ce cluster ; ajoute un document Namespace."})
    return errs


# ── Appel de l'enseignant ───────────────────────────────────────────────────
class RateLimited(Exception):
    def __init__(self, wait, msg):
        super().__init__(msg)
        self.wait = wait


def _retry_after(e) -> float:
    try:
        h = e.response.headers.get("retry-after")
        if h:
            return float(h)
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s", str(e))
    if m:
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3))
    return 60.0


_JSON_MODE = {"ok": True}


async def teacher(client, model, system, user):
    kw = dict(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
              temperature=0.4, max_tokens=6000)
    try:
        try:
            r = await client.chat.completions.create(
                **kw, **({"response_format": {"type": "json_object"}} if _JSON_MODE["ok"] else {}))
        except Exception as e:  # noqa: BLE001  fournisseur sans mode JSON : texte libre
            if getattr(e, "status_code", None) == 400 and "response_format" in str(e):
                _JSON_MODE["ok"] = False
                r = await client.chat.completions.create(**kw)
            else:
                raise
    except Exception as e:  # noqa: BLE001
        if getattr(e, "status_code", None) == 429 or "rate_limit" in str(e).lower():
            raise RateLimited(_retry_after(e), str(e)[:300])
        raise
    content = r.choices[0].message.content or ""
    try:
        data = json.loads(re.sub(r"```json|```", "", content).strip())
    except json.JSONDecodeError:
        data = {"yaml": content, "warnings": []}
    u = getattr(r, "usage", None)
    return {"yaml": str(data.get("yaml") or ""), "warnings": [str(w) for w in data.get("warnings") or []][:4],
            "tokens": (u.prompt_tokens + u.completion_tokens) if u else 0}


def done_ids():
    ids = set()
    if os.path.exists(LOG):
        for line in open(LOG, encoding="utf-8"):
            try:
                ids.add(json.loads(line)["id"])
            except (ValueError, KeyError):
                pass
    return ids


async def run(a):
    from ai_module import host_context, yaml_pipeline as Y
    from ai_module.kubellm_prompt import STUDENT_SYSTEM, student_target, student_user
    from campaign_yaml import PROMPTS as C1
    host_context.host_history = lambda *x, **k: {"available": False}   # profils synthétiques

    real = await host_context.collect(force=True)
    if not real.get("api_kinds"):
        sys.exit("Cluster injoignable : démarre minikube puis relance (kubectl get nodes).")
    holdout = [words(p[2]) for p in C1]
    plan, rejected_close = build_plan(a.seed, a.n, real, holdout, a.holdout_threshold, a.categories, a.tag)

    print(f"Plan : {len(plan)} exemples ({Counter(p['category'] for p in plan).most_common()})")
    print(f"Demandes écartées car trop proches du banc C1 : {rejected_close}")
    if a.plan_only:
        ex = plan[0]
        print("\n--- Exemple d'entrée du modèle ---\n" + student_user(host_context.to_prompt(ex["profile"]), ex["request"]))
        return

    var = PROVIDERS[a.provider]["key"]
    key = os.environ.get(var)
    if not key and os.path.exists(os.path.join(ROOT, ".env")):
        for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
            if line.startswith(var + "="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit(f"{var} introuvable : ajoute la ligne {var}=… dans ~/aikube/.env")
    if a.provider == "groq":
        from groq import AsyncGroq
        client = AsyncGroq(api_key=key)
    else:                           # API compatible OpenAI (Cerebras…)
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=key, base_url=PROVIDERS[a.provider]["base_url"])
    a.model = a.model or PROVIDERS[a.provider]["model"]
    a.rpm = a.rpm if a.rpm is not None else PROVIDERS[a.provider]["rpm"]
    a.tpm = a.tpm if a.tpm is not None else PROVIDERS[a.provider]["tpm"]
    teacher_id = f"{a.provider}/{a.model}"
    print(f"Enseignant : {teacher_id} (mêmes poids ouverts quel que soit le fournisseur)")
    last_call = [0.0]

    for ns in NS_POOL:      # namespaces des profils, pour la validation par le serveur
        subprocess.run(["kubectl", "create", "namespace", ns], capture_output=True, text=True, timeout=45)

    # La règle 7 de l'application (« choices » : 3 à 5 justifications) est retirée : remplacée
    # par « warnings », elle produisait un avertissement dans 18 exemples sur 18 (essai du 26/10).
    system = re.sub(r"\n7\. Dans « choices ».*$", "", Y.SYSTEM_PROMPT, flags=re.S) + (
        "\n\nRéponds UNIQUEMENT par un objet JSON, sans markdown : "
        '{"yaml": "<manifeste complet, documents séparés par --->", '
        '"warnings": []}  — warnings reste VIDE sauf si tu as dû réduire ou modifier la demande '
        'pour la faire tenir dans la capacité ; dans ce cas, une phrase par réduction.')
    system_cap = system + (
        "\n\nCAPACITÉ : compare la demande à la capacité LIBRE du contexte (par nœud ET pour tout le "
        "cluster, RAM de l'hôte comprise pour un cluster local). Si elle tient, ne réduis rien et "
        "laisse warnings vide. Si elle ne tient pas, réduis au plus juste et écris le CALCUL dans "
        "l'avertissement, par exemple : « mémoire réduite de 8Gi à 2Gi : le nœud le plus libre n'a "
        "que 2600Mi » ou « 6 répliques × 2Gi = 12Gi > 9000Mi libres : réduit à 1400Mi par réplique ».")
    already = done_ids()
    todo = [p for p in plan if p["id"] not in already]
    print(f"À faire : {len(todo)} (déjà traités : {len(plan) - len(todo)})\n")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    window = []          # (instant, jetons) sur la dernière minute
    queue = list(enumerate(todo, 1))
    stop = asyncio.Event()
    pace = asyncio.Lock()
    counters = {"ok": 0}
    loop = asyncio.get_running_loop()

    async def throttle():
        async with pace:                    # le rythme est commun à tous les travailleurs
            if a.tpm:
                while sum(t for s_, t in window if time.time() - s_ < 60) > a.tpm:
                    await asyncio.sleep(3)
            if a.rpm:                       # requêtes par minute (Cerebras gratuit : 5)
                gap = 60 / a.rpm - (time.time() - last_call[0])
                if gap > 0:
                    await asyncio.sleep(gap)
                last_call[0] = time.time()

    async def process(k, item):
        ctx_text = host_context.to_prompt(item["profile"])
        base_user = f"{ctx_text}\n\n## Demande de l'utilisateur\n{item['request']}"
        user, attempts, final, t0 = base_user, [], None, time.time()
        net_retries, skipped = 0, False
        for n in range(1, a.max_attempts + 1):
            await throttle()
            while True:
                try:
                    out = await teacher(client, a.model,
                                        system_cap if a.categories == "capacite_plus" else system, user)
                    break
                except RateLimited as e:
                    if e.wait > a.max_wait:
                        if not stop.is_set():
                            print(f"\nLimite de l'API atteinte (attente demandée : {round(e.wait / 60)} min).")
                            print("Relance la même commande plus tard : la génération reprendra ici.")
                        stop.set()
                        return
                    print(f"   … limite de débit, pause {round(e.wait)} s")
                    await asyncio.sleep(e.wait + 1)
                except Exception as e:  # noqa: BLE001
                    net = any(x in str(e).lower() for x in ("timed out", "timeout", "connection", "502", "503"))
                    if net and net_retries < 4:
                        net_retries += 1
                        print(f"   … API injoignable ({str(e)[:60]}), nouvel essai dans 15 s")
                        await asyncio.sleep(15)
                        continue
                    out = {"yaml": "", "warnings": [], "tokens": 0, "error": str(e)[:200], "network": net}
                    break
            window.append((time.time(), out["tokens"]))
            window[:] = [(s_, t) for s_, t in window if time.time() - s_ < 60]
            if out.get("network"):
                skipped = True            # panne réseau : ni succès ni échec de l'enseignant
                break
            if not out["yaml"].strip():
                attempts.append({"n": n, "valid": False, "errors": [["modèle", out.get("error", "réponse vide")]],
                                 "tokens": out["tokens"]})
                continue
            # kubectl est bloquant : exécuté hors de la boucle asynchrone
            check = await loop.run_in_executor(None, Y.validate_text, out["yaml"], item["profile"])
            ns_err = namespace_errors(out["yaml"], item["profile"])
            if ns_err:
                check = {**check, "valid": False, "errors": check["errors"] + ns_err}
            attempts.append({"n": n, "valid": check["valid"], "tokens": out["tokens"],
                             "errors": [[e["source"], e["category"]] for e in check["errors"]]})
            if check["valid"]:
                final = out
                break
            if check.get("server_validated") is None:
                break                      # serveur injoignable : ne pas compter contre l'enseignant
            user = base_user + Y._feedback(out["yaml"], check)

        if skipped:
            print(f"[{k}/{len(todo)}] ⏭ {item['category']:11} API injoignable : repris au prochain lancement")
            return
        rec = {"id": item["id"], "category": item["category"], "request": item["request"],
               "profile": item["profile"]["profile_id"], "valid": bool(final),
               "rank": len(attempts) if final else None, "attempts": attempts,
               "tokens": sum(x["tokens"] for x in attempts), "duration_s": round(time.time() - t0, 1),
               "teacher": teacher_id}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if final:
            counters["ok"] += 1
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "id": item["id"],
                    "messages": [{"role": "system", "content": STUDENT_SYSTEM},
                                 {"role": "user", "content": student_user(ctx_text, item["request"])},
                                 {"role": "assistant", "content": student_target(final["yaml"], reduction_warnings(final["warnings"]))}],
                    "meta": {"category": item["category"], "profile": item["profile"]["profile_id"],
                             "teacher_rank": rec["rank"], "teacher": teacher_id}}, ensure_ascii=False) + "\n")
        mark = "✅" if final else "❌"
        print(f"[{k}/{len(todo)}] {mark} {item['category']:11} essais={len(attempts)} "
              f"jetons={rec['tokens']:5} {item['request'][:70]}")

    async def worker():
        while queue and not stop.is_set():
            k, item = queue.pop(0)
            await process(k, item)

    await asyncio.gather(*[worker() for _ in range(max(1, a.workers))])
    ok_count = counters["ok"]

    print(f"\nTerminé : {ok_count} nouveaux exemples valides → {OUT}")
    print(f"Journal (fiabilité de l'enseignant) : {LOG}")
    print("Rapport : python3 scripts/corpus_v3_report.py")


def refilter():
    import shutil
    if not os.path.exists(OUT):
        sys.exit("corpus_v3.jsonl absent")
    bak = OUT + time.strftime(".%Y%m%d-%H%M%S.bak")     # chaque passage garde sa propre copie
    shutil.copy(OUT, bak)
    rows, changed = [], 0
    for line in open(OUT, encoding="utf-8"):
        r = json.loads(line)
        txt = r["messages"][2]["content"]
        warns = re.findall(r"^# Avertissement : (.*)$", txt, flags=re.M)
        body = re.sub(r"^# Avertissement : .*\n", "", txt, flags=re.M)
        keep = reduction_warnings(warns)
        new = "".join(f"# Avertissement : {w}\n" for w in keep) + body
        changed += new != txt
        r["messages"][2]["content"] = new
        rows.append(r)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{changed}/{len(rows)} exemples nettoyés (copie avant nettoyage : {bak})")
    print(f"Avertissements conservés : {sum('# Avertissement' in r['messages'][2]['content'] for r in rows)}")


PROVIDERS = {
    # Même modèle (gpt-oss-120b, poids ouverts Apache 2.0) chez trois fournisseurs.
    "groq":     {"key": "GROQ_API_KEY", "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
                 "base_url": None, "rpm": 0, "tpm": 7000},
    "cerebras": {"key": "CEREBRAS_API_KEY", "model": "gpt-oss-120b",
                 "base_url": "https://api.cerebras.ai/v1", "rpm": 5, "tpm": 0},
    "openrouter": {"key": "OPENROUTER_API_KEY", "model": "openai/gpt-oss-120b",
                   "base_url": "https://openrouter.ai/api/v1", "rpm": 0, "tpm": 0},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500, help="taille du plan (exemples visés)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="groq")
    ap.add_argument("--model", default=None, help="par défaut : gpt-oss-120b chez le fournisseur choisi")
    ap.add_argument("--rpm", type=int, default=None, help="requêtes/minute (défaut selon le fournisseur)")
    ap.add_argument("--workers", type=int, default=1, help="exemples traités en parallèle (offre payante : 4)")
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--tpm", type=int, default=None, help="jetons/minute (défaut : 7000 pour Groq gratuit) ; 0 = sans limite")
    ap.add_argument("--max-wait", type=int, default=900, help="au-delà (s), s'arrêter et reprendre plus tard")
    ap.add_argument("--holdout-threshold", type=float, default=0.5)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--tag", default="v3",
                    help="nom des fichiers et des identifiants (v3 = corpus d'origine ; v4cap = complément)")
    ap.add_argument("--categories", choices=["all", "capacite_plus"], default="all",
                    help="capacite_plus : seulement des demandes de capacité (complément v4)")
    ap.add_argument("--refilter", action="store_true",
                    help="appliquer le filtre des avertissements au corpus existant (copie .bak conservée)")
    a = ap.parse_args()
    global OUT, LOG
    if a.tag != "v3":
        if a.categories == "all":
            sys.exit("Un --tag autre que v3 sert au complément : ajoute --categories capacite_plus")
        OUT = os.path.join(ROOT, "data", "datasets", f"corpus_{a.tag}.jsonl")
        LOG = os.path.join(ROOT, "data", "observations", f"corpus_{a.tag}_log.jsonl")
    if a.refilter:
        return refilter()
    asyncio.run(run(a))


if __name__ == "__main__":
    main()

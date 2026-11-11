#!/usr/bin/env python3
"""
scripts/build_corrections_v4.py — Exemples de CORRECTION pour KubeLLM v3
================================================================================
Constat (banc C1, KubeLLM v2) : l'élève égale son enseignant au 1er essai (27 contre 25)
mais profite mal de la boucle de correction (30 contre 36 après 3 essais, p = 0,031) :
il n'a jamais vu une réponse rejetée suivie de sa correction.

Chaque exemple produit ici reprend EXACTEMENT ce que l'application envoie au modèle à
la 2e tentative (yaml_pipeline : demande + « Ta tentative précédente a été REJETÉE » +
le YAML rejeté + les erreurs des arbitres), et la réponse attendue est le manifeste
VALIDÉ du corpus.

D'où viennent les réponses rejetées : d'une altération contrôlée d'un manifeste valide
(mémoire au-delà de la capacité, requests/limits retirés, champ mal nommé, version
d'API retirée, namespace non créé), dans les proportions des erreurs réellement
observées chez l'élève et le modèle de base. Les messages d'erreur, eux, sont RÉELS :
ils viennent du serveur Kubernetes (dry-run) et du contrôle de capacité, comme dans
l'application. Aucun appel à une API de modèle : rien ne coûte, rien ne vient de Claude.
Limite à documenter : ce sont des erreurs plausibles, pas les erreurs propres de l'élève.

Le profil de machine de chaque exemple est retrouvé en rejouant le plan déterministe
du corpus (même graine) ; un exemple dont la demande ne correspond pas est écarté.

USAGE (terminal Ubuntu WSL, dans ~/aikube, minikube démarré)
  python3 scripts/build_corrections_v4.py --n 5        # essai
  python3 scripts/build_corrections_v4.py --n 250      # complet (~10 min)
  → data/datasets/corpus_v4_corrections.jsonl (nouveau fichier ; reprise si relancé)
================================================================================
"""
import argparse
import asyncio
import copy
import json
import os
import random
import re
import sys
from collections import Counter

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

DS = os.path.join(ROOT, "data", "datasets")
OUT = os.path.join(DS, "corpus_v4_corrections.jsonl")
LOG = os.path.join(ROOT, "data", "observations", "corpus_v4_corrections_log.jsonl")
SOURCES = ["corpus_v3.jsonl", "corpus_v4cap.jsonl"]

# Proportions inspirées des erreurs au 1er essai (banc C1) : capacité (élève v2 : 9/12),
# requests/limits manquants (base : 26), schéma / champ inconnu, version d'API, namespace.
MUTATIONS = {"capacite": 35, "ressources": 25, "champ": 15, "api": 15, "namespace": 10}
WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Pod"}
OLD_API = {"Deployment": "extensions/v1beta1", "StatefulSet": "apps/v1beta1", "DaemonSet": "extensions/v1beta1",
           "CronJob": "batch/v1beta1", "HorizontalPodAutoscaler": "autoscaling/v2beta1",
           "Ingress": "extensions/v1beta1", "PodDisruptionBudget": "policy/v1beta1"}


def pod_spec(d):
    k, s = d.get("kind"), d.get("spec") or {}
    if k == "Pod":
        return s
    if k == "CronJob":
        return (((s.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec")
    return (s.get("template") or {}).get("spec")


def containers(docs):
    for d in docs:
        if d.get("kind") in WORKLOADS:
            ps = pod_spec(d)
            for c in (ps or {}).get("containers") or []:
                yield d, c


# ── Altérations : chacune renvoie True si elle a pu s'appliquer ──────────────
def m_capacite(docs, prof, rng):
    node = prof["cluster"]["max_node_mem_free_mib"]
    total = prof["cluster"]["total_mem_free_mib"]
    cands = list(containers(docs))
    if not cands:
        return False
    d, c = rng.choice(cands)
    res = c.setdefault("resources", {})
    reps = (d.get("spec") or {}).get("replicas")
    if isinstance(reps, int) and reps >= 1 and rng.random() < 0.4:
        # trop de répliques pour le cluster (chaque pod tient, la somme non)
        req = max(64, int(node * rng.uniform(0.5, 0.9)))
        res.setdefault("requests", {})["memory"] = f"{req}Mi"
        res.setdefault("limits", {})["memory"] = f"{req}Mi"
        d["spec"]["replicas"] = max(reps, total // req + rng.randint(1, 3))
    else:
        big = int(node * rng.uniform(1.3, 2.6))
        big = f"{max(1, round(big / 1024))}Gi" if big > 2048 else f"{big}Mi"
        res.setdefault("requests", {})["memory"] = big
        res.setdefault("limits", {})["memory"] = big
    return True


def m_ressources(docs, prof, rng):
    cands = [(d, c) for d, c in containers(docs) if c.get("resources")]
    if not cands:
        return False
    _, c = rng.choice(cands)
    what = rng.choice(["limits", "limits", "requests", "tout"])
    if what == "tout":
        c.pop("resources")
    else:
        c["resources"].pop(what, None)
        if not c["resources"]:
            c.pop("resources")
    return True


def m_champ(docs, prof, rng):
    cands = list(containers(docs))
    if not cands:
        return False
    _, c = rng.choice(cands)
    ports = c.get("ports") or []
    if ports and "containerPort" in ports[0] and rng.random() < 0.6:
        ports[0]["port"] = ports[0].pop("containerPort")
    elif "imagePullPolicy" in c:
        c["pullPolicy"] = c.pop("imagePullPolicy")
    else:   # champ inventé, au niveau du conteneur au lieu de resources.limits
        c[rng.choice(["memoryLimit", "cpuLimit", "replicas"])] = rng.choice(["256Mi", "500m", 2])
    return True


def m_api(docs, prof, rng):
    cands = [d for d in docs if d.get("kind") in OLD_API]
    if not cands:
        return False
    d = rng.choice(cands)
    d["apiVersion"] = OLD_API[d["kind"]]
    return True


def m_namespace(docs, prof, rng):
    known = set(prof["cluster"]["namespaces"])
    ns_docs = [d for d in docs if d.get("kind") == "Namespace" and d["metadata"]["name"] not in known]
    if not ns_docs:
        return False
    docs.remove(rng.choice(ns_docs))
    return True


FUNCS = {"capacite": m_capacite, "ressources": m_ressources, "champ": m_champ, "api": m_api,
         "namespace": m_namespace}


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


async def main(a):
    from ai_module import host_context, yaml_pipeline as Y
    from ai_module.kubellm_prompt import STUDENT_SYSTEM
    import build_corpus_v3 as B
    from campaign_yaml import PROMPTS as C1
    host_context.host_history = lambda *x, **k: {"available": False}

    real = await host_context.collect(force=True)
    if not real.get("api_kinds"):
        sys.exit("Cluster injoignable : démarre minikube puis relance (kubectl get nodes).")
    holdout = [B.words(p[2]) for p in C1]

    # 1. Exemples sources et profils retrouvés par rejeu du plan
    examples = []
    for name in SOURCES:
        rows = load_jsonl(os.path.join(DS, name))
        if not rows:
            print(f"(source absente ou vide : {name})")
            continue
        groups = Counter()
        for r in rows:
            tag, seed, idx = r["id"].rsplit("-", 2)
            groups[(tag, int(seed))] = max(groups[(tag, int(seed))], int(idx) + 1)
        profiles = {}
        for (tag, seed), n in groups.items():
            mode = "all" if tag == "v3" else "capacite_plus"
            plan, _ = B.build_plan(seed, n, real, holdout, 0.5, mode, tag)
            profiles.update({p["id"]: p for p in plan})
        bad = 0
        for r in rows:
            p = profiles.get(r["id"])
            if not p or not r["messages"][1]["content"].endswith(p["request"].strip()):
                bad += 1
                continue
            examples.append((r, p))
        print(f"{name} : {len(rows)} exemples, {len(rows) - bad} profils retrouvés"
              + (f", {bad} écartés (demande différente du plan)" if bad else ""))
    if not examples:
        sys.exit("Aucun exemple source : il faut data/datasets/corpus_v3.jsonl")

    done = {r["meta"]["source_id"] for r in load_jsonl(OUT)}
    rng = random.Random(a.seed)
    rng.shuffle(examples)
    todo = [e for e in examples if e[0]["id"] not in done][: max(0, a.n - len(done))]
    print(f"Déjà faits : {len(done)} · à faire : {len(todo)}\n")
    for ns in B.NS_POOL:
        await asyncio.to_thread(B.subprocess.run, ["kubectl", "create", "namespace", ns],
                                capture_output=True, text=True, timeout=45)

    stats = Counter()
    for k, (r, p) in enumerate(todo, 1):
        prof = p["profile"]
        target = r["messages"][2]["content"]
        body = re.sub(r"^#.*\n", "", target, flags=re.M)
        try:
            docs = [d for d in yaml.safe_load_all(body) if isinstance(d, dict)]
        except yaml.YAMLError:
            stats["source illisible"] += 1
            continue
        # la cible doit être valide AUJOURD'HUI, sur ce cluster
        ok = await asyncio.to_thread(Y.validate_text, body, prof)
        if not ok["valid"] or B.namespace_errors(body, prof):
            stats["source invalide aujourd'hui"] += 1
            continue

        kinds = list(MUTATIONS)
        first = rng.choices(kinds, weights=[MUTATIONS[x] for x in kinds])[0]
        order = [first] + [x for x in rng.sample(kinds, len(kinds)) if x != first]
        mutated = mut_kind = None
        for kind in order:
            cand = copy.deepcopy(docs)
            if FUNCS[kind](cand, prof, rng):
                mutated, mut_kind = cand, kind
                break
        if mutated is None:
            stats["aucune altération possible"] += 1
            continue
        bad_yaml = yaml.safe_dump_all(mutated, sort_keys=False, allow_unicode=True)
        check = await asyncio.to_thread(Y.validate_text, bad_yaml, prof)
        ns_err = B.namespace_errors(bad_yaml, prof)
        if ns_err:
            check = {**check, "valid": False, "errors": check["errors"] + ns_err}
        if check.get("server_validated") is None and not check["errors"]:
            sys.exit("Serveur Kubernetes injoignable : relance quand minikube répond.")
        if check["valid"] or not check["errors"]:
            stats[f"altération {mut_kind} non détectée"] += 1
            continue

        user = r["messages"][1]["content"] + Y._feedback(bad_yaml, check)
        cats = sorted({e["category"] for e in check["errors"]})
        rec = {"id": f"v4corr-{r['id']}",
               "messages": [{"role": "system", "content": STUDENT_SYSTEM},
                            {"role": "user", "content": user},
                            {"role": "assistant", "content": target}],
               "meta": {"category": f"correction_{mut_kind}", "source_id": r["id"],
                        "source_category": r["meta"]["category"], "error_categories": cats}}
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": rec["id"], "mutation": mut_kind, "errors": cats,
                                "chars": len(user) + len(target)}, ensure_ascii=False) + "\n")
        stats[f"ok {mut_kind}"] += 1
        print(f"[{k}/{len(todo)}] ✅ {mut_kind:10} → {', '.join(cats)[:70]}")

    print("\nBilan :", dict(stats))
    print(f"Corpus de correction : {OUT} ({len(load_jsonl(OUT))} exemples)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=250, help="nombre total d'exemples de correction visés")
    ap.add_argument("--seed", type=int, default=7)
    asyncio.run(main(ap.parse_args()))

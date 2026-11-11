#!/usr/bin/env python3
"""
scripts/anomaly_benchmark.py
================================================================================
Évaluation des détecteurs d'anomalies contre une vérité terrain construite.

POURQUOI CE BANC EXISTE
-----------------------
La comparaison sur cluster réel (detection_comparator.py) mesure un ACCORD
entre détecteurs, jamais une exactitude : aucun des trois n'est une référence.
Sur un cluster sain, elle ne peut même pas les départager — un détecteur muet
et un détecteur parfait produisent le même résultat.

Ce banc lève l'ambiguïté en déployant des pods dont l'état est connu par
construction. La vérité terrain est portée par les étiquettes du pod lui-même
(aikube.bench/expected), ce qui rend impossible toute désynchronisation entre
ce qui est déployé et ce qui sert de référence.

MÉTRIQUES PRODUITES
-------------------
Pour chacun des trois détecteurs :

    Précision   VP / (VP + FP)   proportion d'alertes justifiées
    Rappel      VP / (VP + FN)   proportion d'anomalies repérées
    F1          moyenne harmonique — pénalise le déséquilibre
    Spécificité VN / (VN + FP)   capacité à ne pas surréagir

Le F1 rapporté ici n'a rien à voir avec le F1=0.913 du modèle entraîné : ce
dernier est mesuré sur des données synthétiques générées par les règles
mêmes qu'il apprend. Celui-ci est mesuré sur des pods réels dont les
défaillances sont provoquées et vérifiables.

PROTOCOLE
---------
  1. kubectl apply -f k8s/benchmark/anomaly-benchmark.yaml
  2. attente de stabilisation (les redémarrages doivent s'accumuler)
  3. lecture de l'état réel via kubectl
  4. application des trois détecteurs
  5. confrontation aux étiquettes de vérité
  6. kubectl delete namespace aikube-bench

FENÊTRE D'OBSERVATION
---------------------
Le délai d'attente est déterminant. Kubernetes applique un backoff exponentiel
aux redémarrages (10s, 20s, 40s, 80s…). Trop court, les compteurs n'ont pas
grimpé et le rappel est artificiellement bas ; trop long, les pods en
CrashLoopBackOff ralentissent et le taux horaire retombe. Le défaut de 240 s
permet d'accumuler 4 à 6 redémarrages. Ce paramètre doit être rapporté avec
les résultats : il fait partie du protocole, pas des réglages.

USAGE
  python3 scripts/anomaly_benchmark.py --wait 240
  python3 scripts/anomaly_benchmark.py --no-deploy --wait 0    # pods déjà en place
  python3 scripts/anomaly_benchmark.py --cleanup               # suppression seule
================================================================================
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

# Import du comparateur depuis backend/
HERE         = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))

NAMESPACE   = "aikube-bench"
MANIFEST    = os.path.join(PROJECT_ROOT, "k8s", "benchmark", "anomaly-benchmark.yaml")
RESULTS_DIR = os.path.join(PROJECT_ROOT, "data", "observations")
RESULTS_PATH = os.path.join(RESULTS_DIR, "benchmark_results.jsonl")


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATION DU CLUSTER
# ══════════════════════════════════════════════════════════════════════════════

def run(cmd, timeout=90):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def deploy():
    print(f"▶ Déploiement du banc depuis {MANIFEST}")
    if not os.path.exists(MANIFEST):
        print(f"✖ Manifeste introuvable : {MANIFEST}")
        sys.exit(1)
    r = run(["kubectl", "apply", "-f", MANIFEST])
    if r.returncode != 0:
        print(f"✖ Échec du déploiement :\n{r.stderr}")
        sys.exit(1)
    print(r.stdout.strip())


def cleanup():
    print(f"▶ Suppression du namespace {NAMESPACE}")
    r = run(["kubectl", "delete", "namespace", NAMESPACE, "--ignore-not-found"], timeout=180)
    print(r.stdout.strip() or "namespace supprimé")


def wait_stabilisation(seconds):
    """
    Attente active avec compte rendu. Les redémarrages doivent s'accumuler
    pour que les cas CrashLoopBackOff deviennent détectables.
    """
    if seconds <= 0:
        return
    print(f"▶ Stabilisation ({seconds}s) — accumulation des redémarrages")
    step = 20
    for elapsed in range(0, seconds, step):
        time.sleep(min(step, seconds - elapsed))
        r = run(["kubectl", "get", "pods", "-n", NAMESPACE, "--no-headers"])
        if r.returncode == 0:
            lines = [l for l in r.stdout.strip().split("\n") if l]
            running = sum(1 for l in lines if " Running " in f" {l} ")
            print(f"   {elapsed + step:>4}s — {len(lines)} pods, {running} Running")


def fetch_bench_pods():
    """
    Récupère les pods du banc avec leur étiquette de vérité terrain.
    Les pods sans étiquette (créés hors banc) sont ignorés.
    """
    r = run(["kubectl", "get", "pods", "-n", NAMESPACE, "-o", "json"])
    if r.returncode != 0:
        print(f"✖ Lecture des pods impossible : {r.stderr}")
        sys.exit(1)

    pods = []
    for item in json.loads(r.stdout).get("items", []):
        meta   = item.get("metadata", {})
        spec   = item.get("spec", {})
        status = item.get("status", {})
        labels = meta.get("labels", {}) or {}

        expected = labels.get("aikube.bench/expected")
        if expected not in ("anomaly", "healthy"):
            continue

        restarts = sum(cs.get("restartCount", 0)
                       for cs in (status.get("containerStatuses") or []))

        # Signaux requis par le détecteur v3
        ready = None
        for cond in status.get("conditions", []) or []:
            if cond.get("type") == "Ready":
                ready = cond.get("status") == "True"
                break
        started_at = None
        for cs in (status.get("containerStatuses") or []):
            running = (cs.get("state") or {}).get("running") or {}
            if running.get("startedAt"):
                if started_at is None or running["startedAt"] > started_at:
                    started_at = running["startedAt"]

        # Motif d'attente éventuel (ImagePullBackOff, CrashLoopBackOff…)
        waiting_reason = None
        for cs in (status.get("containerStatuses") or []):
            w = (cs.get("state") or {}).get("waiting")
            if w and w.get("reason"):
                waiting_reason = w["reason"]
                break

        pods.append({
            "name":       meta.get("name", ""),
            "namespace":  meta.get("namespace", NAMESPACE),
            "status":     status.get("phase", "Unknown"),
            "restarts":   restarts,
            "node":       spec.get("nodeName"),
            "ready":      ready,
            "started_at": started_at,
            "labels":     labels,
            "age":        meta.get("creationTimestamp", ""),
            "containers": [c.get("name") for c in spec.get("containers", []) or []],
            # vérité terrain
            "_expected":  1 if expected == "anomaly" else 0,
            "_type":      labels.get("aikube.bench/type", "?"),
            "_waiting":   waiting_reason,
        })
    return pods


# ══════════════════════════════════════════════════════════════════════════════
# MÉTRIQUES
# ══════════════════════════════════════════════════════════════════════════════

def confusion(truth, pred):
    vp = sum(1 for t, p in zip(truth, pred) if t == 1 and p == 1)
    fp = sum(1 for t, p in zip(truth, pred) if t == 0 and p == 1)
    vn = sum(1 for t, p in zip(truth, pred) if t == 0 and p == 0)
    fn = sum(1 for t, p in zip(truth, pred) if t == 1 and p == 0)
    return vp, fp, vn, fn


def metrics(truth, pred):
    vp, fp, vn, fn = confusion(truth, pred)

    # Une métrique indéfinie (dénominateur nul) est rapportée None, jamais 0 :
    # « aucune alerte émise » et « toutes les alertes fausses » sont deux
    # situations distinctes qu'un 0 confondrait.
    precision   = vp / (vp + fp) if (vp + fp) else None
    recall      = vp / (vp + fn) if (vp + fn) else None
    specificity = vn / (vn + fp) if (vn + fp) else None
    accuracy    = (vp + vn) / len(truth) if truth else None

    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)

    rnd = lambda x: round(x, 4) if x is not None else None
    return {
        "confusion":   {"VP": vp, "FP": fp, "VN": vn, "FN": fn},
        "precision":   rnd(precision),
        "recall":      rnd(recall),
        "f1":          rnd(f1),
        "specificity": rnd(specificity),
        "accuracy":    rnd(accuracy),
    }


# ══════════════════════════════════════════════════════════════════════════════
# ÉVALUATION
# ══════════════════════════════════════════════════════════════════════════════

def evaluate(pods):
    from ai_module.detection_comparator import (
        rules_predict, rules_v2_predict, rules_v3_predict, ml_predict
    )

    truth, p_v1, p_v2, p_v3, p_ml = [], [], [], [], []
    details = []

    for pod in pods:
        r1 = rules_predict(pod)
        r2 = rules_v2_predict(pod)
        r3 = rules_v3_predict(pod)
        m  = ml_predict(pod)

        t  = pod["_expected"]
        l1 = 1 if r1["is_anomaly"] else 0
        l2 = 1 if r2["is_anomaly"] else 0
        l3 = 1 if r3["is_anomaly"] else 0
        lm = 1 if m["is_anomaly"] else 0 if m["is_anomaly"] is not None else 0

        truth.append(t); p_v1.append(l1); p_v2.append(l2)
        p_v3.append(l3); p_ml.append(lm)

        details.append({
            "pod":          pod["name"],
            "type":         pod["_type"],
            "expected":     t,
            "status":       pod["status"],
            "waiting":      pod["_waiting"],
            "restarts":     pod["restarts"],
            "age_hours":    r2["age_hours"],
            "restart_rate": r2["restart_rate"],
            "uptime_seconds": r3["uptime_seconds"],
            "rules_v1":     l1,
            "rules_v2":     l2,
            "rules_v3":     l3,
            "ml":           lm,
            "ml_probability": m.get("probability"),
            "all_correct":  (l1 == t and l2 == t and l3 == t and lm == t),
        })

    return {
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "pods_evaluated": len(pods),
        "ground_truth":  {"anomalies": sum(truth), "healthy": len(truth) - sum(truth)},
        "detectors": {
            "rules_v1": metrics(truth, p_v1),
            "rules_v2": metrics(truth, p_v2),
            "rules_v3": metrics(truth, p_v3),
            "ml":       metrics(truth, p_ml),
        },
        "details": details,
    }


def print_report(rep, wait_seconds):
    d = rep["detectors"]
    print(f"\n{'='*78}")
    print("  ÉVALUATION SUR VÉRITÉ TERRAIN CONSTRUITE")
    print(f"{'='*78}")
    print(f"  Pods évalués      : {rep['pods_evaluated']}"
          f"  ({rep['ground_truth']['anomalies']} anormaux, "
          f"{rep['ground_truth']['healthy']} sains)")
    print(f"  Fenêtre d'attente : {wait_seconds}s")

    print(f"\n  {'Détecteur':<12} {'VP':>3} {'FP':>3} {'VN':>3} {'FN':>3}   "
          f"{'Précis.':>8} {'Rappel':>8} {'F1':>8} {'Spécif.':>8}")
    print(f"  {'-'*12} {'-'*3} {'-'*3} {'-'*3} {'-'*3}   "
          f"{'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for key, label in (("rules_v1", "Règles v1"), ("rules_v2", "Règles v2"),
                       ("rules_v3", "Règles v3"), ("ml", "Random For.")):
        m = d[key]; c = m["confusion"]
        fmt = lambda v: f"{v:.3f}" if v is not None else "  n/a "
        print(f"  {label:<12} {c['VP']:>3} {c['FP']:>3} {c['VN']:>3} {c['FN']:>3}   "
              f"{fmt(m['precision']):>8} {fmt(m['recall']):>8} "
              f"{fmt(m['f1']):>8} {fmt(m['specificity']):>8}")

    print(f"\n  Détail par cas")
    print(f"  {'pod':<32} {'vérité':>8} {'v1':>2} {'v2':>3} {'v3':>3} {'ml':>3} {'p_ml':>7} {'up(s)':>7}  état")
    for x in sorted(rep["details"], key=lambda z: (-z["expected"], z["pod"])):
        v = "ANOMALIE" if x["expected"] else "sain"
        mark = lambda pred: "✓" if pred == x["expected"] else "✗"
        p = f"{x['ml_probability']:.3f}" if x["ml_probability"] is not None else "  n/a"
        state = x["waiting"] or x["status"]
        up = f"{x['uptime_seconds']:.0f}" if x["uptime_seconds"] is not None else "  n/a"
        print(f"  {x['pod']:<32} {v:>8} "
              f"{mark(x['rules_v1']):>2} {mark(x['rules_v2']):>3} "
              f"{mark(x['rules_v3']):>3} {mark(x['ml']):>3} "
              f"{p:>7} {up:>7}  {state} r={x['restarts']}")

    # Cas manqués par tous : les plus instructifs pour le mémoire
    missed = [x for x in rep["details"] if x["expected"] == 1
              and x["rules_v1"] == 0 and x["rules_v2"] == 0
              and x["rules_v3"] == 0 and x["ml"] == 0]
    if missed:
        print(f"\n  ⚠ Anomalies manquées par les TROIS détecteurs :")
        for x in missed:
            print(f"      {x['pod']} ({x['type']}) — état {x['waiting'] or x['status']}, "
                  f"{x['restarts']} redémarrages")
        print("      Ces cas définissent la frontière de détection du système.")

    print(f"{'='*78}\n")


def save(rep):
    try:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        with open(RESULTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rep, ensure_ascii=False) + "\n")
        print(f"✓ Résultats ajoutés à {RESULTS_PATH}")
    except Exception as e:
        print(f"⚠ Sauvegarde impossible : {e}")


# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Banc d'évaluation des détecteurs AI-Kube")
    ap.add_argument("--wait", type=int, default=240,
                    help="secondes de stabilisation avant mesure (défaut 240)")
    ap.add_argument("--no-deploy", action="store_true",
                    help="ne pas déployer, mesurer les pods déjà présents")
    ap.add_argument("--keep", action="store_true",
                    help="conserver le namespace après la mesure")
    ap.add_argument("--cleanup", action="store_true",
                    help="supprimer le namespace et quitter")
    args = ap.parse_args()

    if args.cleanup:
        cleanup()
        return

    if not args.no_deploy:
        deploy()
        wait_stabilisation(args.wait)
    elif args.wait > 0:
        wait_stabilisation(args.wait)

    pods = fetch_bench_pods()
    if not pods:
        print("✖ Aucun pod étiqueté trouvé. Le banc est-il déployé ?")
        sys.exit(1)

    rep = evaluate(pods)
    print_report(rep, args.wait)
    save(rep)

    if not args.keep:
        print("ℹ Namespace conservé. Pour supprimer :")
        print(f"    kubectl delete namespace {NAMESPACE}")


if __name__ == "__main__":
    main()

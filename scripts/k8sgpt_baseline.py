#!/usr/bin/env python3
"""
scripts/k8sgpt_baseline.py
================================================================================
Comparaison à un outil existant : K8sGPT.

POURQUOI UNE BASELINE EXTERNE
-----------------------------
Les évaluations précédentes comparent les détecteurs entre eux et à une vérité
terrain construite. Elles ne situent pas le travail par rapport à l'état de
l'art : un relecteur demandera légitimement ce qu'un outil existant obtient sur
le même banc.

K8sGPT est retenu parce qu'il est largement diffusé (projet CNCF sandbox),
qu'il fonctionne sur le même objet — les ressources d'un cluster — et qu'il
possède un mode déterministe.

CE QUI EST COMPARÉ
------------------
Seuls les analyseurs déterministes de K8sGPT sont sollicités (`analyze`, sans
l'option `--explain`). Sa couche d'explication par modèle de langage n'est pas
évaluée : elle reformule des diagnostics déjà établis, elle ne détecte rien de
plus. La comparaison porte donc sur la capacité de détection, à méthode
équivalente.

PRÉCAUTION D'INTERPRÉTATION
---------------------------
K8sGPT n'a pas été conçu pour ce banc, et ses analyseurs couvrent un périmètre
plus large que la détection d'anomalies de pods — services sans point de
terminaison, ingress mal configurés, volumes non liés. Une performance
inférieure sur ce banc particulier ne signifie pas que l'outil est moins bon :
elle indique que les périmètres diffèrent. Le rapport distingue donc les
défaillances relevant du périmètre commun de celles qui n'en relèvent pas.

USAGE
  python3 scripts/k8sgpt_baseline.py --wait 300
  python3 scripts/k8sgpt_baseline.py --no-deploy
================================================================================
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE         = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))
sys.path.insert(0, HERE)

NAMESPACE = "aikube-bench"
OBS_DIR   = os.path.join(PROJECT_ROOT, "data", "observations")
OUT_PATH  = os.path.join(OBS_DIR, "k8sgpt_baseline.jsonl")

DETECTORS = [("rules_v1", "Règles v1"), ("rules_v2", "Règles v2"),
             ("rules_v3", "Règles v3"), ("ml", "Random Forest"),
             ("k8sgpt", "K8sGPT")]


# ══════════════════════════════════════════════════════════════════════════════
# K8SGPT
# ══════════════════════════════════════════════════════════════════════════════

def k8sgpt_available():
    try:
        r = subprocess.run(["k8sgpt", "version"], capture_output=True,
                           text=True, timeout=20)
        return r.returncode == 0, (r.stdout or r.stderr).strip().split("\n")[0]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, None


def run_k8sgpt(namespace):
    """
    Retourne l'ensemble des noms de pods signalés, et le détail brut.

    K8sGPT rapporte des résultats par ressource, sous la forme
    `namespace/nom`. On ne retient que les ressources de type Pod, les autres
    types (Service, Ingress, PVC) sortant du périmètre du banc.
    """
    r = subprocess.run(
        ["k8sgpt", "analyze", "--namespace", namespace, "--output", "json"],
        capture_output=True, text=True, timeout=180)

    if r.returncode != 0 and not r.stdout.strip():
        return None, None, (r.stderr or "échec de k8sgpt analyze").strip()[:300]

    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None, None, "sortie de k8sgpt non exploitable (JSON invalide)"

    flagged = set()
    details = []
    for item in data.get("results", []) or []:
        kind = item.get("kind", "")
        name = item.get("name", "")
        pod = name.split("/")[-1] if "/" in name else name
        details.append({
            "kind": kind, "name": name, "pod": pod,
            "error": [e.get("Text", "")[:200] for e in (item.get("error") or [])],
        })
        if kind == "Pod":
            flagged.add(pod)

    return flagged, details, None


# ══════════════════════════════════════════════════════════════════════════════
# MÉTRIQUES
# ══════════════════════════════════════════════════════════════════════════════

def metrics(truth, pred):
    vp = sum(1 for t, p in zip(truth, pred) if t == 1 and p == 1)
    fp = sum(1 for t, p in zip(truth, pred) if t == 0 and p == 1)
    vn = sum(1 for t, p in zip(truth, pred) if t == 0 and p == 0)
    fn = sum(1 for t, p in zip(truth, pred) if t == 1 and p == 0)

    precision   = vp / (vp + fp) if (vp + fp) else None
    recall      = vp / (vp + fn) if (vp + fn) else None
    specificity = vn / (vn + fp) if (vn + fp) else None
    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)

    rnd = lambda x: round(x, 4) if x is not None else None
    return {"confusion": {"VP": vp, "FP": fp, "VN": vn, "FN": fn},
            "precision": rnd(precision), "recall": rnd(recall),
            "f1": rnd(f1), "specificity": rnd(specificity)}


# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Comparaison à K8sGPT")
    ap.add_argument("--wait", type=int, default=300)
    ap.add_argument("--no-deploy", action="store_true")
    args = ap.parse_args()

    ok, version = k8sgpt_available()
    if not ok:
        print("✖ k8sgpt introuvable dans le PATH.")
        print("  Installation :")
        print("    VERSION=$(curl -s https://api.github.com/repos/k8sgpt-ai/"
              "k8sgpt/releases/latest | grep -Po '\"tag_name\": \"v\\K[^\"]*')")
        print("    curl -LO https://github.com/k8sgpt-ai/k8sgpt/releases/"
              "download/v${VERSION}/k8sgpt_amd64.deb && sudo dpkg -i k8sgpt_amd64.deb")
        sys.exit(1)
    print(f"▶ K8sGPT : {version}")

    if not args.no_deploy:
        print("▶ Déploiement du banc")
        for manifest in ["anomaly-benchmark.yaml", "anomaly-benchmark-edge.yaml"]:
            path = os.path.join(PROJECT_ROOT, "k8s", "benchmark", manifest)
            r = subprocess.run(["kubectl", "apply", "-f", path],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                print(f"✖ {r.stderr[:200]}")
                sys.exit(1)
        print(f"▶ Stabilisation ({args.wait}s)")
        for elapsed in range(0, args.wait, 30):
            time.sleep(min(30, args.wait - elapsed))
            print(f"   {elapsed + 30}s")

    # ── Détecteurs AI-Kube ────────────────────────────────────────────────────
    from anomaly_benchmark import fetch_bench_pods
    from ai_module.detection_comparator import (
        rules_predict, rules_v2_predict, rules_v3_predict, ml_predict)

    pods = fetch_bench_pods()
    if not pods:
        print("✖ Aucun pod étiqueté dans le banc")
        sys.exit(1)

    # ── K8sGPT ────────────────────────────────────────────────────────────────
    print("▶ Analyse par K8sGPT")
    flagged, raw_details, err = run_k8sgpt(NAMESPACE)
    if err:
        print(f"✖ {err}")
        sys.exit(1)
    print(f"  {len(flagged)} pods signalés, {len(raw_details)} résultats bruts")

    # ── Confrontation ─────────────────────────────────────────────────────────
    truth, preds = [], {k: [] for k, _ in DETECTORS}
    rows = []

    for pod in pods:
        t = pod["_expected"]
        m = ml_predict(pod)
        verdict = {
            "rules_v1": 1 if rules_predict(pod)["is_anomaly"] else 0,
            "rules_v2": 1 if rules_v2_predict(pod)["is_anomaly"] else 0,
            "rules_v3": 1 if rules_v3_predict(pod)["is_anomaly"] else 0,
            "ml":       1 if m["is_anomaly"] else 0,
            "k8sgpt":   1 if pod["name"] in flagged else 0,
        }
        truth.append(t)
        for k in preds:
            preds[k].append(verdict[k])
        rows.append({"pod": pod["name"], "type": pod["_type"], "expected": t,
                     "status": pod["status"], "restarts": pod["restarts"],
                     **verdict})

    results = {k: metrics(truth, preds[k]) for k, _ in DETECTORS}

    # ── Rapport ───────────────────────────────────────────────────────────────
    print(f"\n{'='*82}")
    print("  COMPARAISON À UN OUTIL EXISTANT — K8sGPT")
    print(f"{'='*82}")
    print(f"  Pods évalués : {len(pods)}  "
          f"({sum(truth)} anormaux, {len(truth) - sum(truth)} sains)")

    print(f"\n  {'Détecteur':<16} {'VP':>3} {'FP':>3} {'VN':>3} {'FN':>3}   "
          f"{'Précis.':>8} {'Rappel':>8} {'F1':>8} {'Spécif.':>8}")
    print(f"  {'-'*16} {'-'*3} {'-'*3} {'-'*3} {'-'*3}   {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for key, label in DETECTORS:
        m = results[key]; c = m["confusion"]
        f = lambda v: f"{v:.3f}" if v is not None else "  n/a "
        print(f"  {label:<16} {c['VP']:>3} {c['FP']:>3} {c['VN']:>3} {c['FN']:>3}   "
              f"{f(m['precision']):>8} {f(m['recall']):>8} "
              f"{f(m['f1']):>8} {f(m['specificity']):>8}")

    # Désaccords avec K8sGPT : c'est là que se situe l'information
    print(f"\n  Cas où K8sGPT et les règles v3 divergent")
    div = [r for r in rows if r["k8sgpt"] != r["rules_v3"]]
    if not div:
        print("    aucun — les deux approches concordent sur l'ensemble du banc")
    else:
        print(f"    {'pod':<32} {'vérité':>8} {'v3':>4} {'k8sgpt':>7}  type")
        for r in div:
            v = "ANOMALIE" if r["expected"] else "sain"
            print(f"    {r['pod']:<32} {v:>8} {r['rules_v3']:>4} "
                  f"{r['k8sgpt']:>7}  {r['type']}")

    # Résultats hors périmètre : ressources non-Pod signalées par K8sGPT
    others = [d for d in raw_details if d["kind"] != "Pod"]
    if others:
        print(f"\n  Résultats hors périmètre du banc ({len(others)})")
        print("    K8sGPT couvre des ressources que le banc n'évalue pas.")
        for d in others[:8]:
            print(f"      {d['kind']:<14} {d['name']}")

    print(f"\n  Détail par pod")
    print(f"  {'pod':<32} {'vérité':>8} " + "".join(f"{l.split()[-1]:>9}" for _, l in DETECTORS))
    for r in sorted(rows, key=lambda x: (-x["expected"], x["pod"])):
        v = "ANOMALIE" if r["expected"] else "sain"
        line = f"  {r['pod']:<32} {v:>8} "
        for k, _ in DETECTORS:
            mark = "✓" if r[k] == r["expected"] else "✗"
            line += f"{mark:>9}"
        print(line)

    print(f"{'='*82}\n")

    os.makedirs(OBS_DIR, exist_ok=True)
    with open(OUT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "k8sgpt_version": version, "pods_evaluated": len(pods),
            "ground_truth": {"anomalies": sum(truth),
                             "healthy": len(truth) - sum(truth)},
            "detectors": results, "rows": rows,
            "k8sgpt_raw": raw_details,
        }, ensure_ascii=False) + "\n")
    print(f"✓ Résultats : {OUT_PATH}")
    print(f"ℹ Ne pas oublier :  kubectl delete namespace {NAMESPACE}\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scripts/leadtime_k8sgpt.py
================================================================================
Temps d'anticipation, K8sGPT inclus.

RAPPORT AUX TRAVAUX PRÉCÉDENTS
------------------------------
Ce script est autonome et n'altère ni `leadtime_analysis.py` ni ses résultats.
Il écrit dans un journal distinct — `leadtime_k8sgpt.jsonl` — afin que la
campagne d'anticipation initiale, conduite sans baseline externe, reste
comparable et citable telle quelle.

La méthodologie est identique à celle de la campagne précédente :

  · référence : premier instant où KUBERNETES déclare le pod défaillant
    (CrashLoopBackOff, ImagePullBackOff, Failed, etc.), juge indépendant de
    tous les détecteurs évalués ;

  · éligibilité : seuls les pods observés SAINS avant leur défaillance sont
    retenus. On ne peut anticiper ce qui est déjà survenu, et tout pod passe
    par la phase Pending à sa création, ce qui produirait un instant de
    détection nul pour l'ensemble du banc.

QUESTION POSÉE
--------------
La comparaison statique a montré que K8sGPT et les règles v3 obtiennent des
résultats identiques sur le banc (F1 = 1,000, aucune divergence). Une question
reste ouverte : ces approches signalent-elles au MÊME MOMENT ?

Deux détecteurs peuvent converger sur le verdict final tout en différant
nettement sur l'instant du signalement. C'est cet écart que mesure ce script,
et il n'est documenté nulle part dans la littérature sur K8sGPT.

COÛT D'APPEL
------------
`k8sgpt analyze` prend plusieurs secondes par exécution. L'intervalle
d'échantillonnage est donc plus large que celui de la campagne précédente
(20 s contre 15 s), ce qui dégrade légèrement la résolution temporelle. Ce
paramètre est rapporté avec les résultats.

USAGE
  python3 scripts/leadtime_k8sgpt.py --duration 600 --interval 20
  python3 scripts/leadtime_k8sgpt.py --no-deploy --duration 400
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
OUT_PATH  = os.path.join(OBS_DIR, "leadtime_k8sgpt.jsonl")

DETECTORS = [("rules_v1", "Règles v1"), ("rules_v2", "Règles v2"),
             ("rules_v3", "Règles v3"), ("ml", "Random Forest"),
             ("k8sgpt", "K8sGPT")]

K8S_FAILURE_REASONS = {
    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
    "CreateContainerConfigError", "CreateContainerError",
    "InvalidImageName", "RunContainerError",
}
K8S_FAILURE_PHASES = {"Failed"}


# ══════════════════════════════════════════════════════════════════════════════
# COLLECTE
# ══════════════════════════════════════════════════════════════════════════════

def k8sgpt_flagged():
    """Pods signalés par K8sGPT à cet instant. Seuls les résultats de type Pod
    sont retenus : l'outil couvre aussi Service, Ingress, ConfigMap, ressources
    hors du périmètre de ce banc."""
    try:
        r = subprocess.run(
            ["k8sgpt", "analyze", "--namespace", NAMESPACE, "--output", "json"],
            capture_output=True, text=True, timeout=90)
        if not r.stdout.strip():
            return set()
        data = json.loads(r.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return set()

    flagged = set()
    for item in data.get("results", []) or []:
        if item.get("kind") == "Pod":
            name = item.get("name", "")
            flagged.add(name.split("/")[-1] if "/" in name else name)
    return flagged


def sample_pods():
    r = subprocess.run(["kubectl", "get", "pods", "-n", NAMESPACE, "-o", "json"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return None

    pods = []
    for item in json.loads(r.stdout).get("items", []):
        meta   = item.get("metadata", {})
        spec   = item.get("spec", {})
        status = item.get("status", {})
        labels = meta.get("labels", {}) or {}

        expected = labels.get("aikube.bench/expected")
        if expected not in ("anomaly", "healthy"):
            continue

        statuses = status.get("containerStatuses") or []
        restarts = sum(cs.get("restartCount", 0) for cs in statuses)

        ready = None
        for cond in status.get("conditions", []) or []:
            if cond.get("type") == "Ready":
                ready = cond.get("status") == "True"
                break

        started_at = None
        for cs in statuses:
            running = (cs.get("state") or {}).get("running") or {}
            if running.get("startedAt"):
                if started_at is None or running["startedAt"] > started_at:
                    started_at = running["startedAt"]

        k8s_reason = None
        phase = status.get("phase", "Unknown")
        if phase in K8S_FAILURE_PHASES:
            k8s_reason = phase
        else:
            for cs in statuses + (status.get("initContainerStatuses") or []):
                w = (cs.get("state") or {}).get("waiting") or {}
                if w.get("reason") in K8S_FAILURE_REASONS:
                    k8s_reason = w["reason"]
                    break

        pods.append({
            "name": meta.get("name", ""), "namespace": meta.get("namespace", NAMESPACE),
            "status": phase, "restarts": restarts, "node": spec.get("nodeName"),
            "labels": labels, "age": meta.get("creationTimestamp", ""),
            "ready": ready, "started_at": started_at,
            "_expected": 1 if expected == "anomaly" else 0,
            "_type": labels.get("aikube.bench/type", "?"),
            "_k8s_failure": k8s_reason,
        })
    return pods


def verdicts(pod, flagged):
    from ai_module.detection_comparator import (
        rules_predict, rules_v2_predict, rules_v3_predict, ml_predict)
    m = ml_predict(pod)
    return {
        "rules_v1": 1 if rules_predict(pod)["is_anomaly"] else 0,
        "rules_v2": 1 if rules_v2_predict(pod)["is_anomaly"] else 0,
        "rules_v3": 1 if rules_v3_predict(pod)["is_anomaly"] else 0,
        "ml":       1 if m["is_anomaly"] else 0,
        "k8sgpt":   1 if pod["name"] in flagged else 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSE — logique identique à la campagne précédente
# ══════════════════════════════════════════════════════════════════════════════

def first_occurrence(timeline, predicate):
    for t, snap in timeline:
        if predicate(snap):
            return t
    return None


def analyse(series):
    pod_names = sorted({n for _, snap in series for n in snap})
    results = []

    def is_healthy(s):
        return s.get("k8s_failure") is None and s.get("status") in ("Running", "Succeeded")

    for name in pod_names:
        timeline = [(t, snap[name]) for t, snap in series if name in snap]
        if not timeline:
            continue

        meta  = timeline[-1][1]
        t_k8s = first_occurrence(timeline, lambda s: s.get("k8s_failure") is not None)
        t_first_healthy = first_occurrence(timeline, is_healthy)

        eligible = (t_first_healthy is not None
                    and (t_k8s is None or t_k8s >= t_first_healthy))

        entry = {"pod": name, "type": meta.get("type"),
                 "expected": meta.get("expected"),
                 "k8s_failure_reason": meta.get("k8s_failure"),
                 "t_k8s_verdict": t_k8s, "t_first_healthy": t_first_healthy,
                 "eligible": eligible, "detectors": {}}

        for key, _ in DETECTORS:
            t_det = None
            if t_first_healthy is not None:
                for t, snap in timeline:
                    if t >= t_first_healthy and snap["verdicts"].get(key) == 1:
                        t_det = t
                        break

            lead, category = None, None
            if not eligible:
                category = ("défaillant dès la création" if t_first_healthy is None
                            else "défaillance antérieure au démarrage")
            elif t_det is None and t_k8s is None:
                category = "jamais signalé"
            elif t_det is None:
                category = "angle mort"
            elif t_k8s is None:
                category = "hors périmètre"
            else:
                lead = t_k8s - t_det
                category = ("anticipation" if lead > 0 else
                            "simultané" if lead == 0 else "retard")

            entry["detectors"][key] = {"t_first_detection": t_det,
                                       "lead_seconds": lead, "category": category}
        results.append(entry)

    summary = {}
    for key, label in DETECTORS:
        leads = [r["detectors"][key]["lead_seconds"] for r in results
                 if r["eligible"] and r["detectors"][key]["lead_seconds"] is not None]
        cats = {}
        for r in results:
            c = r["detectors"][key]["category"]
            cats[c] = cats.get(c, 0) + 1

        stats = None
        if leads:
            s = sorted(leads); n = len(s)
            median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
            stats = {"mean": round(sum(s) / n, 1), "median": median,
                     "min": min(s), "max": max(s), "n": n,
                     "anticipations": sum(1 for x in s if x > 0),
                     "retards": sum(1 for x in s if x < 0)}
        summary[key] = {"label": label, "lead": stats, "categories": cats}

    return {"pods": results, "summary": summary,
            "eligible_pods": sum(1 for r in results if r["eligible"]),
            "total_pods": len(results)}


# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Anticipation, K8sGPT inclus")
    ap.add_argument("--duration", type=int, default=600)
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--no-deploy", action="store_true")
    args = ap.parse_args()

    try:
        v = subprocess.run(["k8sgpt", "version"], capture_output=True,
                           text=True, timeout=20)
        version = (v.stdout or v.stderr).strip().split("\n")[0]
    except FileNotFoundError:
        print("✖ k8sgpt introuvable")
        sys.exit(1)
    print(f"▶ {version}")

    if not args.no_deploy:
        print("▶ Déploiement du banc")
        for manifest in ["anomaly-benchmark.yaml", "anomaly-benchmark-edge.yaml"]:
            path = os.path.join(PROJECT_ROOT, "k8s", "benchmark", manifest)
            r = subprocess.run(["kubectl", "apply", "-f", path],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                print(f"✖ {r.stderr[:200]}")
                sys.exit(1)

    t0 = time.time()
    series = []
    print(f"\n▶ Observation : {args.duration}s, échantillon toutes les {args.interval}s\n")
    print(f"  {'t (s)':>6}  {'K8s déf.':>9}  détections v1/v2/v3/ML/K8sGPT")

    while time.time() - t0 < args.duration:
        t = int(time.time() - t0)
        pods = sample_pods()
        if pods is None:
            print(f"  {t:>6}  API indisponible")
            time.sleep(args.interval)
            continue

        flagged = k8sgpt_flagged()

        snap = {}
        for p in pods:
            snap[p["name"]] = {
                "status": p["status"], "restarts": p["restarts"],
                "ready": p["ready"], "k8s_failure": p["_k8s_failure"],
                "expected": p["_expected"], "type": p["_type"],
                "verdicts": verdicts(p, flagged),
            }
        series.append((t, snap))

        n_k8s = sum(1 for s in snap.values() if s["k8s_failure"])
        counts = "/".join(str(sum(s["verdicts"][k] for s in snap.values()))
                          for k, _ in DETECTORS)
        print(f"  {t:>6}  {n_k8s:>9}  {counts}")

        time.sleep(max(0, args.interval - (time.time() - t0 - t)))

    if not series:
        print("✖ Aucun échantillon")
        sys.exit(1)

    res = analyse(series)

    print(f"\n{'='*80}")
    print("  TEMPS D'ANTICIPATION — COMPARAISON À K8sGPT")
    print(f"{'='*80}")
    print(f"  Échantillons : {len(series)} · intervalle {args.interval}s")
    print(f"  Pods éligibles : {res['eligible_pods']} / {res['total_pods']}")
    print("  Référence : premier verdict de défaillance de Kubernetes.\n")

    print(f"  {'Détecteur':<16} {'médiane':>9} {'moyenne':>9} {'min':>7} {'max':>7} "
          f"{'n':>4}  {'anticipe':>9} {'retarde':>8}")
    print(f"  {'-'*16} {'-'*9} {'-'*9} {'-'*7} {'-'*7} {'-'*4}  {'-'*9} {'-'*8}")
    for key, label in DETECTORS:
        s = res["summary"][key]["lead"]
        if s is None:
            print(f"  {label:<16} {'aucune mesure exploitable':>50}")
            continue
        print(f"  {label:<16} {s['median']:>9.0f} {s['mean']:>9.1f} {s['min']:>7} "
              f"{s['max']:>7} {s['n']:>4}  {s['anticipations']:>9} {s['retards']:>8}")

    print(f"\n  Instants de première détection (secondes après injection)")
    print(f"  {'pod':<30} {'K8s':>6}" + "".join(f"{l.split()[-1]:>9}" for _, l in DETECTORS))
    for r in sorted(res["pods"], key=lambda x: (-(x["expected"] or 0), x["pod"])):
        tk = f"{r['t_k8s_verdict']}s" if r["t_k8s_verdict"] is not None else "—"
        row = f"  {r['pod']:<30} {tk:>6}"
        for k, _ in DETECTORS:
            d = r["detectors"][k]["t_first_detection"]
            row += f"{(str(d)+'s' if d is not None else '—'):>9}"
        print(row)

    # Écart direct v3 / K8sGPT : c'est la question posée
    print(f"\n  Écart entre Règles v3 et K8sGPT")
    diffs = []
    for r in res["pods"]:
        a = r["detectors"]["rules_v3"]["t_first_detection"]
        b = r["detectors"]["k8sgpt"]["t_first_detection"]
        if a is not None and b is not None and a != b:
            diffs.append((r["pod"], a, b, b - a))
    if not diffs:
        print("    aucun écart : les deux signalent au même échantillon")
    else:
        for pod, a, b, d in sorted(diffs, key=lambda x: x[3]):
            qui = "v3 précède" if d > 0 else "K8sGPT précède"
            print(f"    {pod:<32} v3={a}s  k8sgpt={b}s  → {qui} de {abs(d)}s")

    print(f"{'='*80}\n")

    os.makedirs(OBS_DIR, exist_ok=True)
    with open(OUT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "k8sgpt_version": version,
            "duration": args.duration, "interval": args.interval,
            "samples": len(series), "summary": res["summary"], "pods": res["pods"],
        }, ensure_ascii=False) + "\n")
    print(f"✓ Résultats : {OUT_PATH}")
    print(f"ℹ Ne pas oublier :  kubectl delete namespace {NAMESPACE}\n")


if __name__ == "__main__":
    main()

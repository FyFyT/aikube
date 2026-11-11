#!/usr/bin/env python3
"""
scripts/leadtime_analysis.py
================================================================================
Temps d'anticipation des détecteurs d'anomalies.

QUESTION POSÉE
--------------
Les détecteurs évalués jusqu'ici constatent un état présent : un pod a déjà
redémarré, ou n'est pas disponible. Aucun n'a été conçu pour anticiper. La
question est donc empirique : combien de temps AVANT que la défaillance ne
devienne manifeste un détecteur la signale-t-il — si tant est qu'il l'anticipe ?

DIFFICULTÉ MÉTHODOLOGIQUE : DÉFINIR L'INSTANT DE LA PANNE
--------------------------------------------------------
Prendre l'un des détecteurs comme référence rendrait la mesure circulaire. Il
faut un juge indépendant, et il en existe un : Kubernetes lui-même. Le
kubelet et le contrôleur déclarent un pod défaillant selon leur propre logique
(backoff exponentiel, échec de récupération d'image, terminaison en erreur),
sans rapport avec les règles évaluées ici.

L'instant de référence retenu est donc le premier échantillon où Kubernetes
marque le pod par l'un des états suivants :

    CrashLoopBackOff · ImagePullBackOff · ErrImagePull
    CreateContainerConfigError · CreateContainerError · InvalidImageName
    ou phase = Failed

Le temps d'anticipation d'un détecteur vaut alors :

    lead = t(verdict Kubernetes) − t(premier signalement du détecteur)

Positif, le détecteur précède le système. Négatif, il le suit.

TROIS CATÉGORIES DE RÉSULTAT
----------------------------
  · anticipation mesurable  : les deux instants existent
  · détection hors périmètre : le détecteur signale un pod que Kubernetes ne
    déclare jamais défaillant (cas de edge-never-ready — Running, jamais Ready,
    aucun redémarrage). Non chiffrable en secondes, mais informatif.
  · angle mort : Kubernetes déclare une défaillance que le détecteur ne voit
    jamais.

LIMITE ASSUMÉE
--------------
L'anticipation est mesurée par rapport au verdict de Kubernetes, non par
rapport à la dégradation réelle du service rendu. Un pod peut cesser de servir
avant que Kubernetes ne le déclare, ou continuer de servir après. La référence
est donc conventionnelle — mais externe et reproductible, ce qui est le point
essentiel.

USAGE
  python3 scripts/leadtime_analysis.py --duration 600 --interval 15
  python3 scripts/leadtime_analysis.py --no-deploy --duration 300
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

NAMESPACE   = "aikube-bench"
OBS_DIR     = os.path.join(PROJECT_ROOT, "data", "observations")
OUT_PATH    = os.path.join(OBS_DIR, "leadtime_analysis.jsonl")

DETECTORS = [("rules_v1", "Règles v1"), ("rules_v2", "Règles v2"),
             ("rules_v3", "Règles v3"), ("ml", "Random Forest")]

# États par lesquels Kubernetes déclare lui-même une défaillance.
K8S_FAILURE_REASONS = {
    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
    "CreateContainerConfigError", "CreateContainerError",
    "InvalidImageName", "RunContainerError",
}
K8S_FAILURE_PHASES = {"Failed"}


# ══════════════════════════════════════════════════════════════════════════════
# COLLECTE
# ══════════════════════════════════════════════════════════════════════════════

def sample_pods():
    """Un échantillon de l'état du banc, avec verdict Kubernetes."""
    r = subprocess.run(
        ["kubectl", "get", "pods", "-n", NAMESPACE, "-o", "json"],
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

        # Verdict Kubernetes, toutes causes confondues
        k8s_reason = None
        phase = status.get("phase", "Unknown")
        if phase in K8S_FAILURE_PHASES:
            k8s_reason = phase
        else:
            for cs in statuses:
                w = (cs.get("state") or {}).get("waiting") or {}
                if w.get("reason") in K8S_FAILURE_REASONS:
                    k8s_reason = w["reason"]
                    break
            if k8s_reason is None:
                for cs in status.get("initContainerStatuses", []) or []:
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


def verdicts(pod):
    """Verdict des quatre détecteurs sur un pod."""
    from ai_module.detection_comparator import (
        rules_predict, rules_v2_predict, rules_v3_predict, ml_predict)
    m = ml_predict(pod)
    return {
        "rules_v1": 1 if rules_predict(pod)["is_anomaly"] else 0,
        "rules_v2": 1 if rules_v2_predict(pod)["is_anomaly"] else 0,
        "rules_v3": 1 if rules_v3_predict(pod)["is_anomaly"] else 0,
        "ml":       1 if m["is_anomaly"] else 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSE
# ══════════════════════════════════════════════════════════════════════════════

def first_occurrence(timeline, predicate):
    """Premier instant (en secondes depuis l'injection) vérifiant le prédicat."""
    for t, snap in timeline:
        if predicate(snap):
            return t
    return None


def analyse(series, startup_grace=None):
    """
    ÉLIGIBILITÉ À LA MESURE D'ANTICIPATION

    Un pod n'est éligible que s'il a été observé SAIN au moins une fois avant sa
    défaillance. Deux raisons :

      · On ne peut anticiper que ce qui n'est pas déjà survenu. Un pod
        défaillant dès sa création (image introuvable, planification
        impossible) ne laisse aucune fenêtre d'anticipation.

      · À la création, tout pod passe par Pending le temps du
        ContainerCreating. Les règles signalant Pending comme anomalie,
        l'ensemble des pods — sains compris — est signalé au premier
        échantillon. Sans ce filtre, l'instant de première détection vaut zéro
        pour tous, et le temps d'anticipation se réduit à l'instant du verdict
        Kubernetes. Une première campagne a produit des valeurs identiques pour
        les quatre détecteurs, artefact de ce biais.
    """
    pod_names = sorted({name for _, snap in series for name in snap})
    results = []

    def is_healthy_sample(x):
        return x.get("k8s_failure") is None and x.get("status") in ("Running", "Succeeded")

    for name in pod_names:
        timeline = [(t, snap[name]) for t, snap in series if name in snap]
        if not timeline:
            continue

        meta  = timeline[-1][1]
        t_k8s = first_occurrence(timeline, lambda x: x.get("k8s_failure") is not None)
        t_first_healthy = first_occurrence(timeline, is_healthy_sample)

        if startup_grace is not None:
            t_valid_from = startup_grace
        elif t_first_healthy is not None:
            t_valid_from = t_first_healthy
        else:
            t_valid_from = None

        eligible = (t_first_healthy is not None
                    and (t_k8s is None or t_k8s >= t_first_healthy))

        entry = {
            "pod": name, "type": meta.get("type"), "expected": meta.get("expected"),
            "final_status": meta.get("status"), "final_restarts": meta.get("restarts"),
            "k8s_failure_reason": meta.get("k8s_failure"), "t_k8s_verdict": t_k8s,
            "t_first_healthy": t_first_healthy, "eligible": eligible, "detectors": {},
        }

        for key, _ in DETECTORS:
            t_det = None
            if t_valid_from is not None:
                for t, snap in timeline:
                    if t >= t_valid_from and snap["verdicts"].get(key) == 1:
                        t_det = t
                        break

            lead, category = None, None
            if not eligible:
                category = ("défaillant dès la création" if t_first_healthy is None
                            else "défaillance antérieure au démarrage")
            elif t_det is None and t_k8s is None:
                category = "jamais signalé (ni détecteur ni Kubernetes)"
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
            srt = sorted(leads); n = len(srt)
            median = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
            stats = {"mean": round(sum(srt) / n, 1), "median": median,
                     "min": min(srt), "max": max(srt), "n": n,
                     "anticipations": sum(1 for x in srt if x > 0),
                     "retards": sum(1 for x in srt if x < 0)}
        summary[key] = {"label": label, "lead": stats, "categories": cats}

    return {"pods": results, "summary": summary,
            "eligible_pods": sum(1 for r in results if r["eligible"]),
            "total_pods": len(results)}


# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Temps d'anticipation des détecteurs")
    ap.add_argument("--duration", type=int, default=600, help="durée d'observation (s)")
    ap.add_argument("--interval", type=int, default=15, help="intervalle d'échantillonnage (s)")
    ap.add_argument("--no-deploy", action="store_true")
    ap.add_argument("--grace", type=int, default=None,
                    help="ignorer les détections avant cet instant (s)")
    args = ap.parse_args()

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
    n_samples = args.duration // args.interval

    print(f"\n▶ Observation : {args.duration}s, un échantillon toutes les {args.interval}s "
          f"({n_samples} points)\n")
    print(f"  {'t (s)':>6}  {'pods':>5}  {'K8s défaillants':>16}  détections v1/v2/v3/ML")

    while time.time() - t0 < args.duration:
        t = int(time.time() - t0)
        pods = sample_pods()
        if pods is None:
            print(f"  {t:>6}  API indisponible, nouvelle tentative")
            time.sleep(args.interval)
            continue

        snap = {}
        for p in pods:
            snap[p["name"]] = {
                "status": p["status"], "restarts": p["restarts"],
                "ready": p["ready"], "k8s_failure": p["_k8s_failure"],
                "expected": p["_expected"], "type": p["_type"],
                "verdicts": verdicts(p),
            }
        series.append((t, snap))

        n_k8s = sum(1 for s in snap.values() if s["k8s_failure"])
        counts = "/".join(str(sum(s["verdicts"][k] for s in snap.values()))
                          for k, _ in DETECTORS)
        print(f"  {t:>6}  {len(snap):>5}  {n_k8s:>16}  {counts}")

        time.sleep(max(0, args.interval - (time.time() - t0 - t)))

    if not series:
        print("✖ Aucun échantillon collecté")
        sys.exit(1)

    res = analyse(series, startup_grace=args.grace)

    # ── Rapport ───────────────────────────────────────────────────────────────
    print(f"\n{'='*78}")
    print("  TEMPS D'ANTICIPATION PAR RAPPORT AU VERDICT DE KUBERNETES")
    print(f"{'='*78}")
    print(f"  Échantillons : {len(series)}  ·  intervalle {args.interval}s "
          f"·  durée {args.duration}s")
    print(f"  Pods éligibles : {res['eligible_pods']} / {res['total_pods']} "
          f"(observés sains avant leur défaillance)")
    print("  Référence : premier instant où Kubernetes déclare le pod défaillant.")
    print("  Anticipation positive = le détecteur précède le système.\n")

    print(f"  {'Détecteur':<15} {'médiane':>9} {'moyenne':>9} {'min':>7} {'max':>7} "
          f"{'n':>4}  {'anticipe':>9} {'retarde':>8}")
    print(f"  {'-'*15} {'-'*9} {'-'*9} {'-'*7} {'-'*7} {'-'*4}  {'-'*9} {'-'*8}")
    for key, label in DETECTORS:
        s = res["summary"][key]["lead"]
        if s is None:
            print(f"  {label:<15} {'aucune mesure exploitable':>50}")
            continue
        print(f"  {label:<15} {s['median']:>9.0f} {s['mean']:>9.1f} {s['min']:>7} "
              f"{s['max']:>7} {s['n']:>4}  {s['anticipations']:>9} {s['retards']:>8}")

    print(f"\n  Répartition des cas")
    for key, label in DETECTORS:
        cats = res["summary"][key]["categories"]
        detail = " · ".join(f"{k} : {v}" for k, v in sorted(cats.items()))
        print(f"    {label:<15} {detail}")

    # Cas remarquables
    hors = [(r["pod"], k) for r in res["pods"] for k, _ in DETECTORS
            if r["detectors"][k]["category"] == "hors périmètre"]
    if hors:
        print(f"\n  Détections hors périmètre Kubernetes")
        print("    (le détecteur signale, le système ne déclare jamais rien)")
        seen = set()
        for pod, k in hors:
            if pod in seen:
                continue
            seen.add(pod)
            who = [lbl for kk, lbl in DETECTORS
                   if any(p == pod and kk == k2 for p, k2 in hors)]
            print(f"      {pod:<32} signalé par {', '.join(who)}")

    aveugles = [(r["pod"], k) for r in res["pods"] for k, _ in DETECTORS
                if r["detectors"][k]["category"] == "angle mort"]
    if aveugles:
        print(f"\n  Angles morts (Kubernetes déclare, le détecteur ne voit pas)")
        for pod, k in aveugles[:10]:
            lbl = dict(DETECTORS)[k]
            print(f"      {pod:<32} manqué par {lbl}")

    print(f"\n  Détail par pod")
    print(f"  {'pod':<30} {'K8s':>6}  " + "".join(f"{l.split()[-1]:>8}" for _, l in DETECTORS))
    for r in sorted(res["pods"], key=lambda x: (-(x["expected"] or 0), x["pod"])):
        tk = f"{r['t_k8s_verdict']}s" if r["t_k8s_verdict"] is not None else "—"
        row = f"  {r['pod']:<30} {tk:>6}  "
        for k, _ in DETECTORS:
            d = r["detectors"][k]
            row += f"{(str(d['t_first_detection'])+'s' if d['t_first_detection'] is not None else '—'):>8}"
        print(row)

    print(f"{'='*78}\n")

    os.makedirs(OBS_DIR, exist_ok=True)
    with open(OUT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration": args.duration, "interval": args.interval,
            "samples": len(series), "summary": res["summary"],
            "pods": res["pods"],
        }, ensure_ascii=False) + "\n")
    print(f"✓ Résultats : {OUT_PATH}")
    print(f"ℹ Ne pas oublier :  kubectl delete namespace {NAMESPACE}\n")


if __name__ == "__main__":
    main()

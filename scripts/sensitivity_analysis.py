#!/usr/bin/env python3
"""
scripts/sensitivity_analysis.py
================================================================================
Deux analyses complémentaires aux campagnes de banc.

--------------------------------------------------------------------------------
MODE 1 — BOOTSTRAP  (aucune nouvelle mesure, calcul sur données existantes)
--------------------------------------------------------------------------------
Un F1 de 0,833 mesuré sur 21 pods est un point sans incertitude associée.
Déplacer un seul pod d'une classe à l'autre le modifie de près de 0,05 : la
précision apparente des trois décimales est illusoire.

Le rééchantillonnage bootstrap estime cette incertitude sans hypothèse de
distribution : on tire avec remise B échantillons de même taille que
l'original, on recalcule la métrique sur chacun, et les quantiles empiriques
de la distribution obtenue donnent l'intervalle de confiance.

Limite à rapporter : le bootstrap estime la variabilité d'échantillonnage
DANS la population mesurée. Il ne dit rien de la représentativité du banc
lui-même vis-à-vis des défaillances réelles.

--------------------------------------------------------------------------------
MODE 2 — SENSIBILITÉ À LA FENÊTRE D'OBSERVATION  (une campagne)
--------------------------------------------------------------------------------
Toutes les campagnes ont employé une fenêtre de 300 secondes. Or Kubernetes
applique un backoff exponentiel aux redémarrages (10 s, 20 s, 40 s, 80 s…) :
le compteur observé, et donc le taux, dépendent directement de la durée
d'attente. Un même pod peut être classé différemment selon l'instant de mesure.

Ce mode déploie le banc UNE fois et mesure à plusieurs instants successifs,
produisant l'évolution des métriques en fonction du temps d'observation. Une
seule injection, donc un coût réduit pour le plan de contrôle.

Le résultat attendu — et à vérifier — est que les détecteurs fondés sur le
compteur ou le taux voient leurs métriques varier, tandis que le détecteur
fondé sur la stabilité récente reste stable.

USAGE
  python3 scripts/sensitivity_analysis.py bootstrap
  python3 scripts/sensitivity_analysis.py temporal --at 60,120,300,600,900
================================================================================
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE         = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))

OBS_DIR      = os.path.join(PROJECT_ROOT, "data", "observations")
BENCH_PATH   = os.path.join(OBS_DIR, "benchmark_results.jsonl")
TEMPORAL_PATH = os.path.join(OBS_DIR, "temporal_sensitivity.jsonl")
NAMESPACE    = "aikube-bench"

DETECTORS = [("rules_v1", "Règles v1"), ("rules_v2", "Règles v2"),
             ("rules_v3", "Règles v3"), ("ml", "Random Forest")]


# ══════════════════════════════════════════════════════════════════════════════
# MÉTRIQUES
# ══════════════════════════════════════════════════════════════════════════════

def metrics_from(pairs):
    """pairs : liste de (vérité, prédiction)."""
    vp = sum(1 for t, p in pairs if t == 1 and p == 1)
    fp = sum(1 for t, p in pairs if t == 0 and p == 1)
    vn = sum(1 for t, p in pairs if t == 0 and p == 0)
    fn = sum(1 for t, p in pairs if t == 1 and p == 0)

    precision   = vp / (vp + fp) if (vp + fp) else None
    recall      = vp / (vp + fn) if (vp + fn) else None
    specificity = vn / (vn + fp) if (vn + fp) else None
    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    return {"VP": vp, "FP": fp, "VN": vn, "FN": fn, "precision": precision,
            "recall": recall, "f1": f1, "specificity": specificity}


def load_last_campaign():
    if not os.path.exists(BENCH_PATH):
        return None
    entries = []
    with open(BENCH_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries[-1] if entries else None


# ══════════════════════════════════════════════════════════════════════════════
# MODE 1 — BOOTSTRAP
# ══════════════════════════════════════════════════════════════════════════════

def bootstrap_ci(pairs, metric_key, iterations=5000, alpha=0.05, seed=42):
    """
    Intervalle de confiance par percentiles du bootstrap.

    Les tirages produisant une métrique indéfinie (dénominateur nul) sont
    écartés et comptabilisés : sur de petits échantillons, un rééchantillon
    peut ne contenir aucun cas positif.
    """
    rng = random.Random(seed)
    n = len(pairs)
    values, undefined = [], 0

    for _ in range(iterations):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        m = metrics_from(sample)[metric_key]
        if m is None:
            undefined += 1
        else:
            values.append(m)

    if not values:
        return None

    values.sort()
    lo = values[int(alpha / 2 * len(values))]
    hi = values[min(int((1 - alpha / 2) * len(values)), len(values) - 1)]
    mean = sum(values) / len(values)
    var  = sum((v - mean) ** 2 for v in values) / max(len(values) - 1, 1)

    return {"point": metrics_from(pairs)[metric_key],
            "mean": round(mean, 4), "stdev": round(var ** 0.5, 4),
            "ci_low": round(lo, 4), "ci_high": round(hi, 4),
            "undefined_draws": undefined, "iterations": iterations}


def run_bootstrap(args):
    camp = load_last_campaign()
    if not camp:
        print(f"✖ Aucune campagne dans {BENCH_PATH}")
        sys.exit(1)

    details = camp.get("details", [])
    if not details:
        print("✖ Campagne sans détail par pod")
        sys.exit(1)

    print(f"\n{'='*78}")
    print("  INTERVALLES DE CONFIANCE PAR BOOTSTRAP")
    print(f"{'='*78}")
    print(f"  Campagne     : {camp.get('timestamp','?')}")
    print(f"  Échantillon  : {len(details)} pods")
    print(f"  Rééchantillonnages : {args.iterations}  ·  niveau 95 %")
    print(f"\n  {'Détecteur':<14} {'métrique':<12} {'point':>7} {'IC 95 %':>18} {'écart-type':>11}")
    print(f"  {'-'*14} {'-'*12} {'-'*7} {'-'*18} {'-'*11}")

    results = {}
    for dkey, dlabel in DETECTORS:
        if not details or dkey not in details[0]:
            continue
        pairs = [(d["expected"], d[dkey]) for d in details]
        results[dkey] = {}
        for mkey, mlabel in [("f1", "F1"), ("precision", "Précision"),
                             ("recall", "Rappel"), ("specificity", "Spécificité")]:
            ci = bootstrap_ci(pairs, mkey, args.iterations)
            if ci is None:
                continue
            results[dkey][mkey] = ci
            pt = f"{ci['point']:.3f}" if ci["point"] is not None else "  n/a"
            print(f"  {dlabel:<14} {mlabel:<12} {pt:>7} "
                  f"[{ci['ci_low']:.3f} ; {ci['ci_high']:.3f}]{'':>3} {ci['stdev']:>11.4f}")
        print()

    # ── Détecteurs sans erreur : le bootstrap est dégénéré ───────────────────
    # Si le détecteur ne commet aucune erreur sur l'échantillon original, aucun
    # rééchantillon n'en contiendra : l'intervalle se réduit au point [1 ; 1],
    # ce qui suggère à tort une certitude parfaite. La borne honnête s'obtient
    # par la règle de trois : avec 0 échec sur n essais, la borne inférieure de
    # l'intervalle à 95 % vaut approximativement 1 − 3/n.
    n_obs = len(details)
    perfect = []
    for dkey, dlabel in DETECTORS:
        if dkey not in results or "f1" not in results[dkey]:
            continue
        pairs = [(d["expected"], d[dkey]) for d in details]
        m = metrics_from(pairs)
        if m["FP"] == 0 and m["FN"] == 0:
            perfect.append((dlabel, round(1 - 3 / n_obs, 4)))

    if perfect:
        print("  ⚠ Détecteurs sans erreur sur cet échantillon")
        for dlabel, bound in perfect:
            print(f"    {dlabel:<16} bootstrap dégénéré ([1,000 ; 1,000] est un artefact).")
            print(f"    {'':<16} Règle de trois : borne inférieure ≈ {bound:.3f} "
                  f"(0 erreur sur {n_obs}).")
        print()

    # Largeur moyenne des intervalles : indicateur direct de la faiblesse de l'échantillon
    widths = [r["f1"]["ci_high"] - r["f1"]["ci_low"]
              for r in results.values() if "f1" in r]
    if widths:
        avg = sum(widths) / len(widths)
        print(f"  Largeur moyenne des intervalles sur le F1 : {avg:.3f}")
        if avg > 0.15:
            print("  → Intervalles larges : l'effectif ne permet pas de départager")
            print("    des détecteurs dont les F1 diffèrent de moins de cette largeur.")

        # Chevauchement deux à deux : quels détecteurs sont réellement distinguables ?
        print("\n  Distinguabilité deux à deux (chevauchement des IC sur le F1)")
        keys = [(k, l) for k, l in DETECTORS if k in results and "f1" in results[k]]
        for i, (ka, la) in enumerate(keys):
            for kb, lb in keys[i + 1:]:
                a, b = results[ka]["f1"], results[kb]["f1"]
                overlap = not (a["ci_high"] < b["ci_low"] or b["ci_high"] < a["ci_low"])
                verdict = "NON distinguables" if overlap else "distinguables"
                print(f"    {la:<14} / {lb:<14} {verdict}")

    out = os.path.join(OBS_DIR, "bootstrap_ci.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat(),
                   "campaign": camp.get("timestamp"),
                   "sample_size": len(details),
                   "iterations": args.iterations,
                   "results": results}, f, indent=2, ensure_ascii=False)
    print(f"\n✓ Résultats : {out}")
    print(f"{'='*78}\n")


# ══════════════════════════════════════════════════════════════════════════════
# MODE 2 — SENSIBILITÉ TEMPORELLE
# ══════════════════════════════════════════════════════════════════════════════

def fetch_and_evaluate():
    """Mesure ponctuelle sur le banc déployé."""
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
    from anomaly_benchmark import fetch_bench_pods, evaluate
    pods = fetch_bench_pods()
    if not pods:
        return None
    return evaluate(pods)


def run_temporal(args):
    instants = sorted(int(x) for x in args.at.split(","))
    print(f"\n{'='*78}")
    print("  SENSIBILITÉ À LA FENÊTRE D'OBSERVATION")
    print(f"{'='*78}")
    print(f"  Instants de mesure : {instants} secondes")
    print(f"  Une seule injection, mesures successives sur le même déploiement.")

    if not args.no_deploy:
        for manifest in ["anomaly-benchmark.yaml", "anomaly-benchmark-edge.yaml"]:
            path = os.path.join(PROJECT_ROOT, "k8s", "benchmark", manifest)
            r = subprocess.run(["kubectl", "apply", "-f", path],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                print(f"✖ Déploiement échoué : {r.stderr[:200]}")
                sys.exit(1)
        print(f"\n▶ Banc déployé")

    series = []
    elapsed = 0
    for t in instants:
        wait = t - elapsed
        if wait > 0:
            print(f"\n▶ Attente jusqu'à t = {t}s ({wait}s)…")
            time.sleep(wait)
            elapsed = t

        rep = fetch_and_evaluate()
        if rep is None:
            print(f"  ⚠ t={t}s : aucun pod étiqueté")
            continue

        entry = {"t_seconds": t, "detectors": rep["detectors"],
                 "restarts": {d["pod"]: d["restarts"] for d in rep["details"]}}
        series.append(entry)

        line = f"  t={t:>4}s  "
        for dkey, dlabel in DETECTORS:
            f1 = rep["detectors"].get(dkey, {}).get("f1")
            line += f"{dlabel.split()[-1]}={f1:.3f} " if f1 is not None else f"{dlabel.split()[-1]}=n/a "
        total_restarts = sum(entry["restarts"].values())
        print(line + f" · {total_restarts} redémarrages cumulés")

    # ── Synthèse ──────────────────────────────────────────────────────────────
    if len(series) > 1:
        print(f"\n{'─'*78}")
        print("  ÉVOLUTION DU F1 PAR DÉTECTEUR")
        print(f"{'─'*78}")
        header = "  " + "t (s)".ljust(10) + "".join(l.ljust(16) for _, l in DETECTORS)
        print(header)
        for e in series:
            row = "  " + str(e["t_seconds"]).ljust(10)
            for dkey, _ in DETECTORS:
                f1 = e["detectors"].get(dkey, {}).get("f1")
                row += (f"{f1:.3f}" if f1 is not None else "n/a").ljust(16)
            print(row)

        print(f"\n  Amplitude de variation du F1")
        for dkey, dlabel in DETECTORS:
            vals = [e["detectors"].get(dkey, {}).get("f1") for e in series]
            vals = [v for v in vals if v is not None]
            if len(vals) > 1:
                amp = max(vals) - min(vals)
                verdict = "stable" if amp < 0.02 else ("sensible" if amp < 0.15 else "très sensible")
                print(f"    {dlabel:<16} min {min(vals):.3f} · max {max(vals):.3f} "
                      f"· amplitude {amp:.3f}  ({verdict})")

    with open(TEMPORAL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
                            "instants": instants, "series": series},
                           ensure_ascii=False) + "\n")
    print(f"\n✓ Série enregistrée : {TEMPORAL_PATH}")
    print(f"\nℹ Ne pas oublier :  kubectl delete namespace {NAMESPACE}")
    print(f"{'='*78}\n")


# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Analyses de sensibilité")
    sub = ap.add_subparsers(dest="mode", required=True)

    b = sub.add_parser("bootstrap", help="intervalles de confiance (sans nouvelle mesure)")
    b.add_argument("--iterations", type=int, default=5000)

    t = sub.add_parser("temporal", help="sensibilité à la fenêtre d'observation")
    t.add_argument("--at", default="60,120,300,600,900",
                   help="instants de mesure en secondes, séparés par des virgules")
    t.add_argument("--no-deploy", action="store_true")

    args = ap.parse_args()
    if args.mode == "bootstrap":
        run_bootstrap(args)
    else:
        run_temporal(args)


if __name__ == "__main__":
    main()

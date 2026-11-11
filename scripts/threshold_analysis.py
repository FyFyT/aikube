#!/usr/bin/env python3
"""
scripts/threshold_analysis.py
================================================================================
Justification empirique du seuil de taux de redémarrage.

PROBLÈME
--------
Le détecteur « règles v2 » signale un pod dont le taux de redémarrage dépasse
0,5 par heure. Cette valeur a été posée a priori, sans appui sur une
distribution observée. Un seuil non justifié est le point faible d'une règle :
il invite immédiatement la question « pourquoi 0,5 et pas 0,3 ou 2 ? ».

Ce script y répond par trois analyses indépendantes.

1. DISTRIBUTION OBSERVÉE
   Où se situent réellement les taux, sur les pods de production comme sur les
   pods du banc ? Existe-t-il une rupture naturelle séparant les deux
   populations ? On identifie le plus grand écart relatif entre valeurs
   consécutives triées (analyse de discontinuité).

2. BALAYAGE DE SEUIL
   Pour chaque valeur candidate, calcul de la précision, du rappel, du F1 et de
   la spécificité contre la vérité terrain du banc. Produit une courbe et non
   un point : si la performance est stable sur une large plage, le choix exact
   importe peu — et c'est une justification plus solide qu'un optimum ponctuel,
   qui serait un surajustement au jeu de mesure.

3. SÉPARABILITÉ
   Les deux classes se chevauchent-elles dans l'espace du taux ? Si les pods
   sains et anormaux occupent des intervalles disjoints, un seuil existe. S'ils
   se recouvrent, AUCUN seuil ne peut les séparer, et il faut une variable
   supplémentaire — c'est l'argument qui fonde le détecteur v3.

SOURCES
-------
  · data/observations/benchmark_results.jsonl  vérité terrain (banc)
  · cluster réel, collecté en direct           population de production

USAGE
  python3 scripts/threshold_analysis.py
  python3 scripts/threshold_analysis.py --no-cluster    # banc uniquement
  python3 scripts/threshold_analysis.py --csv           # export pour tracé
================================================================================
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE         = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))

OBS_DIR      = os.path.join(PROJECT_ROOT, "data", "observations")
BENCH_PATH   = os.path.join(OBS_DIR, "benchmark_results.jsonl")
CSV_PATH     = os.path.join(OBS_DIR, "threshold_sweep.csv")

CURRENT_THRESHOLD = 0.5     # valeur en vigueur dans rules_v2


# ══════════════════════════════════════════════════════════════════════════════
# COLLECTE
# ══════════════════════════════════════════════════════════════════════════════

def load_benchmark_samples(all_campaigns=False):
    """
    Extrait (taux, vérité, nom, type) des campagnes de banc enregistrées.

    Par défaut, seule la dernière campagne est utilisée : mélanger des
    campagnes de fenêtres d'observation différentes fausserait les taux, qui
    dépendent directement de la durée d'attente.
    """
    if not os.path.exists(BENCH_PATH):
        return [], None

    entries = []
    with open(BENCH_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not entries:
        return [], None

    selected = entries if all_campaigns else [entries[-1]]

    samples = []
    for e in selected:
        for d in e.get("details", []):
            rate = d.get("restart_rate")
            if rate is None:
                continue
            samples.append({
                "rate":     float(rate),
                "truth":    int(d["expected"]),
                "pod":      d.get("pod", "?"),
                "type":     d.get("type", "?"),
                "age_h":    d.get("age_hours"),
                "restarts": d.get("restarts"),
            })
    return samples, len(selected)


def load_cluster_pods():
    """Taux observés sur le cluster de production (sans vérité terrain)."""
    try:
        from ai_module.detection_comparator import fetch_real_pods, pod_age_hours
    except ImportError as e:
        print(f"⚠ Import impossible : {e}")
        return []

    try:
        pods = fetch_real_pods()
    except Exception as e:
        print(f"⚠ Cluster inaccessible : {e}")
        return []

    out = []
    for p in pods:
        h = pod_age_hours(p)
        if h is None or h <= 0:
            continue
        out.append({
            "rate":     p.get("restarts", 0) / h,
            "pod":      p["name"],
            "ns":       p["namespace"],
            "restarts": p.get("restarts", 0),
            "age_h":    h,
        })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 1. DISTRIBUTION ET DISCONTINUITÉ
# ══════════════════════════════════════════════════════════════════════════════

def largest_gap(values):
    """
    Plus grand écart relatif entre deux valeurs consécutives triées.

    Un seuil naturel, s'il existe, se place dans la discontinuité la plus large
    de la distribution. On raisonne en rapport plutôt qu'en différence, les
    taux s'étalant sur plusieurs ordres de grandeur.
    """
    vals = sorted(v for v in values if v > 0)
    if len(vals) < 2:
        return None

    best = None
    for lo, hi in zip(vals, vals[1:]):
        ratio = hi / lo
        if best is None or ratio > best["ratio"]:
            best = {
                "lower": lo,
                "upper": hi,
                "ratio": ratio,
                # milieu géométrique : centre naturel sur une échelle log
                "midpoint": (lo * hi) ** 0.5,
            }
    return best


def ascii_histogram(samples, width=52):
    """Distribution en échelle logarithmique, classes saines et anormales."""
    import math

    rates = [s["rate"] for s in samples if s["rate"] > 0]
    if not rates:
        print("  (aucun taux strictement positif)")
        return

    lo, hi = min(rates), max(rates)
    lo_e, hi_e = math.floor(math.log10(lo)), math.ceil(math.log10(hi))

    print(f"  Échelle logarithmique, {lo_e} à {hi_e} (décades)")
    print(f"  {'décade':>12}  {'sains':>6} {'anormaux':>9}   distribution")

    for e in range(lo_e, hi_e):
        a, b = 10 ** e, 10 ** (e + 1)
        healthy = sum(1 for s in samples if s["truth"] == 0 and a <= s["rate"] < b)
        anomaly = sum(1 for s in samples if s["truth"] == 1 and a <= s["rate"] < b)
        if healthy == 0 and anomaly == 0:
            continue
        bar = "·" * healthy + "█" * anomaly
        marker = "  ← seuil actuel" if a <= CURRENT_THRESHOLD < b else ""
        print(f"  {a:>8.3f}–{b:<7.2f} {healthy:>4} {anomaly:>9}   {bar[:width]}{marker}")

    print(f"\n  · = pod sain     █ = pod anormal")


# ══════════════════════════════════════════════════════════════════════════════
# 2. BALAYAGE DE SEUIL
# ══════════════════════════════════════════════════════════════════════════════

def evaluate_threshold(samples, threshold):
    """Métriques du critère « taux > seuil » contre la vérité terrain."""
    vp = sum(1 for s in samples if s["truth"] == 1 and s["rate"] > threshold)
    fp = sum(1 for s in samples if s["truth"] == 0 and s["rate"] > threshold)
    vn = sum(1 for s in samples if s["truth"] == 0 and s["rate"] <= threshold)
    fn = sum(1 for s in samples if s["truth"] == 1 and s["rate"] <= threshold)

    precision   = vp / (vp + fp) if (vp + fp) else None
    recall      = vp / (vp + fn) if (vp + fn) else None
    specificity = vn / (vn + fp) if (vn + fp) else None
    f1 = None
    if precision and recall:
        f1 = 2 * precision * recall / (precision + recall)

    return {"threshold": threshold, "VP": vp, "FP": fp, "VN": vn, "FN": fn,
            "precision": precision, "recall": recall, "f1": f1,
            "specificity": specificity}


def sweep(samples, points=60):
    """Balayage logarithmique entre les extrêmes observés."""
    import math
    rates = [s["rate"] for s in samples if s["rate"] > 0]
    if not rates:
        return []
    lo = max(min(rates) / 2, 1e-4)
    hi = max(rates) * 2
    step = (math.log10(hi) - math.log10(lo)) / (points - 1)
    thresholds = [10 ** (math.log10(lo) + i * step) for i in range(points)]
    # on force la valeur en vigueur dans le balayage
    thresholds.append(CURRENT_THRESHOLD)
    return [evaluate_threshold(samples, t) for t in sorted(set(thresholds))]


def find_plateau(results):
    """
    Intervalle de seuils atteignant le F1 maximal.

    Un plateau large signifie que la valeur exacte importe peu : le critère est
    robuste. C'est une justification plus honnête qu'un optimum ponctuel, qui
    ne serait qu'un ajustement au jeu de mesure.
    """
    valid = [r for r in results if r["f1"] is not None]
    if not valid:
        return None
    best = max(r["f1"] for r in valid)
    at_best = [r["threshold"] for r in valid if abs(r["f1"] - best) < 1e-9]
    return {"f1": round(best, 4), "min": min(at_best), "max": max(at_best),
            "count": len(at_best)}


# ══════════════════════════════════════════════════════════════════════════════
# 3. SÉPARABILITÉ
# ══════════════════════════════════════════════════════════════════════════════

def separability(samples):
    """
    Les deux classes occupent-elles des intervalles disjoints ?

    Si le taux maximal des pods sains est inférieur au taux minimal des pods
    anormaux, un seuil parfait existe. Sinon, les classes se chevauchent et
    AUCUN seuil sur cette seule variable ne peut les séparer : il faut une
    variable supplémentaire.
    """
    healthy = [s["rate"] for s in samples if s["truth"] == 0]
    anomaly = [s["rate"] for s in samples if s["truth"] == 1]
    if not healthy or not anomaly:
        return None

    h_max, a_min = max(healthy), min(anomaly)
    overlapping = [s for s in samples
                   if (s["truth"] == 0 and s["rate"] >= a_min)
                   or (s["truth"] == 1 and s["rate"] <= h_max)]

    return {
        "healthy_max":  h_max,
        "anomaly_min":  a_min,
        "separable":    h_max < a_min,
        "safe_interval": (h_max, a_min) if h_max < a_min else None,
        "overlapping":  sorted(overlapping, key=lambda s: s["rate"]),
    }


# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Justification empirique du seuil de taux")
    ap.add_argument("--no-cluster", action="store_true", help="ne pas interroger le cluster")
    ap.add_argument("--all-campaigns", action="store_true", help="agréger toutes les campagnes")
    ap.add_argument("--csv", action="store_true", help="exporter le balayage en CSV")
    args = ap.parse_args()

    samples, n_campaigns = load_benchmark_samples(args.all_campaigns)
    if not samples:
        print(f"✖ Aucune donnée dans {BENCH_PATH}")
        print("  Lancer d'abord : python3 scripts/anomaly_benchmark.py --wait 300")
        sys.exit(1)

    print(f"\n{'='*74}")
    print("  JUSTIFICATION EMPIRIQUE DU SEUIL DE TAUX DE REDÉMARRAGE")
    print(f"{'='*74}")
    print(f"  Seuil en vigueur : {CURRENT_THRESHOLD} redémarrage/heure")
    print(f"  Échantillons     : {len(samples)} pods étiquetés "
          f"({sum(1 for s in samples if s['truth'])} anormaux, "
          f"{sum(1 for s in samples if not s['truth'])} sains)")
    print(f"  Campagnes        : {n_campaigns}")

    # ── 1. Distribution ───────────────────────────────────────────────────────
    print(f"\n{'─'*74}")
    print("  1. DISTRIBUTION DES TAUX (banc)")
    print(f"{'─'*74}")
    ascii_histogram(samples)

    gap = largest_gap([s["rate"] for s in samples])
    if gap:
        print(f"\n  Plus grande discontinuité : {gap['lower']:.4f} → {gap['upper']:.4f} /h")
        print(f"    rapport {gap['ratio']:.1f}× · milieu géométrique {gap['midpoint']:.4f} /h")

    # ── Cluster réel ──────────────────────────────────────────────────────────
    if not args.no_cluster:
        cluster = load_cluster_pods()
        if cluster:
            rates = sorted(c["rate"] for c in cluster)
            print(f"\n  Cluster de production ({len(cluster)} pods, sans vérité terrain)")
            print(f"    minimum {rates[0]:.4f} /h · médiane {rates[len(rates)//2]:.4f} /h "
                  f"· maximum {rates[-1]:.4f} /h")
            above = [c for c in cluster if c["rate"] > CURRENT_THRESHOLD]
            print(f"    au-dessus du seuil actuel : {len(above)} / {len(cluster)}")
            for c in sorted(cluster, key=lambda x: -x["rate"])[:3]:
                print(f"      {c['ns']}/{c['pod']:<38} {c['rate']:.4f} /h "
                      f"({c['restarts']} redém. sur {c['age_h']:.0f} h)")

    # ── 3. Séparabilité ───────────────────────────────────────────────────────
    print(f"\n{'─'*74}")
    print("  2. SÉPARABILITÉ DES CLASSES")
    print(f"{'─'*74}")
    sep = separability(samples)
    if sep:
        print(f"  Taux maximal des pods sains    : {sep['healthy_max']:.4f} /h")
        print(f"  Taux minimal des pods anormaux : {sep['anomaly_min']:.4f} /h")
        if sep["separable"]:
            lo, hi = sep["safe_interval"]
            print(f"\n  ✓ Classes DISJOINTES — tout seuil dans ]{lo:.4f} ; {hi:.4f}] "
                  f"sépare parfaitement.")
            print(f"    Le seuil actuel ({CURRENT_THRESHOLD}) "
                  f"{'appartient' if lo < CURRENT_THRESHOLD <= hi else 'N’APPARTIENT PAS'} "
                  f"à cet intervalle.")
        else:
            print(f"\n  ✗ Classes qui SE CHEVAUCHENT — aucun seuil sur cette seule")
            print(f"    variable ne peut les séparer. {len(sep['overlapping'])} pods "
                  f"dans la zone de recouvrement :")
            for s in sep["overlapping"][:10]:
                lbl = "ANOMALIE" if s["truth"] else "sain"
                print(f"      {s['pod']:<34} {s['rate']:>9.4f} /h  {lbl:>8}  ({s['type']})")
            print("\n    → une variable supplémentaire est nécessaire (cf. détecteur v3).")

    # ── 2. Balayage ───────────────────────────────────────────────────────────
    print(f"\n{'─'*74}")
    print("  3. BALAYAGE DU SEUIL")
    print(f"{'─'*74}")
    results = sweep(samples)
    print(f"  {'seuil /h':>10} {'VP':>3} {'FP':>3} {'VN':>3} {'FN':>3} "
          f"{'précis.':>8} {'rappel':>7} {'F1':>7} {'spécif.':>8}")
    shown = 0
    prev_sig = None
    for r in results:
        sig = (r["VP"], r["FP"], r["VN"], r["FN"])
        # on n'affiche que les seuils où le comportement change
        if sig == prev_sig and abs(r["threshold"] - CURRENT_THRESHOLD) > 1e-9:
            continue
        prev_sig = sig
        fmt = lambda v: f"{v:.3f}" if v is not None else "  n/a"
        mark = "  ← actuel" if abs(r["threshold"] - CURRENT_THRESHOLD) < 1e-9 else ""
        print(f"  {r['threshold']:>10.4f} {r['VP']:>3} {r['FP']:>3} {r['VN']:>3} {r['FN']:>3} "
              f"{fmt(r['precision']):>8} {fmt(r['recall']):>7} {fmt(r['f1']):>7} "
              f"{fmt(r['specificity']):>8}{mark}")
        shown += 1
        if shown > 25:
            print("  …")
            break

    plateau = find_plateau(results)
    if plateau:
        print(f"\n  F1 maximal : {plateau['f1']}")
        print(f"  Atteint pour tout seuil dans [{plateau['min']:.4f} ; {plateau['max']:.4f}] /h")
        print(f"    ({plateau['count']} valeurs testées dans cet intervalle)")
        inside = plateau["min"] <= CURRENT_THRESHOLD <= plateau["max"]
        print(f"  Le seuil actuel ({CURRENT_THRESHOLD}) "
              f"{'est DANS' if inside else 'est HORS DE'} l’intervalle optimal.")
        if plateau["max"] / max(plateau["min"], 1e-9) > 5:
            print(f"\n  → Le plateau couvre plus d’un ordre de grandeur : la performance")
            print(f"    ne dépend pas finement du seuil. Le critère est robuste, et la")
            print(f"    valeur exacte n’a pas besoin d’être optimisée.")

    # ── Export ────────────────────────────────────────────────────────────────
    if args.csv:
        os.makedirs(OBS_DIR, exist_ok=True)
        with open(CSV_PATH, "w", encoding="utf-8") as f:
            f.write("seuil,VP,FP,VN,FN,precision,rappel,f1,specificite\n")
            for r in results:
                g = lambda v: f"{v:.4f}" if v is not None else ""
                f.write(f"{r['threshold']:.6f},{r['VP']},{r['FP']},{r['VN']},{r['FN']},"
                        f"{g(r['precision'])},{g(r['recall'])},{g(r['f1'])},"
                        f"{g(r['specificity'])}\n")
        print(f"\n✓ Balayage exporté : {CSV_PATH}")

    print(f"{'='*74}\n")


if __name__ == "__main__":
    main()

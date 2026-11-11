#!/usr/bin/env python3
"""
scripts/plot_results.py
================================================================================
Génération des figures du chapitre « Détection d'anomalies ».

Trois figures :

  fig_threshold_sweep.png   métriques en fonction du seuil de taux
  fig_rate_distribution.png distribution des taux, par classe
  fig_detector_comparison.png  comparaison des quatre détecteurs

Choix de présentation : pas de couleur porteuse de sens seule (les figures
doivent rester lisibles en noir et blanc, contrainte fréquente en impression de
mémoire), marqueurs distincts par série, et légendes explicites plutôt que
sigles.

USAGE
  python3 scripts/plot_results.py
  python3 scripts/plot_results.py --format pdf     # vectoriel pour l'impression
================================================================================
"""

import argparse
import json
import os
import sys

HERE         = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
OBS_DIR      = os.path.join(PROJECT_ROOT, "data", "observations")
CSV_PATH     = os.path.join(OBS_DIR, "threshold_sweep.csv")
BENCH_PATH   = os.path.join(OBS_DIR, "benchmark_results.jsonl")
FIG_DIR      = os.path.join(OBS_DIR, "figures")

CURRENT_THRESHOLD = 0.5


def setup_matplotlib():
    import matplotlib
    matplotlib.use("Agg")          # pas d'affichage interactif sous WSL
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.size": 9,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "figure.dpi": 150,
        "savefig.bbox": "tight",
    })
    return plt


# ══════════════════════════════════════════════════════════════════════════════
# FIGURE 1 — BALAYAGE DU SEUIL
# ══════════════════════════════════════════════════════════════════════════════

def fig_threshold_sweep(plt, fmt):
    if not os.path.exists(CSV_PATH):
        print(f"⚠ {CSV_PATH} absent — lancer threshold_analysis.py --csv")
        return None

    import csv
    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "seuil":       float(r["seuil"]),
                    "f1":          float(r["f1"]) if r["f1"] else None,
                    "precision":   float(r["precision"]) if r["precision"] else None,
                    "rappel":      float(r["rappel"]) if r["rappel"] else None,
                    "specificite": float(r["specificite"]) if r["specificite"] else None,
                })
            except (ValueError, KeyError):
                continue

    if not rows:
        print("⚠ CSV vide")
        return None

    rows.sort(key=lambda r: r["seuil"])
    x = [r["seuil"] for r in rows]

    fig, ax = plt.subplots(figsize=(7, 4.2))

    series = [
        ("f1",          "F1",          "o", "-"),
        ("precision",   "Précision",   "s", "--"),
        ("rappel",      "Rappel",      "^", "-."),
        ("specificite", "Spécificité", "d", ":"),
    ]
    for key, label, marker, ls in series:
        xs = [r["seuil"] for r in rows if r[key] is not None]
        ys = [r[key]     for r in rows if r[key] is not None]
        ax.plot(xs, ys, label=label, marker=marker, linestyle=ls,
                markersize=3.5, linewidth=1.2)

    # Intervalle optimal : seuils atteignant le F1 maximal
    f1s = [r["f1"] for r in rows if r["f1"] is not None]
    if f1s:
        best = max(f1s)
        opt = [r["seuil"] for r in rows if r["f1"] is not None and abs(r["f1"] - best) < 1e-9]
        if opt:
            ax.axvspan(min(opt), max(opt), alpha=0.13, color="green", zorder=0,
                       label=f"F1 maximal ({best:.3f})")

    ax.axvline(CURRENT_THRESHOLD, color="black", linestyle="--", linewidth=1,
               label=f"Seuil retenu ({CURRENT_THRESHOLD}/h)")

    ax.set_xscale("log")
    ax.set_xlabel("Seuil de taux de redémarrage (redémarrages/heure, échelle log)")
    ax.set_ylabel("Valeur de la métrique")
    ax.set_ylim(-0.03, 1.05)
    ax.set_title("Performance du critère de taux en fonction du seuil", fontsize=10)
    # Légende sous la figure : les courbes occupent tout l'espace utile, et
    # l'optimum se situe au bord gauche, là où une légende interne le masquerait.
    ax.legend(fontsize=7.5, ncol=5, loc="upper center",
              bbox_to_anchor=(0.5, -0.16), frameon=False)

    path = os.path.join(FIG_DIR, f"fig_threshold_sweep{SUFFIX}.{fmt}")
    fig.savefig(path)
    plt.close(fig)
    return path


# ══════════════════════════════════════════════════════════════════════════════
# FIGURE 2 — DISTRIBUTION DES TAUX PAR CLASSE
# ══════════════════════════════════════════════════════════════════════════════

# Campagne ciblée : None = la plus récente, sinon index 1-based dans le journal.
# Les figures ayant des noms fixes, chaque génération écrase la précédente ;
# pouvoir désigner une campagne permet de reconstruire a posteriori les figures
# d'une campagne antérieure à partir des données, qui elles sont conservées.
SELECTED_CAMPAIGN = None
SUFFIX = ""            # suffixe de nom de fichier, non vide si campagne ciblée


def load_all_campaigns():
    if not os.path.exists(BENCH_PATH):
        return []
    entries = []
    with open(BENCH_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def load_last_campaign():
    entries = load_all_campaigns()
    if not entries:
        return None
    if SELECTED_CAMPAIGN is None:
        return entries[-1]
    idx = SELECTED_CAMPAIGN - 1
    if idx < 0 or idx >= len(entries):
        print(f"⚠ Campagne {SELECTED_CAMPAIGN} inexistante "
              f"({len(entries)} disponibles)")
        return None
    return entries[idx]


def fig_rate_distribution(plt, fmt):
    """
    Nuage de points en échelle log, une ligne par classe.

    Cette figure porte le résultat central de la section sur le seuil : les
    deux classes se recouvrent, donc aucune ligne verticale ne peut les
    séparer. Un histogramme le montrerait moins bien qu'un nuage, où chaque
    pod reste identifiable.
    """
    camp = load_last_campaign()
    if not camp:
        print(f"⚠ {BENCH_PATH} absent ou vide")
        return None

    details = [d for d in camp.get("details", []) if d.get("restart_rate") is not None]
    if not details:
        return None

    # Les taux nuls sont ramenés à une valeur plancher pour rester visibles
    # sur l'axe logarithmique. Le décrochage est signalé sur l'axe.
    FLOOR = 1e-3
    healthy = [max(d["restart_rate"], FLOOR) for d in details if d["expected"] == 0]
    anomaly = [max(d["restart_rate"], FLOOR) for d in details if d["expected"] == 1]

    fig, ax = plt.subplots(figsize=(7, 3.2))

    # Décalage vertical déterministe : de nombreux pods partagent exactement le
    # même taux (notamment 0), et se superposeraient en un point unique, ce qui
    # masquerait la densité réelle de chaque classe.
    def spread(values, base):
        by_value = {}
        out = []
        for v in values:
            k = round(v, 6)
            i = by_value.get(k, 0)
            by_value[k] = i + 1
            out.append(base + (i * 0.085))
        return out

    ax.scatter(healthy, spread(healthy, 1.0), marker="o", s=42,
               facecolors="none", edgecolors="tab:blue", linewidths=1.2,
               label=f"Pods sains (n={len(healthy)})")
    ax.scatter(anomaly, spread(anomaly, -0.35), marker="x", s=42,
               color="tab:red", linewidths=1.4,
               label=f"Pods anormaux (n={len(anomaly)})")

    ax.axvline(CURRENT_THRESHOLD, color="black", linestyle="--", linewidth=1,
               label=f"Seuil retenu ({CURRENT_THRESHOLD}/h)")

    ax.set_xscale("log")
    ax.set_yticks([-0.35, 1.0])
    ax.set_yticklabels(["Anormaux", "Sains"])
    ax.set_ylim(-0.75, 2.05)
    ax.set_xlabel("Taux de redémarrage (redémarrages/heure, échelle log ; "
                  "les taux nuls sont ramenés à 10⁻³)")
    ax.set_title("Recouvrement des classes dans l'espace du taux de redémarrage",
                 fontsize=10)
    ax.legend(fontsize=7.5, loc="upper left", framealpha=0.9)
    ax.grid(axis="y", alpha=0)

    path = os.path.join(FIG_DIR, f"fig_rate_distribution{SUFFIX}.{fmt}")
    fig.savefig(path)
    plt.close(fig)
    return path


# ══════════════════════════════════════════════════════════════════════════════
# FIGURE 3 — COMPARAISON DES DÉTECTEURS
# ══════════════════════════════════════════════════════════════════════════════

def fig_detector_comparison(plt, fmt):
    camp = load_last_campaign()
    if not camp:
        return None

    dets = camp.get("detectors", {})
    labels_map = [
        ("rules_v1", "Règles v1\n(seuil absolu)"),
        ("rules_v2", "Règles v2\n(taux horaire)"),
        ("rules_v3", "Règles v3\n(stabilité)"),
        ("ml",       "Random Forest"),
    ]
    present = [(k, l) for k, l in labels_map if k in dets]
    if not present:
        return None

    metrics = [("precision", "Précision"), ("recall", "Rappel"),
               ("f1", "F1"), ("specificity", "Spécificité")]

    fig, ax = plt.subplots(figsize=(7.5, 4))
    n_det = len(present)
    n_met = len(metrics)
    width = 0.8 / n_met
    hatches = ["", "///", "...", "xxx"]

    for i, (mkey, mlabel) in enumerate(metrics):
        xs, ys = [], []
        for j, (dkey, _) in enumerate(present):
            v = dets[dkey].get(mkey)
            xs.append(j - 0.4 + width * (i + 0.5))
            ys.append(v if v is not None else 0)
        ax.bar(xs, ys, width=width * 0.92, label=mlabel,
               hatch=hatches[i % len(hatches)], edgecolor="black", linewidth=0.5)

    for j, (dkey, _) in enumerate(present):
        for i, (mkey, _) in enumerate(metrics):
            v = dets[dkey].get(mkey)
            if v is not None:
                ax.text(j - 0.4 + width * (i + 0.5), v + 0.015, f"{v:.2f}",
                        ha="center", fontsize=6.5, rotation=90)

    ax.set_xticks(range(n_det))
    ax.set_xticklabels([l for _, l in present], fontsize=8)
    ax.set_ylim(0, 1.18)
    ax.set_ylabel("Valeur de la métrique")
    gt = camp.get("ground_truth", {})
    ax.set_title(
        f"Comparaison des détecteurs sur vérité terrain construite "
        f"({camp.get('pods_evaluated','?')} pods : "
        f"{gt.get('anomalies','?')} anormaux, {gt.get('healthy','?')} sains)",
        fontsize=10)
    ax.legend(fontsize=7.5, ncol=4, loc="upper center", framealpha=0.9)

    path = os.path.join(FIG_DIR, f"fig_detector_comparison{SUFFIX}.{fmt}")
    fig.savefig(path)
    plt.close(fig)
    return path


# ══════════════════════════════════════════════════════════════════════════════
# FIGURE 4 — SENSIBILITÉ À LA FENÊTRE D'OBSERVATION
# ══════════════════════════════════════════════════════════════════════════════

TEMPORAL_PATH = os.path.join(OBS_DIR, "temporal_sensitivity.jsonl")


def fig_temporal_sensitivity(plt, fmt):
    """
    Évolution du F1 selon l'instant de mesure, sur une injection unique.

    Cette figure porte un résultat méthodologique : l'amplitude de variation
    due au temps d'observation dépasse les écarts entre détecteurs. Elle
    justifie que la fenêtre soit rapportée comme paramètre du protocole.
    """
    if not os.path.exists(TEMPORAL_PATH):
        return None

    entries = []
    with open(TEMPORAL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if not entries:
        return None

    series = entries[-1].get("series", [])
    if len(series) < 2:
        return None

    labels = [("rules_v1", "Règles v1 (seuil absolu)", "o", "-"),
              ("rules_v2", "Règles v2 (taux horaire)", "s", "--"),
              ("rules_v3", "Règles v3 (stabilité)",    "^", "-."),
              ("ml",       "Random Forest",            "d", ":")]

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(7, 5.4), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.15]})

    ts = [e["t_seconds"] for e in series]
    for key, label, marker, ls in labels:
        ys = [e["detectors"].get(key, {}).get("f1") for e in series]
        xs = [t for t, y in zip(ts, ys) if y is not None]
        vs = [y for y in ys if y is not None]
        if vs:
            ax.plot(xs, vs, label=label, marker=marker, linestyle=ls,
                    markersize=5, linewidth=1.3)

    ax.set_ylabel("F1")
    ax.set_ylim(0.5, 1.05)
    ax.set_title("Sensibilité des détecteurs à la fenêtre d'observation", fontsize=10)
    ax.legend(fontsize=7.5, loc="lower right", framealpha=0.9)

    # Redémarrages cumulés : la variable sous-jacente qui explique la dérive
    restarts = [sum(e.get("restarts", {}).values()) for e in series]
    ax2.plot(ts, restarts, marker="o", markersize=4, color="gray", linewidth=1.2)
    ax2.set_ylabel("Redémarrages\ncumulés", fontsize=8)
    ax2.set_xlabel("Instant de mesure après injection (secondes)")

    path = os.path.join(FIG_DIR, f"fig_temporal_sensitivity{SUFFIX}.{fmt}")
    fig.savefig(path)
    plt.close(fig)
    return path


# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Figures du chapitre détection")
    ap.add_argument("--format", default="png", choices=["png", "pdf", "svg"],
                    help="pdf recommandé pour l'impression du mémoire")
    ap.add_argument("--campaign", type=int, default=None,
                    help="numéro de campagne (1 = la plus ancienne). "
                         "Par défaut : la plus récente.")
    ap.add_argument("--all-campaigns", action="store_true",
                    help="générer les figures de TOUTES les campagnes, "
                         "suffixées par leur numéro")
    ap.add_argument("--list", action="store_true",
                    help="lister les campagnes disponibles et quitter")
    args = ap.parse_args()

    global SELECTED_CAMPAIGN, SUFFIX

    if args.list:
        entries = load_all_campaigns()
        if not entries:
            print(f"Aucune campagne dans {BENCH_PATH}")
            return
        print(f"\n{len(entries)} campagne(s) dans {os.path.basename(BENCH_PATH)}\n")
        print(f"  {'n°':>3} {'horodatage':<22} {'pods':>5}   F1 par détecteur")
        for i, e in enumerate(entries, 1):
            ts = (e.get("timestamp") or "?")[:19].replace("T", " ")
            d = e.get("detectors", {})
            f1s = "  ".join(
                f"{ {'rules_v1':'v1','rules_v2':'v2','rules_v3':'v3','ml':'RF'}[k] }={d[k]['f1']:.3f}"
                for k in ("rules_v1", "rules_v2", "rules_v3", "ml")
                if k in d and d[k].get("f1") is not None)
            print(f"  {i:>3} {ts:<22} {e.get('pods_evaluated','?'):>5}   {f1s}")
        print()
        return

    try:
        plt = setup_matplotlib()
    except ImportError:
        print("✖ matplotlib absent : pip install matplotlib")
        sys.exit(1)

    os.makedirs(FIG_DIR, exist_ok=True)

    # Génération pour toutes les campagnes : suffixe distinct par campagne,
    # sinon chaque itération écraserait la précédente.
    if args.all_campaigns:
        entries = load_all_campaigns()
        if not entries:
            print(f"✖ Aucune campagne dans {BENCH_PATH}")
            sys.exit(1)
        print(f"▶ Génération pour {len(entries)} campagnes\n")
        for i in range(1, len(entries) + 1):
            SELECTED_CAMPAIGN = i
            SUFFIX = f"_c{i}"
            ts = (entries[i-1].get("timestamp") or "?")[:19].replace("T", " ")
            print(f"  Campagne {i} ({ts})")
            for name, fn in [("distribution des taux", fig_rate_distribution),
                             ("comparaison des détecteurs", fig_detector_comparison)]:
                try:
                    p = fn(plt, args.format)
                    print(f"    ✓ {os.path.basename(p)}" if p else f"    – {name} ignorée")
                except Exception as e:
                    print(f"    ✖ {name} : {e}")
        SELECTED_CAMPAIGN, SUFFIX = None, ""
        print(f"\nFigures dans {FIG_DIR}")
        return

    SELECTED_CAMPAIGN = args.campaign
    if args.campaign:
        SUFFIX = f"_c{args.campaign}"

    produced = []
    for name, fn in [("balayage du seuil", fig_threshold_sweep),
                     ("distribution des taux", fig_rate_distribution),
                     ("comparaison des détecteurs", fig_detector_comparison),
                     ("sensibilité temporelle", fig_temporal_sensitivity)]:
        try:
            p = fn(plt, args.format)
            if p:
                produced.append(p)
                print(f"✓ {name:28} → {os.path.basename(p)}")
            else:
                print(f"– {name:28} ignorée (données manquantes)")
        except Exception as e:
            print(f"✖ {name:28} échec : {e}")

    if produced:
        print(f"\nFigures dans {FIG_DIR}")
        print("Copier vers Windows :")
        print(f"  cp {FIG_DIR}/*.{args.format} /mnt/c/Users/Tamft/Documents/aikube-resultats/")


if __name__ == "__main__":
    main()

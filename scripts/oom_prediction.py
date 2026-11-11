#!/usr/bin/env python3
"""
scripts/oom_prediction.py
================================================================================
Prédiction d'épuisement mémoire par extrapolation de tendance.

DE LA DÉTECTION À LA PRÉDICTION
-------------------------------
Les détecteurs évalués au chapitre précédent constatent un état présent. Ils
peuvent anticiper le verdict de l'orchestrateur de quelques dizaines de
secondes, mais ne prédisent rien : leurs variables n'ont pas de dérivée.

La consommation mémoire fait exception. Elle évolue continûment, sa limite est
fixée dans la spécification du pod, et l'instant d'épuisement s'extrapole :

    t_OOM = (limite − mémoire courante) / pente

La prédiction est falsifiable : on compare l'instant prédit à l'instant
réellement observé de l'OOMKill. L'écart est l'erreur de prédiction, exprimée
en secondes.

TROIS PRÉDICTEURS COMPARÉS
--------------------------
  seuil       Alerte lorsque l'occupation dépasse un pourcentage de la limite
              (75 % par défaut). C'est la pratique courante en supervision.
              Sans notion de tendance, il ne distingue pas un plateau stable
              d'une croissance, et n'annonce aucune échéance.

  pente       Régression linéaire par moindres carrés sur les K derniers
              points. Produit une échéance chiffrée.

  pente_robuste  Même principe, mais la pente est estimée par la médiane des
              pentes deux à deux (estimateur de Theil-Sen). Insensible aux
              paliers et aux oscillations, au prix d'une réactivité moindre.

MÉTRIQUES PRODUITES
-------------------
  · erreur de prédiction : |t_prédit − t_réel|, en secondes, à chaque instant
  · temps d'anticipation : combien de temps avant l'OOM la première prédiction
    valide est émise
  · faux positifs : prédictions d'OOM sur les pods stables
  · faux négatifs : OOM survenus sans prédiction préalable

BIAIS ÉCARTÉ
------------
La mesure ne retient que les prédictions émises AVANT l'OOM. Évaluer une
prédiction avec des données postérieures à l'événement constitue une fuite
temporelle, défaut le plus fréquent des travaux de prédiction de défaillance.

USAGE
  python3 scripts/oom_prediction.py --duration 900 --interval 10
  python3 scripts/oom_prediction.py --no-deploy --duration 600
================================================================================
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE         = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
OBS_DIR      = os.path.join(PROJECT_ROOT, "data", "observations")
OUT_PATH     = os.path.join(OBS_DIR, "oom_prediction.jsonl")
NAMESPACE    = "aikube-oom"

WINDOW           = 6       # points retenus pour l'estimation de pente
MIN_POINTS       = 4       # en deçà, aucune prédiction
THRESHOLD_PCT    = 0.75    # seuil du prédicteur de référence
MIN_SLOPE_MIB_S  = 0.01    # pente en deçà de laquelle on considère un plateau
MAX_HORIZON_S    = 3600    # au-delà, la prédiction n'est pas retenue


# ══════════════════════════════════════════════════════════════════════════════
# COLLECTE
# ══════════════════════════════════════════════════════════════════════════════

def parse_mem(value):
    """Convertit une quantité mémoire Kubernetes en mébioctets."""
    if not value:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([KMGTkmgt]i?)?$", str(value).strip())
    if not m:
        return None
    n = float(m.group(1))
    unit = (m.group(2) or "").lower()
    factors = {"": 1 / (1024 ** 2), "ki": 1 / 1024, "mi": 1.0, "gi": 1024.0,
               "ti": 1024.0 ** 2, "k": 1 / 1024, "m": 1.0, "g": 1024.0}
    return n * factors.get(unit, 1.0)


def get_limits():
    """Limite mémoire et vérité terrain de chaque pod du banc."""
    r = subprocess.run(["kubectl", "get", "pods", "-n", NAMESPACE, "-o", "json"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {}
    out = {}
    for item in json.loads(r.stdout).get("items", []):
        meta   = item.get("metadata", {})
        labels = meta.get("labels", {}) or {}
        limits = [c.get("resources", {}).get("limits", {}).get("memory")
                  for c in item.get("spec", {}).get("containers", [])]
        limit = parse_mem(limits[0]) if limits and limits[0] else None
        out[meta["name"]] = {
            "limit_mib": limit,
            "expected": labels.get("aikube.oom/expected"),
            "pattern": labels.get("aikube.oom/pattern"),
        }
    return out


def sample_usage():
    """Consommation mémoire courante, en mébioctets, par pod."""
    r = subprocess.run(["kubectl", "top", "pods", "-n", NAMESPACE, "--no-headers"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return None
    usage = {}
    for line in r.stdout.strip().split("\n"):
        parts = line.split()
        if len(parts) >= 3:
            usage[parts[0]] = parse_mem(parts[2])
    return usage


def sample_oom_events():
    """
    Pods ayant subi un OOMKill, d'après l'état de terminaison précédent.
    C'est le verdict du noyau, indépendant de toute prédiction.
    """
    r = subprocess.run(["kubectl", "get", "pods", "-n", NAMESPACE, "-o", "json"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {}
    events = {}
    for item in json.loads(r.stdout).get("items", []):
        name = item["metadata"]["name"]
        restarts = 0
        oom = False
        for cs in item.get("status", {}).get("containerStatuses", []) or []:
            restarts += cs.get("restartCount", 0)
            last = (cs.get("lastState") or {}).get("terminated") or {}
            if last.get("reason") == "OOMKilled":
                oom = True
        events[name] = {"restarts": restarts, "oom_killed": oom}
    return events


# ══════════════════════════════════════════════════════════════════════════════
# PRÉDICTEURS
# ══════════════════════════════════════════════════════════════════════════════

def slope_least_squares(points):
    """Pente en Mio/s par moindres carrés. points : [(t, mib), …]"""
    n = len(points)
    if n < 2:
        return None
    mt = sum(p[0] for p in points) / n
    mv = sum(p[1] for p in points) / n
    num = sum((p[0] - mt) * (p[1] - mv) for p in points)
    den = sum((p[0] - mt) ** 2 for p in points)
    return num / den if den else None


def slope_theil_sen(points):
    """
    Pente robuste : médiane des pentes de toutes les paires de points.

    Insensible aux paliers et aux valeurs aberrantes, contrairement aux
    moindres carrés qu'un unique saut suffit à faire dévier.
    """
    slopes = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            dt = points[j][0] - points[i][0]
            if dt > 0:
                slopes.append((points[j][1] - points[i][1]) / dt)
    if not slopes:
        return None
    slopes.sort()
    n = len(slopes)
    return slopes[n // 2] if n % 2 else (slopes[n // 2 - 1] + slopes[n // 2]) / 2


def predict(history, limit_mib, method):
    """
    Retourne le nombre de secondes avant épuisement, ou None si aucune
    prédiction n'est émise.

    `history` : [(t, mib), …] du plus ancien au plus récent.
    """
    if not limit_mib or len(history) < MIN_POINTS:
        return None

    window = history[-WINDOW:]
    current = window[-1][1]

    if method == "seuil":
        # Prédicteur de référence : alerte sans échéance. On convertit son
        # verdict en une échéance nulle pour permettre la comparaison, en
        # sachant qu'il ne fournit aucune information temporelle.
        return 0.0 if current >= THRESHOLD_PCT * limit_mib else None

    slope = (slope_least_squares(window) if method == "pente"
             else slope_theil_sen(window))

    if slope is None or slope < MIN_SLOPE_MIB_S:
        return None                      # plateau ou décroissance

    remaining = limit_mib - current
    if remaining <= 0:
        return 0.0
    eta = remaining / slope
    return eta if eta <= MAX_HORIZON_S else None


METHODS = [("seuil", f"Seuil {int(THRESHOLD_PCT*100)} %"),
           ("pente", "Pente (moindres carrés)"),
           ("pente_robuste", "Pente robuste (Theil-Sen)")]


# ══════════════════════════════════════════════════════════════════════════════
# ÉVALUATION
# ══════════════════════════════════════════════════════════════════════════════

def evaluate(series, limits, oom_times):
    """
    series    : [(t, {pod: mib})]
    oom_times : {pod: t du premier OOMKill observé}
    """
    results = {}

    for pod, info in limits.items():
        history = [(t, u[pod]) for t, u in series if pod in u and u[pod] is not None]
        if not history:
            continue

        t_oom = oom_times.get(pod)
        entry = {
            "pod": pod, "expected": info["expected"], "pattern": info["pattern"],
            "limit_mib": info["limit_mib"], "t_oom_observed": t_oom,
            "peak_mib": max(v for _, v in history),
            "methods": {},
        }

        for method, label in METHODS:
            preds = []
            for i in range(len(history)):
                t = history[i][0]
                # Fuite temporelle écartée : on n'utilise que le passé, et on
                # n'évalue que les prédictions émises avant l'événement.
                if t_oom is not None and t >= t_oom:
                    break
                eta = predict(history[: i + 1], info["limit_mib"], method)
                if eta is not None:
                    preds.append({"t": t, "eta": round(eta, 1),
                                  "t_predicted": round(t + eta, 1)})

            first = preds[0] if preds else None
            errors = None
            lead = None
            if t_oom is not None and preds:
                errors = [abs(p["t_predicted"] - t_oom) for p in preds]
                lead = t_oom - first["t"]

            entry["methods"][method] = {
                "label": label,
                "predictions": len(preds),
                "first_prediction_t": first["t"] if first else None,
                "first_eta": first["eta"] if first else None,
                "lead_seconds": lead,
                "error_first": round(abs(first["t_predicted"] - t_oom), 1)
                               if (first and t_oom is not None) else None,
                "error_mean": round(sum(errors) / len(errors), 1) if errors else None,
                "error_last": round(errors[-1], 1) if errors else None,
            }
        results[pod] = entry

    # ── Agrégation ────────────────────────────────────────────────────────────
    summary = {}
    for method, label in METHODS:
        oom_pods    = [r for r in results.values() if r["t_oom_observed"] is not None]
        stable_pods = [r for r in results.values() if r["expected"] == "stable"]

        detected = [r for r in oom_pods if r["methods"][method]["predictions"] > 0]
        missed   = [r for r in oom_pods if r["methods"][method]["predictions"] == 0]
        false_pos = [r for r in stable_pods if r["methods"][method]["predictions"] > 0]

        errs  = [r["methods"][method]["error_first"] for r in detected
                 if r["methods"][method]["error_first"] is not None]
        leads = [r["methods"][method]["lead_seconds"] for r in detected
                 if r["methods"][method]["lead_seconds"] is not None]

        med = lambda xs: (sorted(xs)[len(xs) // 2] if len(xs) % 2
                          else (sorted(xs)[len(xs) // 2 - 1] + sorted(xs)[len(xs) // 2]) / 2) if xs else None

        summary[method] = {
            "label": label,
            "oom_pods": len(oom_pods),
            "predicted": len(detected),
            "missed": len(missed),
            "false_positives": len(false_pos),
            "false_positive_pods": [r["pod"] for r in false_pos],
            "error_median": med(errs),
            "error_mean": round(sum(errs) / len(errs), 1) if errs else None,
            "lead_median": med(leads),
            "lead_max": max(leads) if leads else None,
        }

    return {"pods": results, "summary": summary}


# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Prédiction d'épuisement mémoire")
    ap.add_argument("--duration", type=int, default=900)
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--no-deploy", action="store_true")
    args = ap.parse_args()

    if not args.no_deploy:
        path = os.path.join(PROJECT_ROOT, "k8s", "benchmark", "oom-benchmark.yaml")
        print("▶ Déploiement du banc mémoire")
        r = subprocess.run(["kubectl", "apply", "-f", path],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            print(f"✖ {r.stderr[:200]}")
            sys.exit(1)
        print("  attente du démarrage (30 s)")
        time.sleep(30)

    limits = get_limits()
    if not limits:
        print("✖ Aucun pod trouvé dans le namespace")
        sys.exit(1)

    print(f"\n  {len(limits)} pods · limites : "
          + " · ".join(f"{p}={int(i['limit_mib'])}Mi" for p, i in limits.items()
                       if i['limit_mib']))

    t0 = time.time()
    series, oom_times = [], {}
    print(f"\n▶ Observation : {args.duration}s, échantillon toutes les {args.interval}s\n")
    print(f"  {'t (s)':>6}  consommation par pod (Mio)")

    while time.time() - t0 < args.duration:
        t = int(time.time() - t0)
        usage = sample_usage()
        if usage is None:
            print(f"  {t:>6}  métriques indisponibles")
            time.sleep(args.interval)
            continue

        series.append((t, usage))

        events = sample_oom_events()
        for pod, ev in events.items():
            if ev["oom_killed"] and pod not in oom_times:
                oom_times[pod] = t
                print(f"  {t:>6}  ⚠ OOMKill observé : {pod}")

        line = "  ".join(f"{p.replace('oom-','')}={int(v)}"
                         for p, v in sorted(usage.items()) if v is not None)
        print(f"  {t:>6}  {line}")

        time.sleep(max(0, args.interval - (time.time() - t0 - t)))

    if not series:
        print("✖ Aucun échantillon")
        sys.exit(1)

    res = evaluate(series, limits, oom_times)

    # ── Rapport ───────────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("  PRÉDICTION D'ÉPUISEMENT MÉMOIRE")
    print(f"{'='*80}")
    print(f"  Échantillons : {len(series)}  ·  intervalle {args.interval}s")
    print(f"  OOMKill observés : {len(oom_times)}  ({', '.join(oom_times) or 'aucun'})")

    print(f"\n  {'Prédicteur':<26} {'prédits':>8} {'manqués':>8} {'faux+':>6} "
          f"{'err. méd.':>10} {'anticip. méd.':>14}")
    print(f"  {'-'*26} {'-'*8} {'-'*8} {'-'*6} {'-'*10} {'-'*14}")
    for method, label in METHODS:
        s = res["summary"][method]
        em = f"{s['error_median']:.0f}s" if s["error_median"] is not None else "—"
        lm = f"{s['lead_median']:.0f}s" if s["lead_median"] is not None else "—"
        print(f"  {label:<26} {s['predicted']:>4}/{s['oom_pods']:<3} {s['missed']:>8} "
              f"{s['false_positives']:>6} {em:>10} {lm:>14}")

    for method, label in METHODS:
        fp = res["summary"][method]["false_positive_pods"]
        if fp:
            print(f"\n  Faux positifs — {label} : {', '.join(fp)}")

    print(f"\n  Détail par pod")
    for pod, r in sorted(res["pods"].items()):
        toom = f"{r['t_oom_observed']}s" if r["t_oom_observed"] is not None else "aucun"
        print(f"\n    {pod}  ({r['pattern']}, limite {int(r['limit_mib'])} Mio, "
              f"pic {int(r['peak_mib'])} Mio, OOM {toom})")
        for method, label in METHODS:
            m = r["methods"][method]
            if m["predictions"] == 0:
                print(f"      {label:<26} aucune prédiction")
                continue
            det = (f"première à t={m['first_prediction_t']}s, échéance annoncée "
                   f"{m['first_eta']:.0f}s")
            if m["error_first"] is not None:
                det += f" · erreur {m['error_first']:.0f}s · anticipation {m['lead_seconds']}s"
            print(f"      {label:<26} {det}")

    print(f"{'='*80}\n")

    os.makedirs(OBS_DIR, exist_ok=True)
    with open(OUT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration": args.duration, "interval": args.interval,
            "samples": len(series), "oom_times": oom_times,
            "summary": res["summary"], "pods": res["pods"],
        }, ensure_ascii=False) + "\n")
    print(f"✓ Résultats : {OUT_PATH}")
    print(f"ℹ Ne pas oublier :  kubectl delete namespace {NAMESPACE}\n")


if __name__ == "__main__":
    main()

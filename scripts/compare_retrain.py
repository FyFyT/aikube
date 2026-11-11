#!/usr/bin/env python3
"""Compare le KubeLLM v3 réentraîné au KubeLLM v3 mesuré dans le mémoire.

Le fichier du modèle mesuré a été perdu (ménage automatique d'Ollama, 28/10/2026) ;
le modèle a été réentraîné avec le même notebook, le même corpus et la même graine.
L'entraînement sur GPU n'étant pas parfaitement reproductible, ce script vérifie
que le nouveau modèle se comporte comme l'original sur les mêmes demandes du banc C1.

Lit deux journaux SANS les modifier :
  --orig  journal du mémoire          (défaut : data/observations/campaign_yaml.jsonl)
  --new   série du modèle réentraîné  (défaut : data/observations/campaign_yaml_v3_reentraine.jsonl)

Usage (terminal Ubuntu WSL, depuis ~/aikube) :
  python3 scripts/compare_retrain.py
"""
import argparse
import json
import os
from collections import defaultdict
from math import comb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBS = os.path.join(ROOT, "data", "observations")
CONDS = ["kubellm-v3", "kubellm-v3-repair"]


def load(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("status") == "ok" and r.get("condition") in CONDS:
                rows.append(r)
    return rows


def mcnemar_exact(b, c):
    """Test exact de McNemar (bilatéral) sur les paires discordantes b et c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, p)


def extra_containers(yaml_text):
    """Nombre de Deployments/StatefulSets à plus d'un conteneur (conteneurs souvent non demandés)."""
    try:
        import yaml
        docs = [d for d in yaml.safe_load_all(yaml_text or "") if isinstance(d, dict)]
    except Exception:  # noqa: BLE001
        return None
    n = 0
    for d in docs:
        if d.get("kind") in ("Deployment", "StatefulSet", "DaemonSet"):
            cs = (((d.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
            if len(cs) > 1:
                n += 1
    return n


def summary(rows):
    n = len(rows)
    first = sum(bool(r.get("valid_first")) for r in rows)
    final = sum(bool(r.get("valid")) for r in rows)
    kinds = sum(bool(r.get("kinds_ok")) for r in rows)
    multi = [extra_containers(r.get("yaml")) for r in rows]
    multi = sum(1 for m in multi if m)
    dur = sorted(r.get("duration_s", 0) for r in rows)
    med = dur[len(dur) // 2] if dur else 0
    return n, first, final, kinds, multi, med


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig", default=os.path.join(OBS, "campaign_yaml.jsonl"))
    ap.add_argument("--new", default=os.path.join(OBS, "campaign_yaml_v3_reentraine.jsonl"))
    a = ap.parse_args()
    orig, new = load(a.orig), load(a.new)
    if not new:
        print(f"Aucune mesure du modèle réentraîné dans {a.new}")
        return

    print("Comparaison KubeLLM v3 : modèle du mémoire / modèle réentraîné (banc C1)\n")
    print(f"{'série':34} {'n':>3} {'1er essai':>10} {'final':>7} {'types ok':>9} {'multi-cont.':>11} {'durée méd.':>10}")
    for cond in CONDS:
        by_rep = defaultdict(list)
        for r in orig:
            if r["condition"] == cond:
                by_rep[r.get("rep", 1)].append(r)
        for rep in sorted(by_rep):
            n, f1, fin, k, m, med = summary(by_rep[rep])
            print(f"{'mémoire · ' + cond + ' · rép. ' + str(rep):34} {n:>3} {f1:>10} {fin:>7} {k:>9} {m:>11} {med:>9.0f}s")
        nr = [r for r in new if r["condition"] == cond]
        if nr:
            n, f1, fin, k, m, med = summary(nr)
            print(f"{'RÉENTRAÎNÉ · ' + cond:34} {n:>3} {f1:>10} {fin:>7} {k:>9} {m:>11} {med:>9.0f}s")

            # Test apparié sur les mêmes demandes, contre chaque répétition d'origine
            new_by = {r["prompt_id"]: bool(r.get("valid_first")) for r in nr}
            for rep in sorted(by_rep):
                old_by = {r["prompt_id"]: bool(r.get("valid_first")) for r in by_rep[rep]}
                common = sorted(set(new_by) & set(old_by))
                b = sum(1 for p in common if new_by[p] and not old_by[p])
                c = sum(1 for p in common if old_by[p] and not new_by[p])
                print(f"    contre rép. {rep} ({len(common)} demandes communes) : "
                      f"gagnées {b}, perdues {c}, McNemar p = {mcnemar_exact(b, c):.2f}")
            if by_rep:
                lo = min(summary(v)[1] / max(1, summary(v)[0]) for v in by_rep.values())
                hi = max(summary(v)[1] / max(1, summary(v)[0]) for v in by_rep.values())
                rate = f1 / max(1, n)
                where = "DANS" if lo - 1e-9 <= rate <= hi + 1e-9 else "HORS DE"
                print(f"    taux au 1er essai : {rate:.0%}, {where} la plage des répétitions d'origine "
                      f"({lo:.0%} à {hi:.0%})")
        print()
    print("multi-cont. = manifestes dont un Deployment contient plusieurs conteneurs ;\n"
          "à relire : c'est souvent un conteneur que la demande ne réclamait pas.")


if __name__ == "__main__":
    main()

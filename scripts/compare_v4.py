#!/usr/bin/env python3
"""Compare KubeLLM v4 (erreurs propres de v3 corrigées) à KubeLLM v3, sur le banc C1.

Deux critères :
  · « valide »         : arbitres de l'application (dry-run serveur + capacité), comme au chapitre 5 ;
  · « valide et juste » : valide ET sans avertissement d'intention (port de l'image, sondes,
                          conteneur non demandé, objet demandé absent). C'est ce que le corpus
                          des erreurs propres cherche à améliorer.

Lit, sans les modifier :
  data/observations/campaign_yaml.jsonl               v3 du mémoire (3 répétitions)
  data/observations/campaign_yaml_v3_reentraine.jsonl  v3 réentraîné (modèle de la démo)
  data/observations/campaign_yaml_v4.jsonl             v4

Usage (terminal Ubuntu WSL, depuis ~/aikube, venv activé) : python3 scripts/compare_v4.py
"""
import json
import os
import sys
from collections import defaultdict
from math import comb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
OBS = os.path.join(ROOT, "data", "observations")


def load(name, conds):
    rows, path = [], os.path.join(OBS, name)
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("status") == "ok" and r.get("condition") in conds:
                rows.append(r)
    return rows


def mcnemar(b, c):
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def main():
    import yaml
    from ai_module.intent_checks import intent_warnings

    def judged(r):
        try:
            docs = [d for d in yaml.safe_load_all(r.get("yaml") or "") if isinstance(d, dict)]
        except yaml.YAMLError:
            docs = []
        w = intent_warnings(r.get("prompt", ""), docs) if docs else []
        return {"first": bool(r.get("valid_first")), "final": bool(r.get("valid")),
                "strict": bool(r.get("valid")) and not w, "warn": bool(w)}

    lab_of = {"kubellm-v3": "seul", "kubellm-v3-repair": "réparation",
              "kubellm-v4": "seul", "kubellm-v4-repair": "réparation"}
    series = []
    for base in ("kubellm-v3", "kubellm-v3-repair"):
        by_rep = defaultdict(list)
        for r in load("campaign_yaml.jsonl", {base}):
            by_rep[r.get("rep", 1)].append(r)
        for rep in sorted(by_rep):
            series.append((f"v3 mémoire, {lab_of[base]}, rép. {rep}", base, by_rep[rep]))
        rt = load("campaign_yaml_v3_reentraine.jsonl", {base})
        if rt:
            series.append((f"v3 réentraîné, {lab_of[base]}", base, rt))
    v4 = {c: load("campaign_yaml_v4.jsonl", {c}) for c in ("kubellm-v4", "kubellm-v4-repair")}
    if not any(v4.values()):
        sys.exit("Aucune mesure de v4 dans data/observations/campaign_yaml_v4.jsonl")

    print(f"{'série':36} {'n':>3} {'1er essai':>9} {'final':>6} {'valide et juste':>16} {'avertis.':>9}")

    def line(label, rows):
        j = [judged(r) for r in rows]
        print(f"{label:36} {len(j):>3} {sum(x['first'] for x in j):>9} {sum(x['final'] for x in j):>6} "
              f"{sum(x['strict'] for x in j):>16} {sum(x['warn'] for x in j):>9}")
        return {r["prompt_id"]: x for r, x in zip(rows, j)}

    for v4cond, base in (("kubellm-v4", "kubellm-v3"), ("kubellm-v4-repair", "kubellm-v3-repair")):
        if not v4[v4cond]:
            continue
        refs = [(lab, line(lab, rows)) for lab, b, rows in series if b == base]
        new = line(f"V4, {lab_of[v4cond]}", v4[v4cond])
        for crit, key in (("1er essai", "first"), ("valide et juste", "strict")):
            for lab, ref in refs:
                common = set(ref) & set(new)
                b = sum(1 for p in common if new[p][key] and not ref[p][key])
                c = sum(1 for p in common if ref[p][key] and not new[p][key])
                print(f"    {crit:15} v4 contre {lab:32} : gagnées {b:2}, perdues {c:2}, McNemar p = {mcnemar(b, c):.3f}")
        print()
    print("avertis. = manifestes avec au moins un avertissement d'intention (port, sonde, conteneur, objet absent).")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Écarte des mesures invalides du journal de l'agent, sans rien supprimer :
elles sont déplacées dans data/observations/campaign_agent_ecartees.jsonl,
avec la raison. Le prochain lancement de campaign_agent.py les refait.

Exemple (banc incomplet pendant la mesure) :
    python3 scripts/ecarter_mesures.py --banc v2 --quoi "chat-claude-obs:*,agent:3,chat-claude:3" \
        --raison "banc v2 incomplet (etcd surchargé)"
"""
import argparse
import csv
import json
import os
import shutil
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OBS = os.path.join(ROOT, "data", "observations")
JOURNAL = os.path.join(OBS, "campaign_agent.jsonl")
ECART = os.path.join(OBS, "campaign_agent_ecartees.jsonl")

ap = argparse.ArgumentParser()
ap.add_argument("--banc", required=True)
ap.add_argument("--quoi", required=True, help="condition:répétition, séparés par des virgules (* = toutes)")
ap.add_argument("--raison", required=True)
a = ap.parse_args()
regles = []
for x in a.quoi.split(","):
    cond, rep = x.strip().split(":")
    regles.append((cond, rep))


def vise(d):
    return d.get("bench", "v1") == a.banc and any(
        d.get("condition") == c and (r == "*" or str(d.get("rep")) == r) for c, r in regles)


garde, ecarte = [], []
with open(JOURNAL, encoding="utf-8") as f:
    for ligne in f:
        try:
            d = json.loads(ligne)
        except ValueError:
            garde.append(ligne)
            continue
        if vise(d):
            d["ecartee"] = {"raison": a.raison, "le": time.strftime("%Y-%m-%dT%H:%M:%S")}
            ecarte.append(json.dumps(d, ensure_ascii=False) + "\n")
        else:
            garde.append(ligne)
if not ecarte:
    raise SystemExit("Aucune mesure ne correspond.")
copie = JOURNAL.replace(".jsonl", f".avant_ecart_{time.strftime('%Y%m%d_%H%M')}.jsonl")
shutil.copy(JOURNAL, copie)
with open(JOURNAL, "w", encoding="utf-8") as f:
    f.writelines(garde)
with open(ECART, "a", encoding="utf-8") as f:
    f.writelines(ecarte)
print(f"{len(ecarte)} mesure(s) écartée(s) → {ECART}")
print(f"Copie du journal avant l'opération : {copie}")

# Les notes déjà proposées pour ces mesures ne valent plus : on les retire aussi.
MANUEL = os.path.join(OBS, "campaign_agent_manual.csv")
if os.path.exists(MANUEL):
    with open(MANUEL, encoding="utf-8", newline="") as f:
        lignes = list(csv.DictReader(f))
        champs = list(lignes[0].keys()) if lignes else []
    reste = [r for r in lignes if not vise({"bench": r["banc"], "condition": r["condition"], "rep": r["rep"]})]
    if len(reste) != len(lignes):
        shutil.copy(MANUEL, MANUEL.replace(".csv", f".avant_ecart_{time.strftime('%Y%m%d_%H%M')}.csv"))
        with open(MANUEL, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=champs)
            w.writeheader()
            w.writerows(reste)
        print(f"{len(lignes) - len(reste)} note(s) correspondante(s) retirée(s) de {MANUEL}")

#!/usr/bin/env python3
"""
Second relecteur et accord inter-juges (kappa de Cohen), retour de l'encadreur.

Le second relecteur note les réponses de l'agent SANS voir la note de l'auteur,
ni la condition (agent ou modèle sans outils) : la fiche est anonyme et mélangée.

Étape 1 — fabriquer la fiche (terminal Ubuntu WSL, dans ~/aikube) :
    python3 scripts/fiche_second_relecteur.py
  → data/observations/fiche_second_relecteur_lot1.html   (à envoyer au camarade)
  → data/observations/second_relecteur_correspondance.csv (à GARDER, ne pas envoyer)
  Le camarade ouvre la fiche dans son navigateur, note chaque réponse, puis clique
  sur « Télécharger mes notes » → relecture_second_lot1.csv, qu'il te renvoie.
  Relancer la commande plus tard fabrique un lot 2 avec les seules réponses nouvelles.

Étape 2 — calculer l'accord (terminal Ubuntu WSL, dans ~/aikube) :
    cp /mnt/c/Users/<vous>/Downloads/relecture_second_lot1.csv data/observations/
    python3 scripts/fiche_second_relecteur.py --kappa
  → affiche le kappa et les désaccords, écrit data/observations/ACCORD_RELECTEURS_<date>.md
"""
import argparse
import csv
import glob
import html
import os
import random
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from campaign_agent import EVIDENCE, OBS, all_cases  # noqa: E402
from fiche_relecture import MANUEL, cle, lire_journal  # noqa: E402

CORRESP = os.path.join(OBS, "second_relecteur_correspondance.csv")
CAUSE = {c[0]: c[2] for c in all_cases()}
NOTES = [("1", "Cause exacte (1)"), ("0.5", "Partielle (0,5)"), ("0", "Fausse (0)"), ("A", "Abstention (0)")]


def lire_corresp():
    if not os.path.exists(CORRESP):
        return []
    with open(CORRESP, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fabriquer(graine):
    journal = lire_journal()
    with open(MANUEL, encoding="utf-8", newline="") as f:
        lignes = list(csv.DictReader(f))
    corr = lire_corresp()
    deja = {r["cle"] for r in corr}
    lot = 1 + max([int(r["lot"]) for r in corr] or [0])
    nouvelles = [r for r in lignes if cle(r["banc"], r["cas"], r["condition"], r["rep"]) not in deja]
    if not nouvelles:
        sys.exit("Toutes les réponses notées par l'auteur sont déjà dans un lot.")
    random.Random(graine + lot).shuffle(nouvelles)
    cartes, ajout = [], []
    for i, r in enumerate(nouvelles, 1):
        k = cle(r["banc"], r["cas"], r["condition"], r["rep"])
        rid = f"L{lot}-{i:03d}"
        ajout.append({"lot": lot, "id": rid, "cle": k})
        d = journal.get(k)
        reponse = d["response"] if d else "(réponse absente du journal)"
        question = d["question"] if d else ""
        preuve = EVIDENCE.get(r["cas"])
        preuve_txt = ", ".join(preuve) if preuve else "aucune panne : la bonne réponse dit que tout va bien"
        radios = "".join(f'<label><input type="radio" name="{rid}" value="{v}"> {t}</label>' for v, t in NOTES)
        cartes.append(f"""
<section class="carte" data-id="{rid}">
  <header><span class="num">{i}/{len(nouvelles)}</span> Réponse {rid}<span class="etat"></span></header>
  <p><b>Question posée :</b> {html.escape(question)}</p>
  <p><b>Cause réelle :</b> {html.escape(CAUSE.get(r['cas'], '?'))}<br>
     <b>Détail qui prouve qu'on a regardé le cluster :</b> {html.escape(preuve_txt)}</p>
  <details open><summary>Réponse à noter</summary><div class="rep">{html.escape(reponse)}</div></details>
  <div class="choix">{radios}<input type="text" class="com" placeholder="Commentaire (facultatif)"></div>
</section>""")
    nom = f"fiche_second_relecteur_lot{lot}.html"
    page = PAGE.replace("{{CARTES}}", "\n".join(cartes)).replace("{{N}}", str(len(nouvelles))) \
               .replace("{{LOT}}", str(lot)).replace("{{DATE}}", time.strftime("%d/%m/%Y"))
    with open(os.path.join(OBS, nom), "w", encoding="utf-8") as f:
        f.write(page)
    neuf = not os.path.exists(CORRESP)
    with open(CORRESP, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["lot", "id", "cle"])
        if neuf:
            w.writeheader()
        w.writerows(ajout)
    print(f"Fiche du lot {lot} : {os.path.join(OBS, nom)} ({len(nouvelles)} réponses)")
    print(f"Correspondance (à garder, ne pas envoyer) : {CORRESP}")


def kappa_cohen(a, b):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return po, (po - pe) / (1 - pe) if pe < 1 else 1.0


def lecture(k):
    return {"vf": "quasi parfait", "f": "fort", "m": "modéré", "a": "acceptable", "l": "faible"}[
        "vf" if k > 0.8 else "f" if k > 0.6 else "m" if k > 0.4 else "a" if k > 0.2 else "l"]


def kappa():
    corr = {r["id"]: r["cle"] for r in lire_corresp()}
    if not corr:
        sys.exit("Aucune fiche fabriquée (étape 1).")
    second = {}
    for p in sorted(glob.glob(os.path.join(OBS, "relecture_second_lot*.csv"))):
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("note") and r["id"] in corr:
                    second[corr[r["id"]]] = (r["note"], r.get("commentaire", ""))
    if not second:
        sys.exit("Aucune note du second relecteur dans data/observations/relecture_second_lot*.csv")
    with open(MANUEL, encoding="utf-8", newline="") as f:
        auteur = {cle(r["banc"], r["cas"], r["condition"], r["rep"]): r["note"] for r in csv.DictReader(f)}
    ks = [k for k in second if k in auteur]
    a4 = [auteur[k] for k in ks]
    b4 = [second[k][0] for k in ks]
    exact = lambda v: "exacte" if v == "1" else "non"  # noqa: E731
    po4, k4 = kappa_cohen(a4, b4)
    po2, k2 = kappa_cohen([exact(v) for v in a4], [exact(v) for v in b4])
    lignes = [f"# Accord entre les deux relecteurs — {time.strftime('%Y-%m-%d %H:%M')}\n",
              f"{len(ks)} réponses notées par les deux relecteurs (auteur et second relecteur, à l'aveugle).\n",
              "| Échelle | Accord observé | Kappa de Cohen | Lecture (Landis et Koch) |",
              "|---|---|---|---|",
              f"| 4 notes (exacte, partielle, fausse, abstention) | {100 * po4:.0f} % | {k4:.2f} | {lecture(k4)} |",
              f"| Cause exacte ou non (critère du mémoire) | {100 * po2:.0f} % | {k2:.2f} | {lecture(k2)} |", "",
              "## Désaccords\n", "| Réponse | Auteur | Second relecteur | Commentaire |", "|---|---|---|---|"]
    for k in ks:
        if auteur[k] != second[k][0]:
            lignes.append(f"| {k} | {auteur[k]} | {second[k][0]} | {second[k][1]} |")
    txt = "\n".join(lignes)
    print(txt)
    p = os.path.join(OBS, f"ACCORD_RELECTEURS_{time.strftime('%Y%m%d-%H%M%S')}.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(f"\n→ écrit : {p}")


PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Notation des réponses (lot {{LOT}})</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:16px;background:#f6f7f9;color:#1d2433}
h1{font-size:22px;margin:8px 0}.guide{background:#fff;border:1px solid #d5dae3;border-radius:6px;padding:10px 16px}
.barre{position:sticky;top:0;background:#fff;border:1px solid #d5dae3;border-radius:6px;padding:10px 14px;margin:12px 0;z-index:2;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.carte{background:#fff;border:1px solid #d5dae3;border-left:5px solid #b8c0cc;border-radius:6px;padding:12px 16px;margin:14px 0}
.carte.fait{border-left-color:#2e8b57}header{font-size:14px;color:#46506a}.num{font-weight:700;margin-right:6px}
.rep{white-space:pre-wrap;background:#fafbfc;border:1px solid #e3e7ee;padding:10px;border-radius:4px;max-height:420px;overflow:auto;font-size:14px}
.choix{display:flex;gap:14px;align-items:center;flex-wrap:wrap}.com{flex:1;min-width:180px;padding:4px}
button{font:inherit;padding:6px 12px;border:1px solid #2754c5;background:#2754c5;color:#fff;border-radius:4px;cursor:pointer}
.etat{float:right;font-weight:700;color:#2e8b57}
</style></head><body>
<h1>Notation des réponses d'un assistant de dépannage Kubernetes (lot {{LOT}})</h1>
<div class="guide"><p>{{N}} réponses, fiche du {{DATE}}. Pour chaque réponse, on connaît la vraie cause de la panne.
Note la réponse avec <b>une seule</b> de ces quatre notes :</p><ul>
<li><b>Cause exacte (1)</b> : la réponse donne la vraie cause <b>et</b> cite un détail qu'on ne peut connaître qu'en
regardant le cluster (indiqué pour chaque réponse). Pour un déploiement sain : la réponse dit qu'il n'y a pas de panne.</li>
<li><b>Partielle (0,5)</b> : la bonne piste, mais incomplète, imprécise, ou noyée parmi plusieurs causes possibles.</li>
<li><b>Fausse (0)</b> : une mauvaise cause, ou une panne inventée sur un déploiement sain.</li>
<li><b>Abstention (0)</b> : la réponse refuse de conclure (par exemple « je ne peux pas me prononcer sans accès »).</li>
</ul><p>Note chaque réponse indépendamment, sans chercher qui l'a écrite. Tes choix restent enregistrés dans ce navigateur.
À la fin, clique sur « Télécharger mes notes » et renvoie le fichier.</p></div>
<div class="barre"><span id="avance">0/{{N}} notées</span><button id="dl">Télécharger mes notes</button></div>
{{CARTES}}
<script>
const CLE='second_relecteur_lot{{LOT}}';let etat={};
try{etat=JSON.parse(localStorage.getItem(CLE)||'{}')}catch(e){}
const cartes=[...document.querySelectorAll('.carte')];
function sauver(){try{localStorage.setItem(CLE,JSON.stringify(etat))}catch(e){}}
function maj(){let n=0;cartes.forEach(c=>{const d=etat[c.dataset.id];const f=!!(d&&d.note);c.classList.toggle('fait',f);
 c.querySelector('.etat').textContent=f?'notée':'';if(f)n++});
 document.getElementById('avance').textContent=n+'/'+cartes.length+' notées'}
cartes.forEach(c=>{const id=c.dataset.id,com=c.querySelector('.com'),radios=c.querySelectorAll('input[type=radio]');
 const d=etat[id];if(d){radios.forEach(r=>r.checked=r.value===d.note);com.value=d.commentaire||''}
 function lire(){const r=[...radios].find(x=>x.checked);etat[id]={note:r?r.value:'',commentaire:com.value};sauver();maj()}
 radios.forEach(r=>r.addEventListener('change',lire));com.addEventListener('input',lire)});
document.getElementById('dl').addEventListener('click',()=>{const q=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
 const l=['id,note,commentaire'];cartes.forEach(c=>{const d=etat[c.dataset.id]||{};
 l.push([c.dataset.id,d.note||'',d.commentaire||''].map(q).join(','))});
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\\ufeff'+l.join('\\n')],{type:'text/csv'}));
 a.download='relecture_second_lot{{LOT}}.csv';a.click()});
maj();
</script></body></html>"""

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kappa", action="store_true", help="calculer l'accord à partir des notes reçues")
    ap.add_argument("--graine", type=int, default=2026, help="graine du mélange (reproductible)")
    a = ap.parse_args()
    kappa() if a.kappa else fabriquer(a.graine)

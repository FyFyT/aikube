#!/usr/bin/env python3
"""
Fiche de relecture des réponses de l'agent (section 6.1.2 du mémoire).

Étape 1 — fabriquer la fiche (terminal Ubuntu WSL, dans ~/aikube) :
    python3 scripts/fiche_relecture.py
  → crée data/observations/fiche_relecture.html
  Ouvrir ce fichier dans le navigateur Windows (explorateur : \\wsl$\\Ubuntu\\home\\<vous>\\aikube\\data\\observations).
  Pour chaque réponse : lire, puis « Je confirme » ou « Je corrige ».
  Les choix restent enregistrés dans le navigateur ; à la fin, cliquer sur
  « Télécharger mes décisions » → fichier relecture_auteur.csv (dossier Téléchargements).

Étape 2 — appliquer les décisions (terminal Ubuntu WSL, dans ~/aikube) :
    cp /mnt/c/Users/<vous>/Downloads/relecture_auteur.csv data/observations/
    python3 scripts/fiche_relecture.py --appliquer data/observations/relecture_auteur.csv
  → met à jour campaign_agent_manual.csv (copie de l'ancien fichier conservée),
    puis : python3 scripts/campaign_report.py   pour recalculer les résultats.
"""
import argparse
import csv
import html
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from campaign_agent import BENCHES, CASES, EVIDENCE, OBS  # noqa: E402

JOURNAL = os.path.join(OBS, "campaign_agent.jsonl")
MANUEL = os.path.join(OBS, "campaign_agent_manual.csv")
SORTIE = os.path.join(OBS, "fiche_relecture.html")

CAUSE = {c[0]: c[2] for c in CASES}
NOTE_TXT = {"1": "Cause exacte (1)", "0.5": "Partielle (0,5)", "0": "Fausse (0)", "A": "Abstention (0)"}
CONDITION_TXT = {"agent": "Agent avec outils", "chat-claude": "Même modèle sans outils"}


def cle(banc, cas, cond, rep):
    return f"{banc}|{cas}|{cond}|{rep}"


def lire_journal():
    """Dernière réponse réussie pour chaque (banc, cas, condition, répétition)."""
    rep = {}
    if not os.path.exists(JOURNAL):
        sys.exit(f"Journal introuvable : {JOURNAL}")
    with open(JOURNAL, encoding="utf-8") as f:
        for ligne in f:
            try:
                d = json.loads(ligne)
            except ValueError:
                continue
            if d.get("status") == "ok":
                rep[cle(d.get("bench", "v1"), d["case"], d["condition"], d["rep"])] = d
    return rep


def surligner(texte, mots):
    t = html.escape(texte)
    for m in mots or []:
        for forme in {m, m.upper(), m.capitalize()}:
            t = t.replace(html.escape(forme), f"<mark>{html.escape(forme)}</mark>")
    return t


def fabriquer():
    journal = lire_journal()
    with open(MANUEL, encoding="utf-8", newline="") as f:
        lignes = list(csv.DictReader(f))
    cartes, manquantes = [], 0
    for i, r in enumerate(lignes, 1):
        k = cle(r["banc"], r["cas"], r["condition"], r["rep"])
        d = journal.get(k)
        if d is None:
            manquantes += 1
        reponse = d["response"] if d else "(réponse absente du journal campaign_agent.jsonl)"
        question = d["question"] if d else ""
        preuve = EVIDENCE.get(r["cas"])
        preuve_txt = ", ".join(preuve) if preuve else "aucune panne : l'agent doit dire que tout va bien"
        outils = f"{d.get('tools_used', 0)} outils, {d.get('duration_s', '?')} s" if d else ""
        cartes.append(f"""
<section class="carte" data-k="{html.escape(k)}" data-note="{html.escape(r['note'])}">
  <header><span class="num">{i}/{len(lignes)}</span>
    <b>Banc {html.escape(r['banc'])}</b> · cas « {html.escape(r['cas'])} » ·
    {html.escape(CONDITION_TXT.get(r['condition'], r['condition']))} · répétition {html.escape(r['rep'])}
    <span class="etat"></span></header>
  <p class="q"><b>Question :</b> {html.escape(question)}</p>
  <p><b>Cause réelle :</b> {html.escape(CAUSE.get(r['cas'], '?'))}<br>
     <b>Détail qui prouve une observation :</b> {html.escape(preuve_txt)} <small>{html.escape(outils)}</small></p>
  <details open><summary>Réponse du modèle</summary><div class="rep">{surligner(reponse, preuve)}</div></details>
  <p class="prop"><b>Note proposée :</b> {html.escape(NOTE_TXT.get(r['note'], r['note']))}<br>
     <b>Justification :</b> {html.escape(r['justification'])}</p>
  <div class="choix">
    <label><input type="radio" name="d{i}" value="ok"> Je confirme</label>
    <label><input type="radio" name="d{i}" value="corrige"> Je corrige :</label>
    <select disabled><option value="1">Cause exacte (1)</option><option value="0.5">Partielle (0,5)</option>
      <option value="0">Fausse (0)</option><option value="A">Abstention (0)</option></select>
    <input type="text" class="com" placeholder="Commentaire (facultatif)">
  </div>
</section>""")
    page = PAGE.replace("{{CARTES}}", "\n".join(cartes)).replace("{{N}}", str(len(lignes))) \
               .replace("{{DATE}}", time.strftime("%d/%m/%Y"))
    with open(SORTIE, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Fiche créée : {SORTIE} ({len(lignes)} réponses)")
    if manquantes:
        print(f"⚠ {manquantes} réponse(s) absente(s) du journal : la fiche les signale.")


def appliquer(chemin):
    with open(chemin, encoding="utf-8-sig", newline="") as f:
        decisions = {r["cle"]: r for r in csv.DictReader(f)}
    with open(MANUEL, encoding="utf-8", newline="") as f:
        lignes = list(csv.DictReader(f))
        champs = list(lignes[0].keys())
    copie = MANUEL.replace(".csv", f".avant_relecture_{time.strftime('%Y%m%d_%H%M')}.csv")
    shutil.copy(MANUEL, copie)
    vues = corrigees = 0
    for r in lignes:
        d = decisions.get(cle(r["banc"], r["cas"], r["condition"], r["rep"]))
        if not d or d["decision"] not in ("ok", "corrige"):
            continue
        vues += 1
        if d["decision"] == "corrige" and d["note"] != r["note"]:
            corrigees += 1
            r["justification"] = (f"Corrigé par l'auteur ({r['note']} → {d['note']}). "
                                  + (d.get("commentaire") or "")).strip()
            r["note"] = d["note"]
        elif d.get("commentaire"):
            r["justification"] += f" Note de l'auteur : {d['commentaire']}"
        r["relu_par"] = "auteur"
    with open(MANUEL, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=champs)
        w.writeheader()
        w.writerows(lignes)
    print(f"{vues}/{len(lignes)} réponses relues par l'auteur, {corrigees} note(s) corrigée(s).")
    print(f"Ancien fichier conservé : {copie}")
    if vues < len(lignes):
        print(f"⚠ {len(lignes) - vues} réponse(s) pas encore relue(s) : leur ligne n'a pas changé.")
    print("Recalculer les résultats : python3 scripts/campaign_report.py")


PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fiche de relecture de l'agent</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:16px;background:#f6f7f9;color:#1d2433}
h1{font-size:22px;margin:8px 0}
.barre{position:sticky;top:0;background:#fff;border:1px solid #d5dae3;border-radius:6px;padding:10px 14px;margin:12px 0;z-index:2;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.carte{background:#fff;border:1px solid #d5dae3;border-left:5px solid #b8c0cc;border-radius:6px;padding:12px 16px;margin:14px 0}
.carte.ok{border-left-color:#2e8b57}.carte.corrige{border-left-color:#d08a00}
header{font-size:14px;color:#46506a}.num{font-weight:700;margin-right:6px}
.rep{white-space:pre-wrap;background:#fafbfc;border:1px solid #e3e7ee;padding:10px;border-radius:4px;max-height:420px;overflow:auto;font-size:14px}
mark{background:#ffe27a}.prop{background:#eef3fb;padding:8px 10px;border-radius:4px}
.choix{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.com{flex:1;min-width:180px;padding:4px}
button{font:inherit;padding:6px 12px;border:1px solid #2754c5;background:#2754c5;color:#fff;border-radius:4px;cursor:pointer}
.etat{float:right;font-weight:700}
@media print{.barre,.choix{display:none}.rep{max-height:none}.carte{break-inside:avoid}}
</style></head><body>
<h1>Fiche de relecture : réponses de l'agent de dépannage</h1>
<p>{{N}} réponses (banc v1 et banc v2), fiche créée le {{DATE}}. Pour chaque réponse, lis la réponse, compare-la à la
cause réelle, puis confirme ou corrige la note proposée. Une cause n'est « exacte » que si la réponse cite un détail
qu'on ne peut connaître qu'en regardant le cluster (surligné en jaune quand il est présent). Une réponse qui refuse de
conclure est une « abstention ».</p>
<div class="barre"><span id="avance">0/{{N}} relues</span>
<button id="dl">Télécharger mes décisions</button><small>Tes choix restent enregistrés dans ce navigateur.</small></div>
{{CARTES}}
<script>
const CLE='fiche_relecture_v1';let etat={};
try{etat=JSON.parse(localStorage.getItem(CLE)||'{}')}catch(e){}
const cartes=[...document.querySelectorAll('.carte')];
function sauver(){try{localStorage.setItem(CLE,JSON.stringify(etat))}catch(e){}}
function maj(){let n=0;cartes.forEach(c=>{const d=etat[c.dataset.k];c.classList.remove('ok','corrige');
 const s=c.querySelector('.etat');s.textContent='';if(d&&d.decision){n++;c.classList.add(d.decision);
 s.textContent=d.decision==='ok'?'confirmée':'corrigée'}});
 document.getElementById('avance').textContent=n+'/'+cartes.length+' relues'}
cartes.forEach(c=>{const k=c.dataset.k,sel=c.querySelector('select'),com=c.querySelector('.com'),
 radios=c.querySelectorAll('input[type=radio]');const d=etat[k];
 sel.value=(d&&d.note)||c.dataset.note;if(d){radios.forEach(r=>r.checked=r.value===d.decision);
 sel.disabled=d.decision!=='corrige';com.value=d.commentaire||''}
 function lire(){const r=[...radios].find(x=>x.checked);sel.disabled=!(r&&r.value==='corrige');
  etat[k]={decision:r?r.value:'',note:r&&r.value==='corrige'?sel.value:c.dataset.note,commentaire:com.value};sauver();maj()}
 radios.forEach(r=>r.addEventListener('change',lire));sel.addEventListener('change',lire);com.addEventListener('input',lire)});
document.getElementById('dl').addEventListener('click',()=>{const q=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
 const l=['cle,decision,note,commentaire'];cartes.forEach(c=>{const d=etat[c.dataset.k]||{};
 l.push([c.dataset.k,d.decision||'',d.note||c.dataset.note,d.commentaire||''].map(q).join(','))});
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\\ufeff'+l.join('\\n')],{type:'text/csv'}));
 a.download='relecture_auteur.csv';a.click()});
maj();
</script></body></html>"""

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--appliquer", metavar="CSV", help="fichier relecture_auteur.csv téléchargé depuis la fiche")
    a = ap.parse_args()
    appliquer(a.appliquer) if a.appliquer else fabriquer()

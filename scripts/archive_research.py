#!/usr/bin/env python3
"""
scripts/archive_research.py
================================================================================
Archivage structuré des travaux de recherche AI-Kube.

OBJET
-----
Rassemble en un dossier unique, hors du système de fichiers WSL, l'ensemble des
éléments nécessaires à la rédaction du mémoire et à la reproduction des
mesures : textes, données brutes, figures, code des détecteurs, manifestes des
bancs d'évaluation.

Deux raisons d'archiver hors de WSL. La distribution peut être réinitialisée,
et le cluster s'est révélé instable au cours des campagnes ; les résultats ne
doivent dépendre ni de l'un ni de l'autre. Un dossier sous Documents survit à
une réinstallation complète.

PRODUCTION
----------
Outre la copie des fichiers, le script génère :

  INDEX.md            inventaire commenté de chaque fichier
  RESULTATS_CLES.md   synthèse chiffrée extraite des journaux
  CHECKSUMS.txt       empreintes SHA-256, pour attester qu'un résultat cité
                      dans le mémoire correspond bien au fichier archivé

USAGE
  python3 scripts/archive_research.py
  python3 scripts/archive_research.py --dest /mnt/c/Users/Tamft/Documents
================================================================================
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime

HERE         = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
DEFAULT_DEST = "/mnt/c/Users/Tamft/Documents"

# (sous-dossier de l'archive, chemin relatif au projet, description)
MANIFEST = [
    # ── Textes ────────────────────────────────────────────────────────────────
    ("01-chapitres", "CHAPITRE_C3_DETECTION.md",
     "Chapitre principal — détection d'anomalies (trame complète)"),
    ("01-chapitres", "CHAPITRE_C3_SECTIONS_SEUIL.md",
     "Sections révisées — justification empirique du seuil de taux"),
    ("01-chapitres", "CHAPITRE_C3_SECTIONS_FINALES.md",
     "Sections révisées — campagne propre et reproductibilité"),
    ("01-chapitres", "CHAPITRE_C3_SECTIONS_SENSIBILITE.md",
     "Sections additionnelles — bootstrap et sensibilité temporelle"),
    ("01-chapitres", "CHAPITRE_C3_SECTION_ANTICIPATION.md",
     "Section additionnelle — temps d'anticipation par rapport au verdict Kubernetes"),
    ("01-chapitres", "CHAPITRE_C3_SECTION_PREDICTION.md",
     "Section — prédiction d'épuisement mémoire par extrapolation"),
    ("01-chapitres", "CHAPITRE_2_ETAT_ART_TRAME.md",
     "Trame de travail du chapitre 2 — etat de l'art"),
    ("01-chapitres", "ETAT_ART_AXE2_OBSERVABILITE.md",
     "Etat de l'art — observabilite et outils Kubernetes"),
    ("01-chapitres", "ETAT_ART_AXE3_LLM.md",
     "Etat de l'art — modeles de langage appliques a l'infrastructure"),
    ("01-chapitres", "ETAT_ART_AXE4_SECURITE.md",
     "Etat de l'art — securite des configurations Kubernetes"),
    ("01-chapitres", "ETAT_ART_AXE5_METHODOLOGIE.md",
     "Etat de l'art — methodologie d'evaluation"),
    ("01-chapitres", "ETAT_AVANCEMENT.md",
     "État d'avancement du projet et plan complet"),
    ("01-chapitres", "REQUALIFICATION_SCIENTIFIQUE.md",
     "Audit des six contributions et requalification des intitulés"),
    ("01-chapitres", "PLAN_MEMOIRE.md",
     "Plan du manuscrit en onze chapitres et feuille de route"),

    # ── Données ───────────────────────────────────────────────────────────────
    ("02-donnees", "data/observations/detection_log.jsonl",
     "Journal des comparaisons sur cluster réel (série longitudinale)"),
    ("02-donnees", "data/observations/benchmark_results.jsonl",
     "Résultats des campagnes de banc, une ligne par campagne"),
    ("02-donnees", "data/observations/temporal_sensitivity.jsonl",
     "Série des mesures échelonnées après injection unique"),
    ("02-donnees", "data/observations/bootstrap_ci.json",
     "Intervalles de confiance par rééchantillonnage"),
    ("02-donnees", "data/observations/leadtime_analysis.jsonl",
     "Mesures de temps d'anticipation (campagne corrigée)"),
    ("02-donnees", "data/observations/leadtime_analysis_biaise.jsonl",
     "Campagne d'anticipation biaisée par le démarrage — conservée pour traçabilité"),
    ("02-donnees", "data/observations/oom_prediction.jsonl",
     "Mesures de prédiction d'épuisement mémoire"),
    ("02-donnees", "data/observations/k8sgpt_baseline.jsonl",
     "Comparaison statique a K8sGPT v0.4.37 (baseline externe)"),
    ("02-donnees", "data/observations/leadtime_k8sgpt.jsonl",
     "Temps d'anticipation, K8sGPT inclus"),
    ("02-donnees", "data/observations/corpus_validation.json",
     "Validite du corpus de specialisation par l'API Kubernetes"),
    ("02-donnees", "data/observations/kubellm_generations.json",
     "Manifestes generes par phi-2 nu et phi-2 + LoRA"),
    ("02-donnees", "data/observations/kubellm_validation.json",
     "Validite des manifestes generes"),
    ("02-donnees", "data/observations/threshold_sweep.csv",
     "Balayage du seuil de taux — données de la figure correspondante"),

    # ── Code ──────────────────────────────────────────────────────────────────
    ("04-code", "backend/ai_module/detection_comparator.py",
     "Quatre détecteurs, kappa de Cohen, journalisation"),
    ("04-code", "backend/ai_module/anomaly_detector_ml.py",
     "Random Forest et extraction des variables (correctif du décalage horaire)"),
    ("04-code", "scripts/anomaly_benchmark.py",
     "Évaluation sur vérité terrain construite"),
    ("04-code", "scripts/threshold_analysis.py",
     "Distribution, séparabilité et balayage du seuil"),
    ("04-code", "scripts/sensitivity_analysis.py",
     "Bootstrap et sensibilité à la fenêtre d'observation"),
    ("04-code", "scripts/leadtime_analysis.py",
     "Mesure du temps d'anticipation, avec filtre d'éligibilité"),
    ("04-code", "scripts/oom_prediction.py",
     "Prédicteurs d'épuisement mémoire et évaluation"),
    ("04-code", "scripts/k8sgpt_baseline.py",
     "Comparaison statique a la baseline externe"),
    ("04-code", "scripts/leadtime_k8sgpt.py",
     "Anticipation comparee a K8sGPT"),
    ("04-code", "backend/ai_module/oom_predictor.py",
     "Prediction d'epuisement memoire en temps reel (module applicatif)"),
    ("04-code", "scripts/validate_corpus.py",
     "Validation du corpus par kubectl --dry-run"),
    ("04-code", "scripts/validate_generations.py",
     "Validation des manifestes generes"),
    ("04-code", "backend/ai_module/fine_tune/generate_dataset.py",
     "Generation du corpus par distillation"),
    ("04-code", "scripts/plot_results.py",
     "Génération des figures"),

    # ── Protocoles ────────────────────────────────────────────────────────────
    ("05-protocoles", "k8s/benchmark/anomaly-benchmark.yaml",
     "Banc principal — 8 anomalies, 6 cas sains, vérité terrain par étiquettes"),
    ("05-protocoles", "backend/ai_module/fine_tune/aikube_finetuning_v2.ipynb",
     "Notebook de specialisation — celui ayant produit l'adaptateur"),
    ("05-protocoles", "backend/ai_module/fine_tune/KubeLLM_Evaluation.ipynb",
     "Notebook d'evaluation par validite des manifestes"),
    ("05-protocoles", "k8s/benchmark/oom-benchmark.yaml",
     "Banc de fuites mémoire — 3 trajectoires, 3 pièges"),
    ("05-protocoles", "k8s/benchmark/vuln-benchmark.yaml",
     "Banc de vulnérabilités — 12 défauts, 5 configurations durcies (chapitre 7)"),
    ("05-protocoles", "k8s/benchmark/anomaly-benchmark-edge.yaml",
     "Extension zone grise — 7 cas limites avec prédiction de défaillance"),
]

FIGURES_SRC = "data/observations/figures"


# ══════════════════════════════════════════════════════════════════════════════

def sha256(path, block=1 << 16):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(block):
            h.update(chunk)
    return h.hexdigest()


def extract_key_results():
    """
    Extrait les chiffres saillants des journaux, pour éviter de les recopier à
    la main dans le mémoire — source d'erreurs de transcription.
    """
    out = {"campagnes_banc": [], "cluster_reel": None,
           "bootstrap": None, "temporel": None}

    # Campagnes de banc
    p = os.path.join(PROJECT_ROOT, "data/observations/benchmark_results.jsonl")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                row = {"timestamp": e.get("timestamp"),
                       "pods": e.get("pods_evaluated"),
                       "verite": e.get("ground_truth"), "f1": {}}
                for k, d in (e.get("detectors") or {}).items():
                    row["f1"][k] = d.get("f1")
                out["campagnes_banc"].append(row)

    # Dernière comparaison sur cluster réel
    p = os.path.join(PROJECT_ROOT, "data/observations/detection_log.jsonl")
    if os.path.exists(p):
        last = None
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        pass
        if last:
            out["cluster_reel"] = {"timestamp": last.get("timestamp"),
                                   "pods": last.get("pods_analysed"),
                                   "detections": last.get("detections")}

    # Bootstrap
    p = os.path.join(PROJECT_ROOT, "data/observations/bootstrap_ci.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                b = json.load(f)
            out["bootstrap"] = {
                "sample_size": b.get("sample_size"),
                "f1": {k: v.get("f1") for k, v in (b.get("results") or {}).items()
                       if "f1" in v}}
        except (json.JSONDecodeError, OSError):
            pass

    # Sensibilité temporelle
    p = os.path.join(PROJECT_ROOT, "data/observations/temporal_sensitivity.jsonl")
    if os.path.exists(p):
        last = None
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        pass
        if last:
            out["temporel"] = [
                {"t": s["t_seconds"],
                 "f1": {k: v.get("f1") for k, v in s.get("detectors", {}).items()}}
                for s in last.get("series", [])]

    return out


def write_key_results(dest, res):
    lines = ["# Résultats clés — extraits automatiquement des journaux", "",
             f"*Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}.*", "",
             "> Ces chiffres proviennent directement des fichiers de `02-donnees/`.",
             "> Les recopier plutôt que les ressaisir évite les erreurs de transcription.",
             ""]

    if res["campagnes_banc"]:
        lines += ["## Campagnes sur banc d'anomalies", "",
                  "| Campagne | Pods | Règles v1 | Règles v2 | Règles v3 | Random Forest |",
                  "|---|---|---|---|---|---|"]
        for i, c in enumerate(res["campagnes_banc"], 1):
            f = c["f1"]
            g = lambda k: f"{f[k]:.3f}" if f.get(k) is not None else "—"
            ts = (c["timestamp"] or "")[:16].replace("T", " ")
            lines.append(f"| {i} ({ts}) | {c['pods']} | {g('rules_v1')} | "
                         f"{g('rules_v2')} | {g('rules_v3')} | {g('ml')} |")
        lines.append("")

    if res["cluster_reel"]:
        c = res["cluster_reel"]
        lines += ["## Dernière comparaison sur cluster de production", "",
                  f"- Horodatage : {c['timestamp']}",
                  f"- Pods analysés : {c['pods']}", ""]
        for k, label in [("rules_v1", "Règles v1"), ("rules_v2", "Règles v2"),
                         ("rules_v3", "Règles v3"), ("ml", "Random Forest")]:
            v = (c.get("detections") or {}).get(k)
            if v is not None:
                lines.append(f"- {label} : {v} pod(s) signalé(s)")
        lines.append("")

    if res["bootstrap"]:
        b = res["bootstrap"]
        lines += ["## Intervalles de confiance (bootstrap)", "",
                  f"Échantillon : {b['sample_size']} pods.", "",
                  "| Détecteur | F1 | IC 95 % |", "|---|---|---|"]
        for k, label in [("rules_v1", "Règles v1"), ("rules_v2", "Règles v2"),
                         ("rules_v3", "Règles v3"), ("ml", "Random Forest")]:
            ci = b["f1"].get(k)
            if ci:
                pt = f"{ci['point']:.3f}" if ci.get("point") is not None else "—"
                lines.append(f"| {label} | {pt} | [{ci['ci_low']:.3f} ; {ci['ci_high']:.3f}] |")
        lines += ["", "> Un intervalle réduit à [1,000 ; 1,000] est un artefact du "
                  "bootstrap (aucune erreur dans l'échantillon d'origine). Employer "
                  "la règle de trois : borne inférieure ≈ 1 − 3/n.", ""]

    if res["temporel"]:
        lines += ["## Sensibilité à la fenêtre d'observation", "",
                  "| Instant (s) | Règles v1 | Règles v2 | Règles v3 | Random Forest |",
                  "|---|---|---|---|---|"]
        for s in res["temporel"]:
            g = lambda k: f"{s['f1'][k]:.3f}" if s["f1"].get(k) is not None else "—"
            lines.append(f"| {s['t']} | {g('rules_v1')} | {g('rules_v2')} | "
                         f"{g('rules_v3')} | {g('ml')} |")
        lines.append("")

    with open(os.path.join(dest, "RESULTATS_CLES.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="Archivage des travaux de recherche")
    ap.add_argument("--dest", default=DEFAULT_DEST)
    ap.add_argument("--name", default=None, help="nom du dossier (défaut : daté)")
    args = ap.parse_args()

    name = args.name or f"aikube-recherche-{datetime.now().strftime('%Y-%m-%d')}"
    dest = os.path.join(args.dest, name)

    if not os.path.isdir(args.dest):
        print(f"✖ Destination introuvable : {args.dest}")
        sys.exit(1)

    os.makedirs(dest, exist_ok=True)
    print(f"▶ Archivage vers {dest}\n")

    copied, missing = [], []

    for subdir, rel, desc in MANIFEST:
        src = os.path.join(PROJECT_ROOT, rel)
        if not os.path.exists(src):
            missing.append((rel, desc))
            continue
        target_dir = os.path.join(dest, subdir)
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, os.path.basename(rel))
        shutil.copy2(src, target)
        copied.append((subdir, os.path.basename(rel), desc, sha256(src)))
        print(f"  ✓ {subdir}/{os.path.basename(rel)}")

    # Figures : tout ce qui s'y trouve
    fig_src = os.path.join(PROJECT_ROOT, FIGURES_SRC)
    if os.path.isdir(fig_src):
        fig_dst = os.path.join(dest, "03-figures")
        os.makedirs(fig_dst, exist_ok=True)
        for fn in sorted(os.listdir(fig_src)):
            if fn.lower().endswith((".pdf", ".png", ".svg")):
                shutil.copy2(os.path.join(fig_src, fn), os.path.join(fig_dst, fn))
                copied.append(("03-figures", fn, "Figure du chapitre",
                               sha256(os.path.join(fig_src, fn))))
                print(f"  ✓ 03-figures/{fn}")

    if missing:
        print(f"\n  Absents ({len(missing)}) :")
        for rel, desc in missing:
            print(f"    – {rel}")

    # ── INDEX.md ──────────────────────────────────────────────────────────────
    idx = [f"# Archive de recherche AI-Kube — {datetime.now().strftime('%d/%m/%Y')}",
           "",
           "Ensemble des éléments nécessaires à la rédaction du mémoire et à la "
           "reproduction des mesures.", "",
           "## Organisation", "",
           "| Dossier | Contenu |", "|---|---|",
           "| `01-chapitres` | Textes rédigés et documents de travail |",
           "| `02-donnees` | Journaux bruts des campagnes |",
           "| `03-figures` | Figures du chapitre |",
           "| `04-code` | Code des détecteurs et des analyses |",
           "| `05-protocoles` | Manifestes des bancs d'évaluation |",
           "", "## Inventaire", ""]

    current = None
    for subdir, fname, desc, _ in copied:
        if subdir != current:
            idx += ["", f"### {subdir}", "", "| Fichier | Description |", "|---|---|"]
            current = subdir
        idx.append(f"| `{fname}` | {desc} |")

    idx += ["", "## Reproduction des mesures", "",
            "```bash",
            "# Comparaison sur cluster réel",
            "cd backend && python3 -m ai_module.detection_comparator",
            "",
            "# Banc d'anomalies",
            "kubectl apply -f k8s/benchmark/anomaly-benchmark.yaml",
            "kubectl apply -f k8s/benchmark/anomaly-benchmark-edge.yaml",
            "python3 scripts/anomaly_benchmark.py --no-deploy --wait 300",
            "kubectl delete namespace aikube-bench",
            "",
            "# Analyses",
            "python3 scripts/threshold_analysis.py --csv",
            "python3 scripts/sensitivity_analysis.py bootstrap",
            "python3 scripts/sensitivity_analysis.py temporal --at 60,120,300,600,900",
            "python3 scripts/plot_results.py --format pdf",
            "```", "",
            "## Environnement", "",
            "- Kubernetes v1.28.0, minikube 3 nœuds, pilote Docker",
            "- WSL2 Ubuntu, Python 3.14",
            "- Fenêtre d'observation de référence : 300 secondes", "",
            "> Les empreintes SHA-256 figurent dans `CHECKSUMS.txt`. Elles permettent "
            "d'attester qu'un résultat cité dans le mémoire correspond bien au fichier archivé.",
            ""]

    with open(os.path.join(dest, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(idx))

    # ── CHECKSUMS.txt ─────────────────────────────────────────────────────────
    with open(os.path.join(dest, "CHECKSUMS.txt"), "w", encoding="utf-8") as f:
        f.write(f"# SHA-256 — archive du {datetime.now().isoformat()}\n\n")
        for subdir, fname, _, digest in copied:
            f.write(f"{digest}  {subdir}/{fname}\n")

    # ── RESULTATS_CLES.md ─────────────────────────────────────────────────────
    write_key_results(dest, extract_key_results())

    print(f"\n✓ {len(copied)} fichiers archivés")
    print(f"  INDEX.md            inventaire commenté")
    print(f"  RESULTATS_CLES.md   chiffres extraits des journaux")
    print(f"  CHECKSUMS.txt       empreintes SHA-256")
    print(f"\n  {dest}")


if __name__ == "__main__":
    main()

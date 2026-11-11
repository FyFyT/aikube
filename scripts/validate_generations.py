#!/usr/bin/env python3
"""
scripts/validate_generations.py
================================================================================
Validation des manifestes produits par KubeLLM.

Second temps du protocole d'évaluation de la contribution C1. La génération
s'effectue sur GPU distant (notebook `KubeLLM_Evaluation.ipynb`) ; la
validation s'effectue ici, l'API Kubernetes n'étant pas accessible depuis
l'environnement d'exécution distant.

CE QUE MESURE CE SCRIPT
-----------------------
Trois taux, par modèle, sur le jeu de test :

  extraction   Une structure de manifeste est-elle identifiable dans la sortie ?
               Un modèle non spécialisé produit fréquemment du texte libre.
               Ce taux mesure la capacité à produire une forme exploitable.

  structure    Les champs obligatoires — apiVersion, kind, metadata.name —
               sont-ils présents ?

  validité     `kubectl apply --dry-run=server` accepte-t-il le manifeste ?
               C'est le critère qui détermine si la sortie est utilisable.

Un quatrième indicateur est rapporté : le type de ressource généré correspond-il
à celui attendu ? Produire un Deployment valide là où un Service était demandé
constitue un échec fonctionnel qu'aucune validation syntaxique ne détecte.

POURQUOI CETTE MESURE REMPLACE LE SCORE BLEU
--------------------------------------------
BLEU mesure une similarité textuelle à une référence. Trois limites en ce cas :

  · la référence elle-même est faillible — la validation du corpus établit
    que 12,5 % des paires ne sont pas applicables ;
  · le YAML admet plusieurs sérialisations équivalentes, de sorte qu'un
    manifeste correct peut différer textuellement de sa référence ;
  · l'évaluation initiale employait une génération stochastique sans graine :
    trois mesures du modèle de base ont donné 2,95, 1,31 et 0,51.

La validité par l'API est binaire, déterministe et directement interprétable.

USAGE
  python3 scripts/validate_generations.py
  python3 scripts/validate_generations.py --input chemin/vers/generations.json
================================================================================
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone

HERE         = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
OBS_DIR      = os.path.join(PROJECT_ROOT, "data", "observations")
DEFAULT_IN   = os.path.join(OBS_DIR, "kubellm_generations.json")
OUT_PATH     = os.path.join(OBS_DIR, "kubellm_validation.json")
NAMESPACE    = "aikube-llm-eval"


# ══════════════════════════════════════════════════════════════════════════════
# EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def extract_manifest(text):
    """
    Isole un manifeste dans la sortie brute.

    L'extraction est volontairement tolérante : le modèle peut produire du
    JSON, un bloc YAML délimité, ou du YAML nu précédé ou suivi de texte. Être
    strict pénaliserait un modèle dont la sortie est correcte mais entourée de
    commentaires — ce n'est pas ce qu'on cherche à mesurer.

    Retourne (manifeste, méthode) ou (None, motif d'échec).
    """
    if not text or not text.strip():
        return None, "sortie vide"

    # Objet JSON équilibré contenant apiVersion
    for m in re.finditer(r'\{', text):
        depth, start = 0, m.start()
        for i in range(start, min(len(text), start + 8000)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    frag = text[start:i + 1]
                    if '"apiVersion"' in frag or "'apiVersion'" in frag:
                        try:
                            return json.loads(frag), "json"
                        except json.JSONDecodeError:
                            pass
                    break

    # Bloc délimité par des clôtures de code
    for m in re.finditer(r'```(?:ya?ml)?\s*\n(.*?)```', text, re.S):
        try:
            import yaml
            d = yaml.safe_load(m.group(1))
            if isinstance(d, dict) and "apiVersion" in d:
                return d, "yaml-bloc"
        except Exception:
            continue

    # YAML nu : de la première ligne apiVersion jusqu'à une rupture nette
    m = re.search(r'^apiVersion:.*$', text, re.M)
    if m:
        frag = text[m.start():]
        frag = re.split(r'\n\s*\n\s*[A-ZÀ-Ü]|\n###|\n#{2,}|\nQuestion|\nInstruction',
                        frag)[0]
        try:
            import yaml
            d = yaml.safe_load(frag)
            if isinstance(d, dict) and "apiVersion" in d:
                return d, "yaml-nu"
        except Exception:
            pass

        # Sortie tronquée par la limite de jetons : la génération s'interrompt
        # au milieu d'une ligne et le document ne parse plus. On retire les
        # lignes finales une à une jusqu'à obtenir un document analysable.
        #
        # Cette récupération sert à distinguer deux situations que le taux
        # d'extraction confondrait autrement : un modèle produisant du YAML
        # correct mais coupé par le budget de jetons, et un modèle ne
        # produisant pas de structure du tout. Un manifeste ainsi récupéré
        # reste évidemment incomplet, et échouera à la validation.
        lignes = frag.split("\n")
        for fin in range(len(lignes) - 1, 2, -1):
            try:
                import yaml
                d = yaml.safe_load("\n".join(lignes[:fin]))
                if isinstance(d, dict) and "apiVersion" in d:
                    return d, "yaml-tronque"
            except Exception:
                continue

    return None, "aucune structure de manifeste identifiable"


def check_structure(m):
    if not isinstance(m, dict):
        return False, "pas un objet"
    for champ in ("apiVersion", "kind", "metadata"):
        if champ not in m:
            return False, f"{champ} absent"
    meta = m.get("metadata")
    if not isinstance(meta, dict) or not meta.get("name"):
        return False, "metadata.name absent"
    return True, None


def kubectl_validate(manifest, namespace):
    """
    Le namespace n'est imposé que si le manifeste n'en déclare pas : forcer -n
    sur un objet portant déjà metadata.namespace provoque un refus de kubectl
    sans rapport avec la validité du manifeste.
    """
    declared = (manifest.get("metadata") or {}).get("namespace")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        json.dump(manifest, fh)
        path = fh.name
    try:
        cmd = ["kubectl", "apply", "--dry-run=server", "-f", path]
        if not declared:
            cmd += ["-n", namespace]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if r.returncode == 0:
            return True, None
        return False, re.sub(r"\s+", " ", (r.stderr or r.stdout).strip())[:220]
    except subprocess.TimeoutExpired:
        return False, "expiration kubectl"
    finally:
        os.unlink(path)


def classify(msg):
    """
    Regroupe les erreurs par nature.

    L'ordre des tests importe : « no matches for kind » contient aussi
    « not found » dans certaines formulations de kubectl, ce qui faisait
    classer a tort des erreurs de groupe d'API en « namespace absent ».
    Le cas le plus specifique est donc teste en premier.
    """
    if not msg:
        return "—"
    m = msg.lower()
    if "no matches for kind" in m or "resource mapping not found" in m:
        return "groupe d'API incorrect"
    if "unknown field" in m:
        return "champ inconnu du schéma"
    if "namespaces" in m and "not found" in m:
        return "namespace absent (hors manifeste)"
    if "required value" in m or "required field" in m:
        return "champ obligatoire manquant"
    if "invalid value" in m or "is invalid" in m:
        return "valeur invalide"
    if "cannot unmarshal" in m or "cannot be handled" in m:
        return "type de donnée incorrect"
    if "connection refused" in m or "unable to connect" in m:
        return "cluster inaccessible"
    return "autre"


# ══════════════════════════════════════════════════════════════════════════════

def evaluate(nom, generations, namespace):
    resultats = []
    print(f"\n▶ {nom}")
    print(f"  {'#':>4}  {'attendu':<22} {'généré':<22} {'extrait':>10} {'struct':>7} {'valide':>7}")
    print(f"  {'-'*4}  {'-'*22} {'-'*22} {'-'*10} {'-'*7} {'-'*7}")

    for g in generations:
        manifest, methode = extract_manifest(g.get("generated", ""))
        ok_struct = ok_valide = False
        err = None
        if manifest is not None:
            ok_struct, err = check_structure(manifest)
            if ok_struct:
                ok_valide, err = kubectl_validate(manifest, namespace)

        genere_kind = (manifest or {}).get("kind") if manifest else None
        attendu = g.get("expected_kind") or "?"
        mark = lambda v: "✓" if v else "✗"
        print(f"  {g['index']:>4}  {attendu:<22} {str(genere_kind or '—'):<22} "
              f"{(methode if manifest is not None else '✗'):>10} "
              f"{mark(ok_struct):>7} {mark(ok_valide):>7}")

        resultats.append({
            "index": g["index"], "instruction": g.get("instruction", "")[:100],
            "expected_kind": attendu, "generated_kind": genere_kind,
            "extracted": manifest is not None, "extraction_method": methode,
            "structure_ok": ok_struct, "valid": ok_valide,
            "error": err, "error_category": classify(err),
            "kind_match": bool(genere_kind and genere_kind == attendu),
        })
    return resultats


def synthese(res):
    n = len(res)
    f = lambda k: sum(1 for r in res if r[k])
    pct = lambda a: f"{a/n*100:.1f} %" if n else "—"
    return {"n": n,
            "extraits": f("extracted"), "structure": f("structure_ok"),
            "valides": f("valid"), "kind_match": f("kind_match"),
            "taux_extraction": pct(f("extracted")),
            "taux_structure": pct(f("structure_ok")),
            "taux_validite": pct(f("valid")),
            "taux_kind": pct(f("kind_match"))}


def main():
    ap = argparse.ArgumentParser(description="Validation des générations KubeLLM")
    ap.add_argument("--input", default=DEFAULT_IN)
    args = ap.parse_args()

    if not os.path.exists(args.input):
        print(f"✖ Fichier introuvable : {args.input}")
        print("  Exécuter d'abord le notebook KubeLLM_Evaluation.ipynb sur Colab,")
        print(f"  puis placer kubellm_generations.json dans {OBS_DIR}/")
        sys.exit(1)

    data = json.load(open(args.input, encoding="utf-8"))
    resultats_bruts = data.get("resultats", {})

    print(f"\n{'='*84}")
    print("  VALIDITÉ DES MANIFESTES GÉNÉRÉS PAR KUBELLM")
    print(f"{'='*84}")
    print(f"  Modèle de base   : {data.get('base_model', '?')}")
    print(f"  Jeu de test      : {len(data.get('test_indices', []))} instructions")
    print(f"  Indices          : {data.get('test_indices')}")
    print(f"  Découpage        : {data.get('split', '?')}")
    print(f"  Échantillonnage  : {data.get('sampling', '?')}")

    subprocess.run(["kubectl", "create", "namespace", NAMESPACE],
                   capture_output=True, text=True, timeout=45)

    labels = {"base": "phi-2 (modèle de base)", "kubellm": "phi-2 + LoRA (KubeLLM-v1)"}
    tous, syntheses = {}, {}
    for cle, gens in resultats_bruts.items():
        tous[cle] = evaluate(labels.get(cle, cle), gens, NAMESPACE)
        syntheses[cle] = synthese(tous[cle])

    subprocess.run(["kubectl", "delete", "namespace", NAMESPACE, "--wait=false"],
                   capture_output=True, text=True, timeout=45)

    # ── Synthèse ──────────────────────────────────────────────────────────────
    print(f"\n{'='*84}")
    print("  SYNTHÈSE")
    print(f"{'='*84}")
    print(f"  {'Modèle':<30} {'extrait':>10} {'structure':>11} {'valide':>9} {'bon type':>10}")
    print(f"  {'-'*30} {'-'*10} {'-'*11} {'-'*9} {'-'*10}")
    for cle in ("base", "kubellm"):
        if cle not in syntheses:
            continue
        s = syntheses[cle]
        print(f"  {labels[cle]:<30} {s['taux_extraction']:>10} {s['taux_structure']:>11} "
              f"{s['taux_validite']:>9} {s['taux_kind']:>10}")

    if "base" in syntheses and "kubellm" in syntheses:
        b, k = syntheses["base"], syntheses["kubellm"]
        print(f"\n  Écarts absolus")
        print(f"    sorties exploitables : {b['extraits']}/{b['n']} → {k['extraits']}/{k['n']}")
        print(f"    manifestes valides   : {b['valides']}/{b['n']} → {k['valides']}/{k['n']}")
        print(f"    type correct         : {b['kind_match']}/{b['n']} → {k['kind_match']}/{k['n']}")
        print(f"\n  Le taux d'extraction mesure la capacité à produire une forme")
        print(f"  exploitable ; le taux de validité, la correction du manifeste.")

    # Natures d'échec
    for cle in ("base", "kubellm"):
        if cle not in tous:
            continue
        cats = Counter(r["error_category"] for r in tous[cle]
                       if not r["valid"] and r["error_category"] != "—")
        if cats:
            print(f"\n  Natures d'échec — {labels[cle]}")
            for c, n in cats.most_common():
                print(f"    {n:>3}  {c}")

    print(f"\n{'='*84}\n")

    os.makedirs(OBS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat(),
                   "source": args.input,
                   "test_indices": data.get("test_indices"),
                   "split": data.get("split"), "sampling": data.get("sampling"),
                   "syntheses": syntheses, "details": tous},
                  f, ensure_ascii=False, indent=2)
    print(f"✓ Résultats : {OUT_PATH}\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scripts/evaluate_kubellm.py
================================================================================
Évaluation du modèle spécialisé par validité des manifestes produits.

POURQUOI CETTE MESURE
---------------------
Le score BLEU rapporté pour la contribution C1 — de 0,51 à 28,22 — mesure la
ressemblance textuelle des sorties à des références. Deux limites en découlent.

BLEU ne renseigne pas sur la CORRECTION d'un manifeste. Un YAML peut ressembler
fortement à sa référence et être rejeté par l'API server ; il peut en différer
notablement et être parfaitement applicable, le YAML admettant plusieurs
sérialisations équivalentes.

Et la référence elle-même est faillible : la validation du corpus établit que
12,5 % des paires ne sont pas applicables. Un score de ressemblance à ces
références récompense donc partiellement la reproduction d'erreurs.

La mesure conduite ici est fonctionnelle : le manifeste généré est-il accepté
par le serveur d'API Kubernetes ? Le verdict est binaire, objectif, et
directement interprétable — c'est ce qui détermine si la sortie est utilisable.

PROTOCOLE
---------
  1. Un jeu de test est prélevé du corpus. Le découpage employé lors de
     l'entraînement n'étant pas documenté, une contamination est possible :
     cette limite est rapportée avec les résultats.
  2. Chaque instruction est soumise à phi-2 NU puis à phi-2 + ADAPTATEUR LoRA.
  3. Chaque sortie est analysée (extraction du JSON ou du YAML) puis soumise
     à `kubectl apply --dry-run=server`.
  4. Trois taux sont rapportés : sortie analysable, structurellement complète,
     acceptée par le serveur.

Le premier taux importe autant que le dernier : un modèle de base qui ne produit
pas de structure exploitable échoue avant même la validation.

COÛT
----
L'inférence sur processeur est lente : compter plusieurs minutes par manifeste
et par modèle. Le paramètre --limit permet un échantillon réduit.

USAGE
  python3 scripts/evaluate_kubellm.py --limit 15
  python3 scripts/evaluate_kubellm.py --limit 15 --skip-base
================================================================================
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE         = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
CORPUS       = os.path.join(PROJECT_ROOT, "data", "datasets", "kubernetes_pairs.jsonl")
ADAPTER      = os.path.join(PROJECT_ROOT, "models", "kubellm-v1")
OBS_DIR      = os.path.join(PROJECT_ROOT, "data", "observations")
OUT_PATH     = os.path.join(OBS_DIR, "kubellm_evaluation.json")
NAMESPACE    = "aikube-llm-eval"

BASE_MODEL   = "microsoft/phi-2"
MAX_TOKENS   = 400


# ══════════════════════════════════════════════════════════════════════════════
# EXTRACTION DE LA SORTIE
# ══════════════════════════════════════════════════════════════════════════════

def extract_manifest(text):
    """
    Isole un manifeste dans la sortie brute du modèle.

    Un modèle non spécialisé produit fréquemment du texte libre, des
    répétitions de l'instruction ou du code sans rapport. L'extraction est
    donc tolérante : on cherche d'abord un bloc JSON, puis un bloc YAML
    délimité, puis un fragment YAML reconnaissable.

    Retourne (manifeste, méthode d'extraction) ou (None, raison de l'échec).
    """
    if not text or not text.strip():
        return None, "sortie vide"

    # Bloc JSON : premier objet équilibré contenant apiVersion
    for m in re.finditer(r'\{', text):
        depth, start = 0, m.start()
        for i in range(start, min(len(text), start + 6000)):
            if text[i] == '{': depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    frag = text[start:i+1]
                    if '"apiVersion"' in frag or "'apiVersion'" in frag:
                        try:
                            return json.loads(frag), "json"
                        except json.JSONDecodeError:
                            break
                    break

    # Bloc YAML délimité par des clôtures de code
    for m in re.finditer(r'```(?:ya?ml)?\s*\n(.*?)```', text, re.S):
        try:
            import yaml
            d = yaml.safe_load(m.group(1))
            if isinstance(d, dict) and "apiVersion" in d:
                return d, "yaml-bloc"
        except Exception:
            continue

    # Fragment YAML : de la première ligne apiVersion jusqu'à une rupture
    m = re.search(r'^apiVersion:.*$', text, re.M)
    if m:
        frag = text[m.start():]
        frag = re.split(r'\n\s*\n\s*\S|\n#|\nQuestion|\nInstruction', frag)[0]
        try:
            import yaml
            d = yaml.safe_load(frag)
            if isinstance(d, dict) and "apiVersion" in d:
                return d, "yaml-fragment"
        except Exception:
            pass

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
        return False, re.sub(r"\s+", " ", (r.stderr or r.stdout).strip())[:200]
    except subprocess.TimeoutExpired:
        return False, "expiration kubectl"
    finally:
        os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# GÉNÉRATION
# ══════════════════════════════════════════════════════════════════════════════

def build_prompt(instruction, contexte):
    """
    Format d'invite. Celui employé à l'entraînement n'étant pas documenté,
    un format instruction générique est retenu — limite à rapporter, car le
    modèle spécialisé peut être sensible à cet écart.
    """
    p = f"### Instruction:\n{instruction}\n"
    if contexte:
        p += f"\n### Contexte:\n{contexte}\n"
    p += "\n### Manifeste Kubernetes:\n"
    return p


def load_model(with_adapter):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(ADAPTER if with_adapter else BASE_MODEL,
                                        trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, torch_dtype=torch.float32,
        trust_remote_code=True, low_cpu_mem_usage=True)

    if with_adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, ADAPTER)

    model.eval()
    return model, tok


def generate(model, tok, prompt):
    import torch
    inputs = tok(prompt, return_tensors="pt", truncation=True, max_length=1024)
    with torch.no_grad():
        out = model.generate(
            **inputs, max_new_tokens=MAX_TOKENS,
            do_sample=False,                 # déterministe : la mesure doit être reproductible
            pad_token_id=tok.pad_token_id,
            eos_token_id=tok.eos_token_id)
    texte = tok.decode(out[0], skip_special_tokens=True)
    return texte[len(prompt):] if texte.startswith(prompt) else texte


# ══════════════════════════════════════════════════════════════════════════════

def evaluate_model(nom, with_adapter, echantillon, namespace):
    print(f"\n▶ Chargement — {nom}")
    t0 = time.time()
    try:
        model, tok = load_model(with_adapter)
    except Exception as e:
        print(f"  ✖ {e}")
        return None
    print(f"  chargé en {time.time()-t0:.0f} s")

    resultats = []
    print(f"\n  {'#':>4}  {'kind attendu':<20} {'extrait':>9} {'struct':>7} {'valide':>7}  {'durée':>6}")
    print(f"  {'-'*4}  {'-'*20} {'-'*9} {'-'*7} {'-'*7}  {'-'*6}")

    for ex in echantillon:
        t = time.time()
        try:
            sortie = generate(model, tok, build_prompt(ex["instruction"], ex.get("input")))
        except Exception as e:
            sortie = ""
            print(f"  {ex['index']:>4}  génération échouée : {str(e)[:60]}")

        manifest, methode = extract_manifest(sortie)
        ok_struct = ok_valide = False
        err = None
        if manifest is not None:
            ok_struct, err = check_structure(manifest)
            if ok_struct:
                ok_valide, err = kubectl_validate(manifest, namespace)

        attendu = (ex.get("output") or {}).get("kind", "?")
        m = lambda v: "✓" if v else "✗"
        print(f"  {ex['index']:>4}  {attendu:<20} {(methode if manifest is not None else '✗'):>9} "
              f"{m(ok_struct):>7} {m(ok_valide):>7}  {time.time()-t:>5.0f}s")

        resultats.append({
            "index": ex["index"], "instruction": ex["instruction"][:100],
            "expected_kind": attendu,
            "extracted": manifest is not None, "extraction_method": methode,
            "generated_kind": (manifest or {}).get("kind") if manifest else None,
            "structure_ok": ok_struct, "valid": ok_valide, "error": err,
            "raw_output": sortie[:600],
        })

    del model
    import gc; gc.collect()
    return resultats


def synthese(nom, res):
    n = len(res)
    ext = sum(1 for r in res if r["extracted"])
    st  = sum(1 for r in res if r["structure_ok"])
    va  = sum(1 for r in res if r["valid"])
    kind_ok = sum(1 for r in res
                  if r["generated_kind"] and r["generated_kind"] == r["expected_kind"])
    pct = lambda a: f"{a/n*100:.1f} %" if n else "—"
    return {"nom": nom, "n": n, "extraits": ext, "structure": st, "valides": va,
            "kind_correct": kind_ok,
            "taux_extraction": pct(ext), "taux_structure": pct(st),
            "taux_validite": pct(va), "taux_kind": pct(kind_ok)}


def main():
    ap = argparse.ArgumentParser(description="Évaluation de KubeLLM par validité")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--skip-base", action="store_true",
                    help="n'évaluer que le modèle spécialisé")
    ap.add_argument("--offset", type=int, default=0,
                    help="décalage dans le corpus (choix du jeu de test)")
    args = ap.parse_args()

    if not os.path.isdir(ADAPTER):
        print(f"✖ Adaptateur introuvable : {ADAPTER}")
        sys.exit(1)

    pairs = []
    with open(CORPUS, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if line.strip():
                pairs.append({"index": i, **json.loads(line)})

    # Le jeu de test est prélevé en fin de corpus, choix arbitraire faute de
    # découpage documenté. Cette limite est rapportée avec les résultats.
    echantillon = pairs[len(pairs) - args.limit - args.offset:
                        len(pairs) - args.offset] if args.offset else pairs[-args.limit:]

    print(f"\n{'='*78}")
    print("  ÉVALUATION DE KUBELLM PAR VALIDITÉ DES MANIFESTES")
    print(f"{'='*78}")
    print(f"  Corpus         : {len(pairs)} paires")
    print(f"  Jeu de test    : {len(echantillon)} instructions (indices "
          f"{echantillon[0]['index']}–{echantillon[-1]['index']})")
    print(f"  Adaptateur     : {ADAPTER}")
    print(f"\n  ⚠ Le découpage entraînement/test employé lors de la spécialisation")
    print(f"    n'est pas documenté. Une contamination du jeu de test est possible ;")
    print(f"    les résultats du modèle spécialisé peuvent en être surestimés.")

    subprocess.run(["kubectl", "create", "namespace", NAMESPACE],
                   capture_output=True, text=True, timeout=45)

    resultats = {}
    if not args.skip_base:
        r = evaluate_model("phi-2 (modèle de base)", False, echantillon, NAMESPACE)
        if r: resultats["base"] = r
    r = evaluate_model("phi-2 + LoRA (KubeLLM-v1)", True, echantillon, NAMESPACE)
    if r: resultats["kubellm"] = r

    subprocess.run(["kubectl", "delete", "namespace", NAMESPACE, "--wait=false"],
                   capture_output=True, text=True, timeout=45)

    print(f"\n{'='*78}")
    print("  SYNTHÈSE")
    print(f"{'='*78}")
    print(f"  {'Modèle':<28} {'extrait':>9} {'struct.':>9} {'valide':>9} {'bon kind':>10}")
    print(f"  {'-'*28} {'-'*9} {'-'*9} {'-'*9} {'-'*10}")
    syntheses = {}
    for cle, nom in (("base", "phi-2 (base)"), ("kubellm", "phi-2 + LoRA")):
        if cle not in resultats: continue
        s = synthese(nom, resultats[cle])
        syntheses[cle] = s
        print(f"  {nom:<28} {s['taux_extraction']:>9} {s['taux_structure']:>9} "
              f"{s['taux_validite']:>9} {s['taux_kind']:>10}")

    if "base" in syntheses and "kubellm" in syntheses:
        b, k = syntheses["base"], syntheses["kubellm"]
        print(f"\n  Écart de validité : {b['valides']}/{b['n']} → {k['valides']}/{k['n']}")
        print(f"  Écart d'extraction : {b['extraits']}/{b['n']} → {k['extraits']}/{k['n']}")
        print("\n  Le taux d'extraction mesure la capacité à produire une structure")
        print("  exploitable ; le taux de validité, la correction du manifeste.")

    os.makedirs(OBS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat(),
                   "base_model": BASE_MODEL, "adapter": ADAPTER,
                   "test_indices": [e["index"] for e in echantillon],
                   "syntheses": syntheses, "resultats": resultats},
                  f, ensure_ascii=False, indent=2)
    print(f"\n✓ Résultats : {OUT_PATH}\n")


if __name__ == "__main__":
    main()

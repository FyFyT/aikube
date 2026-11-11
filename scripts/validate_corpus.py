#!/usr/bin/env python3
"""
scripts/validate_corpus.py
================================================================================
Validation du corpus de spécialisation par l'API Kubernetes.

QUESTION POSÉE
--------------
Le corpus de la contribution C1 est produit par un modèle enseignant
(llama-3.3-70b) sans vérification externe. Ses sorties servent de référence à
l'entraînement du modèle élève, et de référence au calcul du score BLEU.

Si une part de ces références n'est pas un manifeste Kubernetes valide, deux
conséquences suivent. Le modèle élève apprend à reproduire des configurations
inapplicables. Et le score BLEU récompense la ressemblance à ces mêmes
références, donc mesure une conformité à des exemples éventuellement fautifs.

Ce script répond à la question par la seule autorité qui fasse foi : le serveur
d'API Kubernetes lui-même.

TROIS NIVEAUX DE VALIDATION
---------------------------
  structure  Le document possède-t-il apiVersion, kind, metadata.name ?
             Vérification locale, sans cluster.

  client     `kubectl apply --dry-run=client` — validation du schéma par le
             client, sans consulter le serveur. Détecte les champs inconnus et
             les types incorrects.

  serveur    `kubectl apply --dry-run=server` — le serveur applique le schéma
             complet, les valeurs par défaut, les webhooks d'admission et les
             contraintes inter-champs, sans créer la ressource. C'est le
             verdict qui compte : un manifeste acceptable ici est applicable
             en production.

Les trois niveaux sont rapportés séparément : un manifeste peut passer la
validation client et échouer au serveur, ce qui caractérise des erreurs
différentes.

PRÉCAUTION
----------
La validation serveur s'effectue dans un namespace dédié, créé puis supprimé.
`--dry-run=server` ne persiste aucune ressource, mais certains manifestes du
corpus référencent des namespaces ou des ressources absentes ; ces échecs sont
distingués des erreurs de structure.

USAGE
  python3 scripts/validate_corpus.py
  python3 scripts/validate_corpus.py --no-server    # sans cluster
  python3 scripts/validate_corpus.py --limit 20     # échantillon
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
CORPUS       = os.path.join(PROJECT_ROOT, "data", "datasets", "kubernetes_pairs.jsonl")
OBS_DIR      = os.path.join(PROJECT_ROOT, "data", "observations")
OUT_PATH     = os.path.join(OBS_DIR, "corpus_validation.json")
NAMESPACE    = "aikube-corpus-check"

REQUIRED = ["apiVersion", "kind", "metadata"]


# ══════════════════════════════════════════════════════════════════════════════

def load_corpus(limit=None):
    pairs = []
    with open(CORPUS, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                pairs.append({"index": i, **json.loads(line)})
            except json.JSONDecodeError as e:
                pairs.append({"index": i, "_parse_error": str(e)[:120]})
            if limit and len(pairs) >= limit:
                break
    return pairs


def check_structure(manifest):
    """Vérification locale : champs obligatoires de tout objet Kubernetes."""
    if not isinstance(manifest, dict):
        return False, f"la sortie n'est pas un objet ({type(manifest).__name__})"
    manquants = [c for c in REQUIRED if c not in manifest]
    if manquants:
        return False, f"champs absents : {', '.join(manquants)}"
    meta = manifest.get("metadata")
    if not isinstance(meta, dict) or not meta.get("name"):
        return False, "metadata.name absent"
    if not manifest.get("kind"):
        return False, "kind vide"
    return True, None


def kubectl_dry_run(manifest, mode, namespace=None):
    """
    Soumet le manifeste a kubectl en mode dry-run.

    Le namespace n'est impose QUE si le manifeste n'en declare pas. Forcer
    -n sur un objet portant deja metadata.namespace provoque un refus de
    kubectl — « the namespace from the provided object does not match » —
    qui n'a rien a voir avec la validite du manifeste. Une premiere version
    de ce script commettait cette erreur et rapportait 51,8 % de validite
    la ou l'essentiel des echecs venaient du protocole.
    """
    declared = (manifest.get("metadata") or {}).get("namespace")
    if declared:
        namespace = None
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        json.dump(manifest, fh)
        path = fh.name
    try:
        cmd = ["kubectl", "apply", f"--dry-run={mode}", "-f", path]
        if namespace:
            cmd += ["-n", namespace]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if r.returncode == 0:
            return True, None
        err = (r.stderr or r.stdout).strip()
        err = re.sub(r"\s+", " ", err)
        return False, err[:250]
    except subprocess.TimeoutExpired:
        return False, "expiration du délai kubectl"
    finally:
        os.unlink(path)


def classify_error(msg):
    """
    Regroupe les erreurs par nature. Distingue notamment les échecs dus à des
    ressources externes absentes — qui ne mettent pas en cause le manifeste —
    des erreurs de schéma, qui le disqualifient.
    """
    if not msg:
        return "—"
    m = msg.lower()
    if "not found" in m and "namespace" in m:
        return "namespace référencé absent (hors manifeste)"
    if "unknown field" in m or "unknown fields" in m:
        return "champ inconnu du schéma"
    if "no matches for kind" in m or "no kind" in m:
        return "type de ressource inexistant"
    if "invalid value" in m or "invalid:" in m:
        return "valeur invalide"
    if "required value" in m or "required field" in m:
        return "champ obligatoire manquant"
    if "cannot unmarshal" in m or "json:" in m:
        return "type de donnée incorrect"
    if "quantities must match" in m or "quantity" in m:
        return "quantité mal formée"
    if "connection refused" in m or "timed out" in m or "unable to connect" in m:
        return "cluster inaccessible"
    return "autre"


# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Validation du corpus C1")
    ap.add_argument("--no-server", action="store_true",
                    help="omettre la validation serveur (sans cluster)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    if not os.path.exists(CORPUS):
        print(f"✖ Corpus introuvable : {CORPUS}")
        sys.exit(1)

    pairs = load_corpus(args.limit)
    print(f"\n▶ Corpus : {len(pairs)} paires — {CORPUS}")

    use_server = not args.no_server
    if use_server:
        r = subprocess.run(["kubectl", "create", "namespace", NAMESPACE],
                           capture_output=True, text=True, timeout=45)
        if r.returncode != 0 and "already exists" not in (r.stderr or ""):
            print(f"⚠ Namespace de test non créé, validation serveur désactivée")
            print(f"  ({(r.stderr or '').strip()[:120]})")
            use_server = False
        else:
            print(f"▶ Namespace de validation : {NAMESPACE}")

    # Les manifestes referencent des namespaces qui n'existent pas sur ce
    # cluster. Leur absence ferait echouer la validation serveur pour une
    # raison etrangere a la qualite du manifeste : on les cree au prealable.
    declared_ns = set()
    for p_ in pairs:
        m = p_.get("output")
        if isinstance(m, dict):
            ns = (m.get("metadata") or {}).get("namespace")
            if ns and ns not in ("default", NAMESPACE):
                declared_ns.add(ns)
    created_ns = []
    if use_server and declared_ns:
        print(f"▶ Creation de {len(declared_ns)} namespaces references par le corpus")
        for ns in sorted(declared_ns):
            r = subprocess.run(["kubectl", "create", "namespace", ns],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                created_ns.append(ns)

    results = []
    print(f"\n  {'#':>4}  {'kind':<22} {'struct':>7} {'client':>7} {'serveur':>8}")
    print(f"  {'-'*4}  {'-'*22} {'-'*7} {'-'*7} {'-'*8}")

    for p in pairs:
        idx = p["index"]
        if "_parse_error" in p:
            results.append({"index": idx, "kind": "?", "structure": False,
                            "client": False, "server": False,
                            "error_structure": p["_parse_error"]})
            print(f"  {idx:>4}  {'(JSON illisible)':<22} {'✗':>7} {'—':>7} {'—':>8}")
            continue

        manifest = p.get("output")
        kind = manifest.get("kind", "?") if isinstance(manifest, dict) else "?"

        ok_struct, err_struct = check_structure(manifest)
        ok_client = ok_server = None
        err_client = err_server = None

        if ok_struct:
            ok_client, err_client = kubectl_dry_run(manifest, "client")
            if use_server:
                ok_server, err_server = kubectl_dry_run(manifest, "server", NAMESPACE)

        results.append({
            "index": idx, "kind": kind,
            "instruction": (p.get("instruction") or "")[:100],
            "structure": ok_struct, "error_structure": err_struct,
            "client": ok_client, "error_client": err_client,
            "server": ok_server, "error_server": err_server,
            "category_client": classify_error(err_client),
            "category_server": classify_error(err_server),
        })

        m = lambda v: "✓" if v else ("✗" if v is False else "—")
        print(f"  {idx:>4}  {kind:<22} {m(ok_struct):>7} {m(ok_client):>7} {m(ok_server):>8}")

    if use_server:
        for ns in [NAMESPACE] + created_ns:
            subprocess.run(["kubectl", "delete", "namespace", ns, "--wait=false"],
                           capture_output=True, text=True, timeout=30)

    # ── Synthèse ──────────────────────────────────────────────────────────────
    n = len(results)
    n_struct = sum(1 for r in results if r["structure"])
    n_client = sum(1 for r in results if r["client"])
    n_server = sum(1 for r in results if r["server"])
    evaluated_server = sum(1 for r in results if r["server"] is not None)

    print(f"\n{'='*74}")
    print("  VALIDITÉ DU CORPUS DE SPÉCIALISATION")
    print(f"{'='*74}")
    print(f"  Paires évaluées : {n}\n")
    pct = lambda a, b: f"{a/b*100:.1f} %" if b else "—"
    print(f"  {'Niveau':<34} {'valides':>9} {'taux':>8}")
    print(f"  {'-'*34} {'-'*9} {'-'*8}")
    print(f"  {'Structure (champs obligatoires)':<34} {n_struct:>4}/{n:<4} {pct(n_struct,n):>8}")
    print(f"  {'Schéma client (--dry-run=client)':<34} {n_client:>4}/{n:<4} {pct(n_client,n):>8}")
    if evaluated_server:
        print(f"  {'Serveur (--dry-run=server)':<34} {n_server:>4}/{evaluated_server:<4} "
              f"{pct(n_server,evaluated_server):>8}")

    # Natures d'échec
    for niveau, cle in (("client", "category_client"), ("serveur", "category_server")):
        cats = Counter(r[cle] for r in results
                       if r.get(cle) and r[cle] != "—"
                       and r.get("client" if niveau == "client" else "server") is False)
        if cats:
            print(f"\n  Natures d'échec — validation {niveau}")
            for c, k in cats.most_common():
                print(f"    {k:>3}  {c}")

    # Par type de ressource
    print(f"\n  Validité serveur par type de ressource")
    par_kind = {}
    for r in results:
        if r["server"] is None:
            continue
        k = r["kind"]
        par_kind.setdefault(k, [0, 0])
        par_kind[k][1] += 1
        if r["server"]:
            par_kind[k][0] += 1
    for k, (ok, tot) in sorted(par_kind.items(), key=lambda x: (x[1][0]/x[1][1], -x[1][1])):
        marque = "  ⚠" if ok < tot else ""
        print(f"    {k:<24} {ok:>3}/{tot:<3} {pct(ok,tot):>8}{marque}")

    # Exemples d'échec
    echecs = [r for r in results if r["server"] is False][:6]
    if echecs:
        print(f"\n  Exemples d'échec")
        for r in echecs:
            print(f"    #{r['index']} {r['kind']} — {r['category_server']}")
            print(f"      {r['instruction']}")
            print(f"      {(r['error_server'] or '')[:150]}")

    print(f"{'='*74}\n")

    os.makedirs(OBS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "corpus": CORPUS, "pairs": n,
            "valid_structure": n_struct, "valid_client": n_client,
            "valid_server": n_server, "evaluated_server": evaluated_server,
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"✓ Résultats : {OUT_PATH}\n")


if __name__ == "__main__":
    main()

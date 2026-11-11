#!/usr/bin/env python3
"""
scripts/corpus_v3_report.py — Fiabilité de l'enseignant et contenu du corpus v3.
Lit data/observations/corpus_v3_log.jsonl et data/datasets/corpus_v3.jsonl.
Usage : python3 scripts/corpus_v3_report.py
"""
import json
import os
from collections import Counter, defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOG = os.path.join(ROOT, "data", "observations", "corpus_v3_log.jsonl")
OUT = os.path.join(ROOT, "data", "datasets", "corpus_v3.jsonl")
PRICE_IN, PRICE_OUT = 0.15, 0.60      # $/M jetons, gpt-oss-120b (offre payante Groq)

rows = [json.loads(l) for l in open(LOG, encoding="utf-8")] if os.path.exists(LOG) else []
# Coupures réseau (« Request timed out ») : ni succès ni échec de l'enseignant, exclues
net = [r for r in rows if not r["valid"] and r["attempts"]
       and all(any("timed out" in c.lower() for _, c in a["errors"]) for a in r["attempts"])]
rows = [r for r in rows if r not in net]
if net:
    print(f"(exclus : {len(net)} exemple(s) perdus sur coupure réseau, pas sur une erreur de l'enseignant)")
if not rows:
    raise SystemExit("Aucune génération (scripts/build_corpus_v3.py).")
by = defaultdict(list)
for r in rows:
    by[r["category"]].append(r)
print(f"Exemples tentés : {len(rows)} · valides : {sum(r['valid'] for r in rows)}")
print(f"\n{'catégorie':12} {'n':>4} {'1er essai':>10} {'≤3 essais':>10}  erreurs du 1er essai")
for cat, rs in sorted(by.items(), key=lambda x: -len(x[1])):
    p1 = sum(r["rank"] == 1 for r in rs)
    pk = sum(r["valid"] for r in rs)
    errs = Counter(f"{s}·{c}" for r in rs if r["attempts"] for s, c in r["attempts"][0]["errors"])
    print(f"{cat:12} {len(rs):>4} {p1 / len(rs):>9.0%} {pk / len(rs):>10.0%}  "
          + ", ".join(f"{k} ({v})" for k, v in errs.most_common(3)))
n = len(rows)
p1 = sum(r["rank"] == 1 for r in rows)
pk = sum(r["valid"] for r in rows)
print(f"{'TOTAL':12} {n:>4} {p1 / n:>9.0%} {pk / n:>10.0%}")
tok = sum(r["tokens"] for r in rows)
print(f"\nJetons consommés : {tok:,}".replace(",", " ")
      + f" (≈ {tok * (0.7 * PRICE_IN + 0.3 * PRICE_OUT) / 1e6:.2f} $ au tarif payant)")
if os.path.exists(OUT):
    corpus = [json.loads(l) for l in open(OUT, encoding="utf-8")]
    print(f"Corpus : {len(corpus)} exemples → {OUT}")
    print("Répartition :", Counter(c["meta"]["category"] for c in corpus).most_common())
    warn = sum("# Avertissement" in c["messages"][2]["content"] for c in corpus)
    print(f"Exemples avec un avertissement de réduction : {warn}")
    wc = Counter(c["meta"]["category"] for c in corpus if "# Avertissement" in c["messages"][2]["content"])
    tot = Counter(c["meta"]["category"] for c in corpus)
    print("  par catégorie (attendu : surtout « capacite ») :",
          ", ".join(f"{k} {wc[k]}/{tot[k]}" for k in sorted(tot, key=lambda k: -wc[k]) if wc[k]))
    ex = [c for c in corpus if "# Avertissement" in c["messages"][2]["content"] and c["meta"]["category"] != "capacite"][:3]
    for c in ex:
        print("  exemple hors capacité :", c["messages"][2]["content"].split("\n")[0][:140])

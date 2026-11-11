#!/usr/bin/env python3
"""
scripts/campaign_report.py — Rapport des campagnes de mesure (C1 générateur, C2 agent, OOM)
================================================================================
Lit les journaux des campagnes et écrit un NOUVEAU rapport horodaté :
  data/observations/RAPPORT_MESURES_<date-heure>.md
Les rapports précédents ne sont jamais écrasés.

USAGE (dans ~/aikube)
  python3 scripts/campaign_report.py
================================================================================
"""
import csv
import json
import math
import os
import statistics as st
import time
from collections import Counter, defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OBS = os.path.join(ROOT, "data", "observations")
CONF_MIN = 0.25


def load(name):
    p = os.path.join(OBS, name)
    rows = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def rate(k, n):
    if n == 0:
        return "—"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = **{100 * k / n:.0f} %** [{100 * lo:.0f}–{100 * hi:.0f}]"


def mcnemar(b, c):
    """Test exact de McNemar (bilatéral) sur les paires discordantes."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def med(xs, fmt="{:.0f}"):
    xs = [x for x in xs if x is not None]
    return fmt.format(st.median(xs)) if xs else "—"


def tokens(usages):
    tin = tout = 0
    for u in usages or []:
        if isinstance(u, dict):
            for k, v in u.items():
                if isinstance(v, (int, float)):
                    if "input" in k or "prompt" in k:
                        tin += v
                    elif "output" in k or "completion" in k:
                        tout += v
    return tin, tout


# ── C1 ───────────────────────────────────────────────────────────────────────

def report_c1(out):
    rows = [r for r in load("campaign_yaml.jsonl") if r.get("status") == "ok"]
    out.append("## C1 — Générateur de manifestes\n")
    if not rows:
        out.append("_Aucune mesure (scripts/campaign_yaml.py)._\n")
        return
    # dernière mesure par (demande, condition, répétition)
    last = {}
    for r in rows:
        last[(r["prompt_id"], r["condition"], r["rep"])] = r
    rows = list(last.values())
    inconcl = [r for r in rows if r.get("server_validated") is None]
    rows = [r for r in rows if r.get("server_validated") is not None]
    conds = sorted({r["condition"] for r in rows})
    out.append("Arbitres indépendants du modèle : `kubectl apply --dry-run=server --validate=strict` "
               "+ contrôle de capacité réelle. pass@1 = valide dès la 1re tentative ; "
               "pass@3 = valide après ≤ 3 tentatives. Intervalles de Wilson à 95 %.\n")
    if inconcl:
        out.append(f"_{len(inconcl)} génération(s) exclue(s) : serveur Kubernetes injoignable._\n")

    out.append("| Condition | n | pass@1 | pass@3 | Types attendus présents | Tentatives moy. | Durée méd. | Jetons (entrée / sortie) |")
    out.append("|---|---|---|---|---|---|---|---|")
    for c in conds:
        rs = [r for r in rows if r["condition"] == c]
        n = len(rs)
        p1 = sum(1 for r in rs if r["valid_first"])
        p3 = sum(1 for r in rs if r["valid"])
        ko = sum(1 for r in rs if r["valid"] and r["kinds_ok"])
        tin = tout = 0
        for r in rs:
            a, b = tokens(r.get("tokens"))
            tin, tout = tin + a, tout + b
        out.append(f"| {c} | {n} | {rate(p1, n)} | {rate(p3, n)} | {ko}/{p3} | "
                   f"{st.mean(r['attempts'] for r in rs):.2f} | {med([r['duration_s'] for r in rs])} s | "
                   f"{tin:,} / {tout:,} |".replace(",", " "))
    out.append("")

    # Variabilité d'une répétition à l'autre (conditions mesurées plusieurs fois)
    multi = [c for c in conds if len({r["rep"] for r in rows if r["condition"] == c}) > 1]
    if multi:
        out.append("### Variabilité entre répétitions (mêmes 36 demandes)\n")
        out.append("| Condition | pass@1 par répétition | pass@3 par répétition | Demandes réussies à chaque fois (pass@1) |")
        out.append("|---|---|---|---|")
        for c in multi:
            rs = [r for r in rows if r["condition"] == c]
            reps = sorted({r["rep"] for r in rs})
            p1 = [sum(r["valid_first"] for r in rs if r["rep"] == k) for k in reps]
            p3 = [sum(r["valid"] for r in rs if r["rep"] == k) for k in reps]
            per = {}
            for r in rs:
                per.setdefault(r["prompt_id"], []).append(r["valid_first"])
            always = sum(1 for v in per.values() if len(v) == len(reps) and all(v))
            never = sum(1 for v in per.values() if len(v) == len(reps) and not any(v))
            out.append(f"| {c} | {' / '.join(map(str, p1))} | {' / '.join(map(str, p3))} | "
                       f"{always} toujours, {never} jamais, {len(per) - always - never} selon la répétition |")
        out.append("")

    # Réparation déterministe : part du modèle, part de la réparation
    rep_conds = [c for c in conds if c.endswith("-repair")]
    if rep_conds:
        out.append("### Réparation déterministe (le modèle propose, les règles corrigent ce qui se calcule)\n")
        out.append("pass@1 **modèle** = 1er manifeste du modèle valide tel quel ; pass@1 **système** = valide "
                   "après réparation du 1er manifeste, sans 2e appel au modèle.\n")
        out.append("| Condition | n | pass@1 modèle | pass@1 système | Réparés au 1er essai | pass@3 système | Règles appliquées (nombre) |")
        out.append("|---|---|---|---|---|---|---|")
        for c in rep_conds:
            rs = [r for r in rows if r["condition"] == c]
            n = len(rs)
            rules = Counter()
            for r in rs:
                for ch in [x for lst in r.get("repair_changes", []) for x in lst]:
                    rules[_rule(ch)] += 1
            out.append(f"| {c} | {n} | {rate(sum(bool(r.get('model_valid_first')) for r in rs), n)} | "
                       f"{rate(sum(r['valid_first'] for r in rs), n)} | {sum(bool(r.get('repaired_first')) for r in rs)} | "
                       f"{rate(sum(r['valid'] for r in rs), n)} | "
                       + (", ".join(f"{k} ({v})" for k, v in rules.most_common()) or "aucune") + " |")
        out.append("")
        for c in rep_conds:
            rest = Counter()
            for r in rows:
                if r["condition"] == c and not r["valid"]:
                    for src, cat in (r["errors"][-1] if r["errors"] else []):
                        rest[f"{src} · {cat}"] += 1
            out.append(f"- {c} — erreurs restantes après réparation et 3 essais : "
                       + (", ".join(f"{k} ({v})" for k, v in rest.most_common()) or "aucune"))
        out.append("")

    # Demandes dépassant la capacité réelle
    out.append("### Demandes qui dépassent la capacité réelle (k01–k10)\n")
    out.append("Le bon comportement est de réduire la demande à ce qui tient sur la machine et de le signaler.\n")
    out.append("| Condition | pass@1 | 1er manifeste rejeté pour capacité |")
    out.append("|---|---|---|")
    for c in conds:
        rs = [r for r in rows if r["condition"] == c and r["capacity_stress"]]
        out.append(f"| {c} | {rate(sum(r['valid_first'] for r in rs), len(rs))} | "
                   f"{sum(r['capacity_error_first'] for r in rs)}/{len(rs)} |")
    out.append("")

    # Par catégorie
    cats = sorted({r["category"] for r in rows})
    out.append("### pass@1 par catégorie\n")
    out.append("| Catégorie | " + " | ".join(conds) + " |")
    out.append("|---|" + "---|" * len(conds))
    for cat in cats:
        cells = []
        for c in conds:
            rs = [r for r in rows if r["condition"] == c and r["category"] == cat]
            cells.append(f"{sum(r['valid_first'] for r in rs)}/{len(rs)}")
        out.append(f"| {cat} | " + " | ".join(cells) + " |")
    out.append("")

    # Erreurs du 1er essai
    out.append("### Erreurs constatées au 1er essai\n")
    out.append("| Condition | Catégories (nombre) |")
    out.append("|---|---|")
    for c in conds:
        cnt = Counter()
        for r in rows:
            if r["condition"] == c and r["errors"]:
                for src, cat in r["errors"][0]:
                    cnt[f"{src} · {cat}"] += 1
        out.append(f"| {c} | " + (", ".join(f"{k} ({v})" for k, v in cnt.most_common()) or "aucune") + " |")
    out.append("")

    # Ablation appariée
    if {"claude", "claude-noctx"} <= set(conds):
        a = {(r["prompt_id"], r["rep"]): r for r in rows if r["condition"] == "claude"}
        b = {(r["prompt_id"], r["rep"]): r for r in rows if r["condition"] == "claude-noctx"}
        keys = sorted(set(a) & set(b))
        out.append("### Ablation : effet de la lecture de la machine hôte (même modèle, mêmes demandes)\n")
        for label, fld, sub in (("pass@1, toutes demandes", "valid_first", None),
                                ("pass@1, demandes de capacité", "valid_first", True),
                                ("pass@3, toutes demandes", "valid", None)):
            ks = [k for k in keys if sub is None or a[k]["capacity_stress"] == sub]
            only_ctx = sum(1 for k in ks if a[k][fld] and not b[k][fld])
            only_no = sum(1 for k in ks if b[k][fld] and not a[k][fld])
            out.append(f"- {label} : {len(ks)} paires ; réussite **avec contexte seulement** : {only_ctx}, "
                       f"**sans contexte seulement** : {only_no} ; McNemar exact p = {fmt_p(mcnemar(only_ctx, only_no))}")
        out.append("")

    # Comparaisons appariées des modèles (mêmes demandes, 1re répétition)
    pairs = [("kubellm", "kubellm-base", "effet du fine-tuning (même modèle de base)"),
             ("kubellm", "groq", "élève 1,5B contre son enseignant gpt-oss-120b"),
             ("kubellm", "claude", "modèle local contre Claude"),
             ("kubellm-v3", "kubellm", "2e entraînement contre le 1er (capacité + corrections)"),
             ("kubellm-v3", "kubellm-base", "2e entraînement contre le modèle de base"),
             ("kubellm-v3", "groq", "élève v3 contre son enseignant"),
             ("kubellm-v3", "claude", "modèle local v3 contre Claude"),
             ("kubellm-v3-repair", "kubellm-v3", "effet de la réparation déterministe (même modèle)"),
             ("kubellm-v3-repair", "claude", "modèle local + réparation contre Claude"),
             ("groq-repair", "groq", "effet de la réparation sur l'enseignant")]
    pairs = [p for p in pairs if {p[0], p[1]} <= set(conds)]
    if pairs:
        out.append("### Comparaisons appariées des modèles (mêmes demandes, 1re répétition)\n")
        for x, y, label in pairs:
            A = {r["prompt_id"]: r for r in rows if r["condition"] == x and r["rep"] == 1}
            B = {r["prompt_id"]: r for r in rows if r["condition"] == y and r["rep"] == 1}
            for sub_label, sub in (("toutes demandes", None), ("hors capacité", False), ("capacité", True)):
                ks = [k for k in sorted(set(A) & set(B)) if sub is None or A[k]["capacity_stress"] == sub]
                if not ks:
                    continue
                for fld, fl in (("valid_first", "pass@1"), ("valid", "pass@3")):
                    ox = sum(1 for k in ks if A[k][fld] and not B[k][fld])
                    oy = sum(1 for k in ks if B[k][fld] and not A[k][fld])
                    out.append(f"- {x} / {y} — {label} — {fl}, {sub_label} ({len(ks)} paires) : "
                               f"{sum(A[k][fld] for k in ks)} contre {sum(B[k][fld] for k in ks)} ; "
                               f"{x} seul : {ox}, {y} seul : {oy} ; McNemar exact p = {fmt_p(mcnemar(ox, oy))}")
        out.append("")


# ── C2 ───────────────────────────────────────────────────────────────────────

def _rule(change: str) -> str:
    """Famille d'une modification de manifest_repair (pour le tableau du rapport)."""
    for key, label in (("apiVersion", "version d'API"), ("déplacé", "champ déplacé"), ("renommé", "champ renommé"),
                       ("champ inconnu", "champ inconnu supprimé"), ("nom", "nom corrigé"),
                       ("Namespace ajouté", "namespace ajouté"), ("fixé à", "requests/limits ajoutés"),
                       ("un pod demandait", "pod ramené à un nœud"), ("l'ensemble demandait", "total ramené au cluster"),
                       ("réduit de", "répliques réduites")):
        if key in change:
            return label
    return "autre"


def fmt_p(p):
    return "< 0,001" if p < 0.001 else f"{p:.3f}".replace(".", ",")


def report_c2(out):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from campaign_agent import score_strict, is_abstention
    rows = [r for r in load("campaign_agent.jsonl") if r.get("status") == "ok"]
    out.append("## C2 — Agent de dépannage\n")
    if not rows:
        out.append("_Aucune mesure (scripts/campaign_agent.py)._\n")
        return
    manual = {}
    csv_p = os.path.join(OBS, "campaign_agent_review.csv")
    if os.path.exists(csv_p):
        with open(csv_p, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                v = (row.get("note_manuelle") or "").strip().replace(",", ".")
                if v:
                    try:
                        manual[(row.get("banc") or "v1", row["cas"], row["condition"], str(row["rep"]))] = float(v)
                    except ValueError:
                        pass
    # Relecture manuelle livrée à part (non écrasée par campaign_agent.py) :
    # colonnes banc, cas, condition, rep, note (1 / 0.5 / 0 / A = abstention), justification
    man_p = os.path.join(OBS, "campaign_agent_manual.csv")
    if os.path.exists(man_p):
        with open(man_p, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                v = (row.get("note") or "").strip().replace(",", ".")
                if v.upper() == "A":
                    manual[(row["banc"], row["cas"], row["condition"], str(row["rep"]))] = "A"
                elif v:
                    try:
                        manual[(row["banc"], row["cas"], row["condition"], str(row["rep"]))] = float(v)
                    except ValueError:
                        pass
    last = {}
    for r in rows:
        r["bench"] = r.get("bench", "v1")
        last[(r["bench"], r["case"], r["condition"], r["rep"])] = r
    rows = list(last.values())
    for r in rows:
        k = (r["bench"], r["case"], r["condition"], str(r["rep"]))
        r["kw"] = r["auto_score"]
        m = manual.get(k)
        r["abst"] = (m == "A") if k in manual else is_abstention(r["response"])
        r["strict"] = 0.0 if m == "A" else m if k in manual else score_strict(r["case"], r["response"], r["auto_score"])
        r["manual"] = k in manual
    out.append("Question neutre identique pour chaque cas ; la cause n'est jamais donnée. "
               "Note 1 = cause exacte, 0,5 = partielle, 0 = fausse.\n\n"
               "- **mots-clés** : la réponse contient les termes de la cause (une réponse qui "
               "énumère toutes les causes possibles peut les atteindre) ;\n"
               "- **stricte** : en plus, un détail qu'on ne peut connaître qu'en observant le cluster "
               "(tag 9.99, port 8081, limite 48Mi, sélecteur « backnd »…) ; une réponse qui refuse de conclure "
               "(« je ne peux pas me prononcer… ») est une **abstention** et vaut 0. "
               "Note manuelle prioritaire si saisie (campaign_agent_manual.csv).\n")
    out.append("Banc **v1** (invalidé) : noms parlants (agent-oom…) et cause écrite en annotation, lue par l'agent dans 4 réponses sur 10. "
               "Banc **v2** : noms neutres (svc-01…), aucune annotation.\n")
    sym = {1.0: "✅", 0.5: "🟡", 0.0: "❌"}
    for bench in sorted({r["bench"] for r in rows}):
        br = [r for r in rows if r["bench"] == bench]
        conds = sorted({r["condition"] for r in br})
        n_manual = sum(r["manual"] for r in br)
        out.append(f"### Banc {bench} ({n_manual}/{len(br)} notes relues à la main)\n")
        out.append("| Condition | Cause exacte (mots-clés) | Cause exacte (stricte) | Partielle | Fausse | "
                   "Abstention | Témoin sain correct | Outils / question (méd.) | Durée méd. |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for c in conds:
            f = [r for r in br if r["condition"] == c and r["case"] != "healthy"]
            h = [r for r in br if r["condition"] == c and r["case"] == "healthy"]
            out.append(f"| {c} | {rate(sum(r['kw'] == 1 for r in f), len(f))} | "
                       f"{rate(sum(r['strict'] == 1 for r in f), len(f))} | "
                       f"{sum(r['strict'] == 0.5 for r in f)} | "
                       f"{sum(r['strict'] == 0 and not r['abst'] for r in f)} | {sum(r['abst'] for r in f)} | "
                       f"{sum(1 for r in h if r['strict'] == 1)}/{len(h)} | "
                       f"{med([r.get('tools_used') or 0 for r in f])} | {med([r['duration_s'] for r in f])} s |")
        out.append("")
        cases = []
        for r in br:
            if r["case"] not in cases:
                cases.append(r["case"])
        out.append("| Cas | Cause attendue | " + " | ".join(f"{c} (mots-clés → stricte)" for c in conds) + " |")
        out.append("|---|---|" + "---|" * len(conds))
        for cid in cases:
            truth = next(r["truth"] for r in br if r["case"] == cid)
            cells = []
            for c in conds:
                rs = sorted((r for r in br if r["case"] == cid and r["condition"] == c), key=lambda r: r["rep"])
                cells.append(" ".join(f"{sym.get(r['kw'], '?')}→{'⏸' if r['abst'] else sym.get(r['strict'], '?')}"
                                      for r in rs) or "—")
            out.append(f"| {cid} | {truth} | " + " | ".join(cells) + " |")
        out.append("")
        if {"agent", "chat-claude"} <= set(conds):
            a = {(r["case"], r["rep"]): r for r in br if r["condition"] == "agent" and r["case"] != "healthy"}
            b = {(r["case"], r["rep"]): r for r in br if r["condition"] == "chat-claude" and r["case"] != "healthy"}
            ks = sorted(set(a) & set(b))
            for label, fld in (("mots-clés", "kw"), ("stricte", "strict")):
                oa = sum(1 for k in ks if a[k][fld] == 1 and b[k][fld] != 1)
                ob = sum(1 for k in ks if b[k][fld] == 1 and a[k][fld] != 1)
                out.append(f"- Agent / chat-claude, notation {label}, {len(ks)} pannes : cause exacte "
                           f"trouvée par l'agent seul : {oa}, par le chat seul : {ob} ; "
                           f"McNemar exact p = {fmt_p(mcnemar(oa, ob))}")
            out.append("")


# ── OOM ──────────────────────────────────────────────────────────────────────

def report_oom(out, fname="oom_campaign.jsonl"):
    rows = load(fname)
    out.append("## Prédiction d'épuisement mémoire (prédicteur de l'application)\n")
    if not rows:
        out.append("_Aucune mesure (scripts/campaign_oom.py)._\n")
        return
    out.append(f"Limite mémoire {rows[0]['limit_mib']} Mio, relevé toutes les {rows[0]['poll_s']} s. "
               f"Prédiction « exposée » = confiance ≥ {CONF_MIN}. Préavis = instant de l'OOM réel − "
               f"instant de la 1re alerte. Erreur d'échéance = instant prédit − instant réel "
               f"(positif = prédiction trop tardive).\n")
    by_rate = defaultdict(list)
    for r in rows:
        by_rate[r["rate_mib_min"]].append(r)
    out.append("| Fuite (Mio/min) | Manches | OOM observés | Préavis prédicteur (méd.) | Préavis seuil 75 % (méd.) | "
               "Erreur à la 1re alerte (méd.) | Erreur absolue, tous relevés (méd.) | Biais (méd.) |")
    out.append("|---|---|---|---|---|---|---|---|")
    control_lines = []
    for rate_v in sorted(by_rate, reverse=True):
        rs = by_rate[rate_v]
        if rate_v == 0:
            fa = 0
            for r in rs:
                if any(s["pred"] and s["pred"]["confidence"] >= CONF_MIN for s in r["samples"]):
                    fa += 1
            dur = [(r["samples"][-1]["t"] - r["samples"][0]["t"]) / 60 for r in rs if r["samples"]]
            control_lines.append(f"Pod témoin sans fuite : {fa}/{len(rs)} manche(s) avec une fausse alerte "
                                 f"(durée observée médiane {med(dur, '{:.0f}')} min).")
            continue
        leads, leads75, first_err, all_abs, bias = [], [], [], [], []
        ooms = [r for r in rs if r.get("oom_t")]
        for r in ooms:
            T = r["oom_t"]
            exposed = [s for s in r["samples"] if s["pred"] and s["pred"]["confidence"] >= CONF_MIN and s["t"] <= T]
            if exposed:
                leads.append((T - exposed[0]["t"]) / 60)
                first_err.append((exposed[0]["pred"]["target"] - T) / 60)
                errs = [(s["pred"]["target"] - T) / 60 for s in exposed]
                all_abs.extend(abs(e) for e in errs)
                bias.extend(errs)
            lim = r.get("limit_mib") or 0
            over = [s for s in r["samples"] if s["mib"] and lim and s["mib"] >= 0.75 * lim and s["t"] <= T]
            if over:
                leads75.append((T - over[0]["t"]) / 60)
        f = "{:.1f} min"
        out.append(f"| {rate_v:g} | {len(rs)} | {len(ooms)} | {med(leads, f)} | {med(leads75, f)} | "
                   f"{med(first_err, f)} | {med(all_abs, f)} | {med(bias, f)} |")
    out.append("")
    out.extend(control_lines)
    out.append("")


def main():
    out = [f"# Rapport des campagnes de mesure — {time.strftime('%Y-%m-%d %H:%M')}\n",
           "_Généré par scripts/campaign_report.py à partir des journaux de data/observations/._\n"]
    report_c1(out)
    report_c2(out)
    report_oom(out)
    text = "\n".join(out)
    os.makedirs(OBS, exist_ok=True)
    path = os.path.join(OBS, f"RAPPORT_MESURES_{time.strftime('%Y%m%d-%H%M%S')}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"\n→ Rapport écrit : {path}")


if __name__ == "__main__":
    main()

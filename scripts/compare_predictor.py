#!/usr/bin/env python3
"""
scripts/compare_predictor.py — Prédicteur mémoire v1 contre v2, mêmes mesures
================================================================================
Lit, sans les modifier :
  data/observations/oom_false_alarms.jsonl       nuit du 29/10, prédicteur v1
  data/observations/oom_false_alarms_v2.jsonl    même charge, prédicteur v2
  data/observations/oom_campaign.jsonl           campagne du chapitre 8 (v1)
  data/observations/oom_campaign_v2.jsonl        même campagne, prédicteur v2

Écrit : data/observations/compare_predictor_report.md

Usage (terminal Ubuntu WSL, dans ~/aikube) : python3 scripts/compare_predictor.py
================================================================================
"""
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import watch_false_alarms as W   # noqa: E402
import campaign_report as CR     # noqa: E402

OBS = W.OBS
OUT = os.path.join(OBS, "compare_predictor_report.md")


def night(name):
    path = os.path.join(OBS, name + ".jsonl")
    if not os.path.exists(path):
        return None
    W.OUT = path
    d = W.build(W.load())
    eps = d["episodes"]
    fa = [e for e in eps if not e["true"]]
    tr = [e for e in eps if e["true"]]

    def pod(e):
        name = e["key"].split("/", 1)[1]
        for p in ("cache-warm", "gc-noisy", "batch-report", "web-nginx", "leak-control"):
            if name.startswith(p):
                return p
        return e["key"].split("/", 1)[0]
    by = {}
    for e in fa:
        by[pod(e)] = by.get(pod(e), 0) + 1
    med = lambda xs: statistics.median(xs) if xs else None  # noqa: E731
    return {"h": d["valid_h"], "alerts": len(eps), "true": len(tr), "false": len(fa),
            "ooms": len(d["ooms"]), "missed": len(d["missed"]), "unseen": len(d["unseen"]),
            "lead": med([e["lead"] / 60 for e in tr]),
            "err": med([abs(e["err"]) / 60 for e in tr if e.get("err") is not None]),
            "fa_min": sum(e["end"] - e["start"] + d["poll_s"] for e in fa) / 60,
            "by": by}


def fmt(v, f="{:.1f}"):
    return "—" if v is None else f.format(v)


def main():
    out = [f"# Prédicteur mémoire : v1 contre v2 — {time.strftime('%Y-%m-%d %H:%M')}\n"]
    a, b = night("oom_false_alarms"), night("oom_false_alarms_v2")
    out.append("## Charge normale de 8 h (fausses alertes et vraies fuites)\n")
    if not a or not b:
        out.append("_Il manque un des deux journaux (oom_false_alarms.jsonl, oom_false_alarms_v2.jsonl)._\n")
    else:
        rows = [
            ("Heures valides", fmt(a["h"]), fmt(b["h"])),
            ("Crashs réels (OOMKilled)", a["ooms"], b["ooms"]),
            ("dont survenus pendant une coupure (non observés)", a["unseen"], b["unseen"]),
            ("dont annoncés", a["ooms"] - a["missed"] - a["unseen"], b["ooms"] - b["missed"] - b["unseen"]),
            ("dont manqués", a["missed"], b["missed"]),
            ("**Fausses alertes**", f"**{a['false']}**", f"**{b['false']}**"),
            ("Fausses alertes par heure", fmt(a["false"] / a["h"], "{:.2f}") if a["h"] else "—",
             fmt(b["false"] / b["h"], "{:.2f}") if b["h"] else "—"),
            ("Temps total d'affichage des fausses alertes", fmt(a["fa_min"], "{:.0f} min"), fmt(b["fa_min"], "{:.0f} min")),
            ("Part des alertes qui étaient vraies",
             fmt(100 * a["true"] / a["alerts"], "{:.0f} %") if a["alerts"] else "—",
             fmt(100 * b["true"] / b["alerts"], "{:.0f} %") if b["alerts"] else "—"),
            ("Préavis médian (vraies fuites)", fmt(a["lead"], "{:.1f} min"), fmt(b["lead"], "{:.1f} min")),
            ("Erreur médiane d'échéance", fmt(a["err"], "{:.1f} min"), fmt(b["err"], "{:.1f} min")),
        ]
        out.append("| | v1 (29/10) | v2 |\n|---|---|---|")
        out += [f"| {r[0]} | {r[1]} | {r[2]} |" for r in rows]
        pods = sorted(set(a["by"]) | set(b["by"]))
        out.append("\nFausses alertes par pod : " + " · ".join(
            f"{p} {a['by'].get(p, 0)} → {b['by'].get(p, 0)}" for p in pods) + "\n")
    for label, fname in (("v1 (chapitre 8)", "oom_campaign.jsonl"), ("v2", "oom_campaign_v2.jsonl")):
        sec = []
        CR.report_oom(sec, fname)
        sec[0] = f"## Campagne du chapitre 8 — prédicteur {label}\n"
        out += sec
    text = "\n".join(out) + "\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"Rapport écrit : {OUT}")


if __name__ == "__main__":
    main()

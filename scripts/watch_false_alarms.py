#!/usr/bin/env python3
"""
scripts/watch_false_alarms.py — Fausses alertes du prédicteur mémoire en conditions réelles
================================================================================
Mesure complémentaire au chapitre 8. La campagne 8.3 n'avait qu'un pod témoin
calme : elle ne dit pas combien d'alertes « crash mémoire annoncé » restent
sans suite sur un cluster qui vit normalement.

Ce script observe le prédicteur TEL QU'IL TOURNE DANS L'APP (/api/ai/predict/memory,
même seuil de confiance 0,25 que les alertes), sans le modifier, pendant plusieurs
heures, et relève en parallèle les vrais crashs mémoire (OOMKilled) donnés par
Kubernetes, juge indépendant.

DÉFINITIONS (calculées par --report)
  épisode d'alerte   suite de relevés où un pod a une prédiction exposée ;
                     il se termine après 3 relevés consécutifs sans prédiction.
  alerte vraie       un OOMKilled du même pod survient entre le début de l'épisode
                     et 5 min après sa fin.
  fausse alerte      épisode sans OOMKilled dans cette fenêtre.
  OOM manqué         OOMKilled qu'aucun épisode n'a précédé.
  heures valides     relevés où l'API répond et où la mesure fonctionne
                     (ni erreur de metrics-server, ni préchauffage).

FICHIERS (nouveaux, rien d'existant n'est modifié)
  data/observations/oom_false_alarms.jsonl        journal des relevés
  data/observations/oom_false_alarms_report.md    rapport (--report)

USAGE (terminal Ubuntu WSL, dans ~/aikube, backend lancé)
  kubectl apply -f k8s/benchmark/false-alarm-workload.yaml
  python3 scripts/watch_false_alarms.py --hours 10      # Ctrl+C possible à tout moment
  python3 scripts/watch_false_alarms.py --report
================================================================================
"""
import argparse
import calendar
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OBS = os.path.join(ROOT, "data", "observations")
OUT = os.path.join(OBS, "oom_false_alarms.jsonl")
REPORT = os.path.join(OBS, "oom_false_alarms_report.md")

GAP_POLLS = 3          # relevés sans prédiction qui clôturent un épisode
GRACE_S = 300          # un OOM jusqu'à 5 min après la fin de l'épisode le valide
YOUNG_S = 600          # conteneur « jeune » : démarré depuis moins de 10 min


# ── Relevé ───────────────────────────────────────────────────────────────────

def iso_to_epoch(s):
    if not s:
        return None
    return calendar.timegm(time.strptime(s.replace("Z", "")[:19], "%Y-%m-%dT%H:%M:%S"))


def kubectl_json(*args, timeout=40):
    try:
        r = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "kubectl : délai dépassé"
    if r.returncode != 0:
        return None, (r.stderr or "kubectl a échoué").strip()[:200]
    return json.loads(r.stdout), None


def pods_snapshot(exclude):
    """Pods ayant une limite mémoire, âge du dernier conteneur démarré, OOMKilled connus."""
    data, err = kubectl_json("get", "pods", "-A", timeout=40)
    if data is None:
        return None, err
    eligible, ages, ooms, groups = [], {}, [], {}
    now = time.time()
    for it in data.get("items", []):
        meta, spec, st = it["metadata"], it.get("spec", {}), it.get("status", {})
        ns = meta.get("namespace")
        if ns in exclude:
            continue
        key = f"{ns}/{meta.get('name')}"
        labels = meta.get("labels") or {}
        groups[key] = labels.get("aikube.fa/expect", "")
        if any((c.get("resources", {}).get("limits") or {}).get("memory")
               for c in spec.get("containers", []) or []):
            eligible.append(key)
        started = []
        for cs in st.get("containerStatuses", []) or []:
            run = (cs.get("state") or {}).get("running") or {}
            if run.get("startedAt"):
                started.append(iso_to_epoch(run["startedAt"]))
            for term in ((cs.get("state") or {}).get("terminated"),
                         (cs.get("lastState") or {}).get("terminated")):
                if term and term.get("reason") == "OOMKilled" and term.get("finishedAt"):
                    ooms.append({"key": key, "t": iso_to_epoch(term["finishedAt"])})
        if started:
            ages[key] = round(now - max(started))
    return {"eligible": eligible, "ages": ages, "ooms": ooms, "groups": groups}, None


def get_predictions(api):
    with urllib.request.urlopen(api + "/api/ai/predict/memory", timeout=30) as r:
        return json.loads(r.read())


def run(a):
    os.makedirs(OBS, exist_ok=True)
    exclude = {x.strip() for x in a.exclude.split(",") if x.strip()}
    end = time.time() + a.hours * 3600
    seen_ooms = set()
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "start", "t": time.time(), "hours": a.hours,
                            "poll": a.poll, "exclude": sorted(exclude), "api": a.api}) + "\n")
        f.flush()
        print(f"Observation pendant {a.hours} h, un relevé toutes les {a.poll} s.")
        print(f"Journal : {OUT}   (Ctrl+C pour arrêter, rien n'est perdu)\n")
        n = 0
        while time.time() < end:
            t0 = time.time()
            rec = {"type": "poll", "t": round(t0, 1)}
            try:
                p = get_predictions(a.api)
                rec["api_ok"] = True
                rec["warmup"] = bool(p.get("warmup"))
                rec["error"] = p.get("error")
                rec["tracked"] = p.get("pods_tracked")
                rec["version"] = p.get("predictor_version", "v1")
                rec["preds"] = [{"key": f"{x['namespace']}/{x['pod']}",
                                 "target": x.get("target_timestamp"),
                                 "eta": x.get("eta_seconds"),
                                 "conf": x.get("confidence"),
                                 "usage": x.get("usage_percent"),
                                 "points": x.get("points_used")}
                                for x in p.get("predictions", [])
                                if x.get("namespace") not in exclude]
            except Exception as e:  # noqa: BLE001 — le backend peut être indisponible un moment
                rec.update({"api_ok": False, "error": str(e)[:200], "preds": []})
            snap, err = pods_snapshot(exclude)
            if snap is None:
                rec["kube_error"] = err
            else:
                rec["eligible"] = len(snap["eligible"])
                keys = {x["key"] for x in rec["preds"]}
                rec["ages"] = {k: v for k, v in snap["ages"].items() if k in keys}
                rec["groups"] = {k: v for k, v in snap["groups"].items() if k in keys and v}
                new = [o for o in snap["ooms"] if (o["key"], o["t"]) not in seen_ooms]
                for o in new:
                    seen_ooms.add((o["key"], o["t"]))
                    o["group"] = snap["groups"].get(o["key"], "")
                rec["ooms"] = new
            f.write(json.dumps(rec) + "\n")
            f.flush()
            n += 1
            if n % 20 == 1 or rec.get("ooms") or not rec.get("api_ok"):
                print(time.strftime("%H:%M:%S"), f"relevé {n}",
                      f"· {len(rec['preds'])} alerte(s)",
                      f"· {len(rec.get('ooms') or [])} nouvel(s) OOM",
                      "" if rec.get("api_ok") else f"· API indisponible ({rec.get('error')})")
            time.sleep(max(1.0, a.poll - (time.time() - t0)))
    print("\nTerminé. Rapport : python3 scripts/watch_false_alarms.py --report")


# ── Rapport ──────────────────────────────────────────────────────────────────

def load():
    if not os.path.exists(OUT):
        sys.exit(f"Aucun journal : {OUT}")
    rows = []
    for line in open(OUT, encoding="utf-8"):
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def build(rows):
    polls = [r for r in rows if r.get("type") == "poll"]
    polls.sort(key=lambda r: r["t"])
    poll_s = next((r["poll"] for r in rows if r.get("type") == "start"), 30)

    def valid(r):
        return r.get("api_ok") and not r.get("error") and not r.get("warmup")

    # heures valides : intervalle jusqu'au relevé suivant, plafonné à 2 relevés
    valid_s = 0.0
    for a, b in zip(polls, polls[1:] + [None]):
        if valid(a):
            valid_s += min((b["t"] - a["t"]) if b else poll_s, 2 * poll_s)

    # Un OOM antérieur au premier relevé (resté dans lastState) n'appartient pas à la mesure
    t_first = polls[0]["t"] if polls else 0
    t_last = polls[-1]["t"] if polls else 0
    ooms, seen = [], set()
    for r in polls:
        for o in r.get("ooms") or []:
            if o["t"] < t_first - poll_s:
                continue
            if (o["key"], o["t"]) not in seen:
                seen.add((o["key"], o["t"]))
                ooms.append(o)

    # Coupures d'observation : relevés absents (script ou PC arrêté) ou invalides
    # (API ou metrics-server en panne) pendant au moins GAP_POLLS relevés.
    # Pendant une coupure on ne sait rien : les alertes ouvertes sont closes
    # « coupées », et un OOM survenu dans la coupure est « non observé ».
    breaks, last_valid = [], None
    for r in polls:
        if valid(r):
            if last_valid is not None and (r["t"] - last_valid["t"] > GAP_POLLS * poll_s):
                breaks.append((last_valid["t"], r["t"]))
            last_valid = r
    in_break = lambda t: any(a < t < b for a, b in breaks)  # noqa: E731

    # épisodes : un pod reste « en alerte » tant qu'il n'a pas manqué GAP_POLLS relevés valides
    open_ep, episodes, missing = {}, [], defaultdict(int)
    groups = {}
    prev_t = None
    for r in polls:
        if not valid(r):
            continue
        if prev_t is not None and r["t"] - prev_t > GAP_POLLS * poll_s:
            for e in open_ep.values():
                e["cut"] = True
            episodes.extend(open_ep.values())
            open_ep, missing = {}, defaultdict(int)
        prev_t = r["t"]
        groups.update(r.get("groups") or {})
        here = {p["key"]: p for p in r.get("preds") or []}
        for k, p in here.items():
            missing[k] = 0
            if k not in open_ep:
                open_ep[k] = {"key": k, "start": r["t"], "end": r["t"], "polls": 1,
                              "target0": p.get("target"), "eta0": p.get("eta"),
                              "conf0": p.get("conf"), "conf_max": p.get("conf") or 0,
                              "points0": p.get("points"),
                              "age0": (r.get("ages") or {}).get(k)}
            else:
                e = open_ep[k]
                e["end"], e["polls"] = r["t"], e["polls"] + 1
                e["conf_max"] = max(e["conf_max"], p.get("conf") or 0)
        for k in list(open_ep):
            if k not in here:
                missing[k] += 1
                if missing[k] >= GAP_POLLS:
                    episodes.append(open_ep.pop(k))
    episodes.extend(open_ep.values())

    used = set()
    for e in sorted(episodes, key=lambda e: e["start"]):
        e["group"] = groups.get(e["key"], "")
        match = [o for o in ooms if o["key"] == e["key"] and (o["key"], o["t"]) not in used
                 and e["start"] <= o["t"] <= e["end"] + GRACE_S]
        if match:
            o = min(match, key=lambda x: x["t"])
            used.add((o["key"], o["t"]))
            e["true"] = True
            e["lead"] = o["t"] - e["start"]
            e["err"] = (e["target0"] - o["t"]) if e.get("target0") else None
        else:
            e["true"] = False
    # Alerte encore ouverte, dont l'échéance n'était pas atteinte à la fin de
    # l'observation, ou interrompue par une coupure : on ne peut pas savoir si le
    # crash serait venu. Exclue du décompte.
    censored = [e for e in episodes if not e["true"] and
                (e.get("cut") or e["end"] >= t_last - GAP_POLLS * poll_s or
                 (e.get("target0") or 0) + GRACE_S > t_last)]
    episodes = [e for e in episodes if e not in censored]
    unseen = [o for o in ooms if (o["key"], o["t"]) not in used and in_break(o["t"])]
    missed = [o for o in ooms if (o["key"], o["t"]) not in used and not in_break(o["t"])]
    return {"polls": polls, "valid_h": valid_s / 3600, "episodes": episodes, "censored": censored,
            "ooms": ooms, "missed": missed, "unseen": unseen, "breaks": breaks, "poll_s": poll_s}


def fam(key, group):
    ns = key.split("/", 1)[0]
    if group == "oom":
        return "témoin qui fuit (leak-control)"
    if ns == "aikube-fa":
        return "charge normale (aikube-fa)"
    if ns == "kube-system":
        return "système (kube-system)"
    return "autres namespaces"


def med(xs, fmt=lambda v: f"{v / 60:.1f} min"):
    xs = [x for x in xs if x is not None]
    return fmt(statistics.median(xs)) if xs else "—"


def report(_a):
    d = build(load())
    eps, ooms, missed = d["episodes"], d["ooms"], d["missed"]
    elig = [r.get("eligible") for r in d["polls"] if r.get("eligible") is not None]
    n_elig = round(statistics.median(elig)) if elig else 0
    span_h = (d["polls"][-1]["t"] - d["polls"][0]["t"]) / 3600 if d["polls"] else 0

    out = ["# Fausses alertes du prédicteur mémoire en conditions réelles\n",
           f"_Généré le {time.strftime('%Y-%m-%d %H:%M')} depuis `{os.path.relpath(OUT, ROOT)}`._\n",
           f"- Durée d'observation : **{span_h:.1f} h**, dont **{d['valid_h']:.1f} h valides** "
           f"(API joignable, metrics-server fonctionnel). Relevé toutes les {d['poll_s']} s.",
           f"- Pods surveillables (avec limite mémoire) : **{n_elig}** en médiane.",
           f"- Version du prédicteur : **{', '.join(sorted({r.get('version') or 'v1' for r in d['polls'] if r.get('api_ok')})) or '—'}**.",
           f"- Alerte = prédiction exposée par l'application (confiance ≥ 0,25), comme dans la cloche.",
           f"- Vraie alerte : OOMKilled du même pod entre le début de l'alerte et {GRACE_S // 60} min après sa fin.\n"]

    fa = [e for e in eps if not e["true"]]
    tr = [e for e in eps if e["true"]]
    out.append("## Synthèse\n")
    out.append("| | Nombre |\n|---|---|")
    out.append(f"| Épisodes d'alerte | {len(eps)} |")
    out.append(f"| dont vraies alertes | {len(tr)} |")
    out.append(f"| dont **fausses alertes** | **{len(fa)}** |")
    out.append(f"| Alertes non conclues à la fin de l'observation (exclues) | {len(d['censored'])} |")
    unseen = d["unseen"]
    out.append(f"| Coupures d'observation (script, API ou mesures arrêtés) | {len(d['breaks'])}, "
               f"{sum(b - a for a, b in d['breaks']) / 60:.0f} min au total |")
    out.append(f"| Crashs mémoire réels (OOMKilled) | {len(ooms)} |")
    out.append(f"| dont survenus pendant une coupure (non observés) | {len(unseen)} |")
    out.append(f"| dont annoncés | {len(ooms) - len(missed) - len(unseen)} |")
    out.append(f"| dont **manqués** | **{len(missed)}** |")
    if d["valid_h"] > 0:
        out.append(f"| Fausses alertes par heure valide | {len(fa) / d['valid_h']:.2f} |")
        if n_elig:
            out.append(f"| Fausses alertes par pod surveillable et par jour | "
                       f"{len(fa) / (d['valid_h'] * n_elig) * 24:.2f} |")
    out.append("")

    out.append("## Par famille de pods\n")
    out.append("| Famille | Alertes | Vraies | Fausses | OOM réels | OOM manqués | Préavis médian | Erreur médiane d'échéance |")
    out.append("|---|---|---|---|---|---|---|---|")
    fams = sorted({fam(e["key"], e["group"]) for e in eps} | {fam(o["key"], o.get("group", "")) for o in ooms})
    for f_ in fams:
        fe = [e for e in eps if fam(e["key"], e["group"]) == f_]
        fo = [o for o in ooms if fam(o["key"], o.get("group", "")) == f_]
        fm = [o for o in missed if fam(o["key"], o.get("group", "")) == f_]
        ft = [e for e in fe if e["true"]]
        out.append(f"| {f_} | {len(fe)} | {len(ft)} | {len(fe) - len(ft)} | {len(fo)} | {len(fm)} | "
                   f"{med([e['lead'] for e in ft])} | "
                   f"{med([abs(e['err']) for e in ft if e.get('err') is not None])} |")
    out.append("")

    out.append("## Les fausses alertes, en détail\n")
    if not fa:
        out.append("_Aucune._\n")
    else:
        young = [e for e in fa if e.get("age0") is not None and e["age0"] < YOUNG_S]
        out.append(f"- Survenues sur un conteneur démarré depuis **moins de {YOUNG_S // 60} min** : "
                   f"**{len(young)} sur {len(fa)}** (montée de mémoire au démarrage).")
        out.append(f"- Durée médiane d'une fausse alerte : "
                   f"{med([e['end'] - e['start'] + d['poll_s'] for e in fa])}.")
        out.append(f"- Confiance au déclenchement (médiane) : "
                   f"{med([e['conf0'] for e in fa], lambda v: f'{v:.2f}')} ; "
                   f"points utilisés (médiane) : {med([e['points0'] for e in fa], lambda v: f'{v:g}')}.\n")
        out.append("| Pod | Début | Durée | Âge du conteneur | Échéance annoncée | Confiance | Points |")
        out.append("|---|---|---|---|---|---|---|")
        for e in sorted(fa, key=lambda e: e["start"]):
            age = f"{e['age0'] / 60:.0f} min" if e.get("age0") is not None else "—"
            out.append(f"| `{e['key']}` | {time.strftime('%d/%m %H:%M', time.localtime(e['start']))} | "
                       f"{(e['end'] - e['start'] + d['poll_s']) / 60:.1f} min | {age} | "
                       f"{(e['eta0'] or 0) / 60:.1f} min | {e['conf0']} | {e['points0']} |")
        out.append("")

    if missed:
        out.append("## Crashs non annoncés\n")
        for o in missed:
            out.append(f"- `{o['key']}` à {time.strftime('%d/%m %H:%M:%S', time.localtime(o['t']))}")
        out.append("")

    out.append("## Lecture\n")
    out.append("- Les pods de `aikube-fa` marqués `no-oom` ne doivent jamais crasher : toute alerte "
               "sur eux est une fausse alerte par construction.")
    out.append("- `leak-control` fuit à 3 Mio/min et crashe environ toutes les 40 min : il vérifie, "
               "dans les mêmes conditions, que les vraies fuites restent annoncées.")
    out.append("- Les pods système (`kube-system`) sont de vrais composants, non modifiés.")
    text = "\n".join(out) + "\n"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"Rapport écrit : {REPORT}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--hours", type=float, default=10)
    ap.add_argument("--poll", type=int, default=30, help="secondes entre deux relevés")
    ap.add_argument("--exclude", default="aikube-oom",
                    help="namespaces ignorés (bancs de fuites), séparés par des virgules")
    ap.add_argument("--report", action="store_true", help="calcule le rapport depuis le journal")
    ap.add_argument("--name", default="oom_false_alarms",
                    help="nom du journal dans data/observations/ (ex. oom_false_alarms_v2) ; "
                         "le rapport prend le même nom suivi de _report.md")
    a = ap.parse_args()
    global OUT, REPORT
    OUT = os.path.join(OBS, a.name + ".jsonl")
    REPORT = os.path.join(OBS, a.name + "_report.md")
    report(a) if a.report else run(a)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scripts/campaign_oom.py — Campagne de mesure de la prédiction OOM de l'application
================================================================================
Mesure le prédicteur TEL QU'IL TOURNE DANS L'APP (/api/ai/predict/memory), et non
une réimplémentation. Fichier de sortie NOUVEAU : data/observations/oom_campaign.jsonl
(scripts/oom_prediction.py et oom_prediction.jsonl ne sont pas touchés).

PROTOCOLE
  Une « manche » lance en parallèle, dans le namespace aikube-oom, un pod par
  vitesse de fuite (Mio/min) + un pod TÉMOIN sans fuite. Tous ont la même limite
  mémoire (192Mi) et restartPolicy: Never. Toutes les 15 s, le script relève la
  prédiction de l'app pour chaque pod. La manche se termine quand chaque pod qui
  fuit a été tué (OOMKilled) ou au délai maximal. On répète N manches.

MÉTRIQUES (calculées par campaign_report.py)
  préavis           temps entre la 1re prédiction exposée (confiance ≥ 0,25) et l'OOM réel
  préavis seuil 75% même chose pour l'alerte classique « mémoire > 75 % de la limite »
  erreur d'échéance |instant prédit − instant réel de l'OOM|, à chaque relevé
  fausse alerte     prédiction exposée sur le pod témoin (qui ne fuit pas)

USAGE (dans ~/aikube, backend lancé depuis au moins 2 minutes)
  python3 scripts/campaign_oom.py                       # 3 manches, fuites 6/12/24 Mio/min
  python3 scripts/campaign_oom.py --reps 1 --rates 24   # essai rapide (~10 min)
================================================================================
"""
import argparse
import calendar
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "data", "observations", "oom_campaign.jsonl")
NS = "aikube-oom"
LIMIT_MI = 192
STEP_S = 10          # la fuite progresse par paliers de 10 s

POD_TMPL = """apiVersion: v1
kind: Pod
metadata:
  name: {name}
  namespace: {ns}
  labels: {{app: oom-campaign, rate: "{rate}"}}
spec:
  restartPolicy: Never
  terminationGracePeriodSeconds: 0
  containers:
    - name: app
      image: python:3.12-alpine
      command:
        - python
        - -c
        - |
          import time
          step = int({rate} * 1024 * 1024 * {step} / 60)
          chunks = []
          while True:
              if step:
                  chunks.append(b"\\x01" * step)
              time.sleep({step})
      resources:
        requests: {{cpu: 20m, memory: 48Mi}}
        limits: {{memory: {limit}Mi}}
"""


def kubectl(*args, stdin=None, timeout=60):
    try:
        return subprocess.run(["kubectl", *args], input=stdin, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", "timeout")


def get(api, path, timeout=30):
    with urllib.request.urlopen(api + path, timeout=timeout) as r:
        return json.loads(r.read())


def iso_to_epoch(s):
    return calendar.timegm(time.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S"))


def pod_status(name):
    """(phase, raison d'arrêt, instant d'arrêt epoch, instant de démarrage epoch)."""
    r = kubectl("get", "pod", name, "-n", NS, "-o", "json", timeout=30)
    if r.returncode != 0:
        return None
    d = json.loads(r.stdout)
    st = d.get("status", {})
    cs = (st.get("containerStatuses") or [{}])[0]
    term = (cs.get("state") or {}).get("terminated") or (cs.get("lastState") or {}).get("terminated")
    running = (cs.get("state") or {}).get("running") or {}
    started = running.get("startedAt") or (term or {}).get("startedAt")
    return {"phase": st.get("phase"),
            "reason": (term or {}).get("reason"),
            "finished": iso_to_epoch(term["finishedAt"]) if term and term.get("finishedAt") else None,
            "started": iso_to_epoch(started) if started else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--rates", default="6,12,24", help="vitesses de fuite en Mio/min")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--poll", type=int, default=15)
    ap.add_argument("--max-minutes", type=int, default=45)
    ap.add_argument("--no-control", action="store_true", help="sans pod témoin")
    ap.add_argument("--out", default=None,
                    help="fichier de résultats (nom seul = dans data/observations/) ; "
                         "par défaut oom_campaign.jsonl")
    a = ap.parse_args()
    global OUT
    if a.out:
        OUT = a.out if os.path.dirname(a.out) else os.path.join(ROOT, "data", "observations", a.out)

    try:
        get(a.api, "/api/ai/predict/memory")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Backend injoignable sur {a.api} : {e}")
    rates = [float(x) for x in a.rates.split(",") if x.strip()]
    if not a.no_control:
        rates.append(0.0)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    run_tag = time.strftime("%Y%m%d-%H%M%S")
    print(f"Campagne OOM {run_tag} : fuites {a.rates} Mio/min (+ témoin), {a.reps} manche(s)")
    print(f"Résultats : {OUT}\n")

    kubectl("create", "namespace", NS)
    for rep in range(1, a.reps + 1):
        pods = {}
        for rate in rates:
            name = f"oom-r{str(rate).replace('.0', '').replace('.', 'p')}-m{rep}-{run_tag[-6:]}"
            r = kubectl("apply", "-f", "-", stdin=POD_TMPL.format(
                name=name, ns=NS, rate=rate, step=STEP_S, limit=LIMIT_MI))
            if r.returncode != 0:
                print(f"⚠ création de {name} impossible : {r.stderr.strip()[:200]}")
                continue
            pods[name] = {"rate": rate, "samples": [], "oom_t": None, "end_reason": None,
                          "created": time.time()}
        print(f"── Manche {rep}/{a.reps} : {len(pods)} pods créés")
        t_end = time.time() + a.max_minutes * 60
        while time.time() < t_end:
            leaking_alive = 0
            for name, p in pods.items():
                if p["end_reason"]:
                    continue
                now = time.time()
                try:
                    h = get(a.api, f"/api/ai/predict/memory/{NS}/{name}")
                except Exception as e:  # noqa: BLE001
                    h = {"error": str(e)[:120]}
                pts = h.get("points") or []
                pred = h.get("prediction")
                p["samples"].append({
                    "t": round(now, 1),
                    "mib": pts[-1]["mib"] if pts else None,
                    "limit_mib": h.get("limit_mib"),
                    "n_points": len(pts),
                    "pred": None if not pred else {
                        "target": round(pred["target_timestamp"], 1),
                        "eta_s": pred["eta_seconds"], "eta_robust_s": pred.get("eta_seconds_robust"),
                        "confidence": pred["confidence"], "slope": pred["slope_mib_per_min"],
                        "usage_pct": pred["usage_percent"]},
                })
                st = pod_status(name)
                if st and st["reason"]:
                    p["end_reason"] = st["reason"]
                    p["oom_t"] = st["finished"] if st["reason"] == "OOMKilled" else None
                    p["started"] = st["started"]
                    print(f"   {name} : {st['reason']} après "
                          f"{round(((st['finished'] or now) - (st['started'] or p['created'])) / 60, 1)} min")
                elif st and p.get("started") is None:
                    p["started"] = st["started"]
                if not p["end_reason"] and p["rate"] > 0:
                    leaking_alive += 1
            if leaking_alive == 0:
                break
            time.sleep(a.poll)
        for name, p in pods.items():
            if not p["end_reason"]:
                p["end_reason"] = "délai dépassé" if p["rate"] > 0 else "témoin (arrêté)"
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "run": run_tag, "rep": rep,
                   "pod": name, "rate_mib_min": p["rate"], "limit_mib": LIMIT_MI,
                   "poll_s": a.poll, "started": p.get("started"), "oom_t": p["oom_t"],
                   "end_reason": p["end_reason"], "samples": p["samples"]}
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            kubectl("delete", "pod", name, "-n", NS, "--wait=false")
        print(f"   manche {rep} enregistrée\n")
        if rep < a.reps:
            time.sleep(60)   # laisse metrics-server oublier les anciens pods
    print("Terminé. Nettoyage : kubectl delete namespace aikube-oom")
    print("Rapport : python3 scripts/campaign_report.py")


if __name__ == "__main__":
    main()

"""
Santé des sources de mesure de la plateforme.

Résultat du 26/10 : metrics-server était hors service (API de métriques sans point
de service joignable après une indisponibilité de l'apiserver). Le prédicteur OOM
et les recommandations restaient muets SANS LE DIRE : une absence d'alerte n'était
pas une absence de risque. Ce module rend la panne visible dans toute l'application.
"""
from __future__ import annotations

import json
import subprocess
import time

_cache: dict = {"t": 0.0, "data": None}


def check(force: bool = False) -> dict:
    if not force and _cache["data"] and time.time() - _cache["t"] < 30:
        return _cache["data"]
    metrics = {"available": None, "reason": ""}
    try:
        r = subprocess.run(["kubectl", "get", "apiservice", "v1beta1.metrics.k8s.io", "-o", "json"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            metrics = {"available": False,
                       "reason": "metrics-server n'est pas installé" if "NotFound" in r.stderr
                       else f"API Kubernetes injoignable : {r.stderr.strip()[:150]}"}
        else:
            cond = next((c for c in json.loads(r.stdout).get("status", {}).get("conditions", [])
                         if c.get("type") == "Available"), {})
            ok = cond.get("status") == "True"
            metrics = {"available": ok,
                       "reason": "" if ok else f"{cond.get('reason', '?')} : {cond.get('message', '')[:200]}"}
    except (OSError, subprocess.TimeoutExpired) as e:
        metrics = {"available": False, "reason": f"vérification impossible : {e}"}
    try:
        from ai_module import oom_predictor, rightsizing
        pred_err, rs_err = oom_predictor._last_error, rightsizing._last_error
    except Exception:  # noqa: BLE001
        pred_err = rs_err = None
    data = {"metrics_server": metrics, "oom_predictor_error": pred_err, "rightsizing_error": rs_err,
            "degraded": metrics["available"] is False or bool(pred_err),
            "impact": ("Prédictions OOM et recommandations de dimensionnement suspendues : "
                       "l'absence d'alerte ne signifie PAS l'absence de risque.")
            if (metrics["available"] is False or pred_err) else "",
            "fix": "kubectl -n kube-system rollout restart deployment metrics-server "
                   "(ou : minikube addons enable metrics-server)" if metrics["available"] is False else "",
            "checked_at": time.time()}
    _cache.update(t=time.time(), data=data)
    return data

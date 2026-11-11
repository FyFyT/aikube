"""
backend/ai_module/oom_predictor.py
================================================================================
Prédiction d'épuisement mémoire en temps réel.

CE QUE CE MODULE ANNONCE, ET CE QU'IL N'ANNONCE PAS
---------------------------------------------------
Il produit une ÉCHÉANCE : « ce conteneur atteindra sa limite mémoire dans
N secondes », assortie d'un INDICE DE CONFIANCE portant sur la qualité de
l'extrapolation.

Cet indice n'est PAS une probabilité de défaillance. Annoncer « 80 % de risque
de crash » supposerait une base fréquentielle — sur N pods observés dans cet
état, X ont effectivement défailli — dont on ne dispose pas. L'indice mesure la
fiabilité de l'ajustement, non la vraisemblance de l'événement.

Trois composantes, toutes calculables :

  R² de la régression        Qualité de l'ajustement linéaire. Une trajectoire
                             en dents de scie ou par paliers produit un R²
                             faible : l'extrapolation y est peu fiable.

  Accord entre estimateurs   Les moindres carrés et l'estimateur de Theil-Sen
                             sont sensibles à des défauts différents. Lorsque
                             leurs échéances convergent, la pente est robuste ;
                             lorsqu'elles divergent, la trajectoire est
                             irrégulière.

  Stabilité temporelle       Les prédictions successives désignent-elles le
                             même instant ? Une date d'échéance qui varie à
                             chaque rafraîchissement signale une extrapolation
                             instable.

              confiance = R²  ×  accord  ×  stabilité

Le produit est délibérément sévère : une seule composante faible suffit à
abaisser l'indice, ce qui est le comportement souhaité pour un signal
opérationnel.

COLLECTE
--------
`metrics-server` n'actualise ses agrégats qu'environ toutes les 60 secondes.
Un fil d'exécution dédié échantillonne toutes les 30 secondes et conserve un
historique glissant en mémoire — l'application ne dispose pas de base de séries
temporelles. Cet historique est donc perdu au redémarrage du serveur, limite
assumée pour un outil d'observation, non de supervision.

LIMITE DE VALIDITÉ
------------------
La méthode s'applique aux ressources bornées dont la consommation croît de
manière monotone. Elle ne prédit pas les défaillances sans grandeur cumulative :
erreurs applicatives, pannes réseau, défauts de configuration.
================================================================================
"""

import re
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

SAMPLE_INTERVAL   = 15      # metrics-server rafraichit a ~60 s ; on
                            # echantillonne plus souvent pour capter
                            # chaque changement des sa parution
HISTORY_SIZE      = 40      # points conservés par conteneur (≈ 20 minutes)
WINDOW            = 8       # points utilisés pour l'estimation de pente
# ── Version v2 (30/10/2026) ────────────────────────────────────────────────
# La mesure de 8 h sur une charge « normale » (scripts/watch_false_alarms.py,
# data/observations/oom_false_alarms.jsonl) a donné 11 OOM annoncés sur 11, mais
# 35 fausses alertes. Deux causes, deux corrections :
#   1. plateau non reconnu : une mesure identique à la précédente est ignorée, donc
#      une mémoire qui cesse de croître n'apporte plus aucun point et la dernière
#      prédiction restait affichée indéfiniment (cache-warm : 7 alertes de 68 min) ;
#      -> au-delà de PLATEAU_S sans nouvelle valeur, la prédiction est retirée ;
#   2. décisions sur 3 points d'une mémoire bruitée (gc-noisy : 28 alertes)
#      -> au moins MIN_POINTS = 5 points, et un ajustement linéaire R² >= MIN_R2.
PREDICTOR_VERSION = "v2"
MIN_POINTS        = 5       # minimum pour estimer une pente (v1 : 3)
MIN_R2            = 0.8     # trajectoire assez linéaire pour être extrapolée (v1 : aucun)
PLATEAU_S         = 180     # sans nouvelle valeur depuis 3 min = plateau (v1 : aucun)
MIN_SLOPE_MIB_S   = 0.01    # pente en deçà de laquelle on considère un plateau
MAX_HORIZON_S     = 7200    # au-delà de 2 h, la prédiction n'est pas retenue
MIN_CONFIDENCE    = 0.25    # en deçà, la prédiction n'est pas exposée
RESTART_DROP_RATIO = 0.5    # chute relative signalant un redémarrage de conteneur
MAX_GAP_SECONDS    = 180    # au-delà, la continuité de la série est rompue

# Persistance de l'historique.
# La série vit en mémoire, faute de base de séries temporelles. En
# développement, uvicorn --reload redémarre le processus à chaque modification
# de fichier et efface donc l'historique accumulé. Une sauvegarde sur disque
# permet à la série de survivre à ces redémarrages, ce qui est indispensable
# pour observer une fuite lente.
import json as _json
import os as _os

_STATE_DIR  = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                            "..", "..", "data", "observations")
_STATE_PATH = _os.path.abspath(_os.path.join(_STATE_DIR, "oom_history.json"))

_state_lock = threading.Lock()
_history: Dict[str, deque] = {}          # "ns/pod" -> deque[(t, mib)]
_limits: Dict[str, float] = {}           # "ns/pod" -> limite en Mio
_predictions: Dict[str, dict] = {}       # dernière prédiction par pod
_collector_started = False
_last_error: Optional[str] = None
_started_at = time.time()


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSIONS
# ══════════════════════════════════════════════════════════════════════════════

def parse_mem(value) -> Optional[float]:
    """Quantité mémoire Kubernetes vers mébioctets."""
    if not value:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([KMGTkmgt]i?)?$", str(value).strip())
    if not m:
        return None
    n = float(m.group(1))
    unit = (m.group(2) or "").lower()
    factors = {"": 1 / (1024 ** 2), "ki": 1 / 1024, "mi": 1.0, "gi": 1024.0,
               "ti": 1024.0 ** 2, "k": 1 / 1024, "m": 1.0, "g": 1024.0}
    return n * factors.get(unit, 1.0)


# ══════════════════════════════════════════════════════════════════════════════
# ESTIMATEURS DE PENTE
# ══════════════════════════════════════════════════════════════════════════════

def slope_least_squares(points: List[Tuple[float, float]]) -> Optional[float]:
    n = len(points)
    if n < 2:
        return None
    mt = sum(p[0] for p in points) / n
    mv = sum(p[1] for p in points) / n
    den = sum((p[0] - mt) ** 2 for p in points)
    if den == 0:
        return None
    return sum((p[0] - mt) * (p[1] - mv) for p in points) / den


def slope_theil_sen(points: List[Tuple[float, float]]) -> Optional[float]:
    """Médiane des pentes deux à deux : insensible aux paliers isolés."""
    slopes = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            dt = points[j][0] - points[i][0]
            if dt > 0:
                slopes.append((points[j][1] - points[i][1]) / dt)
    if not slopes:
        return None
    slopes.sort()
    n = len(slopes)
    return slopes[n // 2] if n % 2 else (slopes[n // 2 - 1] + slopes[n // 2]) / 2


def r_squared(points: List[Tuple[float, float]], slope: float) -> float:
    """Coefficient de détermination de l'ajustement linéaire."""
    n = len(points)
    if n < 3:
        return 0.0
    mt = sum(p[0] for p in points) / n
    mv = sum(p[1] for p in points) / n
    intercept = mv - slope * mt
    ss_res = sum((v - (slope * t + intercept)) ** 2 for t, v in points)
    ss_tot = sum((v - mv) ** 2 for _, v in points)
    if ss_tot == 0:
        return 0.0
    return max(0.0, min(1.0, 1 - ss_res / ss_tot))


# ══════════════════════════════════════════════════════════════════════════════
# PRÉDICTION
# ══════════════════════════════════════════════════════════════════════════════

def compute_prediction(key: str, history: deque, limit_mib: float,
                       previous: Optional[dict]) -> Optional[dict]:
    """
    Retourne la prédiction pour un conteneur, ou None si aucune n'est émise.

    `previous` sert au calcul de la stabilité temporelle : on compare l'instant
    d'échéance annoncé maintenant à celui annoncé au relevé précédent.
    """
    points = list(history)
    if len(points) < MIN_POINTS or not limit_mib:
        return None

    window = points[-WINDOW:]
    now_t, current = window[-1]

    slope_ls = slope_least_squares(window)
    slope_ts = slope_theil_sen(window)
    if slope_ls is None or slope_ls < MIN_SLOPE_MIB_S:
        return None                      # plateau, décroissance ou données plates

    remaining = limit_mib - current
    if remaining <= 0:
        eta_ls = 0.0
    else:
        eta_ls = remaining / slope_ls
    if eta_ls > MAX_HORIZON_S:
        return None

    eta_ts = None
    if slope_ts and slope_ts >= MIN_SLOPE_MIB_S:
        eta_ts = max(0.0, remaining / slope_ts)

    # ── Composantes de confiance ──────────────────────────────────────────────
    r2 = r_squared(window, slope_ls)
    if r2 < MIN_R2:
        return None                      # trajectoire trop irrégulière pour être prolongée

    # Accord entre estimateurs : 1 si identiques, décroît avec l'écart relatif
    if eta_ts is None:
        agreement = 0.4                  # un seul estimateur exploitable
    else:
        spread = abs(eta_ls - eta_ts)
        scale = max(eta_ls, eta_ts, 1.0)
        agreement = max(0.0, 1.0 - spread / scale)

    # Stabilité : l'échéance annoncée désigne-t-elle le même instant qu'avant ?
    stability = 0.6                      # valeur neutre au premier calcul
    if previous and previous.get("target_timestamp") is not None:
        drift = abs((now_t + eta_ls) - previous["target_timestamp"])
        horizon = max(eta_ls, 60.0)
        stability = max(0.0, 1.0 - drift / horizon)

    confidence = r2 * agreement * stability

    if confidence < MIN_CONFIDENCE:
        return None

    return {
        "eta_seconds": round(eta_ls, 1),
        "eta_seconds_robust": round(eta_ts, 1) if eta_ts is not None else None,
        "target_timestamp": now_t + eta_ls,
        "confidence": round(confidence, 3),
        "components": {"r_squared": round(r2, 3),
                       "agreement": round(agreement, 3),
                       "stability": round(stability, 3)},
        "slope_mib_per_min": round(slope_ls * 60, 2),
        "current_mib": round(current, 1),
        "limit_mib": round(limit_mib, 1),
        "usage_percent": round(current / limit_mib * 100, 1),
        "points_used": len(window),
    }


# ══════════════════════════════════════════════════════════════════════════════
# COLLECTE
# ══════════════════════════════════════════════════════════════════════════════

def _refresh_limits():
    """Limites mémoire déclarées, relevées moins souvent que la consommation."""
    r = subprocess.run(["kubectl", "get", "pods", "-A", "-o", "json"],
                       capture_output=True, text=True, timeout=25)
    if r.returncode != 0:
        return
    import json
    limits = {}
    for item in json.loads(r.stdout).get("items", []):
        meta = item["metadata"]
        key = f"{meta.get('namespace')}/{meta.get('name')}"
        total = 0.0
        found = False
        for c in item.get("spec", {}).get("containers", []) or []:
            lim = (c.get("resources", {}).get("limits") or {}).get("memory")
            v = parse_mem(lim)
            if v:
                total += v
                found = True
        if found:
            limits[key] = total
    with _state_lock:
        _limits.clear()
        _limits.update(limits)


def _sample_once():
    """Un relevé de consommation, suivi du recalcul des prédictions."""
    global _last_error
    r = subprocess.run(["kubectl", "top", "pods", "-A", "--no-headers"],
                       capture_output=True, text=True, timeout=25)
    if r.returncode != 0:
        _last_error = (r.stderr or "kubectl top a échoué").strip()[:200]
        return

    _last_error = None
    t = time.time()

    with _state_lock:
        for line in r.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) < 4:
                continue
            key = f"{parts[0]}/{parts[1]}"
            mib = parse_mem(parts[3])
            if mib is None:
                continue
            if key not in _history:
                _history[key] = deque(maxlen=HISTORY_SIZE)
            # metrics-server ne rafraîchit qu'à ~60 s : on évite d'empiler des
            # valeurs identiques, qui gonfleraient artificiellement le R².
            if _history[key] and _history[key][-1][1] == mib:
                continue

            # Interruption de la collecte : au-delà d'un certain écart, les
            # points antérieurs ne décrivent plus une trajectoire continue et
            # fausseraient l'estimation de pente.
            if _history[key] and (t - _history[key][-1][0]) > MAX_GAP_SECONDS:
                _history[key].clear()
                _predictions.pop(key, None)

            # Un redémarrage de conteneur remet la mémoire à zéro : la
            # trajectoire antérieure ne décrit plus le processus courant.
            # Conserver l'historique produirait une série en dents de scie,
            # sur laquelle toute extrapolation serait dénuée de sens.
            if _history[key]:
                previous = _history[key][-1][1]
                if previous > 0 and mib < previous * RESTART_DROP_RATIO:
                    _history[key].clear()
                    _predictions.pop(key, None)

            _history[key].append((t, mib))

        for key, hist in _history.items():
            limit = _limits.get(key)
            if not limit:
                _predictions.pop(key, None)
                continue
            # Plateau : la mémoire n'a plus changé depuis PLATEAU_S. Les valeurs
            # identiques n'étant pas enregistrées, sans ce test la dernière
            # prédiction resterait figée tant que la mémoire ne bouge pas.
            if hist and t - hist[-1][0] > PLATEAU_S:
                _predictions.pop(key, None)
                continue
            pred = compute_prediction(key, hist, limit, _predictions.get(key))
            if pred:
                _predictions[key] = pred
            else:
                _predictions.pop(key, None)


def _persist():
    """Sauvegarde l'historique. Les erreurs sont ignorées : la persistance est
    un confort, jamais une dépendance du calcul."""
    try:
        _os.makedirs(_STATE_DIR, exist_ok=True)
        with _state_lock:
            data = {"started_at": _started_at,
                    "history": {k: list(v) for k, v in _history.items()}}
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            _json.dump(data, f)
    except Exception:
        pass


def _restore():
    """Recharge l'historique d'une exécution précédente, si elle est récente."""
    global _started_at
    try:
        if not _os.path.exists(_STATE_PATH):
            return
        # Au-delà d'une heure, la série est trop ancienne pour être pertinente.
        if time.time() - _os.path.getmtime(_STATE_PATH) > 3600:
            return
        with open(_STATE_PATH, encoding="utf-8") as f:
            data = _json.load(f)

        with _state_lock:
            for k, pts in data.get("history", {}).items():
                _history[k] = deque([tuple(p) for p in pts], maxlen=HISTORY_SIZE)
        print(f"oom_predictor : historique restauré ({len(_history)} pods)")
    except Exception:
        pass


def _collector_loop():
    _restore()
    counter = 0
    while True:
        try:
            if counter % 10 == 0:        # limites rafraîchies toutes les 5 min
                _refresh_limits()
            _sample_once()
        except Exception as e:
            globals()["_last_error"] = str(e)[:200]
        counter += 1
        if counter % 4 == 0:
            _persist()
        time.sleep(SAMPLE_INTERVAL)


def ensure_collector():
    """Démarre le fil de collecte au premier appel."""
    global _collector_started
    with _state_lock:
        if _collector_started:
            return
        _collector_started = True
    threading.Thread(target=_collector_loop, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
# API PUBLIQUE
# ══════════════════════════════════════════════════════════════════════════════

def get_predictions(min_confidence: float = MIN_CONFIDENCE) -> dict:
    """
    Prédictions courantes, triées par échéance croissante.

    Le champ `warmup` indique que l'historique n'est pas encore suffisant :
    l'absence de prédiction ne signifie alors pas l'absence de risque.
    """
    ensure_collector()

    with _state_lock:
        tracked = len(_history)
        enough = sum(1 for h in _history.values() if len(h) >= MIN_POINTS)
        preds = []
        for key, p in _predictions.items():
            if p["confidence"] < min_confidence:
                continue
            ns, _, name = key.partition("/")
            preds.append({"namespace": ns, "pod": name, **p})
        error = _last_error

    preds.sort(key=lambda p: p["eta_seconds"])

    with _state_lock:
        diagnostics = sorted(
            ({"pod": k, "points": len(h),
              "last_mib": round(h[-1][1], 1) if h else None,
              "limit_mib": round(_limits[k], 1) if k in _limits else None}
             for k, h in _history.items()),
            key=lambda d: -d["points"])[:15]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pods_tracked": tracked,
        "pods_with_history": enough,
        "warmup": enough == 0,
        "sample_interval_seconds": SAMPLE_INTERVAL,
        "min_confidence": min_confidence,
        "predictor_version": PREDICTOR_VERSION,
        "params": {"min_points": MIN_POINTS, "min_r2": MIN_R2, "plateau_s": PLATEAU_S,
                   "window": WINDOW, "sample_interval_s": SAMPLE_INTERVAL},
        "predictions": preds,
        "diagnostics": diagnostics,
        "error": error,
        "note": ("La confiance qualifie la fiabilité de l'extrapolation "
                 "(ajustement linéaire, accord entre estimateurs, stabilité "
                 "des prédictions successives). Ce n'est pas une probabilité "
                 "de défaillance."),
    }


def get_pod_history(namespace: str, pod: str) -> dict:
    """Série mémoire d'un conteneur, pour tracé."""
    ensure_collector()
    key = f"{namespace}/{pod}"
    with _state_lock:
        hist = list(_history.get(key, []))
        limit = _limits.get(key)
        pred = _predictions.get(key)
    return {
        "namespace": namespace, "pod": pod,
        "limit_mib": round(limit, 1) if limit else None,
        "points": [{"t": round(t, 1), "mib": round(v, 1)} for t, v in hist],
        "prediction": pred,
    }

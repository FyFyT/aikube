"""Prédicteur mémoire v2 : plateau reconnu, pas de décision sur trop peu de points ou sur du bruit."""
import importlib
from collections import deque

P = importlib.import_module("ai_module.oom_predictor")


def _hist(points):
    return deque(points, maxlen=P.HISTORY_SIZE)


def test_fuite_lineaire_annoncee():
    h = _hist([(60 * i, 20 + 3 * i) for i in range(6)])        # 3 Mio/min, limite 128
    pred = P.compute_prediction("ns/p", h, 128, None)
    assert pred and abs(pred["eta_seconds"] - (128 - 35) / 3 * 60) < 5


def test_trop_peu_de_points():
    h = _hist([(60 * i, 20 + 3 * i) for i in range(P.MIN_POINTS - 1)])
    assert P.compute_prediction("ns/p", h, 128, None) is None


def test_memoire_bruitee_ignoree():
    vals = [30, 70, 35, 80, 40, 75, 45, 90]                   # monte et descend, légère pente
    h = _hist([(60 * i, v) for i, v in enumerate(vals)])
    assert P.compute_prediction("ns/p", h, 160, None) is None


def test_plateau_retire_la_prediction(monkeypatch):
    with P._state_lock:
        P._history.clear(); P._limits.clear(); P._predictions.clear()
        P._history["ns/cache"] = _hist([(1000 + 60 * i, 20 + 8 * i) for i in range(6)])
        P._limits["ns/cache"] = 160.0
    t0 = 1000 + 60 * 5

    class R:                                                    # kubectl top : même valeur
        returncode, stderr = 0, ""
        stdout = "ns cache 1m 60Mi\n"
    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(P.time, "time", lambda: t0 + 30)
    P._sample_once()
    assert "ns/cache" in P._predictions                         # croissance récente : alerte
    monkeypatch.setattr(P.time, "time", lambda: t0 + P.PLATEAU_S + 30)
    P._sample_once()
    assert "ns/cache" not in P._predictions                     # plateau : alerte retirée

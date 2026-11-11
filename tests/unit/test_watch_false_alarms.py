"""Rapport des fausses alertes : épisodes, appariement aux OOM, manqués."""
import importlib.util
import json
import os

spec = importlib.util.spec_from_file_location(
    "wfa", os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "watch_false_alarms.py"))
wfa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wfa)


def _poll(t, preds=(), ooms=(), ages=None, groups=None, ok=True):
    return {"type": "poll", "t": t, "api_ok": ok, "warmup": False, "error": None,
            "preds": [{"key": k, "target": tg, "eta": tg - t, "conf": 0.9, "points": 4} for k, tg in preds],
            "ooms": list(ooms), "ages": ages or {}, "groups": groups or {}, "eligible": 10}


def test_vraie_fausse_et_manquee():
    rows = [{"type": "start", "poll": 30}]
    # fuite : alerte de t=0 à t=60, OOM à t=100 -> vraie, préavis 100 s, erreur 20 s
    # cache : alerte de t=0 à t=30 puis plus rien -> fausse (conteneur jeune)
    for t in range(0, 1500, 30):
        preds = []
        if t <= 60:
            preds.append(("aikube-fa/leak", 120))
        if t <= 30:
            preds.append(("aikube-fa/cache", 300))
        ooms = [{"key": "aikube-fa/leak", "t": 100, "group": "oom"}] if t == 120 else []
        if t == 300:
            ooms.append({"key": "kube-system/x", "t": 290, "group": ""})   # jamais annoncé
        rows.append(_poll(t, preds, ooms, ages={"aikube-fa/cache": 120, "aikube-fa/leak": 2000},
                          groups={"aikube-fa/leak": "oom", "aikube-fa/cache": "no-oom"}))
    d = wfa.build(rows)
    by = {e["key"]: e for e in d["episodes"]}
    assert by["aikube-fa/leak"]["true"] and by["aikube-fa/leak"]["lead"] == 100
    assert by["aikube-fa/leak"]["err"] == 20
    assert by["aikube-fa/cache"]["true"] is False and by["aikube-fa/cache"]["age0"] == 120
    assert [o["key"] for o in d["missed"]] == ["kube-system/x"]


def test_releves_invalides_ignores():
    rows = [{"type": "start", "poll": 30}, _poll(0, [("a/b", 500)], ok=False), _poll(30)]
    d = wfa.build(rows)
    assert d["episodes"] == [] and d["valid_h"] > 0


def test_rapport_ecrit(tmp_path, monkeypatch):
    j = tmp_path / "j.jsonl"
    rows = [{"type": "start", "poll": 30}] + [_poll(t, [("aikube-fa/cache", 120)] if t < 90 else [],
                                                   groups={"aikube-fa/cache": "no-oom"}) for t in range(0, 900, 30)]
    j.write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(wfa, "OUT", str(j))
    monkeypatch.setattr(wfa, "REPORT", str(tmp_path / "r.md"))
    wfa.report(None)
    txt = (tmp_path / "r.md").read_text()
    assert "fausses alertes** | **1**" in txt


def test_oom_anterieur_et_alerte_non_conclue():
    rows = [{"type": "start", "poll": 30}]
    for t in range(1000, 1300, 30):
        ooms = [{"key": "a/old", "t": 10, "group": ""}] if t == 1000 else []
        rows.append(_poll(t, [("a/late", 5000)] if t >= 1200 else [], ooms))
    d = wfa.build(rows)
    assert d["ooms"] == [] and d["missed"] == []
    assert d["episodes"] == [] and len(d["censored"]) == 1


def test_coupure_ne_fusionne_pas_deux_cycles():
    rows = [{"type": "start", "poll": 30}]
    # cycle 1 : alerte de 0 à 300, puis le script s'arrête (coupure 330 -> 3000),
    # l'OOM du cycle 1 tombe dans la coupure ; cycle 2 : alerte de 3000 à 3600, OOM à 3590
    for t in range(0, 330, 30):
        rows.append(_poll(t, [("aikube-fa/leak", 1200)], groups={"aikube-fa/leak": "oom"}))
    for t in range(3000, 3900, 30):
        preds = [("aikube-fa/leak", 3620)] if t <= 3600 else []
        ooms = [{"key": "aikube-fa/leak", "t": 1100, "group": "oom"},
                {"key": "aikube-fa/leak", "t": 3590, "group": "oom"}] if t == 3600 else []
        rows.append(_poll(t, preds, ooms, groups={"aikube-fa/leak": "oom"}))
    d = wfa.build(rows)
    assert [o["t"] for o in d["unseen"]] == [1100]
    assert d["missed"] == []
    assert [e["lead"] for e in d["episodes"] if e["true"]] == [590]
    assert len(d["censored"]) == 1 and d["censored"][0].get("cut")

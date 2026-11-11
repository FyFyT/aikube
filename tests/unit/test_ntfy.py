"""Notifications ntfy : une alerte envoyée une fois, sa disparition signalée, pas d'envoi sans canal."""
from ai_module import ntfy_notifier as N


def _a(i, sev="critical", kind="pod"):
    return {"id": i, "severity": sev, "kind": kind, "title": f"alerte {i}", "detail": "d"}


def test_cycle(monkeypatch):
    sent = []
    monkeypatch.setattr(N, "publish", lambda t, m, priority=3, tags=None: sent.append((t, priority)) or True)
    N._sent.clear()
    N.cycle([_a("x")], first=True)                         # démarrage : un seul résumé
    assert len(sent) == 1 and "surveille" in sent[0][0]
    sent.clear()
    N.cycle([_a("x"), _a("y"), _a("z", "warning", "reco")])  # y nouveau ; z pas important
    assert [t for t, _ in sent] == ["alerte y"]
    sent.clear()
    N.cycle([_a("x"), _a("y")])                            # rien de neuf : rien envoyé
    assert sent == []
    N.cycle([_a("y")])                                      # x disparaît : « résolu »
    assert sent == [("Résolu : alerte x", 2)]


def test_oom_annonce_est_important():
    assert N.important(_a("o", "warning", "oom")) and not N.important(_a("r", "warning", "reco"))


def test_sans_canal(monkeypatch):
    monkeypatch.setattr(N, "_cfg", lambda: ("", "https://ntfy.sh", ""))
    assert N.publish("t", "m") is False and N.enabled() is False

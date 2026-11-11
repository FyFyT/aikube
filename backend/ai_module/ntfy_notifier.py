"""
Notifications sur le téléphone par ntfy (https://ntfy.sh), application libre.

Pourquoi : Expo Go ne peut plus afficher de notifications sur Android, et une
application compilée ne les affiche que si elle tourne. ntfy, lui, réveille le
téléphone même verrouillé et application AI-Kube fermée : le backend publie
chaque nouvelle alerte importante sur un « canal » (topic) secret, et
l'application ntfy du téléphone, abonnée à ce canal, l'affiche.

Configuration (~/aikube/.env) :
  NTFY_TOPIC=aikube-xxxxxxxx      obligatoire pour activer ; un nom long et imprévisible,
                                  car n'importe qui connaissant le nom peut lire le canal
  NTFY_SERVER=https://ntfy.sh     facultatif (serveur public par défaut)
  NTFY_TOKEN=...                  facultatif (serveur protégé)

Ce qui est envoyé : les alertes de l'écran d'accueil mobile (même calcul), si elles
sont critiques, ou si ce sont des crashs mémoire annoncés, des déploiements en échec
ou une panne des mesures. Une alerte n'est envoyée qu'une fois ; sa disparition est
signalée (« résolu »). Au démarrage, un seul message résume les alertes en cours.

Confidentialité : le titre et le détail des alertes (noms de pods, namespaces)
passent par le serveur ntfy choisi. Aucun secret, aucun log, aucun manifeste.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

INTERVAL_S = 60
MAX_PER_CYCLE = 5
IMPORTANT_KINDS = {"oom", "deploy", "metrics"}

_started = False
_lock = threading.Lock()
_sent: dict[str, dict] = {}          # id de l'alerte -> alerte envoyée
_last = {"t": None, "ok": None, "error": None, "sent": 0}


def _cfg():
    from core.config import settings
    topic = (getattr(settings, "NTFY_TOPIC", None) or "").strip()
    server = (getattr(settings, "NTFY_SERVER", None) or "https://ntfy.sh").rstrip("/")
    token = (getattr(settings, "NTFY_TOKEN", None) or "").strip()
    return topic, server, token


def enabled() -> bool:
    return bool(_cfg()[0])


def publish(title: str, message: str, priority: int = 3, tags: list[str] | None = None) -> bool:
    """Envoie une notification. Publication JSON : accents permis dans le titre."""
    topic, server, token = _cfg()
    if not topic:
        return False
    body = json.dumps({"topic": topic, "title": title[:120], "message": message[:1000],
                       "priority": priority, "tags": tags or []}).encode("utf-8")
    req = urllib.request.Request(server, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = 200 <= r.status < 300
        _last.update(ok=ok, error=None if ok else f"HTTP {r.status}")
        if ok:
            _last["sent"] += 1
        return ok
    except Exception as e:  # noqa: BLE001 — une notification ratée ne doit rien casser
        _last.update(ok=False, error=str(e)[:200])
        return False


def important(alert: dict) -> bool:
    return alert.get("severity") == "critical" or alert.get("kind") in IMPORTANT_KINDS


def _prio(alert: dict) -> int:
    return 5 if alert.get("severity") == "critical" else 4


def _tags(alert: dict) -> list[str]:
    return {"oom": ["chart_with_upwards_trend"], "deploy": ["rewind"],
            "metrics": ["warning"], "pod": ["rotating_light"],
            "security": ["lock"]}.get(alert.get("kind"), ["bell"])


def cycle(alerts: list[dict], first: bool = False) -> int:
    """Compare les alertes courantes à celles déjà envoyées. Renvoie le nombre de messages."""
    now = {a["id"]: a for a in alerts if important(a)}
    sent = 0
    with _lock:
        if first:
            _sent.update(now)
            titles = "\n".join(f"• {a['title']}" for a in list(now.values())[:8])
            publish("AI-Kube surveille le cluster",
                    f"{len(now)} alerte(s) importante(s) en cours." + (f"\n{titles}" if titles else ""),
                    priority=3, tags=["white_check_mark"])
            return 1
        new = [a for i, a in now.items() if i not in _sent]
        gone = [a for i, a in _sent.items() if i not in now]
        for a in new[:MAX_PER_CYCLE]:
            sent += publish(a["title"], a.get("detail") or "", _prio(a), _tags(a))
        if len(new) > MAX_PER_CYCLE:
            sent += publish(f"{len(new) - MAX_PER_CYCLE} autre(s) alerte(s)",
                            "\n".join(f"• {a['title']}" for a in new[MAX_PER_CYCLE:][:10]),
                            4, ["bell"])
        for a in gone[:MAX_PER_CYCLE]:
            sent += publish(f"Résolu : {a['title']}", "L'alerte a disparu.", 2, ["white_check_mark"])
        for a in new:
            _sent[a["id"]] = a
        for a in gone:
            _sent.pop(a["id"], None)
    return sent


def _loop():
    from api import mobile_router as M
    first = True
    while True:
        try:
            data = M._summary()
            M._cache.update(t=time.time(), data=data)      # l'écran d'accueil en profite
            cycle(data.get("alerts", []), first=first)
            first = False
            _last["t"] = time.time()
        except Exception as e:  # noqa: BLE001
            _last.update(error=str(e)[:200])
        time.sleep(INTERVAL_S)


def ensure_started() -> None:
    global _started
    if not enabled():
        print("ℹ️  Notifications ntfy désactivées (NTFY_TOPIC absent de .env)")
        return
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True).start()
    print(f"🔔 Notifications ntfy actives (canal {_cfg()[0][:6]}…)")


def status() -> dict:
    topic, server, _ = _cfg()
    return {"enabled": bool(topic), "server": server, "topic": topic or None,
            "last_cycle": _last["t"], "last_ok": _last["ok"], "last_error": _last["error"],
            "sent": _last["sent"], "tracked": len(_sent)}

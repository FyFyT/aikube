#!/usr/bin/env python3
"""Envoie une notification de test sur le téléphone (ntfy) et montre les alertes qui seraient envoyées.

Usage (terminal Ubuntu WSL, dans ~/aikube, venv activé) : python3 scripts/ntfy_test.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
os.chdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from ai_module import ntfy_notifier as N  # noqa: E402

topic, server, _ = N._cfg()
if not topic:
    sys.exit("NTFY_TOPIC absent de ~/aikube/.env : ajoute par exemple  NTFY_TOPIC=aikube-<mot-secret>")
print(f"Serveur : {server}   canal : {topic}")
ok = N.publish("AI-Kube : test", "Si tu lis ceci sur ton téléphone, les notifications fonctionnent.", 4, ["tada"])
print("Notification de test :", "envoyée" if ok else f"ÉCHEC ({N._last['error']})")
from api import mobile_router as M  # noqa: E402
alerts = [a for a in M._summary().get("alerts", []) if N.important(a)]
print(f"\n{len(alerts)} alerte(s) importante(s) en ce moment (celles que le backend enverrait) :")
for a in alerts:
    print(f"  [{a['severity']}] {a['title']}")

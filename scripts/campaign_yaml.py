#!/usr/bin/env python3
"""
scripts/campaign_yaml.py — Campagne de mesure du générateur de manifestes (chapitre C1)
================================================================================
Envoie un jeu FIXE de 36 demandes au générateur de l'application, dans plusieurs
conditions, et enregistre pour chacune le verdict des arbitres indépendants
(dry-run serveur + contrôle de capacité).

CONDITIONS
  claude         modèle + lecture de la machine hôte + boucle de correction (l'app)
  claude-noctx   ABLATION : même modèle, même boucle, SANS le contexte hôte/cluster
  groq           openai/gpt-oss-120b (modèle ouvert) + contexte + boucle (si GROQ_API_KEY)
  kubellm        KubeLLM v2, fine-tuné sur le corpus v3 (Ollama local)
  kubellm-base   le même modèle de base sans fine-tuning (témoin)
  kubellm-v3     2e entraînement : corpus v3 + capacité + exemples de correction
  kubellm-v3-repair  le même + réparation déterministe avant chaque nouvel essai
                 (manifest_repair : version d'API, champs mal placés, noms, capacité…)
  groq-repair    l'enseignant + la même réparation (généralité de la réparation)

MÉTRIQUES (calculées par campaign_report.py)
  pass@1    manifeste valide dès la 1re tentative
  pass@3    valide après au plus 3 tentatives (boucle de correction)
  erreurs   catégories d'erreur des tentatives rejetées
  capacité  demandes « impossibles » : le 1er manifeste respecte-t-il la RAM réelle ?

REPRISE : les couples (demande, condition, répétition) déjà mesurés sont sautés.
On peut interrompre (Ctrl+C) et relancer sans perte.

USAGE (dans ~/aikube, backend lancé)
  python3 scripts/campaign_yaml.py                       # claude + claude-noctx
  python3 scripts/campaign_yaml.py --conditions claude,claude-noctx,groq
  python3 scripts/campaign_yaml.py --limit 5             # essai rapide
================================================================================
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "data", "observations", "campaign_yaml.jsonl")

# id, catégorie, demande, kinds attendus (au moins ceux-là), demande de capacité impossible ?
PROMPTS = [
    ("d01", "workload", "Déployer nginx avec 2 replicas, exposé sur le port 80", ["Deployment", "Service"], False),
    ("d02", "workload", "API Flask (image python:3.12-slim) avec 3 replicas et un Service ClusterIP sur le port 5000", ["Deployment", "Service"], False),
    ("d03", "workload", "Deployment Redis 7 avec une sonde de disponibilité TCP sur 6379", ["Deployment"], False),
    ("d04", "workload", "Application Node.js 20 avec 2 replicas, variables d'environnement NODE_ENV=production et PORT=3000", ["Deployment"], False),
    ("d05", "workload", "Deployment Grafana exposé via un Service NodePort", ["Deployment", "Service"], False),
    ("s01", "stateful", "StatefulSet PostgreSQL 16 avec un volume persistant de 2Gi", ["StatefulSet"], False),
    ("s02", "stateful", "StatefulSet MongoDB à 3 répliques avec un Service headless", ["StatefulSet", "Service"], False),
    ("s03", "stateful", "PersistentVolumeClaim de 1Gi et un pod busybox qui l'utilise", ["PersistentVolumeClaim"], False),
    ("b01", "batch", "CronJob de sauvegarde qui s'exécute tous les jours à 2 h du matin avec l'image alpine", ["CronJob"], False),
    ("b02", "batch", "Job qui calcule pi avec perl, 3 exécutions en parallèle", ["Job"], False),
    ("b03", "batch", "CronJob toutes les 15 minutes qui nettoie /tmp, sans exécutions concurrentes", ["CronJob"], False),
    ("n01", "network", "NetworkPolicy qui refuse tout le trafic entrant dans le namespace default", ["NetworkPolicy"], False),
    ("n02", "network", "NetworkPolicy qui n'autorise que les pods app=frontend à joindre app=backend sur le port 8080", ["NetworkPolicy"], False),
    ("n03", "network", "Ingress pour le service web sur le chemin /api avec l'hôte demo.local", ["Ingress"], False),
    ("a01", "autoscaling", "HorizontalPodAutoscaler pour le deployment web : 2 à 10 replicas si CPU > 70 %", ["HorizontalPodAutoscaler"], False),
    ("a02", "autoscaling", "Deployment nginx et son HPA basé sur la mémoire à 80 %", ["Deployment", "HorizontalPodAutoscaler"], False),
    ("a03", "autoscaling", "PodDisruptionBudget garantissant au moins 2 pods disponibles pour app=api", ["PodDisruptionBudget"], False),
    ("c01", "config", "ConfigMap contenant un fichier nginx.conf minimal, monté dans un Deployment nginx", ["ConfigMap", "Deployment"], False),
    ("c02", "config", "Secret contenant un utilisateur et un mot de passe de base de données, utilisé par un Deployment", ["Secret", "Deployment"], False),
    ("c03", "config", "ResourceQuota limitant le namespace default à 4 CPU et 8Gi de mémoire", ["ResourceQuota"], False),
    ("c04", "config", "LimitRange imposant des limites par défaut de 500m CPU et 256Mi aux conteneurs", ["LimitRange"], False),
    ("r01", "rbac", "ServiceAccount, Role en lecture seule sur les pods et RoleBinding associé", ["ServiceAccount", "Role", "RoleBinding"], False),
    ("r02", "rbac", "ClusterRole permettant de lister les nœuds, lié à un ServiceAccount monitoring", ["ClusterRole", "ClusterRoleBinding"], False),
    ("x01", "daemonset", "DaemonSet de collecte de logs fluent-bit sur tous les nœuds", ["DaemonSet"], False),
    ("x02", "namespace", "Créer un namespace staging et y déployer nginx avec 2 replicas", ["Namespace", "Deployment"], False),
    ("x03", "security", "Deployment durci : utilisateur non root, système de fichiers en lecture seule, aucune capacité", ["Deployment"], False),
    ("k01", "capacite", "Redis avec 32Gi de RAM", ["Deployment"], True),
    ("k02", "capacite", "API Flask avec 5 replicas de 4Gi de RAM chacune", ["Deployment"], True),
    ("k03", "capacite", "DaemonSet demandant 3 CPU et 2Gi par nœud", ["DaemonSet"], True),
    ("k04", "capacite", "Base de données PostgreSQL avec la plus grande mémoire possible sur ce cluster", ["StatefulSet"], True),
    # Ajoutées après la 1re campagne (effet plafond : 4 demandes ne suffisent pas à départager)
    ("k05", "capacite", "Elasticsearch en StatefulSet avec 3 nœuds de 8Gi de RAM chacun", ["StatefulSet"], True),
    ("k06", "capacite", "Deployment nginx de 20 replicas demandant chacun 1 CPU", ["Deployment"], True),
    ("k07", "capacite", "Job de calcul scientifique demandant 16 CPU", ["Job"], True),
    ("k08", "capacite", "Cache Memcached en Deployment avec 16Gi de RAM", ["Deployment"], True),
    ("k09", "capacite", "Kafka en StatefulSet avec 3 brokers à 6Gi de RAM chacun", ["StatefulSet"], True),
    ("k10", "capacite", "Job d'entraînement de modèle avec 12Gi de RAM et 4 CPU", ["Job"], True),
]

CONDITIONS = {
    "claude":       {"model": "claude", "use_context": True},
    "claude-noctx": {"model": "claude", "use_context": False},
    "groq":         {"model": "groq",   "use_context": True},
    # Fine-tuning (Ollama local) : même boucle, mêmes arbitres, même format d'entrée
    "kubellm":      {"model": "kubellm", "use_context": True},
    "kubellm-base": {"model": "kubellm-base", "use_context": True},
    "kubellm-v3":   {"model": "kubellm-v3", "use_context": True},
    # Système hybride : le modèle propose, la réparation déterministe corrige ce qui se calcule
    "kubellm-v3-repair": {"model": "kubellm-v3", "use_context": True, "repair": True},
    # 3e entraînement : corpus de v3 + erreurs PROPRES de v3 corrigées (build_corpus_v5_self.py)
    "kubellm-v4":   {"model": "kubellm-v4", "use_context": True},
    "kubellm-v4-repair": {"model": "kubellm-v4", "use_context": True, "repair": True},
    "groq-repair":  {"model": "groq", "use_context": True, "repair": True},
}


def post(api, path, body, timeout=420):   # modèles locaux sur processeur : --timeout 1800
    req = urllib.request.Request(api + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(api, path, timeout=60):
    with urllib.request.urlopen(api + path, timeout=timeout) as r:
        return json.loads(r.read())


def done_keys(out=None):
    out = out or OUT
    keys = set()
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("status") == "ok":
                        keys.add((d["prompt_id"], d["condition"], d["rep"]))
                except (ValueError, KeyError):
                    pass
    return keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--conditions", default="claude,claude-noctx")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="nombre de demandes (0 = toutes)")
    ap.add_argument("--only", default="", help="catégories séparées par des virgules (ex. capacite)")
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--pause", type=int, default=5, help="secondes entre deux générations")
    ap.add_argument("--timeout", type=int, default=420, help="délai max d'une génération (s) ; 1800 pour un modèle local")
    ap.add_argument("--out", default="", help="fichier de résultats (défaut : data/observations/campaign_yaml.jsonl) ; "
                    "un nom de fichier seul est rangé dans data/observations/. Sert à ne jamais mélanger une "
                    "nouvelle série (ex. modèle réentraîné) avec le journal mesuré du mémoire.")
    a = ap.parse_args()

    try:
        hc = get(a.api, "/api/ai/host-context")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Backend injoignable sur {a.api} : {e}")
    providers = hc.get("providers", [])
    conds = [c.strip() for c in a.conditions.split(",") if c.strip()]
    for c in list(conds):
        if c not in CONDITIONS:
            sys.exit(f"Condition inconnue : {c}")
        if CONDITIONS[c]["model"] not in providers:
            print(f"⚠ condition {c} ignorée : clé {CONDITIONS[c]['model']} absente")
            conds.remove(c)
    cats = {x.strip() for x in a.only.split(",") if x.strip()}
    prompts = [p for p in PROMPTS if not cats or p[1] in cats]
    prompts = prompts[: a.limit] if a.limit else prompts
    out = a.out or OUT
    if a.out and not os.path.isabs(a.out) and os.sep not in a.out:
        out = os.path.join(ROOT, "data", "observations", a.out)
    already = done_keys(out)
    todo = [(p, c, r) for r in range(1, a.reps + 1) for p in prompts for c in conds
            if (p[0], c, r) not in already]
    print(f"Campagne C1 : {len(prompts)} demandes × {len(conds)} conditions × {a.reps} rép. "
          f"→ {len(todo)} à faire ({len(already)} déjà mesurées)")
    print(f"Résultats : {out}\n")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    for i, ((pid, cat, text, kinds, impossible), cond, rep) in enumerate(todo, 1):
        cfg = CONDITIONS[cond]
        t0 = time.time()
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "prompt_id": pid, "category": cat,
               "prompt": text, "expected_kinds": kinds, "capacity_stress": impossible,
               "condition": cond, "rep": rep}
        try:
            r = post(a.api, "/api/ai/generate-yaml",
                     {"description": text, "model": cfg["model"], "use_context": cfg["use_context"],
                      "max_attempts": a.max_attempts, "campaign": "C1",
                      "repair": bool(cfg.get("repair"))}, timeout=a.timeout)
            atts = r.get("attempts", [])
            got = {k.split("/")[0] for k in r.get("kinds", [])}
            rec.update(status="ok", valid=r.get("valid"), server_validated=r.get("server_validated"),
                       attempts=len(atts), valid_first=bool(atts and atts[0]["valid"]),
                       errors=[[(e["source"], e["category"]) for e in at["errors"]] for at in atts],
                       kinds=sorted(got), kinds_ok=set(kinds) <= got,
                       capacity_error_first=bool(atts) and any(e["source"] == "capacité" for e in atts[0]["errors"]),
                       model=r.get("model_used"),
                       tokens=[at.get("usage") for at in atts],
                       duration_s=round(time.time() - t0, 1), yaml=r.get("yaml", "")[:6000])
            if cfg.get("repair"):
                # séparer ce que le MODÈLE a produit de ce que la réparation a corrigé
                reps = [at.get("repair") for at in atts]
                rec.update(repair=True,
                           model_valid_first=bool(atts) and atts[0]["valid"] and not (reps[0] and reps[0]["kept"]),
                           repaired_first=bool(reps and reps[0] and reps[0]["kept"] and reps[0]["valid_after"]),
                           model_errors_first=(reps[0]["errors_before"] if reps and reps[0]
                                               else [e["category"] for e in (atts[0]["errors"] if atts else [])]),
                           repair_changes=[(x or {}).get("changes", []) for x in reps],
                           yaml_model_first=((atts[0].get("yaml_model") or "")[:6000] if atts else ""))
            mark = "✅" if r.get("valid") else ("⚪" if r.get("server_validated") is None else "❌")
            if rec.get("repaired_first"):
                mark += " 🔧"
            print(f"[{i}/{len(todo)}] {mark} {pid} {cond:13} tentatives={len(atts)} "
                  f"1er={'oui' if rec['valid_first'] else 'non'} kinds={'ok' if rec['kinds_ok'] else got} "
                  f"({rec['duration_s']} s)")
        except urllib.error.HTTPError as e:
            rec.update(status="error", error=f"HTTP {e.code} : {e.read()[:300].decode('utf-8', 'ignore')}")
            print(f"[{i}/{len(todo)}] ⚠ {pid} {cond} : {rec['error'][:120]}")
        except Exception as e:  # noqa: BLE001
            rec.update(status="error", error=str(e)[:300])
            print(f"[{i}/{len(todo)}] ⚠ {pid} {cond} : {rec['error'][:120]}")
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        time.sleep(a.pause)
    print("\nTerminé. Rapport : python3 scripts/campaign_report.py")


if __name__ == "__main__":
    main()

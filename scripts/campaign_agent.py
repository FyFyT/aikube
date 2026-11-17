#!/usr/bin/env python3
"""
scripts/campaign_agent.py — Campagne de mesure de l'agent de dépannage (chapitre C2)
================================================================================
Pose la MÊME question neutre sur chacune des 10 situations du banc
k8s/benchmark/agent-benchmark.yaml, dans plusieurs conditions :

  agent         l'agent de l'app : enquête avec ses outils (get, describe, logs…)
  chat-claude   TÉMOIN : même modèle, sans outils, avec le contexte hôte injecté
                (l'approche « assistant à contexte » des outils existants)
  chat-groq     même témoin avec le modèle Groq (GROQ_MODEL)
  chat-claude-obs  TÉMOIN FORT (retour de l'encadreur, H2) : même modèle, sans outils,
                mais la question est accompagnée de l'état du namespace relevé par
                kubectl juste avant (pods, déploiements, services, endpoints, PVC,
                événements), comme le fait K8sGPT
  chat-groq-obs le même témoin fort avec gpt-oss-120b

BANC D'UN TIERS (test indépendant)
  --bench tiers lit k8s/benchmark/tiers/cas_tiers.json (écrit par un camarade,
  avec son manifeste) ; voir k8s/benchmark/tiers/LISEZMOI.md

La question ne donne jamais la cause. Le cas 10 est un déploiement SAIN :
l'outil ne doit pas inventer de panne.

NOTATION
  automatique : mots-clés de la cause (tous les groupes trouvés = correct,
                une partie = partiel, aucun = faux)
  manuelle    : data/observations/campaign_agent_review.csv, colonne
                « note_manuelle » (1 / 0.5 / 0). Si elle est remplie,
                campaign_report.py l'utilise à la place de la note automatique.

USAGE (dans ~/aikube, backend lancé)
  kubectl apply -f k8s/benchmark/agent-benchmark-v2.yaml
  # attendre 3 minutes
  python3 scripts/campaign_agent.py                  # banc v2 (défaut)
  python3 scripts/campaign_agent.py --conditions agent,chat-claude --reps 2
================================================================================
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OBS = os.path.join(ROOT, "data", "observations")
OUT = os.path.join(OBS, "campaign_agent.jsonl")
CSV_OUT = os.path.join(OBS, "campaign_agent_review.csv")
NS = "aikube-agent"   # remplacé par --bench

# Banc v1 : noms parlants (agent-oom, agent-image…) et cause écrite en annotation.
# Défaut constaté après la 1re campagne : le chat SANS outils devine la cause d'après
# le nom, et l'agent peut lire l'annotation avec « describe ». Le banc v2 corrige
# les deux : noms neutres (svc-01…), aucune annotation, vérité terrain ici seulement.
BENCHES = {
    "v1": {"ns": "aikube-agent", "targets": {
        "image": "le déploiement agent-image", "crash": "le déploiement agent-crash",
        "oom": "le déploiement agent-oom", "pending": "le déploiement agent-pending",
        "selector": "le déploiement agent-selector", "config": "le déploiement agent-config",
        "probe": "le déploiement agent-probe", "service": "le service agent-svc",
        "storage": "le déploiement agent-storage", "healthy": "le déploiement agent-healthy"}},
    "v2": {"ns": "aikube-agent2", "targets": {
        "storage": "le déploiement svc-01", "healthy": "le déploiement svc-02",
        "crash": "le déploiement svc-03", "probe": "le déploiement svc-04",
        "image": "le déploiement svc-05", "selector": "le déploiement svc-06",
        "oom": "le déploiement svc-07", "config": "le déploiement svc-08",
        "pending": "le déploiement svc-09", "service": "le service web-front"}},
}

# PREUVE OBSERVÉE (notation stricte) : un détail qu'on ne peut connaître qu'en
# regardant le cluster. Une réponse qui énumère toutes les causes possibles
# atteint les mots-clés, pas la preuve.
EVIDENCE = {
    "image": ["9.99"], "crash": ["database_url"], "oom": ["48"], "pending": ["64"],
    "selector": ["nvme", "disktype"], "config": ["agent-settings"], "probe": ["8081"],
    "service": ["backnd"], "storage": ["fast-ssd"], "healthy": None,
}

# id, objet de la question, cause (vérité terrain), groupes de mots-clés (un mot par groupe suffit)
CASES = [
    ("image", "le déploiement agent-image", "tag d'image inexistant",
     [["imagepull", "errimagepull", "pull"],
      ["9.99", "tag", "inexistant", "n'existe pas", "introuvable", "not found", "manifest unknown"]]),
    ("crash", "le déploiement agent-crash", "variable DATABASE_URL manquante → exit 1",
     [["database_url"],
      ["crashloop", "exit 1", "code 1", "s'arrete", "redemarr", "plante", "crash"]]),
    ("oom", "le déploiement agent-oom", "OOMKilled, limite 48Mi trop basse",
     [["oom"], ["limite", "limit", "48mi", "memoire"]]),
    ("pending", "le déploiement agent-pending", "request de 64 CPU impossible",
     [["insufficient cpu", "64"],
      ["pending", "planifi", "schedul", "aucun noeud", "no nodes", "attente"]]),
    ("selector", "le déploiement agent-selector", "nodeSelector sans nœud correspondant",
     [["nodeselector", "disktype", "nvme"],
      ["aucun noeud", "no nodes", "didn't match", "ne correspond", "pending", "affinity", "etiquette"]]),
    ("config", "le déploiement agent-config", "ConfigMap agent-settings absente",
     [["agent-settings", "configmap"],
      ["introuvable", "not found", "absente", "inexistant", "n'existe pas", "manquant",
       "createcontainerconfigerror"]]),
    ("probe", "le déploiement agent-probe", "readinessProbe sur le port 8081 au lieu de 80",
     [["readiness", "disponibilite", "sonde"], ["8081"]]),
    ("service", "le service agent-svc", "sélecteur app=agent-backnd (faute de frappe), aucun endpoint",
     [["selecteur", "selector"], ["backnd", "faute", "endpoint", "aucun pod", "ne correspond"]]),
    ("storage", "le déploiement agent-storage", "PVC sur une StorageClass inexistante",
     [["fast-ssd-inexistante", "storageclass", "storage class"],
      ["pvc", "persistentvolumeclaim", "lie", "bound", "pending"]]),
    ("healthy", "le déploiement agent-healthy", "aucune panne (témoin)", None),
]

# ── Banc écrit par un tiers (facultatif) ─────────────────────────────────────
TIERS_FILE = os.path.join(ROOT, "k8s", "benchmark", "tiers", "cas_tiers.json")
TIERS_CASES = []


def load_tiers():
    """Charge le banc du tiers s'il existe : cas, cibles et preuves."""
    if not os.path.exists(TIERS_FILE):
        return
    with open(TIERS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    targets = {}
    TIERS_CASES.clear()
    for c in data["cas"]:
        cid = str(c["id"]).strip()
        groups = c.get("mots_cles")  # None = déploiement sain (témoin)
        TIERS_CASES.append((cid, c["cible"], c["cause"], groups))
        targets[cid] = c["cible"]
        EVIDENCE[cid] = c.get("preuve")
    BENCHES["tiers"] = {"ns": data["namespace"], "targets": targets, "manifest": data.get("manifeste", "")}


def all_cases():
    return CASES + TIERS_CASES


HEALTHY_OK = ["aucun probleme", "pas de probleme", "aucune anomalie", "aucune erreur", "sain",
              "fonctionne correctement", "fonctionne normalement", "en bonne sante", "healthy",
              "rien d'anormal", "tout est normal", "aucune panne"]

# Correctif du 26/10 : la 1re campagne v2 posait la question avec « aikube-agent »
# au lieu de « aikube-agent2 » (le champ « question » du journal en garde la trace).
QUESTION = ("Dans le namespace {ns}, {target} pose-t-il un problème ? "
            "Si oui, quelle en est la cause exacte et comment la corriger ?")

CONDITIONS = {
    "agent":       {"path": "/api/ai/agent", "body": {}, "needs": "claude"},
    "chat-claude": {"path": "/api/ai/chat", "body": {"model": "claude"}, "needs": "claude"},
    "chat-groq":   {"path": "/api/ai/chat", "body": {"model": "groq"}, "needs": "groq"},
    "chat-claude-obs": {"path": "/api/ai/chat", "body": {"model": "claude"}, "needs": "claude", "obs": True},
    "chat-groq-obs":   {"path": "/api/ai/chat", "body": {"model": "groq"}, "needs": "groq", "obs": True},
}

OBS_CMDS = [
    ["kubectl", "get", "deployments,replicasets,pods,services,endpoints,persistentvolumeclaims",
     "-n", "{ns}", "-o", "wide"],
    ["kubectl", "get", "events", "-n", "{ns}", "--sort-by=.lastTimestamp"],
]
OBS_MAX = 12000


def observation(ns):
    """État du namespace tel qu'un outil comme K8sGPT le voit (sans journaux ni describe)."""
    parts = []
    for cmd in OBS_CMDS:
        c = [x.format(ns=ns) for x in cmd]
        try:
            r = subprocess.run(c, capture_output=True, text=True, timeout=60)
            txt = (r.stdout or r.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            txt = f"(échec : {e})"
        if "events" in cmd:
            lines = txt.splitlines()
            txt = "\n".join(lines[:1] + lines[-60:]) if len(lines) > 61 else txt
        parts.append("$ " + " ".join(c) + "\n" + txt)
    out = "\n\n".join(parts)
    return out[:OBS_MAX]


OBS_INTRO = ("\n\nVoici l'état observé du namespace {ns}, relevé avec kubectl juste avant la question :\n"
             "```\n{obs}\n```")


def norm(s: str) -> str:
    s = (s or "").lower().replace("œ", "oe").replace("’", "'").replace("`", "")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def score(case, text: str):
    t = norm(text)
    _, _, _, groups = case
    if groups is None:
        hit = [k for k in HEALTHY_OK if k in t]
        return (1.0 if hit else 0.0), hit
    found = []
    for g in groups:
        m = next((k for k in g if norm(k) in t), None)
        found.append(m)
    n = sum(1 for m in found if m)
    return (1.0 if n == len(groups) else 0.5 if n else 0.0), found


ABSTAIN = ["je ne peux pas me prononcer", "je ne peux pas vous donner", "je ne peux pas confirmer",
           "je ne peux pas repondre", "je ne peux pas inspecter", "je ne peux pas diagnostiquer",
           "je n'ai pas acces", "je n'ai aucun acces", "je n'ai pas d'acces", "sans inventer",
           "ne pas inventer", "je ne vais pas inventer", "je ne vais pas deviner"]


def is_abstention(text: str) -> bool:
    """La réponse refuse de conclure (début de réponse). Relu à la main : c'est le
    comportement du chat sans outils dans 30 réponses sur 30."""
    return any(k in norm(text)[:500] for k in ABSTAIN)


def score_strict(case_id: str, text: str, auto: float) -> float:
    """Note stricte : abstention = 0 ; sinon la cause exacte ET un détail observé
    (sinon au plus 0,5)."""
    if is_abstention(text):
        return 0.0
    ev = EVIDENCE.get(case_id)
    if ev is None:
        return auto
    if any(norm(k) in norm(text) for k in ev):
        return auto
    return min(auto, 0.5)


def post(api, path, body, timeout=600):
    req = urllib.request.Request(api + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(api, path, timeout=60):
    with urllib.request.urlopen(api + path, timeout=timeout) as r:
        return json.loads(r.read())


def bench_state():
    """État observé des pods du banc au moment de la mesure (conservé avec les résultats)."""
    try:
        r = subprocess.run(["kubectl", "get", "pods", "-n", NS, "--no-headers"],
                           capture_output=True, text=True, timeout=40)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    if r.returncode != 0:
        return None, r.stderr.strip()[:300]
    return r.stdout.strip(), None


def done_keys():
    keys = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("status") == "ok":
                        keys.add((d.get("bench", "v1"), d["case"], d["condition"], d["rep"]))
                except (ValueError, KeyError):
                    pass
    return keys


def write_csv():
    """Fichier de relecture manuelle. Les notes manuelles déjà saisies sont conservées."""
    manual = {}
    if os.path.exists(CSV_OUT):
        with open(CSV_OUT, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("note_manuelle", "").strip():
                    manual[(row.get("banc", "v1"), row["cas"], row["condition"], row["rep"])] = row["note_manuelle"].strip()
    rows = []
    with open(OUT, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("status") != "ok":
                continue
            b = d.get("bench", "v1")
            k = (b, d["case"], d["condition"], str(d["rep"]))
            rows.append({"banc": b, "cas": d["case"], "condition": d["condition"], "rep": d["rep"],
                         "cause_attendue": d["truth"], "note_auto": d["auto_score"],
                         "note_stricte": score_strict(d["case"], d["response"], d["auto_score"]),
                         "note_manuelle": manual.get(k, ""),
                         "reponse": d["response"][:3000].replace("\r", "")})
    with open(CSV_OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                           ["banc", "cas", "condition", "rep", "cause_attendue", "note_auto",
                            "note_stricte", "note_manuelle", "reponse"])
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--conditions", default="agent,chat-claude")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--cases", default="", help="ids séparés par des virgules (défaut : tous)")
    ap.add_argument("--pause", type=int, default=10)
    ap.add_argument("--bench", default="v2", choices=sorted(set(BENCHES) | {"tiers"}))
    a = ap.parse_args()

    try:
        providers = get(a.api, "/api/ai/host-context").get("providers", [])
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Backend injoignable sur {a.api} : {e}")
    global NS
    if a.bench == "tiers" and "tiers" not in BENCHES:
        sys.exit(f"Banc du tiers introuvable : {TIERS_FILE}")
    NS = BENCHES[a.bench]["ns"]
    state, err = bench_state()
    if state is None:
        man = (BENCHES[a.bench].get("manifest") or
               f"k8s/benchmark/agent-benchmark{'' if a.bench == 'v1' else '-' + a.bench}.yaml")
        sys.exit(f"Banc introuvable ({err}).\n→ kubectl apply -f {man}, attendre 3 min, relancer.")
    print("État du banc :\n" + state + "\n")

    conds = [c.strip() for c in a.conditions.split(",") if c.strip()]
    for c in list(conds):
        if c not in CONDITIONS:
            sys.exit(f"Condition inconnue : {c}")
        if CONDITIONS[c]["needs"] not in providers:
            print(f"⚠ condition {c} ignorée : clé {CONDITIONS[c]['needs']} absente")
            conds.remove(c)
    wanted = {x.strip() for x in a.cases.split(",") if x.strip()}
    base = TIERS_CASES if a.bench == "tiers" else CASES
    cases = [c for c in base if not wanted or c[0] in wanted]
    already = done_keys()
    todo = [(c, cond, r) for r in range(1, a.reps + 1) for c in cases for cond in conds
            if (a.bench, c[0], cond, r) not in already]
    print(f"Campagne C2 : {len(cases)} cas × {len(conds)} conditions × {a.reps} rép. "
          f"→ {len(todo)} à faire ({len(already)} déjà mesurés)\n")
    os.makedirs(OBS, exist_ok=True)

    for i, (case, cond, rep) in enumerate(todo, 1):
        cid, _, truth, _ = case
        target = BENCHES[a.bench]["targets"][cid]
        cfg = CONDITIONS[cond]
        q = QUESTION.format(ns=NS, target=target)
        t0 = time.time()
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "bench": a.bench, "case": cid, "condition": cond,
               "rep": rep, "question": q, "truth": truth}
        message = q
        if cfg.get("obs"):
            obs = observation(NS)
            rec["observation"] = obs
            message = q + OBS_INTRO.format(ns=NS, obs=obs)
        try:
            r = post(a.api, cfg["path"], {"message": message, "history": [], **cfg["body"]})
            text = r.get("response", "")
            s, found = score(case, text)
            rec.update(status="ok", response=text, auto_score=s, matched=found,
                       strict_score=score_strict(cid, text, s),
                       mode=r.get("mode"), model=r.get("model_used"),
                       tools_used=r.get("tools_used", 0),
                       tools=[t.get("tool") for t in r.get("trace", [])],
                       iterations=r.get("iterations"), usage=r.get("usage"),
                       duration_s=round(time.time() - t0, 1))
            mark = {1.0: "✅", 0.5: "🟡", 0.0: "❌"}[rec["strict_score"]]
            print(f"[{i}/{len(todo)}] {mark} {cid:9} {cond:12} outils={rec['tools_used']:2} "
                  f"({rec['duration_s']} s)")
        except urllib.error.HTTPError as e:
            rec.update(status="error", error=f"HTTP {e.code} : {e.read()[:300].decode('utf-8', 'ignore')}")
            print(f"[{i}/{len(todo)}] ⚠ {cid} {cond} : {rec['error'][:120]}")
        except Exception as e:  # noqa: BLE001
            rec.update(status="error", error=str(e)[:300])
            print(f"[{i}/{len(todo)}] ⚠ {cid} {cond} : {rec['error'][:120]}")
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        time.sleep(a.pause)

    if os.path.exists(OUT):
        write_csv()
        print(f"\nRelecture manuelle : {CSV_OUT}")
    print("Rapport : python3 scripts/campaign_report.py")


load_tiers()

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scripts/enrich_corpus.py
================================================================================
Enrichissement du corpus de spécialisation, avec validation en boucle.

DIAGNOSTIC À L'ORIGINE DE CE SCRIPT
-----------------------------------
L'évaluation du modèle spécialisé établit que sa principale source d'échec est
le GROUPE D'API : il écrit `apiVersion: v1` pour des ressources exigeant
`networking.k8s.io/v1`, `rbac.authorization.k8s.io/v1` ou `batch/v1`.

Le corpus n'est pourtant pas fautif sur ce point — chaque ressource y porte le
bon groupe. Le problème est sa DISTRIBUTION :

    v1                             47 occurrences (42 %)
    apps/v1                        29
    networking.k8s.io/v1           14
    batch/v1                       12
    rbac.authorization.k8s.io/v1    7

Le modèle apprend une régularité statistique — « le plus souvent, c'est v1 » —
et l'applique par défaut aux ressources peu représentées. Role (7 exemples),
NetworkPolicy (5) et CronJob (5) reçoivent ainsi `v1`.

DEUX CORRECTIONS
----------------
1. Rééquilibrage : porter chaque type de ressource à un effectif cible, en
   priorité ceux dont le groupe d'API est long.

2. Validation en boucle : chaque paire générée est soumise à
   `kubectl apply --dry-run=server` AVANT d'entrer dans le corpus. Une paire
   invalide est régénérée, jusqu'à un nombre maximal de tentatives.

   C'est la correction méthodologique majeure. Le générateur initial ne
   validait rien : 14 des 112 paires du corpus d'origine sont inapplicables,
   et le modèle a été entraîné à reproduire ces erreurs.

RÉSULTAT SECONDAIRE
-------------------
Le taux de réussite du modèle enseignant, par type de ressource et par rang de
tentative, constitue une mesure de la fiabilité de la distillation. Cette
donnée n'est pas publiée à notre connaissance, et le script la rapporte.

USAGE
  python3 scripts/enrich_corpus.py --target 15 --dry-run   # plan sans generation
  python3 scripts/enrich_corpus.py --target 15
  python3 scripts/enrich_corpus.py --target 15 --max-attempts 4
================================================================================
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE         = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
CORPUS_IN    = os.path.join(PROJECT_ROOT, "data", "datasets", "kubernetes_pairs.jsonl")
CORPUS_OUT   = os.path.join(PROJECT_ROOT, "data", "datasets", "kubernetes_pairs_v2.jsonl")
OBS_DIR      = os.path.join(PROJECT_ROOT, "data", "observations")
REPORT       = os.path.join(OBS_DIR, "corpus_enrichment.json")
NAMESPACE    = "aikube-corpus-gen"

# llama-3.3-70b-versatile (modèle enseignant du corpus v1) a été retiré par Groq le 16/08/2026
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# Groupes d'API corrects, imposés dans l'invite. Le modèle enseignant se trompe
# notamment sur autoscaling : le corpus d'origine emploie autoscaling/v2beta2,
# retiré depuis Kubernetes 1.26.
API_GROUPS = {
    "Deployment": "apps/v1", "StatefulSet": "apps/v1", "DaemonSet": "apps/v1",
    "Service": "v1", "ConfigMap": "v1", "Secret": "v1",
    "PersistentVolume": "v1", "PersistentVolumeClaim": "v1",
    "ResourceQuota": "v1", "LimitRange": "v1", "ServiceAccount": "v1",
    "Ingress": "networking.k8s.io/v1", "NetworkPolicy": "networking.k8s.io/v1",
    "Job": "batch/v1", "CronJob": "batch/v1",
    "Role": "rbac.authorization.k8s.io/v1",
    "RoleBinding": "rbac.authorization.k8s.io/v1",
    "HorizontalPodAutoscaler": "autoscaling/v2",
    "PodDisruptionBudget": "policy/v1",
}

APPS = ["flask-api", "nginx", "redis", "postgres", "node-app", "ml-worker",
        "rabbitmq", "mongodb", "grafana", "prometheus", "kafka", "elasticsearch",
        "traefik", "memcached", "celery-worker", "api-gateway"]
NAMESPACES = ["default", "production", "staging", "monitoring", "data", "web"]


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSE DU CORPUS
# ══════════════════════════════════════════════════════════════════════════════

def load_corpus(path):
    pairs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    pairs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return pairs


def distribution(pairs):
    par_kind = Counter()
    par_api = Counter()
    for p in pairs:
        o = p.get("output")
        if isinstance(o, dict):
            par_kind[o.get("kind", "?")] += 1
            par_api[o.get("apiVersion", "?")] += 1
    return par_kind, par_api


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def validate(manifest, namespace):
    """
    Soumet le manifeste au serveur d'API. Le namespace n'est imposé que si le
    manifeste n'en déclare pas : forcer -n sur un objet en déclarant un
    provoque un refus sans rapport avec sa validité.
    """
    if not isinstance(manifest, dict):
        return False, "sortie non structurée"
    for champ in ("apiVersion", "kind", "metadata"):
        if champ not in manifest:
            return False, f"{champ} absent"
    if not (manifest.get("metadata") or {}).get("name"):
        return False, "metadata.name absent"

    declared = (manifest.get("metadata") or {}).get("namespace")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        json.dump(manifest, fh)
        path = fh.name
    try:
        cmd = ["kubectl", "apply", "--dry-run=server", "-f", path]
        if not declared:
            cmd += ["-n", namespace]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if r.returncode == 0:
            return True, None
        return False, re.sub(r"\s+", " ", (r.stderr or r.stdout).strip())[:200]
    except subprocess.TimeoutExpired:
        return False, "expiration kubectl"
    finally:
        os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# GÉNÉRATION
# ══════════════════════════════════════════════════════════════════════════════

def build_instruction(kind, app, ns, variante):
    """Descriptions variées pour éviter des instructions trop uniformes."""
    modeles = {
        "Deployment":   [f"Déployer {app} avec 3 répliques dans le namespace {ns}",
                         f"Créer un déploiement {app} avec des sondes de vivacité et de disponibilité",
                         f"Déployer {app} avec des limites de ressources définies"],
        "Service":      [f"Exposer {app} via un Service ClusterIP dans {ns}",
                         f"Créer un Service NodePort pour {app}",
                         f"Exposer {app} en interne sur le port 8080"],
        "Ingress":      [f"Créer un Ingress pour {app} sur le domaine {app}.example.com",
                         f"Router le trafic HTTPS vers {app} avec un Ingress",
                         f"Créer un Ingress avec règle de chemin /api vers {app}"],
        "NetworkPolicy":[f"Créer une NetworkPolicy refusant tout trafic dans {ns}",
                         f"Autoriser uniquement le trafic depuis {app} vers la base de données",
                         f"Isoler les pods de {ns} du reste du cluster"],
        "Role":         [f"Créer un rôle de lecture seule sur les pods dans {ns}",
                         f"Définir un rôle autorisant la gestion des ConfigMaps dans {ns}",
                         f"Créer un rôle permettant de lister les Services de {ns}"],
        "RoleBinding":  [f"Lier le compte de service {app} à un rôle de lecture dans {ns}",
                         f"Associer un rôle existant au compte de service de {app}"],
        "Job":          [f"Créer un job de migration de base de données pour {app}",
                         f"Lancer un job de traitement par lots pour {app}",
                         f"Créer un job de sauvegarde de {app}"],
        "CronJob":      [f"Planifier une sauvegarde quotidienne de {app} à 2h",
                         f"Créer un CronJob de nettoyage hebdomadaire pour {app}",
                         f"Planifier un rapport horaire pour {app}"],
        "ConfigMap":    [f"Créer un ConfigMap de configuration pour {app}",
                         f"Stocker les variables d'environnement de {app} dans un ConfigMap"],
        "Secret":       [f"Créer un Secret pour les identifiants de {app}",
                         f"Stocker la clé d'API de {app} dans un Secret"],
        "ResourceQuota":[f"Limiter les ressources du namespace {ns}",
                         f"Créer un quota de 10 pods et 4 Go de mémoire pour {ns}"],
        "StatefulSet":  [f"Déployer {app} en StatefulSet avec stockage persistant",
                         f"Créer un StatefulSet de 3 répliques pour {app}"],
        "DaemonSet":    [f"Déployer {app} sur tous les nœuds via un DaemonSet",
                         f"Créer un DaemonSet de collecte de journaux avec {app}"],
        "PersistentVolumeClaim": [f"Créer une demande de volume de 10 Go pour {app}",
                                  f"Provisionner un volume persistant pour {app} dans {ns}"],
        "HorizontalPodAutoscaler": [f"Créer un autoscaler pour {app} entre 2 et 10 répliques",
                                    f"Mettre à l'échelle {app} selon l'utilisation processeur"],
        "PodDisruptionBudget": [f"Garantir au moins 2 répliques de {app} disponibles",
                                f"Créer un budget de perturbation pour {app}"],
        "ServiceAccount": [f"Créer un compte de service pour {app} dans {ns}"],
        "LimitRange":   [f"Définir des limites par défaut pour les conteneurs de {ns}"],
    }
    choix = modeles.get(kind, [f"Créer un {kind} pour {app} dans le namespace {ns}"])
    return choix[variante % len(choix)]


async def generate_pair(client, kind, app, ns, variante, feedback=None):
    """
    Une paire instruction/manifeste.

    `feedback` transmet l'erreur de la tentative précédente : le modèle
    enseignant corrige souvent son erreur lorsqu'elle lui est signalée, ce qui
    améliore sensiblement le taux de réussite au second essai.
    """
    api = API_GROUPS.get(kind, "v1")
    instruction = build_instruction(kind, app, ns, variante)

    system = f"""Tu génères des exemples d'entraînement pour spécialiser un modèle sur Kubernetes.

CONTRAINTES ABSOLUES :
- La ressource doit être de type {kind}
- apiVersion doit valoir exactement : {api}
- Le manifeste doit être applicable sur Kubernetes 1.28 sans erreur
- N'emploie aucune version d'API dépréciée ni aucun champ inexistant
- metadata.name obligatoire, en minuscules avec tirets

Réponds UNIQUEMENT par un objet JSON, sans balises de code :
{{"instruction": "...", "input": "contexte du cluster", "output": {{ manifeste complet }}}}
Le champ output doit être un OBJET JSON, non une chaîne."""

    user = f"Génère un exemple pour : {instruction}"
    if feedback:
        user += (f"\n\nLa tentative précédente a été rejetée par l'API Kubernetes "
                 f"avec l'erreur suivante. Corrige-la :\n{feedback}")

    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=0.6, max_tokens=1200)

    contenu = resp.choices[0].message.content
    clean = re.sub(r"```json|```", "", contenu).strip()
    paire = json.loads(clean)

    # Le modèle renvoie parfois output sous forme de chaîne YAML
    if isinstance(paire.get("output"), str):
        import yaml
        paire["output"] = yaml.safe_load(paire["output"])

    return paire


# ══════════════════════════════════════════════════════════════════════════════

async def run(args):
    try:
        from groq import AsyncGroq
    except ImportError:
        print("✖ pip install groq")
        sys.exit(1)

    cle = os.environ.get("GROQ_API_KEY")
    if not cle:
        env = os.path.join(PROJECT_ROOT, ".env")
        if os.path.exists(env):
            for ligne in open(env, encoding="utf-8"):
                if ligne.startswith("GROQ_API_KEY"):
                    cle = ligne.split("=", 1)[1].strip().strip('"').strip("'")
    if not cle:
        print("✖ GROQ_API_KEY introuvable (variable d'environnement ou .env)")
        sys.exit(1)

    pairs = load_corpus(CORPUS_IN)
    par_kind, par_api = distribution(pairs)

    print(f"\n{'='*76}")
    print("  ENRICHISSEMENT DU CORPUS DE SPÉCIALISATION")
    print(f"{'='*76}")
    print(f"  Corpus actuel : {len(pairs)} paires")
    print(f"\n  Distribution par groupe d'API")
    total = sum(par_api.values())
    for a, n in par_api.most_common():
        print(f"    {n:>3}  ({n/total*100:>4.1f} %)  {a}")

    # ── Plan de génération ────────────────────────────────────────────────────
    # Seules les ressources DÉJÀ présentes dans le corpus sont renforcées.
    # Le diagnostic porte sur le déséquilibre entre groupes d'API, non sur un
    # défaut de couverture : introduire des ressources nouvelles en faible
    # nombre reproduirait le problème qu'on cherche à corriger.
    #
    # --include-new lève cette restriction si l'on souhaite étendre la
    # couverture, ce qui relève d'un autre objectif.
    manquants = {}
    for kind, api in API_GROUPS.items():
        actuel = par_kind.get(kind, 0)
        if actuel == 0 and not args.include_new:
            continue
        if actuel < args.target:
            manquants[kind] = args.target - actuel

    total_a_generer = sum(manquants.values())
    print(f"\n  Cible : {args.target} exemples par type de ressource")
    print(f"  À générer : {total_a_generer} paires\n")
    print(f"    {'ressource':<26} {'actuel':>7} {'manque':>7}  groupe d'API")
    for kind, n in sorted(manquants.items(), key=lambda x: -x[1]):
        print(f"    {kind:<26} {par_kind.get(kind,0):>7} {n:>7}  {API_GROUPS[kind]}")

    if args.dry_run:
        print(f"\n  (mode plan — aucune génération)\n")
        return

    # ── Namespaces de validation ──────────────────────────────────────────────
    subprocess.run(["kubectl", "create", "namespace", NAMESPACE],
                   capture_output=True, text=True, timeout=45)
    crees = []
    for ns in NAMESPACES:
        if ns == "default":
            continue
        r = subprocess.run(["kubectl", "create", "namespace", ns],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            crees.append(ns)

    client = AsyncGroq(api_key=cle)
    nouvelles = []
    stats = defaultdict(lambda: {"tentatives": 0, "reussites": 0, "par_rang": Counter()})
    echecs = []

    print(f"\n{'─'*76}")
    print("  GÉNÉRATION")
    print(f"{'─'*76}")

    for kind, nb in sorted(manquants.items(), key=lambda x: -x[1]):
        print(f"\n  {kind} ({API_GROUPS[kind]}) — {nb} à produire")
        obtenues = 0
        variante = 0
        while obtenues < nb:
            app = APPS[(variante * 3 + obtenues) % len(APPS)]
            ns  = NAMESPACES[(variante + obtenues) % len(NAMESPACES)]
            feedback = None
            succes = False

            for essai in range(1, args.max_attempts + 1):
                stats[kind]["tentatives"] += 1
                try:
                    paire = await generate_pair(client, kind, app, ns, variante, feedback)
                except Exception as e:
                    feedback = f"Erreur de génération : {str(e)[:120]}"
                    continue

                ok, err = validate(paire.get("output"), NAMESPACE)
                if ok:
                    nouvelles.append(paire)
                    stats[kind]["reussites"] += 1
                    stats[kind]["par_rang"][essai] += 1
                    obtenues += 1
                    succes = True
                    print(f"    [{obtenues}/{nb}] valide (essai {essai})")
                    break
                feedback = err

            if not succes:
                echecs.append({"kind": kind, "instruction": build_instruction(kind, app, ns, variante),
                               "derniere_erreur": feedback})
                print(f"    ✗ abandon après {args.max_attempts} essais — {(feedback or '')[:90]}")
                obtenues += 1     # on ne boucle pas indéfiniment
            variante += 1
            await asyncio.sleep(0.4)   # ménage la limite de débit de l'API

    for ns in [NAMESPACE] + crees:
        subprocess.run(["kubectl", "delete", "namespace", ns, "--wait=false"],
                       capture_output=True, text=True, timeout=30)

    # ── Corpus enrichi ────────────────────────────────────────────────────────
    # Les paires d'origine invalides sont écartées : le modèle ne doit pas
    # apprendre à reproduire des manifestes inapplicables.
    print(f"\n{'─'*76}")
    print("  VALIDATION DU CORPUS D'ORIGINE")
    print(f"{'─'*76}")
    subprocess.run(["kubectl", "create", "namespace", NAMESPACE],
                   capture_output=True, text=True, timeout=45)
    conserves, rejetes = [], []
    for p in pairs:
        ok, err = validate(p.get("output"), NAMESPACE)
        (conserves if ok else rejetes).append((p, err))
    subprocess.run(["kubectl", "delete", "namespace", NAMESPACE, "--wait=false"],
                   capture_output=True, text=True, timeout=30)
    print(f"  {len(conserves)}/{len(pairs)} paires d'origine conservées")
    print(f"  {len(rejetes)} écartées")

    final = [p for p, _ in conserves] + nouvelles
    os.makedirs(os.path.dirname(CORPUS_OUT), exist_ok=True)
    with open(CORPUS_OUT, "w", encoding="utf-8") as f:
        for p in final:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    # ── Rapport ───────────────────────────────────────────────────────────────
    nk, na = distribution(final)
    print(f"\n{'='*76}")
    print("  RÉSULTAT")
    print(f"{'='*76}")
    print(f"  Corpus d'origine : {len(pairs)}")
    print(f"  Conservées       : {len(conserves)}")
    print(f"  Générées         : {len(nouvelles)}")
    print(f"  Corpus final     : {len(final)}  →  {CORPUS_OUT}")

    print(f"\n  Nouvelle distribution par groupe d'API")
    tot = sum(na.values())
    for a, n in na.most_common():
        print(f"    {n:>3}  ({n/tot*100:>4.1f} %)  {a}")

    print(f"\n  Fiabilité du modèle enseignant, par ressource")
    print(f"    {'ressource':<26} {'essais':>7} {'valides':>8} {'taux':>7}  {'1er essai':>10}")
    for kind in sorted(stats, key=lambda k: -stats[k]["tentatives"]):
        s = stats[kind]
        taux = s["reussites"] / s["tentatives"] * 100 if s["tentatives"] else 0
        premier = s["par_rang"].get(1, 0)
        print(f"    {kind:<26} {s['tentatives']:>7} {s['reussites']:>8} "
              f"{taux:>6.1f} % {premier:>10}")

    tot_t = sum(s["tentatives"] for s in stats.values())
    tot_r = sum(s["reussites"] for s in stats.values())
    tot_1 = sum(s["par_rang"].get(1, 0) for s in stats.values())
    if tot_t:
        print(f"\n    Ensemble : {tot_r}/{tot_t} tentatives valides ({tot_r/tot_t*100:.1f} %)")
        print(f"    Dont valides au premier essai : {tot_1} ({tot_1/max(tot_r,1)*100:.1f} % des réussites)")
        print(f"\n    Le taux de réussite au premier essai mesure la fiabilité de la")
        print(f"    distillation sans validation — celle du corpus d'origine.")

    os.makedirs(OBS_DIR, exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "modele_enseignant": MODEL, "cible_par_ressource": args.target,
            "corpus_origine": len(pairs), "conservees": len(conserves),
            "rejetees": [{"kind": (p.get("output") or {}).get("kind"), "erreur": e}
                         for p, e in rejetes],
            "generees": len(nouvelles), "corpus_final": len(final),
            "distribution_finale": dict(na),
            "fiabilite_enseignant": {k: {"tentatives": v["tentatives"],
                                          "reussites": v["reussites"],
                                          "par_rang": dict(v["par_rang"])}
                                      for k, v in stats.items()},
            "echecs": echecs,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n✓ Rapport : {REPORT}\n")


def main():
    ap = argparse.ArgumentParser(description="Enrichissement du corpus")
    ap.add_argument("--target", type=int, default=15,
                    help="effectif cible par type de ressource")
    ap.add_argument("--max-attempts", type=int, default=3,
                    help="tentatives par paire avant abandon")
    ap.add_argument("--dry-run", action="store_true",
                    help="afficher le plan sans générer")
    ap.add_argument("--include-new", action="store_true",
                    help="inclure les ressources absentes du corpus "
                         "(étend la couverture, ne corrige pas le déséquilibre)")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()

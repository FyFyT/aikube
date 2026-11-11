"""
backend/ai_module/detection_comparator.py
================================================================================
C3 — Comparaison entre détection à base de règles et détection statistique.

OBJET SCIENTIFIQUE
------------------
Les deux approches de détection d'anomalies présentes dans AI-Kube n'avaient
jamais été confrontées sur des données réelles :

  · le moteur de RÈGLES EXPERTES, dérivé de la documentation Kubernetes,
    seul actif en production (endpoint /api/ai/detect) ;
  · le classifieur RANDOM FOREST, entraîné sur un générateur paramétrique,
    jusqu'ici évalué uniquement sur ses propres données synthétiques.

Ce module les applique aux MÊMES pods réels et mesure leur accord. C'est la
seule évaluation du projet dont le résultat ne soit pas prévisible par
construction : rien dans la conception des deux systèmes ne détermine à
l'avance où ils divergeront.

MÉTHODOLOGIE
------------
Le problème est formellement identique à celui de l'accord inter-annotateurs :
deux « juges » indépendants étiquettent les mêmes observations en binaire
(anomalie / normal). La métrique appropriée est le **kappa de Cohen**, qui
corrige l'accord observé de l'accord attendu par hasard :

        kappa = (p_o - p_e) / (1 - p_e)

Un accord brut élevé n'a aucune valeur si la classe majoritaire domine : sur un
cluster sain à 95 %, deux détecteurs qui répondent toujours « normal »
s'accordent à 95 % sans rien détecter. Le kappa neutralise cet effet.

Interprétation usuelle (Landis & Koch, 1977) :
    < 0.00  désaccord     0.01–0.20  très faible    0.21–0.40  faible
    0.41–0.60  modéré     0.61–0.80  fort           0.81–1.00  quasi parfait

JOURNALISATION
--------------
Chaque exécution est ajoutée à data/observations/detection_log.jsonl. Accumulée
sur plusieurs jours, cette série permet d'étudier la stabilité de l'accord et
d'analyser les désaccords récurrents — ceux qui portent une information, par
opposition aux divergences ponctuelles liées à un pod transitoire.

LIMITE ASSUMÉE
--------------
Aucune des deux méthodes n'est une vérité terrain. Ce module mesure un ACCORD,
pas une exactitude. Établir laquelle a raison exigerait un étiquetage manuel
indépendant des pods observés — travail proposé en perspectives.
================================================================================
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Chemin ancré sur l'emplacement de CE fichier, et non sur le répertoire
# courant : une exécution manuelle depuis backend/, un lancement par cron et
# l'appel via l'API uvicorn doivent tous écrire dans le MÊME journal, sinon la
# série d'observations se fragmente silencieusement.
#   ce fichier : <racine>/backend/ai_module/detection_comparator.py
#   racine     : deux niveaux au-dessus
_HERE         = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT  = os.path.abspath(os.path.join(_HERE, "..", ".."))
LOG_DIR       = os.path.join(PROJECT_ROOT, "data", "observations")
LOG_PATH      = os.path.join(LOG_DIR, "detection_log.jsonl")

# Seuils du moteur de règles — identiques à ceux de /api/ai/detect.
# Toute modification ici doit être répercutée dans ai_router.py, sinon la
# comparaison perd son sens.
RESTART_THRESHOLD = 3

_detector = None


def get_detector():
    """Chargement paresseux du Random Forest (désérialisation coûteuse)."""
    global _detector
    if _detector is None:
        from ai_module.anomaly_detector_ml import AnomalyDetectorML
        _detector = AnomalyDetectorML()
    return _detector


# ══════════════════════════════════════════════════════════════════════════════
# COLLECTE DES PODS RÉELS
# ══════════════════════════════════════════════════════════════════════════════

# Namespaces exclus par défaut de la comparaison « cluster réel ».
# Le banc d'évaluation ne doit PAS être mesuré comme s'il était du trafic
# ordinaire : ses pods sont défaillants par construction, ils fausseraient à la
# fois les taux de détection et l'interprétation des désaccords.
EXCLUDED_NAMESPACES = {"aikube-bench"}


def fetch_real_pods(exclude_namespaces: Optional[set] = None) -> List[Dict[str, Any]]:
    """
    Normalise la sortie de `kubectl get pods -A -o json` vers le format attendu
    par extract_features_from_pod().

    Les namespaces de EXCLUDED_NAMESPACES sont écartés : passer un ensemble
    vide permet de les réintégrer explicitement si besoin.
    """
    excluded = EXCLUDED_NAMESPACES if exclude_namespaces is None else exclude_namespaces
    result = subprocess.run(
        ["kubectl", "get", "pods", "-A", "-o", "json"],
        capture_output=True, text=True, timeout=25
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl a échoué : {result.stderr.strip()[:200]}")

    data = json.loads(result.stdout)
    pods = []

    for item in data.get("items", []):
        meta   = item.get("metadata", {})
        spec   = item.get("spec", {})
        status = item.get("status", {})

        if meta.get("namespace") in excluded:
            continue

        restarts = sum(
            cs.get("restartCount", 0)
            for cs in status.get("containerStatuses", []) or []
        )

        # ── Deux signaux absents du vecteur de features initial ───────────────
        # Ils ont été identifiés comme manquants par l'analyse du banc :
        #   · `ready`      : un pod Running mais jamais Ready ne rend aucun
        #                    service. Aucun détecteur v1/v2/ML ne pouvait le voir.
        #   · `started_at` : la date de démarrage du conteneur COURANT. Deux
        #                    pods à 2 redémarrages et même âge peuvent être l'un
        #                    stabilisé, l'autre en dégradation continue — seule
        #                    cette date les distingue.
        ready = None
        started_at = None
        waiting_reason = None
        for cs in status.get("containerStatuses", []) or []:
            w = ((cs.get("state") or {}).get("waiting") or {}).get("reason")
            if w:
                waiting_reason = w
        for cond in status.get("conditions", []) or []:
            if cond.get("type") == "Ready":
                ready = cond.get("status") == "True"
                break
        for cs in status.get("containerStatuses", []) or []:
            running = (cs.get("state") or {}).get("running") or {}
            if running.get("startedAt"):
                # Sur pod multi-conteneurs, on retient le démarrage le plus
                # récent : c'est le redémarrage le plus proche dans le temps.
                if started_at is None or running["startedAt"] > started_at:
                    started_at = running["startedAt"]

        pods.append({
            "name":       meta.get("name", ""),
            "namespace":  meta.get("namespace", "default"),
            "status":     status.get("phase", "Unknown"),
            "restarts":   restarts,
            "node":       spec.get("nodeName"),
            "ready":       ready,
            "started_at":  started_at,
            "waiting_reason": waiting_reason,
            "labels":     meta.get("labels", {}) or {},
            "age":        meta.get("creationTimestamp", ""),
            "containers": [c.get("name") for c in spec.get("containers", []) or []],
        })

    return pods


# ══════════════════════════════════════════════════════════════════════════════
# DÉTECTEUR 1 — RÈGLES EXPERTES
# ══════════════════════════════════════════════════════════════════════════════

def rules_predict(pod: Dict[str, Any]) -> Dict[str, Any]:
    """
    Réplique exacte de la logique de /api/ai/detect.

    Les trois conditions sont indépendantes et non pondérées : le moteur ne
    produit pas de score continu, seulement un verdict binaire assorti d'un
    motif. C'est une propriété importante pour la comparaison — le Random
    Forest, lui, produit une probabilité.
    """
    reasons = []

    if pod.get("restarts", 0) >= RESTART_THRESHOLD:
        reasons.append(f"CrashLoopBackOff ({pod['restarts']} redémarrages ≥ {RESTART_THRESHOLD})")

    if pod.get("status") == "Pending":
        reasons.append("Pod non planifié (Pending)")

    if pod.get("status") == "Failed":
        reasons.append("Pod terminé en échec (Failed)")

    return {
        "is_anomaly": len(reasons) > 0,
        "reasons":    reasons,
        "method":     "rules",
    }


# ══════════════════════════════════════════════════════════════════════════════
# DÉTECTEUR 2 — RÈGLES NORMALISÉES PAR LE TAUX (v2)
# ══════════════════════════════════════════════════════════════════════════════

# Un pod qui a redémarré 62 fois en 11 jours (0,23/h) est en fonctionnement
# nominal dégradé ; un pod qui a redémarré 3 fois en une heure (3,0/h) est en
# CrashLoopBackOff actif. Le compte ABSOLU ne distingue pas ces deux situations,
# le TAUX si. C'est l'hypothèse testée par ce second moteur de règles.
RESTART_RATE_THRESHOLD = 0.5        # redémarrages par heure ≈ 12 par jour

# Les composants du plan de contrôle redémarrent structurellement plus souvent
# (élections de leader, rotations de certificats, pression mémoire du nœud).
SYSTEM_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease"}
SYSTEM_RATE_MULTIPLIER = 4.0


def pod_age_hours(pod: Dict[str, Any]) -> Optional[float]:
    """Âge du pod en heures, ou None si l'horodatage est inexploitable."""
    age_str = (pod.get("age") or "").strip()
    if not age_str:
        return None
    try:
        created = datetime.fromisoformat(age_str.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max((datetime.now(timezone.utc) - created).total_seconds() / 3600.0, 1e-6)
    except (ValueError, TypeError):
        return None


def rules_v2_predict(pod: Dict[str, Any]) -> Dict[str, Any]:
    """
    Règles expertes révisées : le critère de redémarrage est normalisé par
    l'âge du pod, et le seuil est relâché pour les namespaces système.

    Les critères non ambigus (Pending, Failed, Running sans nœud) restent
    inchangés : ils ne dépendent pas de la durée d'observation.
    """
    reasons = []
    status  = pod.get("status")

    if status == "Pending":
        reasons.append("Pod non planifié (Pending)")
    if status == "Failed":
        reasons.append("Pod terminé en échec (Failed)")
    if status == "Running" and not pod.get("node"):
        reasons.append("Running sans nœud assigné")

    restarts = pod.get("restarts", 0)
    hours    = pod_age_hours(pod)

    if hours is None:
        # Âge inconnu : on se replie sur le seuil absolu plutôt que d'inventer
        # un taux, et on le signale explicitement.
        if restarts >= RESTART_THRESHOLD:
            reasons.append(f"{restarts} redémarrages (âge inconnu — repli sur seuil absolu)")
        rate = None
    else:
        rate = restarts / hours
        threshold = RESTART_RATE_THRESHOLD * (
            SYSTEM_RATE_MULTIPLIER if pod.get("namespace") in SYSTEM_NAMESPACES else 1.0
        )
        if rate > threshold:
            reasons.append(
                f"Taux de redémarrage {rate:.3f}/h > {threshold:.2f}/h "
                f"({restarts} redémarrages en {hours:.1f} h)"
            )

    return {
        "is_anomaly":   len(reasons) > 0,
        "reasons":      reasons,
        "restart_rate": round(rate, 4) if rate is not None else None,
        "age_hours":    round(hours, 2) if hours is not None else None,
        "method":       "rules_v2",
    }


# ══════════════════════════════════════════════════════════════════════════════
# DÉTECTEUR 3 — CRITÈRE DE STABILITÉ (v3)
# ══════════════════════════════════════════════════════════════════════════════
#
# CONSTRUIT À PARTIR DES ÉCHECS MESURÉS
# -------------------------------------
# Le banc a mis en évidence deux couples de pods aux signatures identiques mais
# aux vérités opposées :
#
#   edge-recovered        Running, 2 redémarrages, 5 min → SAIN
#   edge-slow-degradation Running, 2 redémarrages, 5 min → ANOMALIE
#
# Aucune combinaison de (statut, nombre de redémarrages, âge) ne peut les
# séparer : l'information nécessaire est ailleurs. Ce qui les distingue est le
# TEMPS ÉCOULÉ DEPUIS LE DERNIER REDÉMARRAGE — le premier tourne sans incident
# depuis 4 minutes, le second vient de redémarrer.
#
# Le critère devient donc : un pod n'est pas anormal parce qu'il A redémarré,
# mais parce qu'il redémarre ENCORE. Le passé compte moins que la tendance.
#
# Second apport : la condition Ready. Un pod Running dont la sonde de
# disponibilité échoue indéfiniment ne rend aucun service, sans jamais
# redémarrer (edge-never-ready). Les trois détecteurs précédents y étaient
# structurellement aveugles.
#
# ⚠ AVERTISSEMENT MÉTHODOLOGIQUE — À REPORTER DANS LE MÉMOIRE
# -----------------------------------------------------------
# Ce détecteur a été conçu APRÈS observation des erreurs sur le banc étendu.
# Ses bons résultats sur CE banc sont donc partiellement circulaires : c'est du
# surajustement au jeu de développement, exactement le défaut reproché à
# KubeBench. Les scores de v3 sur le banc étendu ne doivent PAS être présentés
# comme une validation.
#
# Validation honnête possible :
#   · exécution sur le cluster réel, non utilisé pour la conception ;
#   · construction d'un troisième banc, avec des cas non anticipés ;
#   · au minimum, mention explicite de la limite.
# ══════════════════════════════════════════════════════════════════════════════

STABILITY_WINDOW_SECONDS = 120.0   # durée sans incident valant stabilisation
READY_GRACE_SECONDS      = 120.0   # tolérance avant d'exiger Ready


def seconds_since(timestamp: Optional[str]) -> Optional[float]:
    """Secondes écoulées depuis un horodatage ISO Kubernetes."""
    if not timestamp:
        return None
    try:
        t = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return max((datetime.now(timezone.utc) - t).total_seconds(), 0.0)
    except (ValueError, TypeError):
        return None


def rules_v3_predict(pod: Dict[str, Any]) -> Dict[str, Any]:
    """
    Détection fondée sur la stabilité courante plutôt que sur l'historique.
    """
    reasons = []
    status  = pod.get("status")

    # Une tâche achevée n'est pas une anomalie : c'est son déroulement normal.
    if status == "Succeeded":
        return {"is_anomaly": False, "reasons": [], "method": "rules_v3",
                "uptime_seconds": None, "stabilised": True}

    if status == "Failed":
        reasons.append("Pod terminé en échec (Failed)")
    if status == "Pending":
        reasons.append("Pod bloqué avant démarrage (Pending)")
    if status == "Running" and not pod.get("node"):
        reasons.append("Running sans nœud assigné")

    uptime     = seconds_since(pod.get("started_at"))
    restarts   = pod.get("restarts", 0)
    age_h      = pod_age_hours(pod)
    stabilised = None

    if status == "Running":
        # Redémarrages : seule compte la stabilité actuelle.
        if restarts > 0:
            if uptime is None:
                # Aucun conteneur en cours d'exécution malgré la phase Running :
                # le pod est entre deux redémarrages.
                reasons.append(f"{restarts} redémarrages, aucun conteneur actif")
                stabilised = False
            elif uptime < STABILITY_WINDOW_SECONDS:
                reasons.append(
                    f"Redémarrage il y a {uptime:.0f}s "
                    f"(< {STABILITY_WINDOW_SECONDS:.0f}s) — instabilité en cours, "
                    f"{restarts} au total"
                )
                stabilised = False
            else:
                stabilised = True

        # Disponibilité : un pod jamais prêt ne rend aucun service.
        ready = pod.get("ready")
        if ready is False and age_h is not None and age_h * 3600 > READY_GRACE_SECONDS:
            reasons.append(
                f"Non disponible depuis {age_h * 3600:.0f}s "
                f"(> {READY_GRACE_SECONDS:.0f}s) sans redémarrer"
            )

    return {
        "is_anomaly":     len(reasons) > 0,
        "reasons":        reasons,
        "uptime_seconds": round(uptime, 1) if uptime is not None else None,
        "stabilised":     stabilised,
        "method":         "rules_v3",
    }


# ══════════════════════════════════════════════════════════════════════════════
# DÉTECTEUR 4 — RANDOM FOREST
# ══════════════════════════════════════════════════════════════════════════════

def ml_predict(pod: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prédiction du Random Forest, accompagnée des variables les plus
    contributives (feature_importances_ du modèle, filtrées à >0.05).

    C'est cette attribution d'importance qui constitue la composante
    explicable du système — et non l'injection de contexte dans les prompts,
    initialement étiquetée « XAI ».
    """
    try:
        detector = get_detector()
        result   = detector.predict_pod(pod)
        if "error" in result:
            return {"is_anomaly": None, "probability": None, "error": result["error"], "method": "random_forest"}
        return {
            "is_anomaly":  bool(result["is_anomaly"]),
            "probability": result["probability"],
            "confidence":  result.get("confidence"),
            "explanation": result.get("explanation", []),
            "method":      "random_forest",
        }
    except Exception as e:
        return {"is_anomaly": None, "probability": None, "error": str(e), "method": "random_forest"}


# ══════════════════════════════════════════════════════════════════════════════
# MÉTRIQUES D'ACCORD
# ══════════════════════════════════════════════════════════════════════════════

def kappa_status(a: List[int], b: List[int]) -> Optional[str]:
    """
    Diagnostic préalable : le kappa est-il interprétable sur ces deux vecteurs ?

    Le kappa suppose que les deux juges répartissent les observations entre
    PLUSIEURS classes. Si l'un d'eux répond toujours la même chose, l'accord
    attendu par hasard égale l'accord observé et le kappa vaut mécaniquement 0
    — quel que soit l'accord réel. Sur nos mesures, des accords de 0 %, 12,5 %
    et 87,5 % produisaient tous « kappa = 0.0 », ce qui n'informe sur rien et
    laisse croire à une erreur de calcul.

    Retourne None si le kappa est exploitable, sinon la raison de sa
    non-interprétabilité.
    """
    if not a or not b:
        return "aucune observation"
    ca, cb = len(set(a)), len(set(b))
    if ca == 1 and cb == 1:
        return ("les deux détecteurs sont constants et concordants"
                if a[0] == b[0] else
                "les deux détecteurs sont constants et opposés")
    if ca == 1:
        return "le premier détecteur ne produit qu'une seule classe"
    if cb == 1:
        return "le second détecteur ne produit qu'une seule classe"
    return None


def cohen_kappa(rules_labels: List[int], ml_labels: List[int]) -> Optional[float]:
    """
    Kappa de Cohen, calculé sans dépendance externe pour rester vérifiable à la
    main dans le mémoire. Vérifié identique à sklearn.metrics.cohen_kappa_score.

    Retourne None dès que le calcul n'est pas interprétable (cf. kappa_status),
    plutôt qu'un 0.0 trompeur.
    """
    n = len(rules_labels)
    if n == 0:
        return None

    # Cas dégénérés : un juge constant rend le kappa non informatif.
    if kappa_status(rules_labels, ml_labels) is not None:
        return None

    # Accord observé
    agree = sum(1 for a, b in zip(rules_labels, ml_labels) if a == b)
    p_o = agree / n

    # Accord attendu par hasard : produit des marginales, sommé sur les classes
    p_e = 0.0
    for cls in (0, 1):
        p_rules = sum(1 for a in rules_labels if a == cls) / n
        p_ml    = sum(1 for b in ml_labels    if b == cls) / n
        p_e += p_rules * p_ml

    if abs(1.0 - p_e) < 1e-12:
        return None

    return round((p_o - p_e) / (1.0 - p_e), 4)


def interpret_kappa(k: Optional[float]) -> str:
    """Échelle de Landis & Koch (1977)."""
    if k is None:
        return "indéfini (une seule classe observée)"
    if k < 0:    return "désaccord systématique"
    if k <= 0.20: return "très faible"
    if k <= 0.40: return "faible"
    if k <= 0.60: return "modéré"
    if k <= 0.80: return "fort"
    return "quasi parfait"


# ══════════════════════════════════════════════════════════════════════════════
# COMPARAISON COMPLÈTE
# ══════════════════════════════════════════════════════════════════════════════

def compare(pods: Optional[List[Dict[str, Any]]] = None, log: bool = True) -> Dict[str, Any]:
    """
    Applique les TROIS détecteurs aux mêmes pods et mesure les accords deux à
    deux.

    Le kappa est calculé pour chaque paire :
        rules_v1 / rules_v2   → effet de la normalisation par le taux
        rules_v1 / ml         → comparaison initiale
        rules_v2 / ml         → comparaison après correction du biais

    Aucune de ces mesures n'est une exactitude : il n'existe pas de vérité
    terrain. Ce sont des accords entre détecteurs.
    """
    if pods is None:
        pods = fetch_real_pods()

    observations = []
    v1_labels, v2_labels, v3_labels, ml_labels = [], [], [], []
    ml_unavailable = 0
    age_unknown    = 0

    for pod in pods:
        r1 = rules_predict(pod)
        r2 = rules_v2_predict(pod)
        r3 = rules_v3_predict(pod)
        m  = ml_predict(pod)

        if m["is_anomaly"] is None:
            ml_unavailable += 1
            continue
        if r2["age_hours"] is None:
            age_unknown += 1

        l1 = 1 if r1["is_anomaly"] else 0
        l2 = 1 if r2["is_anomaly"] else 0
        l3 = 1 if r3["is_anomaly"] else 0
        lm = 1 if m["is_anomaly"] else 0
        v1_labels.append(l1)
        v2_labels.append(l2)
        v3_labels.append(l3)
        ml_labels.append(lm)

        observations.append({
            "pod":            pod["name"],
            "namespace":      pod["namespace"],
            "status":         pod["status"],
            "restarts":       pod["restarts"],
            "node":           pod["node"],
            "age_hours":      r2["age_hours"],
            "restart_rate":   r2["restart_rate"],
            "uptime_seconds": r3["uptime_seconds"],
            "ready":          pod.get("ready"),
            "rules_v1":       l1,
            "rules_v2":       l2,
            "rules_v3":       l3,
            "ml":             lm,
            "ml_probability": m["probability"],
            "unanimous":      l1 == l2 == l3 == lm,
            "rules_v1_reasons": r1["reasons"],
            "rules_v2_reasons": r2["reasons"],
            "rules_v3_reasons": r3["reasons"],
            "ml_explanation":   m.get("explanation", []),
        })

    n = len(observations)

    def pair_stats(a: List[int], b: List[int], name_a: str, name_b: str) -> Dict[str, Any]:
        both1 = sum(1 for x, y in zip(a, b) if x == 1 and y == 1)
        both0 = sum(1 for x, y in zip(a, b) if x == 0 and y == 0)
        only_a = sum(1 for x, y in zip(a, b) if x == 1 and y == 0)
        only_b = sum(1 for x, y in zip(a, b) if x == 0 and y == 1)
        k      = cohen_kappa(a, b)
        status = kappa_status(a, b)
        return {
            "pair":           f"{name_a} / {name_b}",
            "kappa_undefined_reason": status,
            "both_anomaly":   both1,
            "both_normal":    both0,
            f"{name_a}_only": only_a,
            f"{name_b}_only": only_b,
            "raw_percent":    round((both1 + both0) / n * 100, 2) if n else 0.0,
            "cohen_kappa":    k,
            "interpretation": status if status else interpret_kappa(k),
        }

    comparisons = {
        "v1_vs_v2": pair_stats(v1_labels, v2_labels, "rules_v1", "rules_v2"),
        "v1_vs_v3": pair_stats(v1_labels, v3_labels, "rules_v1", "rules_v3"),
        "v2_vs_v3": pair_stats(v2_labels, v3_labels, "rules_v2", "rules_v3"),
        "v1_vs_ml": pair_stats(v1_labels, ml_labels, "rules_v1", "ml"),
        "v2_vs_ml": pair_stats(v2_labels, ml_labels, "rules_v2", "ml"),
        "v3_vs_ml": pair_stats(v3_labels, ml_labels, "rules_v3", "ml"),
    }

    disagreements = [o for o in observations if not o["unanimous"]]

    report = {
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "pods_analysed":  n,
        "ml_unavailable": ml_unavailable,
        "age_unknown":    age_unknown,
        "detections": {
            "rules_v1": sum(v1_labels),
            "rules_v2": sum(v2_labels),
            "rules_v3": sum(v3_labels),
            "ml":       sum(ml_labels),
        },
        "comparisons":        comparisons,
        "disagreements":      disagreements,
        "disagreement_count": len(disagreements),
        "observations":       observations,
    }

    if log:
        append_log(report)

    return report


def append_log(report: Dict[str, Any]) -> None:
    """
    Ajoute une entrée compacte au journal (sans le détail des observations,
    qui ferait grossir le fichier inutilement). Les désaccords sont conservés :
    ce sont eux qui portent l'information.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        entry = {
            "timestamp":     report["timestamp"],
            "pods_analysed": report["pods_analysed"],
            "age_unknown":   report["age_unknown"],
            "detections":    report["detections"],
            "comparisons":   report["comparisons"],
            "disagreements": [
                {
                    "pod":       d["pod"],
                    "namespace": d["namespace"],
                    "status":    d["status"],
                    "restarts":  d["restarts"],
                    "age_hours": d["age_hours"],
                    "restart_rate": d["restart_rate"],
                    "rules_v1":  d["rules_v1"],
                    "rules_v2":  d["rules_v2"],
                    "rules_v3":  d["rules_v3"],
                    "ml":        d["ml"],
                    "ml_probability": d["ml_probability"],
                }
                for d in report["disagreements"]
            ],
        }
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️ Journalisation impossible : {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSE DE LA SÉRIE ACCUMULÉE
# ══════════════════════════════════════════════════════════════════════════════

def analyse_log() -> Dict[str, Any]:
    """
    Agrège le journal accumulé. À exécuter après plusieurs jours de collecte
    pour produire les chiffres du mémoire.

    Le point d'intérêt est la RÉCURRENCE : un pod en désaccord une seule fois
    reflète un état transitoire ; un pod en désaccord à chaque observation
    révèle une divergence structurelle entre les deux approches.
    """
    if not os.path.exists(LOG_PATH):
        return {"error": "Aucun journal — exécuter compare() au moins une fois."}

    entries = []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    e = json.loads(line)
                    if "comparisons" in e:          # ignore les entrées v1 du journal
                        entries.append(e)
                except json.JSONDecodeError:
                    continue

    if not entries:
        return {"error": "Journal vide ou ancien format — relancer compare()."}

    def kappa_series(pair_key: str) -> Dict[str, Any]:
        # Même précaution : les paires impliquant v3 sont absentes des entrées
        # antérieures à son introduction.
        ks = [e["comparisons"][pair_key]["cohen_kappa"] for e in entries
              if pair_key in e.get("comparisons", {})
              and e["comparisons"][pair_key].get("cohen_kappa") is not None]
        if not ks:
            return {"mean": None, "min": None, "max": None, "stdev": None,
                    "interpretation": "indéfini sur toute la série",
                    "defined_observations": 0}
        mu = sum(ks) / len(ks)
        sd = None
        if len(ks) > 1:
            sd = round((sum((k - mu) ** 2 for k in ks) / (len(ks) - 1)) ** 0.5, 4)
        return {
            "mean":  round(mu, 4),
            "min":   round(min(ks), 4),
            "max":   round(max(ks), 4),
            "stdev": sd,
            "interpretation": interpret_kappa(round(mu, 4)),
            "defined_observations": len(ks),
        }

    # Récurrence des désaccords par pod
    recurrence: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        for d in e.get("disagreements", []):
            key = f"{d['namespace']}/{d['pod']}"
            if key not in recurrence:
                recurrence[key] = {
                    "occurrences": 0, "last_status": d["status"],
                    "last_restarts": d["restarts"], "last_rate": d.get("restart_rate"),
                    "pattern": {},
                }
            rec = recurrence[key]
            rec["occurrences"] += 1
            sig = (f"v1={d['rules_v1']} v2={d['rules_v2']} "
                   f"v3={d.get('rules_v3','?')} ml={d['ml']}")
            rec["pattern"][sig] = rec["pattern"].get(sig, 0) + 1
            rec["last_status"]   = d["status"]
            rec["last_restarts"] = d["restarts"]
            rec["last_rate"]     = d.get("restart_rate")

    ranked = sorted(recurrence.items(), key=lambda kv: kv[1]["occurrences"], reverse=True)

    def mean_det(k):
        """
        Moyenne calculée sur les seules entrées contenant la clé.

        Le journal s'étend sur plusieurs versions du comparateur : les entrées
        antérieures à l'introduction du détecteur v3 n'en portent pas trace.
        Les ignorer plutôt que d'échouer permet d'exploiter l'historique
        complet, en signalant sur combien d'entrées chaque moyenne repose.
        """
        vals = [e["detections"][k] for e in entries
                if isinstance(e.get("detections"), dict) and k in e["detections"]]
        if not vals:
            return None
        return {"mean": round(sum(vals) / len(vals), 2),
                "min": min(vals), "max": max(vals), "observations": len(vals)}

    return {
        "observations_count": len(entries),
        "period": {
            "from": entries[0]["timestamp"],
            "to":   entries[-1]["timestamp"],
        },
        "mean_detections": {
            k: mean_det(k) for k in ("rules_v1", "rules_v2", "rules_v3", "ml")
        },
        "kappa": {
            key: kappa_series(key)
            for key in ("v1_vs_v2", "v1_vs_v3", "v2_vs_v3",
                        "v1_vs_ml", "v2_vs_ml", "v3_vs_ml")
        },
        "recurrent_disagreements": [
            {"pod": k, **v} for k, v in ranked[:20]
        ],
        "unique_pods_in_disagreement": len(recurrence),
    }


# ══════════════════════════════════════════════════════════════════════════════
# EXÉCUTION EN LIGNE DE COMMANDE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "analyse":
        print(json.dumps(analyse_log(), indent=2, ensure_ascii=False))
    else:
        rep = compare()
        print(f"\n{'='*74}")
        print("  COMPARAISON DES DÉTECTEURS D'ANOMALIES")
        print(f"{'='*74}")
        print(f"  Pods analysés            : {rep['pods_analysed']}")
        if rep["age_unknown"]:
            print(f"  Âge inexploitable        : {rep['age_unknown']}  ⚠")
        print(f"\n  Détections")
        print(f"    Règles v1 (seuil absolu) : {rep['detections']['rules_v1']}")
        print(f"    Règles v2 (taux/heure)   : {rep['detections']['rules_v2']}")
        print(f"    Règles v3 (stabilité)    : {rep['detections']['rules_v3']}")
        print(f"    Random Forest            : {rep['detections']['ml']}")

        print(f"\n  Accords deux à deux")
        for key in ("v1_vs_v2", "v1_vs_v3", "v2_vs_v3",
                    "v1_vs_ml", "v2_vs_ml", "v3_vs_ml"):
            c = rep["comparisons"][key]
            kv = f"{c['cohen_kappa']:.4f}" if c["cohen_kappa"] is not None else "  n/d"
            print(f"    {c['pair']:24} accord {c['raw_percent']:6.2f} %  "
                  f"kappa {kv:>7}  ({c['interpretation']})")

        print(f"\n  Désaccords (non unanimes) : {rep['disagreement_count']}")
        for d in rep["disagreements"][:15]:
            rate = f"{d['restart_rate']:.3f}/h" if d["restart_rate"] is not None else "n/a"
            age  = f"{d['age_hours']:.1f} h"    if d["age_hours"]    is not None else "n/a"
            print(f"    {d['namespace']}/{d['pod']}")
            print(f"      statut={d['status']} restarts={d['restarts']} âge={age} taux={rate}")
            up = f"{d['uptime_seconds']:.0f}s" if d.get("uptime_seconds") is not None else "n/a"
            print(f"      v1={d['rules_v1']} v2={d['rules_v2']} "
                  f"v3={d['rules_v3']} ml={d['ml']} (p={d['ml_probability']}, "
                  f"uptime={up}, ready={d.get('ready')})")
        print(f"{'='*74}\n")

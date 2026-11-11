"""
Scanner de configuration Kubernetes et catalogue de correctifs.

Requalification (chapitre 7) : détecter `privileged: true` n'est pas une
contribution — Kubescape, Trivy, Polaris et kube-bench le font. Ce module en
fournit une version lisible et vérifiable pour l'application ; la contribution
porte sur la REMÉDIATION (remediation.py) : appliquer un correctif, vérifier
qu'il ne casse rien, revenir en arrière sinon, et mesurer le taux de réussite.

Changements par rapport à l'ancien scanner (security_router.py, conservé) :
  * analyse au niveau du WORKLOAD (Deployment, StatefulSet…) et non du pod :
    c'est ce qu'on peut corriger, et cela évite de compter N fois N répliques ;
  * 7 angles morts comblés : capabilities, allowPrivilegeEscalation, hostPID /
    hostIPC, hostPath, exécution en root malgré un securityContext présent,
    RBAC trop large, jeton de compte de service exposé ;
  * faux positifs corrigés : « API_URL » ou « MONKEY_COUNT » ne sont plus pris
    pour des secrets (l'ancien test cherchait la sous-chaîne « KEY ») ;
  * score de risque monotone (max CVSS + accumulation), conformité exprimée en
    pourcentage de workloads, donc indépendante de la taille du cluster.

Correspondances MITRE ATT&CK for Containers : T1611 Escape to Host, T1613
Container and Resource Discovery, T1046 Network Service Discovery, T1552
Unsecured Credentials, T1528 Steal Application Access Token, T1078 Valid
Accounts. L'absence de NetworkPolicy relève du durcissement, sans technique
Containers correspondante (Kubescape exclut lui-même ce contrôle de son
référentiel MITRE).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from typing import Any, Optional

SYSTEM_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease",
                     "kubernetes-dashboard", "local-path-storage", "ingress-nginx"}
CONTROLLER_KINDS = ("Deployment", "StatefulSet", "DaemonSet")
POWERFUL_ROLES = {"cluster-admin": "critical", "admin": "warning", "edit": "warning"}

CHECKS: dict[str, dict] = {
    "privileged": {
        "title": "Conteneur privilégié", "severity": "critical", "cvss": 9.0,
        "technique": "T1611 — Escape to Host", "tactic": "Privilege Escalation",
        "why": "privileged: true donne toutes les capacités du noyau et l'accès aux périphériques "
               "de l'hôte : sortir du conteneur est trivial.",
        "fix": "auto", "fix_desc": "privileged: false"},
    "dangerous_capability": {
        "title": "Capacité Linux dangereuse", "severity": "critical", "cvss": 8.5,
        "technique": "T1611 — Escape to Host", "tactic": "Privilege Escalation",
        "why": "SYS_ADMIN, NET_ADMIN, SYS_PTRACE… équivalent presque à un conteneur privilégié.",
        "fix": "auto", "fix_desc": "capabilities: retirer les ajouts, drop: [ALL]"},
    "host_pid_ipc": {
        "title": "Espace de processus de l'hôte partagé", "severity": "critical", "cvss": 8.0,
        "technique": "T1613 — Container and Resource Discovery", "tactic": "Discovery",
        "why": "hostPID / hostIPC rendent visibles (et souvent manipulables) les processus de l'hôte.",
        "fix": "auto", "fix_desc": "hostPID: false, hostIPC: false"},
    "host_network": {
        "title": "Réseau de l'hôte", "severity": "critical", "cvss": 8.0,
        "technique": "T1046 — Network Service Discovery", "tactic": "Discovery",
        "why": "hostNetwork expose toutes les interfaces de l'hôte : découverte de services, "
               "écoute du trafic local.",
        "fix": "auto", "fix_desc": "hostNetwork: false (peut casser un service qui écoute sur l'hôte)"},
    "host_path_sensitive": {
        "title": "Montage d'un chemin sensible de l'hôte", "severity": "critical", "cvss": 9.0,
        "technique": "T1611 — Escape to Host", "tactic": "Privilege Escalation",
        "why": "Monter /, /etc, /var/run/docker.sock… revient à donner la main sur la machine.",
        "fix": "ai", "fix_desc": "remplacer par emptyDir, ConfigMap ou PVC selon l'usage"},
    "host_path": {
        "title": "Montage hostPath", "severity": "warning", "cvss": 6.0,
        "technique": "T1611 — Escape to Host", "tactic": "Privilege Escalation",
        "why": "Le conteneur lit ou écrit directement sur le disque du nœud.",
        "fix": "ai", "fix_desc": "remplacer par emptyDir ou PVC"},
    "secret_in_env": {
        "title": "Secret en clair dans l'environnement", "severity": "critical", "cvss": 8.5,
        "technique": "T1552 — Unsecured Credentials", "tactic": "Credential Access",
        "why": "La valeur est lisible par quiconque peut décrire le workload ou lire etcd.",
        "fix": "auto", "fix_desc": "déplacer la valeur dans un Secret et la référencer (secretKeyRef)"},
    "privilege_escalation": {
        "title": "Escalade de privilèges autorisée", "severity": "warning", "cvss": 6.0,
        "technique": "T1611 — Escape to Host", "tactic": "Privilege Escalation",
        "why": "Sans allowPrivilegeEscalation: false, un binaire setuid peut obtenir plus de "
               "droits que le processus parent.",
        "fix": "auto", "fix_desc": "allowPrivilegeEscalation: false"},
    "run_as_root": {
        "title": "Exécution en root", "severity": "warning", "cvss": 7.0,
        "technique": "T1611 — Escape to Host", "tactic": "Privilege Escalation",
        "why": "Un processus root dans le conteneur aggrave toute faille d'isolation.",
        "fix": "auto", "fix_desc": "runAsNonRoot: true, runAsUser: 10001 (peut casser une image "
                                  "qui exige root)"},
    "sa_token_privileged": {
        "title": "Jeton de compte de service puissant monté", "severity": "warning", "cvss": 7.5,
        "technique": "T1528 — Steal Application Access Token", "tactic": "Credential Access",
        "why": "Le jeton monté dans le pod donne des droits étendus sur l'API à quiconque "
               "compromet le conteneur.",
        "fix": "auto", "fix_desc": "automountServiceAccountToken: false"},
    "rbac_permissive": {
        "title": "Droits RBAC trop larges", "severity": "warning", "cvss": 7.0,
        "technique": "T1078 — Valid Accounts", "tactic": "Privilege Escalation",
        "why": "Un compte non système reçoit un rôle très large (cluster-admin, admin, edit, ou *).",
        "fix": "manual", "fix_desc": "remplacer par un Role limité aux ressources nécessaires"},
    "no_network_policy": {
        "title": "Namespace sans NetworkPolicy", "severity": "warning", "cvss": 6.5,
        "technique": "Hors ATT&CK Containers (durcissement)", "tactic": "Durcissement",
        "why": "Tout pod du namespace est joignable depuis tout le cluster : mouvement latéral libre.",
        "fix": "auto", "fix_desc": "NetworkPolicy « default-deny » sur le trafic entrant "
                                   "(vérifier que le CNI applique les NetworkPolicies)"},
    "no_readonly_rootfs": {
        "title": "Système de fichiers racine modifiable", "severity": "info", "cvss": 3.0,
        "technique": "Durcissement", "tactic": "Durcissement",
        "why": "Un attaquant peut déposer des fichiers dans le conteneur.",
        "fix": "ai", "fix_desc": "readOnlyRootFilesystem: true + emptyDir pour les répertoires écrits"},
    "caps_not_dropped": {
        "title": "Capacités par défaut conservées", "severity": "info", "cvss": 3.0,
        "technique": "Durcissement", "tactic": "Durcissement",
        "why": "Les capacités par défaut (NET_RAW…) restent disponibles.",
        "fix": "auto", "fix_desc": "capabilities.drop: [ALL]"},
    "no_limits": {
        "title": "Aucune limite de ressources", "severity": "info", "cvss": 3.0,
        "technique": "Disponibilité", "tactic": "Disponibilité",
        "why": "Un conteneur emballé peut épuiser le nœud (voir la page Ressources).",
        "fix": "manual", "fix_desc": "définir requests/limits (page Ressources & reco.)"},
    "image_latest": {
        "title": "Image sans version précise", "severity": "info", "cvss": 2.0,
        "technique": "Chaîne d'approvisionnement", "tactic": "Durcissement",
        "why": ":latest ou aucun tag : le contenu déployé peut changer sans que le manifeste change.",
        "fix": "manual", "fix_desc": "épingler un tag ou un digest"},
}

DANGEROUS_CAPS = {"SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "SYS_MODULE", "DAC_READ_SEARCH",
                  "DAC_OVERRIDE", "SYS_RAWIO", "SYS_BOOT", "BPF", "PERFMON", "ALL"}
SENSITIVE_PATHS = ["/etc", "/root", "/proc", "/sys", "/dev", "/boot", "/home",
                   "/var/run/docker.sock", "/run/containerd", "/var/run/containerd",
                   "/var/lib/kubelet", "/var/lib/docker", "/var/lib/etcd", "/etc/kubernetes"]
SECRET_WORDS = {"PASSWORD", "PASSWD", "PWD", "SECRET", "TOKEN", "APIKEY", "PRIVATEKEY",
                "CREDENTIAL", "CREDENTIALS", "ACCESSKEY", "SECRETKEY", "AUTHKEY"}
BENIGN_SUFFIX = {"URL", "URI", "HOST", "PORT", "ENDPOINT", "PATH", "FILE", "DIR", "ALGORITHM",
                 "NAME", "USER", "USERNAME", "COUNT", "ENABLED", "TYPE", "LENGTH", "TTL",
                 "TIMEOUT", "VERSION", "FORMAT", "HEADER", "EXPIRY", "EXPIRATION", "REF"}
SECRET_VALUE_RE = re.compile(
    r"(gsk_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|"
    r"xox[abp]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY)")


# ── Lecture du cluster ──────────────────────────────────────────────────────

def _kubectl_json(args: list[str], timeout: int = 30) -> dict:
    r = subprocess.run(["kubectl"] + args + ["-o", "json"], capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return json.loads(r.stdout)


def _safe_items(args: list[str]) -> list[dict]:
    try:
        return _kubectl_json(args).get("items", [])
    except Exception:  # noqa: BLE001
        return []


def is_secret_env(name: str, value: Any) -> bool:
    if value in (None, "") or not isinstance(value, str):
        return False
    if SECRET_VALUE_RE.search(value):
        return True
    tokens = [t for t in re.split(r"[_\-.]", name.upper()) if t]
    if not tokens or tokens[-1] in BENIGN_SUFFIX:
        return False
    words = set(tokens) | {a + b for a, b in zip(tokens, tokens[1:])}
    return bool(words & SECRET_WORDS) and not value.startswith("$(")


def _pod_spec_of(kind: str, obj: dict) -> Optional[dict]:
    spec = obj.get("spec", {})
    if kind == "Pod":
        return spec
    if kind == "CronJob":
        return ((spec.get("jobTemplate") or {}).get("spec") or {}).get("template", {}).get("spec")
    return (spec.get("template") or {}).get("spec")


# ── Contrôles ───────────────────────────────────────────────────────────────

def _check_pod_spec(spec: dict, sa_power: dict[str, str], namespace: str) -> list[dict]:
    out: list[dict] = []
    psc = spec.get("securityContext") or {}

    def add(check, container=None, detail="", severity=None, cvss=None, extra=None):
        out.append({"check": check, "container": container, "detail": detail,
                    "severity": severity or CHECKS[check]["severity"],
                    "cvss": cvss or CHECKS[check]["cvss"], **(extra or {})})

    if spec.get("hostPID") or spec.get("hostIPC"):
        add("host_pid_ipc", detail=", ".join(k for k in ("hostPID", "hostIPC") if spec.get(k)))
    if spec.get("hostNetwork"):
        add("host_network", detail="hostNetwork: true")
    for v in spec.get("volumes", []) or []:
        hp = (v.get("hostPath") or {}).get("path")
        if not hp:
            continue
        p = hp.rstrip("/") or "/"
        sensitive = p == "/" or any(p == s or p.startswith(s + "/") for s in SENSITIVE_PATHS)
        add("host_path_sensitive" if sensitive else "host_path",
            detail=f"volume « {v.get('name')} » → {hp}", extra={"volume": v.get("name")})

    sa = spec.get("serviceAccountName") or spec.get("serviceAccount") or "default"
    power = sa_power.get(f"{namespace}/{sa}")
    if power and spec.get("automountServiceAccountToken") is not False:
        add("sa_token_privileged", detail=f"compte « {sa} » lié à {power['role']}",
            severity="critical" if power["level"] == "critical" else "warning")

    for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
        cn = c.get("name")
        sc = c.get("securityContext") or {}
        privileged = sc.get("privileged") is True
        if privileged:
            add("privileged", cn, "privileged: true")
        caps = sc.get("capabilities") or {}
        added = {str(x).upper() for x in caps.get("add") or []}
        bad = added & DANGEROUS_CAPS
        if bad:
            add("dangerous_capability", cn, "ajoutées : " + ", ".join(sorted(bad)))
        if not privileged and sc.get("allowPrivilegeEscalation") is not False:
            add("privilege_escalation", cn,
                "allowPrivilegeEscalation: true" if sc.get("allowPrivilegeEscalation") else
                "allowPrivilegeEscalation non défini (vaut true par défaut)")
        user = sc.get("runAsUser", psc.get("runAsUser"))
        nonroot = sc.get("runAsNonRoot", psc.get("runAsNonRoot"))
        if user == 0:
            add("run_as_root", cn, "runAsUser: 0 explicite")
        elif not nonroot and user is None:
            add("run_as_root", cn, "rien n'empêche l'exécution en root (runAsNonRoot absent)",
                cvss=5.5)
        for e in c.get("env", []) or []:
            if "value" in e and is_secret_env(e.get("name", ""), e.get("value")):
                add("secret_in_env", cn, f"variable « {e['name']} » en clair",
                    extra={"env": e["name"]})
        if not sc.get("readOnlyRootFilesystem"):
            add("no_readonly_rootfs", cn)
        if "ALL" not in {str(x).upper() for x in caps.get("drop") or []} and not privileged:
            add("caps_not_dropped", cn)
        if not ((c.get("resources") or {}).get("limits")):
            add("no_limits", cn)
        img = str(c.get("image", ""))
        if img.endswith(":latest") or (":" not in img.split("/")[-1] and "@" not in img):
            add("image_latest", cn, img)
    return out


def _rbac(include_system: bool) -> tuple[list[dict], dict[str, dict]]:
    """Liaisons trop larges, et pouvoir de chaque compte de service."""
    wildcard_roles = set()
    for r in _safe_items(["get", "clusterroles"]) + _safe_items(["get", "roles", "-A"]):
        for rule in r.get("rules") or []:
            if "*" in (rule.get("verbs") or []) and "*" in (rule.get("resources") or []):
                ns = r["metadata"].get("namespace", "")
                wildcard_roles.add(f"{r['kind']}:{ns}:{r['metadata']['name']}")
    findings, sa_power = [], {}
    bindings = _safe_items(["get", "clusterrolebindings"]) + _safe_items(["get", "rolebindings", "-A"])
    for b in bindings:
        meta, ref = b["metadata"], b.get("roleRef", {})
        bns = meta.get("namespace")
        role = ref.get("name", "")
        level = POWERFUL_ROLES.get(role)
        if not level:
            key = f"{ref.get('kind')}:{bns if ref.get('kind') == 'Role' else ''}:{role}"
            if key in wildcard_roles:
                level = "warning"
        if not level:
            continue
        for s in b.get("subjects") or []:
            sns = s.get("namespace") or bns or ""
            name = s.get("name", "")
            system = (name.startswith("system:") or sns in SYSTEM_NAMESPACES
                      or meta["name"].startswith("system:") or name in ("kubeadm:cluster-admins",)
                      or meta["name"] in ("minikube-rbac", "storage-provisioner"))
            if s.get("kind") == "ServiceAccount":
                sa_power[f"{sns}/{name}"] = {"role": f"{ref.get('kind')} {role}", "level": level}
            if system and not include_system:
                continue
            findings.append({
                "check": "rbac_permissive", "kind": b["kind"], "namespace": bns or "cluster",
                "name": meta["name"], "container": None,
                "detail": f"{s.get('kind')} « {name} » → {ref.get('kind')} {role}"
                          + (" (règles *)" if role not in POWERFUL_ROLES else ""),
                "severity": "critical" if level == "critical" else "warning",
                "cvss": 9.0 if level == "critical" else 7.0,
                "labels": meta.get("labels") or {},
            })
    return findings, sa_power


def _finding_id(f: dict) -> str:
    raw = "|".join(str(f.get(k, "")) for k in ("check", "kind", "namespace", "name", "container",
                                                "env", "volume"))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


_LAST_SCAN: dict = {}          # (include_system, include_info) -> résultat
CACHE_MAX_AGE = 600


def find_finding(finding_id: str) -> tuple[Optional[dict], list[dict]]:
    """Retrouve un constat dans le dernier scan (≤ 10 min), sinon relance un scan.
    Évite de rescanner tout le cluster à chaque aperçu de correctif."""
    for res in list(_LAST_SCAN.values()):
        if time.time() - res["generated_at"] < CACHE_MAX_AGE:
            f = next((x for x in res["findings"] if x["id"] == finding_id), None)
            if f:
                return f, res["findings"]
    res = scan(include_system=False, include_info=True)
    return next((x for x in res["findings"] if x["id"] == finding_id), None), res["findings"]


def scan(include_system: bool = False, include_info: bool = True) -> dict:
    res = _scan(include_system, include_info)
    _LAST_SCAN[(include_system, include_info)] = res
    return res


def _scan(include_system: bool = False, include_info: bool = True) -> dict:
    t0 = time.time()
    rbac_findings, sa_power = _rbac(include_system)

    workloads: list[dict] = []
    for kind, res in (("Deployment", "deployments"), ("StatefulSet", "statefulsets"),
                      ("DaemonSet", "daemonsets"), ("CronJob", "cronjobs"), ("Job", "jobs"),
                      ("Pod", "pods")):
        for obj in _safe_items(["get", res, "-A"]):
            meta = obj["metadata"]
            if meta.get("ownerReferences"):           # géré par un contrôleur : analysé via lui
                continue
            if meta["namespace"] in SYSTEM_NAMESPACES and not include_system:
                continue
            spec = _pod_spec_of(kind, obj)
            if spec is None:
                continue
            workloads.append({"kind": kind, "namespace": meta["namespace"], "name": meta["name"],
                              "spec": spec, "labels": meta.get("labels") or {}})

    findings: list[dict] = []
    for w in workloads:
        for f in _check_pod_spec(w["spec"], sa_power, w["namespace"]):
            if f["severity"] == "info" and not include_info:
                continue
            findings.append({**f, "kind": w["kind"], "namespace": w["namespace"], "name": w["name"],
                             "labels": w["labels"]})
    findings += rbac_findings

    # Namespaces sans NetworkPolicy (seulement ceux qui hébergent des workloads)
    policies = {p["metadata"]["namespace"] for p in _safe_items(["get", "networkpolicies", "-A"])}
    ns_labels = {n["metadata"]["name"]: n["metadata"].get("labels") or {}
                 for n in _safe_items(["get", "namespaces"])}
    for ns in sorted({w["namespace"] for w in workloads}):
        if ns not in policies and (include_system or ns not in SYSTEM_NAMESPACES):
            findings.append({"check": "no_network_policy", "kind": "Namespace", "namespace": ns,
                             "name": ns, "container": None, "detail": "aucune NetworkPolicy",
                             "severity": "warning", "cvss": 6.5, "labels": ns_labels.get(ns, {})})

    for f in findings:
        meta = CHECKS[f["check"]]
        f.update(id=_finding_id(f), title=meta["title"], technique=meta["technique"],
                 tactic=meta["tactic"], why=meta["why"], fix_kind=meta["fix"],
                 fix_desc=meta["fix_desc"])
        if f["fix_kind"] == "auto" and f["kind"] not in CONTROLLER_KINDS + ("Namespace",):
            f["fix_kind"] = "ai" if f["kind"] == "Pod" else "manual"
            f["fix_note"] = ("Pod autonome : ses champs de sécurité ne sont pas modifiables en place. "
                             "Il faut le recréer (correction IA) ou le confier à un Deployment.")

    order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order[f["severity"]], -f["cvss"], f["namespace"], f["name"]))
    serious = [f for f in findings if f["severity"] != "info"]
    risk = min(100, round(max(f["cvss"] for f in serious) * 10 + 2 * (len(serious) - 1))) \
        if serious else 0
    bad_workloads = {(f["kind"], f["namespace"], f["name"]) for f in serious
                     if f["kind"] not in ("Namespace", "RoleBinding", "ClusterRoleBinding")}
    crit_workloads = {(f["kind"], f["namespace"], f["name"]) for f in findings
                      if f["severity"] == "critical" and f["kind"] not in
                      ("Namespace", "RoleBinding", "ClusterRoleBinding")}
    n_w = len(workloads)
    return {
        "generated_at": time.time(), "scan_ms": round((time.time() - t0) * 1000),
        "workloads": n_w,
        "workload_ids": [f"{w['kind']}|{w['namespace']}|{w['name']}" for w in workloads],
        "summary": {
            "critical": sum(1 for f in findings if f["severity"] == "critical"),
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "info": sum(1 for f in findings if f["severity"] == "info"),
            "risk_score": risk,
            "risk_grade": "A" if risk < 20 else "B" if risk < 40 else "C" if risk < 60 else "D",
            "workloads_clean_pct": round(100 * (n_w - len(bad_workloads)) / n_w) if n_w else 100,
            "workloads_without_critical_pct": round(100 * (n_w - len(crit_workloads)) / n_w) if n_w else 100,
            "auto_fixable": sum(1 for f in serious if f["fix_kind"] == "auto"),
            "techniques": sorted({f["technique"] for f in serious if f["technique"].startswith("T")}),
        },
        "findings": findings,
    }


# ── Correctifs déterministes ────────────────────────────────────────────────

def _template_path(kind: str) -> list[str]:
    return ["spec", "template", "spec"]


def _nest(path: list[str], leaf: dict) -> dict:
    d = leaf
    for k in reversed(path):
        d = {k: d}
    return d


def build_fix(f: dict, workload: Optional[dict] = None) -> dict:
    """Correctif déterministe d'un constat. Renvoie les opérations kubectl à exécuter."""
    check, kind, ns, name, cn = f["check"], f["kind"], f["namespace"], f["name"], f.get("container")
    if f.get("fix_kind") != "auto":
        return {"ok": False, "error": "Pas de correctif automatique pour ce constat"}

    if check == "no_network_policy":
        np = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
              "metadata": {"name": "aikube-default-deny-ingress", "namespace": ns,
                           "labels": {"aikube.io/remediation": "no_network_policy"}},
              "spec": {"podSelector": {}, "policyTypes": ["Ingress"]}}
        return {"ok": True, "ops": [{"op": "apply", "manifest": np}], "rollout": False,
                "summary": "Créer une NetworkPolicy qui refuse le trafic entrant par défaut",
                "undo": [{"op": "delete", "args": ["networkpolicy", "aikube-default-deny-ingress",
                                                   "-n", ns]}]}

    if kind not in CONTROLLER_KINDS:
        return {"ok": False, "error": f"{kind} non corrigeable en place"}

    base = _template_path(kind)
    target = f"{kind.lower()}/{name}"

    def container_patch(sc: dict) -> dict:
        return _nest(base, {"containers": [{"name": cn, "securityContext": sc}]})

    if check == "privileged":
        patch, summary = container_patch({"privileged": False}), "privileged: false"
    elif check == "dangerous_capability":
        patch = container_patch({"capabilities": {"add": [], "drop": ["ALL"]}})
        summary = "retirer les capacités ajoutées, drop: [ALL]"
    elif check == "caps_not_dropped":
        patch, summary = container_patch({"capabilities": {"drop": ["ALL"]}}), "capabilities.drop: [ALL]"
    elif check == "privilege_escalation":
        patch, summary = container_patch({"allowPrivilegeEscalation": False}), "allowPrivilegeEscalation: false"
    elif check == "run_as_root":
        patch = container_patch({"runAsNonRoot": True, "runAsUser": 10001})
        summary = "runAsNonRoot: true, runAsUser: 10001"
    elif check == "host_pid_ipc":
        patch, summary = _nest(base, {"hostPID": False, "hostIPC": False}), "hostPID/hostIPC: false"
    elif check == "host_network":
        patch, summary = _nest(base, {"hostNetwork": False}), "hostNetwork: false"
    elif check == "sa_token_privileged":
        patch, summary = _nest(base, {"automountServiceAccountToken": False}), \
            "automountServiceAccountToken: false"
    elif check == "secret_in_env":
        return _fix_secret_env(f, workload)
    else:
        return {"ok": False, "error": f"Pas de correctif pour {check}"}
    return {"ok": True, "rollout": True, "target": target, "namespace": ns, "summary": summary,
            "ops": [{"op": "patch", "target": target, "type": "strategic", "patch": patch}]}


def _fix_secret_env(f: dict, workload: Optional[dict]) -> dict:
    """Crée un Secret avec la valeur actuelle, puis remplace la variable par une référence."""
    if not workload:
        return {"ok": False, "error": "définition du workload requise"}
    kind, ns, name, cn, env_name = f["kind"], f["namespace"], f["name"], f["container"], f["env"]
    spec = _pod_spec_of(kind, workload) or {}
    containers = spec.get("containers") or []
    ci = next((i for i, c in enumerate(containers) if c.get("name") == cn), None)
    if ci is None:
        return {"ok": False, "error": f"conteneur {cn} introuvable"}
    env = containers[ci].get("env") or []
    ei = next((i for i, e in enumerate(env) if e.get("name") == env_name and "value" in e), None)
    if ei is None:
        return {"ok": False, "error": f"variable {env_name} déjà corrigée ou introuvable"}
    secret_name = f"{name}-env"[:63]
    key = env_name.lower().replace("_", "-")
    secret = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
              "metadata": {"name": secret_name, "namespace": ns,
                           "labels": {"aikube.io/remediation": "secret_in_env"}},
              "stringData": {key: env[ei]["value"]}}
    json_patch = [{"op": "replace",
                   "path": f"/spec/template/spec/containers/{ci}/env/{ei}",
                   "value": {"name": env_name,
                             "valueFrom": {"secretKeyRef": {"name": secret_name, "key": key}}}}]
    return {"ok": True, "rollout": True, "target": f"{kind.lower()}/{name}", "namespace": ns,
            "summary": f"Secret « {secret_name} » (clé {key}) + secretKeyRef pour {env_name}",
            "ops": [{"op": "apply", "manifest": secret, "merge_secret": True},
                    {"op": "patch", "target": f"{kind.lower()}/{name}", "type": "json",
                     "patch": json_patch}]}


# ── Évaluation sur le banc étiqueté ─────────────────────────────────────────

def evaluate_benchmark() -> dict:
    """
    Compare le scanner à la vérité terrain portée par les étiquettes aikube.vuln/*
    du banc k8s/benchmark/vuln-benchmark.yaml. Une ressource est « détectée »
    si elle reçoit au moins un constat critique ou avertissement.
    """
    res = scan(include_system=False, include_info=False)
    flagged: dict[tuple, list[str]] = {}
    for f in res["findings"]:
        flagged.setdefault((f["kind"], f["namespace"], f["name"]), []).append(f["check"])

    truth = []
    for kind, args in (("Pod", ["get", "pods", "-A", "-l", "aikube.vuln/expected"]),
                       ("Namespace", ["get", "namespaces", "-l", "aikube.vuln/expected"]),
                       ("RoleBinding", ["get", "rolebindings", "-A", "-l", "aikube.vuln/expected"])):
        for o in _safe_items(args):
            m = o["metadata"]
            ns = m["name"] if kind == "Namespace" else m.get("namespace")
            lab = m.get("labels") or {}
            checks = flagged.get((kind, ns, m["name"]), [])
            truth.append({"kind": kind, "namespace": ns, "name": m["name"],
                          "expected": lab.get("aikube.vuln/expected"),
                          "type": lab.get("aikube.vuln/type"),
                          "technique": lab.get("aikube.vuln/technique"),
                          "predicted_old": lab.get("aikube.vuln/detected-by-current") == "true",
                          "detected": bool(checks), "checks": sorted(set(checks))})
    tp = sum(1 for t in truth if t["expected"] == "vulnerable" and t["detected"])
    fn = sum(1 for t in truth if t["expected"] == "vulnerable" and not t["detected"])
    fp = sum(1 for t in truth if t["expected"] == "hardened" and t["detected"])
    tn = sum(1 for t in truth if t["expected"] == "hardened" and not t["detected"])
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else None
    return {"resources": len(truth), "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "precision": round(prec, 3) if prec is not None else None,
            "recall": round(rec, 3) if rec is not None else None,
            "f1": round(f1, 3) if f1 is not None else None,
            "details": sorted(truth, key=lambda t: (t["expected"] or "", t["name"]))}

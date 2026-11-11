# ai_module/intent_checks.py
# -----------------------------------------------------------------------------
# Écarts possibles entre la DEMANDE et le manifeste — avertissements seulement.
#
# Le dry-run serveur et le contrôle de capacité jugent la forme et la place,
# pas l'intention (mémoire, sections 10.3 et 10.4). Trois écarts fréquents se
# repèrent pourtant par simple calcul :
#   1. un conteneur que la demande ne mentionne pas (conteneur « en trop ») ;
#   2. un port qui ne correspond pas à celui sur lequel l'image écoute
#      (ex. nginx-unprivileged écoute sur 8080, pas 80), y compris dans les
#      sondes readiness / liveness / startup ;
#   3. un type d'objet demandé mais absent (Service, HPA, PVC…).
#
# Ces contrôles ne changent PAS le verdict « valide » : ils produisent des
# avertissements à relire. Les mesures du mémoire restent donc comparables.
# Ce sont des heuristiques : elles peuvent se tromper dans les deux sens.
# -----------------------------------------------------------------------------
from __future__ import annotations

import re
import unicodedata

# Ports d'écoute par défaut d'images courantes (sans registre ni tag). Un ENSEMBLE :
# plusieurs images écoutent sur plusieurs ports (rabbitmq : AMQP et interface web…).
KNOWN_PORTS = {
    "nginx": {80}, "httpd": {80}, "caddy": {80, 443}, "traefik": {80, 443, 8080},
    "nginxinc/nginx-unprivileged": {8080}, "bitnami/nginx": {8080, 8443},
    "bitnami/apache": {8080, 8443}, "bitnamilegacy/nginx": {8080, 8443},
    "redis": {6379}, "bitnami/redis": {6379}, "valkey/valkey": {6379},
    "postgres": {5432}, "bitnami/postgresql": {5432},
    "mysql": {3306, 33060}, "mariadb": {3306}, "bitnami/mysql": {3306},
    "mongo": {27017}, "bitnami/mongodb": {27017},
    "rabbitmq": {5672, 15672, 15692, 25672, 4369}, "memcached": {11211},
    "elasticsearch": {9200, 9300}, "minio/minio": {9000, 9001},
    "grafana/grafana": {3000}, "prom/prometheus": {9090},
    "prom/node-exporter": {9100}, "prom/alertmanager": {9093, 9094},
}


# Objets que la demande peut réclamer explicitement : mots-clés -> kind
REQUESTED_KINDS = [
    # (motif, objet demandé, objets qui satisfont la demande)
    (r"\bdeployment\b|\bdéploie|\bdeploie", "Deployment", {"Deployment", "StatefulSet"}),
    # « compte de service » / « service account » désignent un ServiceAccount, pas un Service
    (r"(?<!compte de )\bservice\b(?!\s*account)", "Service", {"Service"}),
    # « exposer » : un Service ou un Ingress suffit
    (r"\bexpos", "Service", {"Service", "Ingress"}),
    (r"\bingress\b", "Ingress", {"Ingress"}),
    (r"\bhpa\b|autoscal|mise à l'échelle auto", "HorizontalPodAutoscaler", {"HorizontalPodAutoscaler"}),
    (r"\bstatefulset\b", "StatefulSet", {"StatefulSet"}),
    (r"\bdaemonset\b", "DaemonSet", {"DaemonSet"}),
    (r"\bcronjob\b|tous les jours|chaque nuit|quotidien", "CronJob", {"CronJob"}),
    (r"\bpvc\b|volume persistant|persistentvolumeclaim", "PersistentVolumeClaim",
     {"PersistentVolumeClaim", "StatefulSet"}),
    (r"\bconfigmap\b", "ConfigMap", {"ConfigMap"}),
    (r"\bnetworkpolicy\b|politique réseau", "NetworkPolicy", {"NetworkPolicy"}),
    (r"\bserviceaccount\b|compte de service|service account", "ServiceAccount", {"ServiceAccount"}),
]


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", (text or "").lower())
    return t


def _image_base(image: str) -> str:
    """« docker.io/nginxinc/nginx-unprivileged:1.25@sha… » -> « nginxinc/nginx-unprivileged »"""
    img = (image or "").split("@")[0]
    parts = img.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        parts = parts[1:]                                  # retire le registre
    base = "/".join(parts)
    base = base.rsplit(":", 1)[0] if ":" in base.split("/")[-1] else base
    if base.startswith("library/"):
        base = base[len("library/"):]
    return base


def _pod_spec(doc: dict) -> dict | None:
    kind = doc.get("kind")
    spec = doc.get("spec") or {}
    if kind == "Pod":
        return spec
    if kind == "CronJob":
        return (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec")
    if kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"):
        return (spec.get("template") or {}).get("spec")
    return None


def _labels(doc: dict) -> dict:
    kind = doc.get("kind")
    spec = doc.get("spec") or {}
    if kind == "Pod":
        return (doc.get("metadata") or {}).get("labels") or {}
    return ((spec.get("template") or {}).get("metadata") or {}).get("labels") or {}


def intent_warnings(description: str, docs: list[dict]) -> list[str]:
    desc = _norm(description)
    words = set(re.findall(r"[a-z0-9][a-z0-9._-]+", desc))
    out: list[str] = []
    workloads = []

    for d in docs:
        if not isinstance(d, dict):
            continue
        ps = _pod_spec(d)
        if not ps:
            continue
        name = (d.get("metadata") or {}).get("name", "?")
        containers = ps.get("containers") or []
        listen = {}                                        # nom du conteneur -> ports d'écoute
        for i, c in enumerate(containers):
            img = str(c.get("image", ""))
            base = _image_base(img)
            declared = {p.get("containerPort") for p in (c.get("ports") or []) if isinstance(p, dict)}
            known = KNOWN_PORTS.get(base)
            listen[c.get("name", f"#{i}")] = set(known) if known else {p for p in declared if p}

            # 1. conteneur non mentionné (au-delà du premier)
            if i > 0:
                tokens = {c.get("name", "")} | set(re.split(r"[/:._-]", base))
                tokens = {t.lower() for t in tokens if len(t) >= 4}
                if not any(t in desc for t in tokens):
                    out.append(f"{d.get('kind')}/{name} : le conteneur « {c.get('name')} » "
                               f"(image {img}) n'est pas mentionné dans la demande. Vérifier qu'il est voulu.")

            # 2. port déclaré différent du port d'écoute connu de l'image
            if known and declared and not (known & declared):
                out.append(f"{d.get('kind')}/{name} : l'image {base} écoute sur le port "
                           f"{', '.join(str(x) for x in sorted(known))}, "
                           f"mais le conteneur déclare {', '.join(str(p) for p in sorted(declared))}.")

            # 2 ter. sondes (readiness, liveness, startup) sur un port où l'image n'écoute pas :
            # le pod ne serait jamais prêt, ou serait redémarré sans fin
            ports_ok = listen[c.get("name", f"#{i}")]
            port_names = {p.get("name") for p in (c.get("ports") or []) if isinstance(p, dict) and p.get("name")}
            for probe_kind in ("readinessProbe", "livenessProbe", "startupProbe"):
                probe = c.get(probe_kind) or {}
                for mode in ("httpGet", "tcpSocket", "grpc"):
                    port = (probe.get(mode) or {}).get("port")
                    if port is None:
                        continue
                    if isinstance(port, int) and ports_ok and port not in ports_ok:
                        effet = ("le pod ne serait jamais prêt" if probe_kind == "readinessProbe"
                                 else "le conteneur serait redémarré en boucle")
                        out.append(f"{d.get('kind')}/{name} : {probe_kind} teste le port {port}, "
                                   f"alors que le conteneur « {c.get('name')} » écoute sur "
                                   f"{', '.join(str(x) for x in sorted(ports_ok))} ; {effet}.")
                    elif isinstance(port, str) and not port.isdigit() and port not in port_names:
                        out.append(f"{d.get('kind')}/{name} : {probe_kind} vise le port nommé « {port} », "
                                   f"qui n'existe pas dans le conteneur « {c.get('name')} ».")
        workloads.append((d, _labels(d), listen))

    # 2 bis. Service dont le targetPort ne correspond à aucun port d'écoute connu
    for s in docs:
        if not isinstance(s, dict) or s.get("kind") != "Service":
            continue
        sel = (s.get("spec") or {}).get("selector") or {}
        if not sel:
            continue
        targets = [w for w in workloads if all(w[1].get(k) == v for k, v in sel.items())]
        if not targets:
            continue
        listening = set().union(*(set().union(*w[2].values()) if w[2] else set() for w in targets))
        for p in (s.get("spec") or {}).get("ports") or []:
            tp = p.get("targetPort", p.get("port"))
            if isinstance(tp, int) and listening and tp not in listening:
                out.append(f"Service/{(s.get('metadata') or {}).get('name', '?')} : targetPort {tp}, "
                           f"alors que les pods visés écoutent sur "
                           f"{', '.join(str(x) for x in sorted(listening))}. Le trafic n'arriverait pas.")

    # 3. type d'objet demandé mais absent
    # « HPA pour le deployment web » désigne un objet EXISTANT : ce n'est pas une demande de création
    present = {d.get("kind") for d in docs if isinstance(d, dict)}
    reference = re.compile(r"(pour|du|au|aux|à|vers|sur|devant|derrière|cibl\w*)\s+(le|la|les|l'|un|une)?\s*$")
    seen_kinds = set()
    for pattern, kind, ok_kinds in REQUESTED_KINDS:
        asked = [m for m in re.finditer(pattern, desc) if not reference.search(desc[:m.start()])]
        if asked and not (ok_kinds & present) and kind not in seen_kinds:
            seen_kinds.add(kind)
            out.append(f"La demande mentionne un {kind}, mais le manifeste n'en contient pas.")

    return list(dict.fromkeys(out))

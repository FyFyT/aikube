# Contrôles d'intention : écarts entre la demande et le manifeste (avertissements seulement)
import yaml

from ai_module.intent_checks import intent_warnings

NGINX = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: web}
spec:
  replicas: 3
  selector: {matchLabels: {app: web}}
  template:
    metadata: {labels: {app: web}}
    spec:
      containers:
      - name: nginx
        image: nginxinc/nginx-unprivileged:1.25.2
        ports: [{containerPort: 80}]
      - name: exporter
        image: prom/node-exporter:v1.8.2
---
apiVersion: v1
kind: Service
metadata: {name: web}
spec:
  selector: {app: web}
  ports: [{port: 80, targetPort: 80}]
"""


def docs(text):
    return [d for d in yaml.safe_load_all(text) if d]


def test_port_et_conteneur_non_demande():
    w = intent_warnings("nginx avec 3 replicas exposé sur le port 80", docs(NGINX))
    assert any("écoute sur le port 8080" in x for x in w)
    assert any("exporter" in x and "pas mentionné" in x for x in w)
    assert any(x.startswith("Service/web : targetPort 80") for x in w)


def test_conteneur_mentionne_pas_signale():
    w = intent_warnings("nginx avec un sidecar node-exporter, exposé", docs(NGINX))
    assert not any("pas mentionné" in x for x in w)


def test_objet_demande_absent_mais_pas_objet_existant():
    d = docs(NGINX)[:1]
    assert any("HorizontalPodAutoscaler" in x for x in intent_warnings("web avec un HPA si CPU > 70 %", d))
    # « pour le deployment web » désigne un objet existant : pas d'avertissement
    hpa = [{"apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler", "metadata": {"name": "web"}}]
    assert intent_warnings("HorizontalPodAutoscaler pour le deployment web", hpa) == []


def test_sondes_sur_le_mauvais_port():
    d = docs(NGINX)
    c = d[0]["spec"]["template"]["spec"]["containers"][0]
    c["readinessProbe"] = {"httpGet": {"path": "/", "port": 80}}
    c["livenessProbe"] = {"tcpSocket": {"port": "http"}}
    w = intent_warnings("nginx avec un sidecar node-exporter, exposé", d)
    assert any("readinessProbe teste le port 80" in x and "jamais prêt" in x for x in w)
    assert any("port nommé « http »" in x for x in w)
    # sonde correcte : pas d'avertissement sur les sondes
    c["readinessProbe"] = {"httpGet": {"path": "/", "port": 8080}}
    c.pop("livenessProbe")
    assert not any("Probe" in x for x in intent_warnings("nginx avec un sidecar node-exporter, exposé", d))


def test_compte_de_service_n_est_pas_un_service():
    role = [{"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "r"}},
            {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "b"}}]
    w = intent_warnings("Donner au compte de service ci-bot le droit de redémarrer les deployments", role)
    assert not any("Service" in x for x in w)
    w = intent_warnings("Role and binding for the service account ci-bot", role)
    assert not any("mentionne un Service," in x for x in w)


def test_exposer_via_ingress_suffit():
    ing = [{"apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": {"name": "minio"}}]
    assert intent_warnings("Exposer minio à l'extérieur via un Ingress", ing) == []


def test_images_a_plusieurs_ports():
    rmq = [{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "rmq"},
            "spec": {"template": {"metadata": {"labels": {"app": "rmq"}}, "spec": {"containers": [
                {"name": "rabbitmq", "image": "rabbitmq:3.13-management",
                 "ports": [{"containerPort": 15672}],
                 "readinessProbe": {"tcpSocket": {"port": 15672}}}]}}}}]
    assert intent_warnings("rabbitmq avec son interface web", rmq) == []

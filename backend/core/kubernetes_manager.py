from kubernetes import client, config
from typing import Optional
import logging, os, asyncio

logger = logging.getLogger(__name__)

class KubernetesManager:
    def __init__(self):
        self.v1: Optional[client.CoreV1Api] = None
        self.apps_v1: Optional[client.AppsV1Api] = None
        self.networking_v1: Optional[client.NetworkingV1Api] = None
        self.rbac_v1: Optional[client.RbacAuthorizationV1Api] = None
        self.connected = False

    async def connect(self):
        try:
            kubeconfig = os.path.expanduser("~/.kube/config")
            config.load_kube_config(config_file=kubeconfig)
            self.v1            = client.CoreV1Api()
            self.apps_v1       = client.AppsV1Api()
            self.networking_v1 = client.NetworkingV1Api()
            self.rbac_v1       = client.RbacAuthorizationV1Api()
            self.v1.list_node(_request_timeout=5)
            self.connected = True
            print("✅ Kubernetes: connecté")
        except Exception as e:
            print(f"⚠️ K8s non connecté: {e}")
            self.connected = False

    def safe_call(self, fn, *args, timeout=8, **kwargs):
        """Appel K8s avec timeout pour éviter les blocages"""
        kwargs['_request_timeout'] = timeout
        return fn(*args, **kwargs)

k8s_manager = KubernetesManager()

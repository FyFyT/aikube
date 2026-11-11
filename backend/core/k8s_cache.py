"""
Cache simple pour les appels Kubernetes.
Évite de refaire les mêmes appels en moins de 30 secondes.
"""
import time
from typing import Any, Optional

class K8sCache:
    def __init__(self, ttl: int = 30):
        self.ttl = ttl
        self._cache = {}

    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            data, ts = self._cache[key]
            if time.time() - ts < self.ttl:
                return data
        return None

    def set(self, key: str, value: Any):
        self._cache[key] = (value, time.time())

    def invalidate(self, key: str = None):
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

cache = K8sCache(ttl=30)

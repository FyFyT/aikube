"""Réparation déterministe (manifest_repair) et son intégration dans la boucle du générateur."""
import asyncio

import yaml

from ai_module import manifest_repair as M
from ai_module import yaml_pipeline as Y

SERVED = {"Pod": "v1", "Deployment": "apps/v1", "DaemonSet": "apps/v1", "Service": "v1", "Namespace": "v1",
          "ClusterRole": "rbac.authorization.k8s.io/v1", "ServiceAccount": "v1"}
CTX = {"api_kinds": {k: {"apiVersion": v, "namespaced": k not in ("Namespace", "ClusterRole")} for k, v in SERVED.items()},
       "host": {"ram_available_mib": 4000},
       "cluster": {"local_cluster": True, "namespaces": ["default"], "storage_classes": [],
                   "nodes": [{"name": "n1", "ready": True, "schedulable": True, "cpu_free_m": 2000, "mem_free_mib": 3000},
                             {"name": "n2", "ready": True, "schedulable": True, "cpu_free_m": 2000, "mem_free_mib": 3000}],
                   "total_cpu_free_m": 4000, "total_mem_free_mib": 4000, "declared_mem_free_mib": 6000,
                   "max_node_cpu_free_m": 2000, "max_node_mem_free_mib": 3000}}


def dep(replicas=1, mem="256Mi", cpu="100m", extra_pod=None, extra_c=None, cname="web"):
    c = {"name": cname, "image": "nginx:1.27",
         "resources": {"requests": {"cpu": cpu, "memory": mem}, "limits": {"cpu": cpu, "memory": mem}}}
    c.update(extra_c or {})
    pod = {"containers": [c]}
    pod.update(extra_pod or {})
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web", "namespace": "default"},
            "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": "web"}},
                     "template": {"metadata": {"labels": {"app": "web"}}, "spec": pod}}}


def run(*docs, check=None):
    r = M.repair(yaml.safe_dump_all(docs, sort_keys=False), check, CTX)
    return r, [d for d in yaml.safe_load_all(r["yaml"]) if d]


def test_version_api_remplacee_par_celle_du_serveur():
    r, out = run({"apiVersion": "apps/v1", "kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": [
        {"name": "c", "image": "busybox:1.36", "resources": M.DEFAULT_RES}]}})
    assert out[0]["apiVersion"] == "v1" and any("apiVersion" in c for c in r["changes"])


def test_sondes_deplacees_du_pod_vers_le_conteneur():
    probe = {"httpGet": {"path": "/", "port": 80}}
    _, out = run(dep(extra_pod={"readinessProbe": probe}))
    ps = out[0]["spec"]["template"]["spec"]
    assert "readinessProbe" not in ps and ps["containers"][0]["readinessProbe"] == probe


def test_fsgroup_deplace_vers_le_pod_et_nom_invalide_corrige():
    _, out = run(dep(extra_c={"securityContext": {"fsGroup": 2000}}, cname='web"'))
    ps = out[0]["spec"]["template"]["spec"]
    assert ps["securityContext"]["fsGroup"] == 2000 and ps["containers"][0]["name"] == "web"


def test_capacite_totale_ramenee_dans_la_ram_de_l_hote():
    r, out = run(dep(replicas=10, mem="1Gi"))
    docs, _ = Y.parse_docs(r["yaml"])
    assert not Y.static_checks(docs, CTX)["errors"]
    assert out[0]["spec"]["replicas"] == 10          # le nombre demandé est conservé si possible
    assert r["yaml"].startswith("# Avertissement : réparation automatique")


def test_pod_trop_gros_pour_un_noeud():
    r, _ = run(dep(mem="8Gi", cpu="4"))
    docs, _ = Y.parse_docs(r["yaml"])
    assert not Y.static_checks(docs, CTX)["errors"]


def test_champ_inconnu_supprime_d_apres_le_serveur():
    check = {"dry_run": {"error": 'Error from server (BadRequest): error when creating "STDIN": Deployment in version '
                                  '"v1" cannot be handled as a Deployment: strict decoding error: unknown field '
                                  '"spec.template.spec.containers[0].memoryLimit"'}}
    r, out = run(dep(extra_c={"memoryLimit": "1Gi"}), check=check)
    assert "memoryLimit" not in out[0]["spec"]["template"]["spec"]["containers"][0]


def test_manifeste_correct_inchange():
    r = M.repair(yaml.safe_dump(dep()), None, CTX)
    assert r["changes"] == []


def test_boucle_du_generateur_garde_le_manifeste_du_modele(monkeypatch):
    bad = yaml.safe_dump({**dep(), "apiVersion": "extensions/v1beta1"})

    async def fake_collect(force=False):
        return CTX

    async def fake_llm(system, user, provider):
        return {"yaml": bad, "provider": "kubellm-v3", "model": "kubellm-v3"}

    def fake_validate(text, ctx):
        ok = all(d["apiVersion"] != "extensions/v1beta1" for d in yaml.safe_load_all(text) if d)
        return {"valid": ok, "server_validated": ok, "warnings": [], "capacity": None, "kinds": [], "dry_run": {},
                "errors": [] if ok else [{"source": "serveur Kubernetes", "category": "groupe d'API incorrect", "message": "x"}]}

    monkeypatch.setattr(Y.host_context, "collect", fake_collect)
    monkeypatch.setattr(Y.host_context, "to_prompt", lambda ctx: "## Contexte")
    monkeypatch.setattr(Y.llm_client, "generate_manifest", fake_llm)
    monkeypatch.setattr(Y, "validate_text", fake_validate)
    monkeypatch.setattr(Y, "_log", lambda e: None)
    res = asyncio.run(Y.generate("web", provider="kubellm-v3", repair=True))
    a = res["attempts"][0]
    assert res["valid"] and res["attempts_used"] == 1 and res["repaired"]
    assert a["yaml_model"] == bad and a["repair"]["errors_before"] == ["groupe d'API incorrect"]
    res2 = asyncio.run(Y.generate("web", provider="kubellm-v3", repair=False))   # sans réparation : inchangé
    assert not res2["valid"] and res2["attempts_used"] == 3

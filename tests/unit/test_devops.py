"""Tests unitaires de la partie DevOps et de la notation des campagnes (sans cluster)."""
import pytest

from ai_module import safe_deploy as D
from ai_module import helm_ops as H
from ai_module import yaml_pipeline as Y


def dep(image="nginx:1.27", replicas=1, ns=None):
    d = {"apiVersion": "apps/v1", "kind": "Deployment",
         "metadata": {"name": "web"},
         "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": "web"}},
                  "template": {"metadata": {"labels": {"app": "web"}},
                               "spec": {"containers": [{"name": "web", "image": image}]}}}}
    if ns:
        d["metadata"]["namespace"] = ns
    return d


def test_namespace_complete_uniquement_si_absent():
    docs = D.set_namespace([dep(), dep(ns="prod"), {"apiVersion": "v1", "kind": "Namespace",
                                                    "metadata": {"name": "x"}}], "default")
    assert docs[0]["metadata"]["namespace"] == "default"
    assert docs[1]["metadata"]["namespace"] == "prod"
    assert "namespace" not in docs[2]["metadata"]


def test_creation():
    assert D.classify_change(None, dep())["action"] == "création"


def test_inchange():
    live = dep()
    live["metadata"]["resourceVersion"] = "12"
    live["status"] = {"readyReplicas": 1}
    assert D.classify_change(live, dep())["action"] == "inchangé"


def test_changement_image_redemarre_les_pods():
    c = D.classify_change(dep(), dep(image="nginx:1.28"))
    assert c == {"action": "modification", "restarts_pods": True}


def test_changement_replicas_sans_redemarrage():
    c = D.classify_change(dep(), dep(replicas=3))
    assert c == {"action": "modification", "restarts_pods": False}


def test_hachage_de_gabarit_ignore():
    a = dep()["spec"]["template"]
    b = dep()["spec"]["template"]
    b["metadata"]["labels"]["pod-template-hash"] = "abc"
    assert D._strip_template(a) == D._strip_template(b)


def test_controle_securite_du_manifeste():
    d = dep(image="nginx")
    d["spec"]["template"]["spec"]["containers"][0]["securityContext"] = {"privileged": True}
    checks = {f["check"] for f in D._security(D.set_namespace([d], "default"))}
    assert "privileged" in checks and "image_latest" in checks


@pytest.mark.parametrize("sets,ok", [(["replicaCount=2"], True), (["a=b; rm -rf /"], True),
                                     (["--post-renderer=x"], False), (["x"], False)])
def test_helm_set_valide(sets, ok):
    args, err = H._upgrade_args("web", "default", "bitnami/nginx", None, sets)
    assert (err is None) == ok
    if ok:
        assert args[0] == "upgrade" and f"--set={sets[0]}" in args   # passé en argument, jamais au shell


def test_helm_refuse_noms_invalides():
    assert H._upgrade_args("Web Server", "default", "bitnami/nginx", None, [])[1]
    assert H._upgrade_args("web", "default", "--repo=evil", None, [])[1]


def test_yaml_illisible():
    docs, err = Y.parse_docs("kind: [")
    assert not docs and "illisible" in err


def test_abstention_comptee_zero():
    from campaign_agent import is_abstention, score_strict
    rep = "Je ne peux pas me prononcer : je n'ai pas accès à l'état du cluster. Vérifiez ImagePullBackOff…"
    assert is_abstention(rep)
    assert score_strict("image", rep, 1.0) == 0.0
    assert score_strict("image", "Le tag busybox:9.99 n'existe pas : ImagePullBackOff.", 1.0) == 1.0

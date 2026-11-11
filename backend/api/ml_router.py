from fastapi import APIRouter, HTTPException
import sys, os, subprocess, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

router = APIRouter()

_detector = None

def get_detector():
    global _detector
    if _detector is None:
        from ai_module.anomaly_detector_ml import AnomalyDetectorML
        _detector = AnomalyDetectorML()
    return _detector

@router.get("/metrics")
async def get_model_metrics():
    try:
        return get_detector().get_metrics()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/retrain")
async def retrain_model():
    try:
        metrics = get_detector().retrain()
        return {"status":"success","metrics":metrics.get('metrics',{})}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/scan")
async def ml_scan_cluster():
    try:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-A", "-o", "json"],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        pods = []
        for item in data.get("items", []):
            meta   = item["metadata"]
            spec   = item.get("spec", {})
            status = item.get("status", {})
            restarts = sum(cs.get("restartCount", 0) for cs in status.get("containerStatuses", []))
            pods.append({
                "name":      meta["name"],
                "namespace": meta["namespace"],
                "status":    status.get("phase", "Unknown"),
                "node":      spec.get("nodeName"),
                "restarts":  restarts,
                "age":       meta.get("creationTimestamp", ""),
                "labels":    meta.get("labels", {}),
            })
        from ai_module.anomaly_detector_ml import scan_cluster_fast
        return scan_cluster_fast(pods)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/feature-importance")
async def get_feature_importance():
    try:
        fi = get_detector().get_metrics().get('feature_importance', {})
        return {"features": [{"feature": k, "importance": v, "importance_pct": round(v*100,1)} for k,v in fi.items()]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/confusion-matrix")
async def get_confusion_matrix():
    try:
        metrics = get_detector().get_metrics()
        return {"confusion_matrix": metrics.get('confusion_matrix',{}), "metrics": metrics.get('metrics',{})}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/roc-curve")
async def get_roc_curve():
    try:
        metrics = get_detector().get_metrics()
        return {"roc_curve": metrics.get('roc_curve',[]), "auc": metrics.get('metrics',{}).get('roc_auc',0)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/kubebench")
async def run_kubebench():
    """
    KubeBench v2.0 — 50 scénarios de benchmark pour évaluer le modèle RF.
    Contribution scientifique C6.
    """
    SCENARIOS = [
        # ── CRASHLOOP (10 scénarios) ──────────────────────────────
        {"id":1,  "category":"CrashLoop", "name":"CrashLoop léger",          "input":{"name":"api","status":"Running","restarts":3, "namespace":"default",    "node":"n1","labels":{"app":"api"}},    "expected":1},
        {"id":2,  "category":"CrashLoop", "name":"CrashLoop modéré",         "input":{"name":"api","status":"Running","restarts":7, "namespace":"production",  "node":"n1","labels":{}},              "expected":1},
        {"id":3,  "category":"CrashLoop", "name":"CrashLoop critique",       "input":{"name":"api","status":"Running","restarts":15,"namespace":"default",    "node":"n2","labels":{}},              "expected":1},
        {"id":4,  "category":"CrashLoop", "name":"CrashLoop extrême",        "input":{"name":"api","status":"Running","restarts":50,"namespace":"production",  "node":"n3","labels":{}},              "expected":1},
        {"id":5,  "category":"CrashLoop", "name":"CrashLoop kube-system",    "input":{"name":"dns","status":"Running","restarts":25,"namespace":"kube-system", "node":"n1","labels":{"k8s-app":"dns"}},"expected":1},
        {"id":6,  "category":"CrashLoop", "name":"1 restart — normal",       "input":{"name":"app","status":"Running","restarts":1, "namespace":"default",    "node":"n1","labels":{"app":"web"}},   "expected":0},
        {"id":7,  "category":"CrashLoop", "name":"2 restarts — limite",      "input":{"name":"app","status":"Running","restarts":2, "namespace":"default",    "node":"n1","labels":{"app":"web"}},   "expected":0},
        {"id":8,  "category":"CrashLoop", "name":"0 restart — sain",         "input":{"name":"app","status":"Running","restarts":0, "namespace":"monitoring",  "node":"n2","labels":{"app":"prom"}},  "expected":0},
        {"id":9,  "category":"CrashLoop", "name":"kube-system seuil haut",   "input":{"name":"proxy","status":"Running","restarts":19,"namespace":"kube-system","node":"n1","labels":{}},             "expected":0},
        {"id":10, "category":"CrashLoop", "name":"kube-system au seuil",     "input":{"name":"proxy","status":"Running","restarts":20,"namespace":"kube-system","node":"n1","labels":{}},             "expected":1},

        # ── POD PENDING (10 scénarios) ────────────────────────────
        {"id":11, "category":"Pending",   "name":"Pending sans node",         "input":{"name":"app","status":"Pending","restarts":0, "namespace":"default",    "node":None, "labels":{}},              "expected":1},
        {"id":12, "category":"Pending",   "name":"Pending production",        "input":{"name":"api","status":"Pending","restarts":0, "namespace":"production",  "node":None, "labels":{"app":"api"}},   "expected":1},
        {"id":13, "category":"Pending",   "name":"Pending AI namespace",      "input":{"name":"ml", "status":"Pending","restarts":0, "namespace":"ai-ns",       "node":None, "labels":{}},              "expected":1},
        {"id":14, "category":"Pending",   "name":"Pending avec restarts",     "input":{"name":"app","status":"Pending","restarts":2, "namespace":"default",    "node":None, "labels":{}},              "expected":1},
        {"id":15, "category":"Pending",   "name":"Pending staging",           "input":{"name":"app","status":"Pending","restarts":0, "namespace":"staging",     "node":None, "labels":{}},              "expected":1},
        {"id":16, "category":"Pending",   "name":"Running avec node — sain",  "input":{"name":"app","status":"Running","restarts":0, "namespace":"default",    "node":"n1", "labels":{"app":"web"}},   "expected":0},
        {"id":17, "category":"Pending",   "name":"Running monitoring sain",   "input":{"name":"prom","status":"Running","restarts":0, "namespace":"monitoring",  "node":"n2", "labels":{"app":"prom"}},  "expected":0},
        {"id":18, "category":"Pending",   "name":"Running production sain",   "input":{"name":"api","status":"Running","restarts":0, "namespace":"production",  "node":"n1", "labels":{"app":"api"}},   "expected":0},
        {"id":19, "category":"Pending",   "name":"Succeeded — terminé OK",    "input":{"name":"job","status":"Succeeded","restarts":0,"namespace":"default",    "node":"n1", "labels":{}},              "expected":0},
        {"id":20, "category":"Pending",   "name":"Unknown — node perdu",      "input":{"name":"app","status":"Unknown","restarts":0, "namespace":"default",    "node":None, "labels":{}},              "expected":1},

        # ── POD FAILED (8 scénarios) ──────────────────────────────
        {"id":21, "category":"Failed",    "name":"Failed standard",           "input":{"name":"app","status":"Failed","restarts":0, "namespace":"default",    "node":"n1","labels":{}},              "expected":1},
        {"id":22, "category":"Failed",    "name":"Failed avec restarts",      "input":{"name":"app","status":"Failed","restarts":5, "namespace":"default",    "node":"n1","labels":{}},              "expected":1},
        {"id":23, "category":"Failed",    "name":"Failed production critique","input":{"name":"api","status":"Failed","restarts":1, "namespace":"production",  "node":"n2","labels":{"app":"api"}},   "expected":1},
        {"id":24, "category":"Failed",    "name":"Failed kube-system",        "input":{"name":"dns","status":"Failed","restarts":0, "namespace":"kube-system", "node":"n1","labels":{}},              "expected":1},
        {"id":25, "category":"Failed",    "name":"Failed staging",            "input":{"name":"app","status":"Failed","restarts":2, "namespace":"staging",     "node":"n3","labels":{}},              "expected":1},
        {"id":26, "category":"Failed",    "name":"Failed monitoring",         "input":{"name":"prom","status":"Failed","restarts":0, "namespace":"monitoring",  "node":"n2","labels":{}},              "expected":1},
        {"id":27, "category":"Failed",    "name":"Failed ai-ns",              "input":{"name":"ml", "status":"Failed","restarts":0, "namespace":"ai-ns",       "node":"n1","labels":{}},              "expected":1},
        {"id":28, "category":"Failed",    "name":"Failed sans node",          "input":{"name":"app","status":"Failed","restarts":0, "namespace":"default",    "node":None,"labels":{}},              "expected":1},

        # ── PODS SAINS (12 scénarios) ─────────────────────────────
        {"id":29, "category":"Healthy",   "name":"Running avec labels app",   "input":{"name":"web","status":"Running","restarts":0, "namespace":"default",    "node":"n1","labels":{"app":"web"}},   "expected":0},
        {"id":30, "category":"Healthy",   "name":"Running sans labels",       "input":{"name":"app","status":"Running","restarts":0, "namespace":"default",    "node":"n2","labels":{}},              "expected":0},
        {"id":31, "category":"Healthy",   "name":"Running multi-labels",      "input":{"name":"api","status":"Running","restarts":0, "namespace":"production",  "node":"n1","labels":{"app":"api","env":"prod"}}, "expected":0},
        {"id":32, "category":"Healthy",   "name":"Running monitoring",        "input":{"name":"prom","status":"Running","restarts":0,"namespace":"monitoring",  "node":"n2","labels":{"app":"prom"}},  "expected":0},
        {"id":33, "category":"Healthy",   "name":"Running AI namespace",      "input":{"name":"ml", "status":"Running","restarts":0, "namespace":"ai-ns",       "node":"n3","labels":{"app":"ml"}},    "expected":0},
        {"id":34, "category":"Healthy",   "name":"Running test-app",          "input":{"name":"log","status":"Running","restarts":0, "namespace":"test-app",    "node":"n1","labels":{"app":"log"}},   "expected":0},
        {"id":35, "category":"Healthy",   "name":"kube-system DNS sain",      "input":{"name":"dns","status":"Running","restarts":5, "namespace":"kube-system", "node":"n1","labels":{"k8s-app":"dns"}},"expected":0},
        {"id":36, "category":"Healthy",   "name":"kube-system etcd sain",     "input":{"name":"etcd","status":"Running","restarts":10,"namespace":"kube-system","node":"n1","labels":{}},             "expected":0},
        {"id":37, "category":"Healthy",   "name":"kube-system apiserver",     "input":{"name":"api","status":"Running","restarts":15,"namespace":"kube-system","node":"n1","labels":{}},              "expected":0},
        {"id":38, "category":"Healthy",   "name":"kube-system scheduler",     "input":{"name":"sch","status":"Running","restarts":8, "namespace":"kube-system","node":"n1","labels":{}},              "expected":0},
        {"id":39, "category":"Healthy",   "name":"Running 1 restart sain",    "input":{"name":"app","status":"Running","restarts":1, "namespace":"staging",     "node":"n2","labels":{"app":"web"}},   "expected":0},
        {"id":40, "category":"Healthy",   "name":"Running 2 restarts sain",   "input":{"name":"api","status":"Running","restarts":2, "namespace":"default",    "node":"n1","labels":{"app":"api"}},   "expected":0},

        # ── CAS LIMITES (10 scénarios) ────────────────────────────
        {"id":41, "category":"Edge",      "name":"3 restarts user ns",        "input":{"name":"app","status":"Running","restarts":3, "namespace":"default",    "node":"n1","labels":{"app":"web"}},   "expected":1},
        {"id":42, "category":"Edge",      "name":"19 restarts kube-system",   "input":{"name":"prx","status":"Running","restarts":19,"namespace":"kube-system","node":"n1","labels":{}},              "expected":0},
        {"id":43, "category":"Edge",      "name":"21 restarts kube-system",   "input":{"name":"prx","status":"Running","restarts":21,"namespace":"kube-system","node":"n1","labels":{}},              "expected":1},
        {"id":44, "category":"Edge",      "name":"Running + Pending simulé",  "input":{"name":"app","status":"Pending","restarts":3, "namespace":"default",    "node":"n1","labels":{}},              "expected":1},
        {"id":45, "category":"Edge",      "name":"Failed 0 restart node ok",  "input":{"name":"job","status":"Failed","restarts":0, "namespace":"default",    "node":"n2","labels":{}},              "expected":1},
        {"id":46, "category":"Edge",      "name":"Succeeded multi restart",   "input":{"name":"job","status":"Succeeded","restarts":5,"namespace":"default",    "node":"n1","labels":{}},              "expected":0},
        {"id":47, "category":"Edge",      "name":"Running no-node anomalie",  "input":{"name":"app","status":"Running","restarts":0, "namespace":"production",  "node":None,"labels":{}},              "expected":1},
        {"id":48, "category":"Edge",      "name":"Running many labels sain",  "input":{"name":"app","status":"Running","restarts":0, "namespace":"default",    "node":"n1","labels":{"app":"a","env":"prod","tier":"front","version":"v2"}}, "expected":0},
        {"id":49, "category":"Edge",      "name":"production 3 restarts",     "input":{"name":"api","status":"Running","restarts":3, "namespace":"production",  "node":"n2","labels":{"app":"api"}},   "expected":1},
        {"id":50, "category":"Edge",      "name":"staging 2 restarts sain",   "input":{"name":"app","status":"Running","restarts":2, "namespace":"staging",     "node":"n1","labels":{"app":"web"}},   "expected":0},
    ]

    try:
        from ai_module.anomaly_detector_ml import predict_pod_v2
        results = []
        correct = 0
        by_category = {}

        for s in SCENARIOS:
            pred      = predict_pod_v2(s['input'])
            predicted = 1 if pred['is_anomaly'] else 0
            is_correct= predicted == s['expected']
            if is_correct:
                correct += 1

            cat = s['category']
            if cat not in by_category:
                by_category[cat] = {"total":0,"correct":0}
            by_category[cat]["total"]  += 1
            by_category[cat]["correct"] += 1 if is_correct else 0

            results.append({
                "id":          s['id'],
                "category":    s['category'],
                "name":        s['name'],
                "expected":    "Anomalie" if s['expected']==1 else "Normal",
                "predicted":   pred['label'],
                "probability": pred['probability'],
                "correct":     is_correct,
            })

        accuracy = correct / len(SCENARIOS)

        # Précision par catégorie
        category_scores = {
            cat: round(v["correct"]/v["total"]*100, 1)
            for cat, v in by_category.items()
        }

        return {
            "benchmark":       "KubeBench v2.0",
            "version":         "2.0",
            "total_scenarios": len(SCENARIOS),
            "correct":         correct,
            "accuracy":        round(accuracy, 4),
            "accuracy_pct":    round(accuracy*100, 1),
            "category_scores": category_scores,
            "results":         results,
            "model_used":      "RandomForest + règles expertes",
            "scientific_note": "Contribution C6 — 50 scénarios couvrant CrashLoop, Pending, Failed, Healthy, Edge cases",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

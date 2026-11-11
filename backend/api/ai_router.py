from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import json, re, subprocess
from core.config import settings

router = APIRouter()

class YAMLRequest(BaseModel):
    description: str
    model: Optional[str] = "auto"   # auto = Claude si clé, sinon Groq ; kubellm / kubellm-base = Ollama local ; kubellm-v1 = ancien
    max_attempts: Optional[int] = 3
    use_context: Optional[bool] = True     # False = ablation (mesure C1)
    campaign: Optional[str] = None
    repair: Optional[bool] = False          # réparation déterministe avant chaque nouvel essai

class ChatRequest(BaseModel):
    message: str
    history: Optional[list] = []
    model: Optional[str] = "groq"

YAML_SYSTEM = """Tu es KubeLLM, expert Kubernetes.
Génère un YAML valide et optimisé selon la description ET l'état du cluster fourni.
Réponds UNIQUEMENT en JSON sans markdown:
{
  "yaml": "contenu YAML complet",
  "reasoning": [{"step": 1, "title": "titre", "explanation": "explication"}],
  "confidence": 0.94,
  "warnings": []
}"""

CHAT_SYSTEM = """Tu es KubeLLM, assistant expert Kubernetes.
Réponds toujours en français, de façon claire et concise.
Pour les YAML, utilise des blocs ```yaml."""

async def get_cluster_context() -> dict:
    try:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-A", "--no-headers"],
            capture_output=True, text=True, timeout=15
        )
        lines = [l for l in result.stdout.strip().split('\n') if l]
        nodes_r = subprocess.run(
            ["kubectl", "get", "nodes", "--no-headers"],
            capture_output=True, text=True, timeout=15
        )
        return {
            "nodes":       len([l for l in nodes_r.stdout.strip().split('\n') if l]),
            "pods_running": sum(1 for l in lines if 'Running' in l),
            "pods_pending": sum(1 for l in lines if 'Pending' in l),
            "pods_failed":  sum(1 for l in lines if 'Error' in l or 'CrashLoop' in l),
        }
    except:
        return {"status": "indisponible"}

async def call_groq(messages: list) -> str:
    from groq import AsyncGroq
    client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    response = await client.chat.completions.create(
        model=settings.GROQ_MODEL,
        messages=messages,
        temperature=0.2,
        max_tokens=2000,
    )
    return response.choices[0].message.content

def parse_json_response(content: str, model_name: str) -> dict:
    try:
        clean = re.sub(r'```json|```', '', content).strip()
        data  = json.loads(clean)
        data["model_used"] = model_name
        return data
    except:
        return {
            "yaml":       content,
            "reasoning":  [],
            "confidence": 0.5,
            "warnings":   ["Format inattendu"],
            "model_used": model_name,
        }

class ValidateRequest(BaseModel):
    yaml: str


@router.get("/host-context")
async def host_context_endpoint(refresh: bool = False):
    """État réel de la machine hôte et du cluster, tel que le modèle le reçoit."""
    from ai_module import host_context
    ctx = await host_context.collect(force=refresh)
    from ai_module.llm_client import available_providers
    return {**ctx, "providers": available_providers(),
            "prompt_preview": host_context.to_prompt(ctx)}


@router.post("/generate-yaml")
async def generate_yaml(request: YAMLRequest):
    """
    Génération vérifiée : contexte hôte → modèle → contrôle de capacité →
    `kubectl apply --dry-run=server` → correction automatique (≤ 3 tentatives).
    """
    if request.model != "kubellm-v1":
        from ai_module import yaml_pipeline
        from ai_module.llm_client import LLMUnavailable, LOCAL_MODELS
        provider = request.model if request.model in ("claude", "groq", *LOCAL_MODELS) else None
        if provider in LOCAL_MODELS:
            from ai_module.llm_client import available_providers
            if provider not in available_providers():
                raise HTTPException(status_code=503, detail=f"{provider} indisponible : Ollama arrêté ou modèle non installé")
        try:
            return await yaml_pipeline.generate(request.description, provider=provider,
                                                max_attempts=request.max_attempts or 3,
                                                use_context=request.use_context is not False,
                                                campaign=request.campaign,
                                                repair=bool(request.repair))
        except LLMUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Génération échouée : {e}")

    context = await get_cluster_context()

    # Modèle fine-tuné local : uniquement sur demande explicite (objet d'étude C1)
    if request.model == "kubellm-v1":
        try:
            from ai_module.kubellm_inference import generate_with_kubellm, is_available
            if is_available():
                result = generate_with_kubellm(request.description, context)
                if result:
                    result["cluster_context"] = context
                    return result
        except Exception as e:
            print(f"⚠️ KubeLLM fallback: {e}")

    # 2. Fallback Groq
    if settings.GROQ_API_KEY:
        prompt   = f"Description: {request.description}\n\nÉtat cluster: {json.dumps(context)}"
        messages = [
            {"role": "system", "content": YAML_SYSTEM},
            {"role": "user",   "content": prompt}
        ]
        content = await call_groq(messages)
        result  = parse_json_response(content, "groq-llama-3.3")
        result["cluster_context"] = context
        return result

    # 3. Template de base
    return {
        "yaml":        f"# Template généré\n# {request.description}\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: mon-app\n  namespace: default\nspec:\n  replicas: 1\n  selector:\n    matchLabels:\n      app: mon-app\n  template:\n    metadata:\n      labels:\n        app: mon-app\n    spec:\n      containers:\n      - name: mon-app\n        image: nginx:latest\n        resources:\n          requests:\n            cpu: '100m'\n            memory: '128Mi'\n          limits:\n            cpu: '500m'\n            memory: '512Mi'\n",
        "reasoning":   [{"step": 1, "title": "Template", "explanation": "Aucune API configurée"}],
        "confidence":  0.3,
        "warnings":    ["Configurer GROQ_API_KEY dans .env"],
        "model_used":  "template",
        "cluster_context": context,
    }

@router.get("/detect")
async def detect_anomalies():
    """
    Détection d'anomalies en production — règles v3 (stabilité récente et
    disponibilité), retenues à l'issue des mesures du chapitre 7.

    Avant : règles v1, qui signalaient tout pod ayant redémarré 3 fois ou plus
    depuis sa création. Ce compteur cumulé mesure l'âge du pod plutôt que sa
    santé : après un redémarrage de minikube, tous les pods système étaient
    signalés « critiques ». v3 ne signale un pod que s'il redémarre ENCORE
    (dernier redémarrage il y a moins de 2 min), s'il n'est jamais prêt, ou s'il
    est bloqué (Pending) ou en échec (Failed).
    """
    problems = []
    try:
        import json as j
        from ai_module.detection_comparator import fetch_real_pods, rules_v3_predict

        pods = fetch_real_pods(exclude_namespaces=set())
        for pod in pods:
            verdict = rules_v3_predict(pod)
            if not verdict["is_anomaly"]:
                continue
            name, ns, status = pod["name"], pod["namespace"], pod["status"]
            reasons = verdict["reasons"]
            waiting = pod.get("waiting_reason")
            if status == "Failed":
                ptype, severity = "PodFailed", "critical"
            elif status == "Pending":
                ptype, severity = "PodPending", "critical"
            elif verdict.get("stabilised") is False:
                # le type est la raison réelle donnée par Kubernetes quand elle existe
                ptype, severity = (waiting or "RedémarrageRécent"), "critical"
            else:
                ptype, severity = "NonDisponible", "warning"
            problems.append({
                "id":          f"{ptype.lower()}-{ns}-{name}",
                "type":        ptype,
                "severity":    severity,
                "title":       f"{ptype} · {name}",
                "description": " ; ".join(reasons),
                "reasons":     reasons,
                "namespace":   ns,
                "resource":    name,
                "restarts":    pod.get("restarts", 0),
                "detector":    "rules_v3",
            })

        # Services sans endpoints
        svc_result = subprocess.run(
            ["kubectl", "get", "services", "-A", "-o", "json"],
            capture_output=True, text=True, timeout=20
        )
        svcs = j.loads(svc_result.stdout or "{}")
        for svc in svcs.get("items", []):
            selector = svc.get("spec", {}).get("selector", {})
            if selector:
                svc_ns   = svc["metadata"]["namespace"]
                svc_name = svc["metadata"]["name"]
                label_sel = ",".join(f"{k}={v}" for k,v in selector.items())
                pods_r = subprocess.run(
                    ["kubectl", "get", "pods", "-n", svc_ns, "-l", label_sel, "--no-headers"],
                    capture_output=True, text=True, timeout=15
                )
                if not pods_r.stdout.strip():
                    problems.append({
                        "id":          f"svc-{svc_ns}-{svc_name}",
                        "type":        "ServiceSansPod",
                        "severity":    "critical",
                        "title":       f"ServiceSansPod · {svc_name}",
                        "description": "Le selector du Service ne correspond à aucun pod.",
                        "reasons":     ["Le selector du Service ne correspond à aucun pod."],
                        "namespace":   svc_ns,
                        "resource":    svc_name,
                        "detector":    "rules_v3",
                    })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    severity_order = {"critical": 0, "warning": 1, "info": 2}
    problems.sort(key=lambda x: severity_order.get(x.get("severity","info"), 2))
    return {
        "detector": "rules_v3",
        "problems": problems,
        "critical": len([p for p in problems if p["severity"]=="critical"]),
        "warnings": len([p for p in problems if p["severity"]=="warning"]),
        "total":    len(problems)
    }


# ══════════════════════════════════════════════════════════════════════════════
# C3 — COMPARAISON RÈGLES EXPERTES / RANDOM FOREST
#
# /detect ci-dessus reste le moteur de PRODUCTION : règles seules, rapide,
# déterministe. Les endpoints ci-dessous ajoutent la confrontation avec le
# modèle statistique, sans modifier le comportement de production.
#
# Cette séparation est volontaire et doit être défendue comme telle : mettre un
# modèle entraîné sur données synthétiques en production sans validation
# préalable serait méthodologiquement indéfendable. On mesure d'abord, on
# décide ensuite.
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# PRÉDICTION D'ÉPUISEMENT MÉMOIRE
#
# Contrairement à /detect qui constate un état présent, ces endpoints annoncent
# une échéance. La prédiction n'est possible que sur une grandeur continue
# bornée : la consommation mémoire face à la limite du conteneur.
#
# L'indice de confiance qualifie la fiabilité de l'EXTRAPOLATION — ajustement
# linéaire, accord entre estimateurs, stabilité des prédictions successives.
# Ce n'est pas une probabilité de défaillance : l'établir exigerait une base
# fréquentielle dont on ne dispose pas.
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/predict/memory")
async def predict_memory_exhaustion(min_confidence: float = 0.25):
    """
    Conteneurs dont la consommation mémoire tend vers leur limite.

    Le champ `warmup` signale un historique insuffisant : l'absence de
    prédiction ne vaut alors pas absence de risque.
    """
    try:
        from ai_module.oom_predictor import get_predictions
        return get_predictions(min_confidence=min_confidence)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/predict/memory/{namespace}/{pod_name}")
async def predict_memory_pod(namespace: str, pod_name: str):
    """Série mémoire d'un conteneur et sa prédiction courante, pour tracé."""
    try:
        from ai_module.oom_predictor import get_pod_history
        return get_pod_history(namespace, pod_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/detect/compare")
async def compare_detectors():
    """
    Applique les deux détecteurs aux mêmes pods réels et mesure leur accord.

    Retourne l'accord brut, le kappa de Cohen, la matrice croisée et le détail
    de chaque désaccord. Chaque appel est journalisé pour analyse longitudinale.
    """
    try:
        from ai_module.detection_comparator import compare
        return compare(log=True)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=f"Modèle indisponible : {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/detect/compare/history")
async def compare_history():
    """
    Agrège le journal accumulé : kappa moyen, écart-type, et surtout les
    désaccords RÉCURRENTS — ceux qui reviennent d'une observation à l'autre et
    révèlent une divergence structurelle plutôt qu'un état transitoire.
    """
    try:
        from ai_module.detection_comparator import analyse_log
        return analyse_log()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate-yaml")
async def validate_yaml(request: ValidateRequest):
    """Revalide un YAML édité à la main, avec les mêmes arbitres que la génération."""
    import asyncio
    from ai_module import host_context, yaml_pipeline
    ctx = await host_context.collect()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, yaml_pipeline.validate_text, request.yaml, ctx)


@router.get("/generate-yaml/stats")
async def generation_stats():
    """Taux de manifestes validés au 1er essai et après correction (données du chapitre C1)."""
    from ai_module import yaml_pipeline
    return yaml_pipeline.stats()


class AgentRequest(BaseModel):
    message: str
    history: Optional[list] = []


@router.post("/agent")
async def agent(request: AgentRequest):
    """
    Agent de dépannage : enquête sur le cluster avec des outils en lecture seule
    (get, describe, logs, événements, top…) avant de répondre. Renvoie la réponse
    et la trace de chaque vérification effectuée.
    """
    from ai_module.llm_client import available_providers
    if "claude" not in available_providers():
        # Sans clé Anthropic : repli sur l'assistant à contexte injecté
        res = await chat(ChatRequest(message=request.message, history=request.history, model="groq"))
        return {**res, "trace": [], "tools_used": 0, "mode": "contexte seul"}
    from ai_module import k8s_agent
    try:
        out = await k8s_agent.run(request.message, request.history)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Agent indisponible : {e}")
    return {**out, "mode": "agent"}


@router.post("/chat")
async def chat(request: ChatRequest):
    from ai_module import host_context, llm_client
    ctx = await host_context.collect()
    system = f"{CHAT_SYSTEM}\n\n{host_context.to_prompt(ctx)}"
    history = [h for h in (request.history or [])[-10:] if isinstance(h, dict)]
    # La page Assistant inclut déjà le message courant dans l'historique
    if history and history[-1].get("role") == "user" and history[-1].get("content") == request.message:
        messages = history
    else:
        messages = history + [{"role": "user", "content": request.message}]
    provider = request.model if request.model in ("claude", "groq") else None
    try:
        out = await llm_client.chat(system, messages, provider=provider)
    except llm_client.LLMUnavailable as e:
        return {"response": f"⚠️ {e}", "model_used": "none",
                "cluster_context": await get_cluster_context()}
    return {
        "response":    out["text"],
        "model_used":  out["model"],
        "cluster_context": await get_cluster_context(),
    }

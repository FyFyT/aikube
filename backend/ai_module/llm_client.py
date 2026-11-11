"""
Accès unifié aux modèles de langage distants.

Ordre de préférence :
  1. Claude (API Anthropic) si ANTHROPIC_API_KEY est défini ;
  2. Groq (llama-3.3-70b) si GROQ_API_KEY est défini.

Modèles LOCAUX servis par Ollama (fine-tuning, chapitre C1) :
  kubellm       modèle spécialisé v2 (corpus v3 vérifié, LoRA sur Qwen2.5-Coder-1.5B)
  kubellm-base  le même modèle de base SANS fine-tuning (témoin)
Ils ne sont jamais choisis par défaut : uniquement sur demande explicite, pour la
mesure. L'ancien kubellm-v1 (phi-2) reste hors ligne : 0/12 manifestes applicables.

Note de conformité : les sorties de Claude servent ici à répondre à
l'utilisateur. Elles ne doivent PAS servir à constituer le corpus
d'entraînement du modèle fine-tuné (conditions d'utilisation d'Anthropic).
Le corpus v3 est distillé depuis gpt-oss-120b (poids ouverts), via Groq ou Cerebras.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from core.config import settings

MANIFEST_TOOL = {
    "name": "submit_manifest",
    "description": "Soumet le manifeste Kubernetes généré.",
    "input_schema": {
        "type": "object",
        "properties": {
            "yaml": {"type": "string",
                     "description": "Manifeste(s) YAML complet(s), documents séparés par ---"},
            # NB : ne pas demander le « raisonnement » du modèle dans un champ d'outil :
            # la documentation d'Anthropic signale que cela peut déclencher un refus
            # (réponse vide). On demande les CHOIX de configuration et leur justification.
            "choices": {
                "type": "array",
                "description": "Choix de configuration (namespace, image, ressources, exposition…) "
                               "et la donnée réelle du cluster qui justifie chacun",
                "items": {"type": "object",
                          "properties": {"title": {"type": "string"},
                                         "explanation": {"type": "string"}},
                          "required": ["title", "explanation"]},
            },
            "warnings": {"type": "array", "items": {"type": "string"},
                         "description": "Points d'attention pour l'utilisateur"},
        },
        "required": ["yaml", "choices"],
    },
}


class LLMUnavailable(RuntimeError):
    pass


LOCAL_MODELS = {"kubellm": "KUBELLM_MODEL", "kubellm-base": "KUBELLM_BASE_MODEL",
                "kubellm-v3": "KUBELLM_V3_MODEL", "kubellm-v4": "KUBELLM_V4_MODEL"}
_ollama = {"t": 0.0, "names": set()}


def _ollama_names() -> set:
    """Modèles installés dans Ollama (cache 30 s ; vide si Ollama ne tourne pas)."""
    if time.time() - _ollama["t"] < 30:
        return _ollama["names"]
    names = set()
    try:
        import httpx
        r = httpx.get(f"{settings.OLLAMA_URL}/api/tags", timeout=2)
        if r.status_code == 200:
            for m in r.json().get("models", []):
                n = m.get("name", "")
                names.add(n)
                if n.endswith(":latest"):
                    names.add(n[:-7])
    except Exception:  # noqa: BLE001
        pass
    _ollama.update(t=time.time(), names=names)
    return names


def available_providers() -> list[str]:
    out = []
    if getattr(settings, "ANTHROPIC_API_KEY", None):
        out.append("claude")
    if settings.GROQ_API_KEY:
        out.append("groq")
    names = _ollama_names()
    for prov, var in LOCAL_MODELS.items():       # jamais en tête : jamais choisi par défaut
        if getattr(settings, var) in names:
            out.append(prov)
    return out


def pick_provider(requested: Optional[str]) -> str:
    provs = available_providers()
    if not provs:
        raise LLMUnavailable("Aucune clé configurée : ajoute ANTHROPIC_API_KEY "
                             "(ou GROQ_API_KEY) dans le fichier .env")
    if requested in provs:
        return requested
    return provs[0]


def model_label(provider: str) -> str:
    if provider == "claude":
        return settings.ANTHROPIC_MODEL
    if provider in LOCAL_MODELS:
        return getattr(settings, LOCAL_MODELS[provider])
    return settings.GROQ_MODEL


# ── Génération structurée d'un manifeste ────────────────────────────────────

async def generate_manifest(system: str, user: str,
                            provider: Optional[str] = None) -> dict:
    """Renvoie {yaml, reasoning, warnings, provider, model, latency_ms, usage}."""
    prov = pick_provider(provider)
    t0 = time.time()
    if prov == "claude":
        out = await _claude_manifest(system, user)
    elif prov in LOCAL_MODELS:
        out = await _ollama_manifest(user, getattr(settings, LOCAL_MODELS[prov]))
    else:
        out = await _groq_manifest(system, user)
    out.update(provider=prov, model=model_label(prov),
               latency_ms=round((time.time() - t0) * 1000))
    if "choices" in out and not out.get("reasoning"):
        out["reasoning"] = out.pop("choices")
    out["yaml"] = _strip_fences(out.get("yaml", "") or "")
    out.setdefault("reasoning", [])
    out.setdefault("warnings", [])
    return out


async def _ollama_manifest(user: str, model: str) -> dict:
    """Modèle local. On utilise le prompt système de l'ENTRAÎNEMENT (kubellm_prompt), pas
    celui de Claude : un modèle interrogé dans un autre format que celui appris serait
    mesuré sur autre chose. Le témoin (modèle de base) reçoit exactement le même."""
    import httpx
    from ai_module.kubellm_prompt import STUDENT_SYSTEM, parse_student_output
    async with httpx.AsyncClient(timeout=600) as c:
        r = await c.post(f"{settings.OLLAMA_URL}/api/chat", json={
            "model": model, "stream": False,
            "messages": [{"role": "system", "content": STUDENT_SYSTEM},
                         {"role": "user", "content": user}],
            # 1200 jetons suffisent à un manifeste ; au-delà, c'est un modèle qui divague,
            # ce qui coûte plusieurs minutes sur processeur
            "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 1200},
            "keep_alive": "30m"})
    if r.status_code != 200:
        raise LLMUnavailable(f"Ollama ({model}) : {r.status_code} {r.text[:200]}")
    j = r.json()
    out = parse_student_output((j.get("message") or {}).get("content", ""))
    out["usage"] = {"input_tokens": j.get("prompt_eval_count", 0), "output_tokens": j.get("eval_count", 0)}
    out["stop_reason"] = j.get("done_reason")
    return out


async def _claude_manifest(system: str, user: str) -> dict:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    # Certains modèles récents refusent tool_choice "tool"/"any" : on laisse
    # le choix en "auto", on exige l'outil par consigne, et on sait relire
    # une réponse en texte libre si le modèle ne l'appelle pas.
    resp = await client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=8000,
        system=system + "\n\nSoumets TOUJOURS ta réponse en appelant l'outil submit_manifest.",
        tools=[MANIFEST_TOOL],
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": user}],
    )
    data: dict = {}
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_manifest":
            data = dict(block.input)
            break
    if not data:
        text = "".join(getattr(b, "text", "") or "" for b in resp.content)
        data = _parse_free_text(text) if text.strip() else {"yaml": "", "warnings": []}
    data["stop_reason"] = resp.stop_reason
    data["block_types"] = [getattr(b, "type", "?") for b in resp.content]
    data["usage"] = {"input_tokens": resp.usage.input_tokens,
                     "output_tokens": resp.usage.output_tokens}
    return data


def _parse_free_text(text: str) -> dict:
    """Réponse sans outil : JSON brut, ou bloc ```yaml + explication autour."""
    clean = re.sub(r"```json|```", "", text).strip() if text.strip().startswith(("{", "```json")) else ""
    if clean:
        try:
            d = json.loads(clean)
            if isinstance(d, dict) and d.get("yaml"):
                return d
        except json.JSONDecodeError:
            pass
    blocks = re.findall(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.S)
    if blocks:
        prose = re.sub(r"```(?:ya?ml)?\s*\n.*?```", "", text, flags=re.S).strip()
        reasoning = ([{"title": "Explication du modèle", "explanation": prose[:1500]}]
                     if prose else [])
        return {"yaml": "\n---\n".join(b.strip() for b in blocks), "reasoning": reasoning,
                "warnings": []}
    return {"yaml": text, "reasoning": [], "warnings": ["Réponse non structurée"]}


async def _groq_manifest(system: str, user: str) -> dict:
    from groq import AsyncGroq
    client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    sys_json = system + (
        "\n\nRéponds UNIQUEMENT par un objet JSON, sans markdown : "
        '{"yaml": "...", "choices": [{"title": "...", "explanation": "..."}], '
        '"warnings": ["..."]}')
    kwargs = dict(model=settings.GROQ_MODEL,
                  messages=[{"role": "system", "content": sys_json},
                            {"role": "user", "content": user}],
                  temperature=0.2, max_tokens=8000)   # gpt-oss raisonne avant de répondre
    try:
        resp = await client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
    except Exception:  # noqa: BLE001  modèle sans mode JSON : on parse le texte libre
        resp = await client.chat.completions.create(**kwargs)
    content = resp.choices[0].message.content or ""
    try:
        data = json.loads(re.sub(r"```json|```", "", content).strip())
    except json.JSONDecodeError:
        data = {"yaml": content, "reasoning": [], "warnings": ["Format JSON inattendu"]}
    u = getattr(resp, "usage", None)
    if u:
        data["usage"] = {"input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens}
    return data


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    m = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", t, re.S)
    return (m.group(1) if m else t).strip() + "\n"


# ── Conversation libre (assistant) ──────────────────────────────────────────

async def chat(system: str, messages: list[dict],
               provider: Optional[str] = None, max_tokens: int = 2000) -> dict:
    prov = pick_provider(provider)
    if prov in LOCAL_MODELS:                 # les modèles locaux ne servent qu'à générer du YAML
        prov = pick_provider(None)
    t0 = time.time()
    # Ne garder que les rôles acceptés et du texte
    clean = [{"role": m["role"], "content": str(m.get("content", ""))}
             for m in messages if m.get("role") in ("user", "assistant")]
    if prov == "claude":
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        # L'API exige une alternance commençant par 'user'
        while clean and clean[0]["role"] != "user":
            clean.pop(0)
        resp = await client.messages.create(
            model=settings.ANTHROPIC_MODEL, max_tokens=max_tokens,
            system=system, messages=clean)
        text = "".join(getattr(b, "text", "") for b in resp.content)
    else:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        resp = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "system", "content": system}] + clean,
            temperature=0.3, max_tokens=max(6000, max_tokens))
        text = resp.choices[0].message.content
    return {"text": text, "provider": prov, "model": model_label(prov),
            "latency_ms": round((time.time() - t0) * 1000)}

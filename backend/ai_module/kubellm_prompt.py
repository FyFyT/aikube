"""
Format d'entrée et de sortie du modèle spécialisé (KubeLLM v2).

Le MÊME format sert à construire le corpus (scripts/build_corpus_v3.py) et à
interroger le modèle dans l'application : un modèle entraîné sur un format et
interrogé sur un autre est mesuré sur autre chose que ce qu'il a appris.

Entrée  : état de l'hôte et du cluster (host_context.to_prompt) + demande.
Sortie  : uniquement le manifeste YAML ; les réductions imposées par la capacité
          sont signalées en tête par des commentaires « # Avertissement : … ».
"""
from __future__ import annotations

import re

STUDENT_SYSTEM = """Tu es KubeLLM, spécialiste Kubernetes de la plateforme AI-Kube.
Tu reçois l'état réel de la machine hôte et du cluster, puis une demande.
Réponds UNIQUEMENT par le manifeste YAML, prêt à appliquer sur CE cluster :
- versions d'API exactement comme listées ;
- requests ET limits (cpu, memory) pour chaque conteneur, qui tiennent dans la capacité libre
  (un pod sur UN nœud, la somme des répliques dans le cluster) ;
- namespace « default » sauf demande contraire (document Namespace s'il n'existe pas) ;
- images avec un tag précis, jamais :latest ;
- si tu dois réduire la demande, écris en tête « # Avertissement : » et la raison."""


def student_user(context_text: str, request: str) -> str:
    return f"{context_text}\n\n## Demande de l'utilisateur\n{request.strip()}"


def student_target(yaml_text: str, warnings: list[str] | None = None) -> str:
    head = "".join(f"# Avertissement : {w.strip()}\n" for w in (warnings or []) if w and w.strip())
    return head + yaml_text.strip() + "\n"


def parse_student_output(text: str) -> dict:
    """Sépare les avertissements (commentaires de tête) du YAML ; retire d'éventuelles balises."""
    t = re.sub(r"^```(?:ya?ml)?\s*|\s*```\s*$", "", text.strip())
    warnings = re.findall(r"^#\s*Avertissement\s*:\s*(.+)$", t, flags=re.M)
    return {"yaml": t, "warnings": warnings, "reasoning": []}

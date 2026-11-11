from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    APP_NAME: str = "AI-Kube"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    SECRET_KEY: str = "dev-secret"
    KUBECONFIG: str = "~/.kube/config"
    K8S_NAMESPACE: str = "default"
    OPENAI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: str = "claude-sonnet-5-5"
    # llama-3.3-70b-versatile a été retiré par Groq le 16/08/2026
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    HUGGINGFACE_TOKEN: Optional[str] = None
    BASE_MODEL: str = "microsoft/phi-2"
    FINE_TUNED_MODEL_PATH: str = "./models/fine_tuned/kubellm-v1"
    USE_FINE_TUNED: bool = False
    PROMETHEUS_URL: str = "http://localhost:9090"
    # CI/CD (page DevOps) : dépôt GitHub « propriétaire/nom » ; jeton facultatif
    # (lecture seule, nécessaire pour un dépôt privé ou au-delà de 60 requêtes/heure)
    GITHUB_REPO: Optional[str] = None
    GITHUB_TOKEN: Optional[str] = None
    # Application mobile : jeton exigé pour tout accès qui ne vient pas de la machine elle-même
    MOBILE_TOKEN: Optional[str] = None
    # Notifications sur le téléphone par ntfy (application libre) : canal secret à inventer
    NTFY_TOPIC: Optional[str] = None
    NTFY_SERVER: str = "https://ntfy.sh"
    NTFY_TOKEN: Optional[str] = None
    # Modèles locaux (fine-tuning) servis par Ollama
    OLLAMA_URL: str = "http://localhost:11434"
    KUBELLM_MODEL: str = "kubellm-v2"
    KUBELLM_BASE_MODEL: str = "qwen2.5-coder:1.5b"
    KUBELLM_V3_MODEL: str = "kubellm-v3"   # 2e entraînement (capacité + corrections)
    KUBELLM_V4_MODEL: str = "kubellm-v4"   # 3e entraînement : erreurs propres de v3 corrigées

    class Config:
        env_file = "/home/tamft/aikube/.env"
        extra = "ignore"

settings = Settings()

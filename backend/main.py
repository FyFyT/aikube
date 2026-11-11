from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from core.config import settings
from core.kubernetes_manager import k8s_manager
from api.cluster_router import router as cluster_router
from api.ai_router import router as ai_router
from api.security_router import router as security_router
from api.devops_router import router as devops_router
from api.websocket_router import router as ws_router
from api.resources_router import router as resources_router
from api.devops_v2_router import router as devops_v2_router
from api.mobile_router import router as mobile_router
from api.apps_router import router as apps_router

try:
    from api.ml_router import router as ml_router
    ML_AVAILABLE = True
except Exception as e:
    print(f"⚠️ ML router non disponible: {e}")
    ML_AVAILABLE = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 AI-Kube v{settings.APP_VERSION} démarrage...")
    await k8s_manager.connect()
    print(f"✅ Kubernetes: {'connecté' if k8s_manager.connected else 'non connecté'}")
    # Collecte de l'usage réel dès le démarrage : les recommandations de
    # dimensionnement exigent quelques minutes d'historique.
    try:
        from ai_module import rightsizing, oom_predictor
        rightsizing.ensure_collector()
        oom_predictor.ensure_collector()
    except Exception as e:
        print(f"⚠️ Collecte d'usage non démarrée: {e}")
    # Notifications sur le téléphone (ntfy), si NTFY_TOPIC est défini dans .env
    try:
        from ai_module import ntfy_notifier
        ntfy_notifier.ensure_started()
    except Exception as e:
        print(f"⚠️ Notifications ntfy non démarrées: {e}")
    yield
    print("👋 Arrêt AI-Kube")

app = FastAPI(
    title="AI-Kube API",
    description="Intelligent Kubernetes Manager — Master Recherche 2025",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Accès distant (application mobile) ─────────────────────────────────────
# Le backend peut modifier le cluster (déploiements, retours arrière…). Dès qu'il
# écoute sur le réseau (--host 0.0.0.0), toute requête qui ne vient pas de la
# machine elle-même doit présenter le jeton MOBILE_TOKEN du fichier .env.
import hmac
from fastapi import Request
from fastapi.responses import JSONResponse

LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}


@app.middleware("http")
async def remote_access_guard(request: Request, call_next):
    client = request.client.host if request.client else ""
    if client in LOCAL_CLIENTS or request.method == "OPTIONS":
        return await call_next(request)
    token = getattr(settings, "MOBILE_TOKEN", None)
    if not token:
        return JSONResponse({"detail": "Accès distant désactivé : définir MOBILE_TOKEN dans .env"},
                            status_code=403)
    given = request.headers.get("authorization", "")
    if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
        return JSONResponse({"detail": "Jeton invalide"}, status_code=401)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000","http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cluster_router,  prefix="/api/cluster",  tags=["Cluster"])
app.include_router(ai_router,       prefix="/api/ai",       tags=["IA"])
app.include_router(security_router, prefix="/api/security", tags=["Sécurité"])
app.include_router(devops_v2_router, prefix="/api/devops/v2", tags=["DevOps v2"])
app.include_router(mobile_router, prefix="/api/mobile", tags=["Mobile"])
app.include_router(apps_router, prefix="/api/apps", tags=["Mes applications"])
app.include_router(devops_router,   prefix="/api/devops",   tags=["DevOps"])
app.include_router(ws_router, prefix="/ws", tags=["WebSocket"])
app.include_router(resources_router, prefix="/api/resources", tags=["Ressources"])
if ML_AVAILABLE:
    app.include_router(ml_router,   prefix="/api/ml",       tags=["ML Science"])

@app.get("/")
async def root():
    return {
        "app": "AI-Kube",
        "version": settings.APP_VERSION,
        "status": "running",
        "k8s_connected": k8s_manager.connected,
        "endpoints": {
            "cluster":  "/api/cluster",
            "ia":       "/api/ai",
            "security": "/api/security",
            "devops":   "/api/devops",
            "ml":       "/api/ml" if ML_AVAILABLE else "non disponible",
            "docs":     "/docs",
        }
    }

@app.get("/health")
async def health():
    return {"status":"healthy","k8s":k8s_manager.connected}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

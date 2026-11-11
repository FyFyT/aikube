"""
API « Mes applications » : importer, modifier, construire, déployer et ouvrir sa propre application.

  GET    /api/apps/list                         applications connues (espace de travail, versions)
  GET    /api/apps/inspect?path=…               ce que contient un dossier du PC
  POST   /api/apps/{nom}/import/path            copier un dossier du PC dans l'espace de travail
  POST   /api/apps/{nom}/import/zip             importer un .zip (corps brut, sans multipart)
  POST   /api/apps/{nom}/import/begin           dossier choisi dans le navigateur : début…
  PUT    /api/apps/{nom}/import/file?path=…     …un fichier à la fois (corps brut)…
  POST   /api/apps/{nom}/import/finish          …puis remplacement de l'espace de travail
  GET    /api/apps/{nom}/files                  liste des fichiers
  GET    /api/apps/{nom}/file?path=…            contenu d'un fichier
  PUT    /api/apps/{nom}/file                   enregistrer (ou créer) un fichier
  DELETE /api/apps/{nom}/file?path=…            supprimer un fichier
  GET    /api/apps/{nom}/export                 télécharger l'espace de travail en .zip
  POST   /api/apps/{nom}/ai-edit                propositions de l'IA (rien n'est écrit)
  POST   /api/apps/build                        construction sur tous les nœuds (tâche de fond)
  GET    /api/apps/jobs/{id}                    avancement de la construction
  GET    /api/apps/images                       versions déjà construites
  POST   /api/apps/manifest                     Deployment + Service pour le déploiement surveillé
  GET    /api/apps/services                     services ouvrables dans le navigateur
  POST   /api/apps/access                       ouvrir un accès (port-forward géré)
  DELETE /api/apps/access/{ns}/{service}        fermer cet accès

Le déploiement lui-même passe par la page Déploiement (aperçu, surveillance,
retour arrière automatique) : ce routeur ne modifie jamais le cluster.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ai_module import app_builder as ab

router = APIRouter()


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ab.AppError as e:
        raise HTTPException(status_code=400, detail=str(e))


class BuildIn(BaseModel):
    name: str
    tag: str
    path: Optional[str] = None          # absent : l'espace de travail de l'application
    port: Optional[int] = None


class ManifestIn(BaseModel):
    name: str
    tag: str
    port: int
    replicas: int = 2
    namespace: str = "default"


class AccessIn(BaseModel):
    namespace: str
    service: str
    port: int


class PathIn(BaseModel):
    path: str


class FileIn(BaseModel):
    path: str
    content: str


class AiEditIn(BaseModel):
    instruction: str
    path: Optional[str] = None


@router.get("/list")
def list_apps():
    return {"apps": ab.list_apps()}


@router.get("/inspect")
def inspect(path: str):
    src = _guard(ab.resolve_source, path)
    return ab.inspect_dir(src)


# ── Import ──────────────────────────────────────────────────────────────────

@router.post("/{name}/import/path")
def import_path(name: str, body: PathIn):
    return _guard(ab.import_path, name, body.path)


@router.post("/{name}/import/zip")
async def import_zip(name: str, request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Aucun fichier reçu.")
    return _guard(ab.import_zip, name, data)


@router.post("/{name}/import/begin")
def import_begin(name: str):
    _guard(ab.import_begin, name)
    return {"ok": True}


@router.put("/{name}/import/file")
async def import_file(name: str, path: str, request: Request):
    data = await request.body()
    _guard(ab.import_file, name, path, data)
    return {"ok": True}


@router.post("/{name}/import/finish")
def import_finish(name: str):
    return _guard(ab.import_finish, name)


# ── Fichiers de l'espace de travail ─────────────────────────────────────────

@router.get("/{name}/files")
def files(name: str):
    return {"files": _guard(ab.list_files, name)}


@router.get("/{name}/file")
def read_file(name: str, path: str):
    return _guard(ab.read_file, name, path)


@router.put("/{name}/file")
def write_file(name: str, body: FileIn):
    return _guard(ab.write_file, name, body.path, body.content)


@router.delete("/{name}/file")
def delete_file(name: str, path: str):
    return {"deleted": _guard(ab.delete_file, name, path)}


@router.get("/{name}/export")
def export(name: str):
    data = _guard(ab.export_zip, name)
    return Response(content=data, media_type="application/zip",
                     headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})


@router.post("/{name}/ai-edit")
async def ai_edit(name: str, body: AiEditIn):
    try:
        return await ab.ai_edit(name, body.instruction, body.path)
    except ab.AppError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Construction, manifeste, accès ──────────────────────────────────────────

@router.post("/build")
def build(body: BuildIn):
    return _guard(ab.start_build, body.path, body.name, body.tag, body.port)


@router.get("/jobs/{job_id}")
def job(job_id: str):
    j = ab.get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Construction inconnue (le backend a peut-être redémarré).")
    return j


@router.get("/images")
def images():
    return {"images": ab.list_images()}


@router.post("/manifest")
def manifest(body: ManifestIn):
    return {"yaml": _guard(ab.manifest, body.name, body.tag, body.port, body.replicas, body.namespace)}


@router.get("/services")
def services():
    return {"services": _guard(ab.list_services), "access": ab.list_access()}


@router.post("/access")
def open_access(body: AccessIn):
    return _guard(ab.open_access, body.namespace, body.service, body.port)


@router.delete("/access/{namespace}/{service}")
def close_access(namespace: str, service: str):
    return {"closed": ab.close_access(f"{namespace}/{service}")}

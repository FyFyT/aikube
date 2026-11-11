"""Tests de « Mes applications » (sans cluster : fonctions pures et import de zip)."""
import io
import os
import zipfile

import pytest

from ai_module import app_builder as ab


def test_noms_et_versions():
    assert ab.check_name("monapp") == "monapp"
    assert ab.check_tag("1.0") == "1.0"
    for bad in ("MonApp", "-x", "a_b", "x" * 41, ""):
        with pytest.raises(ab.AppError):
            ab.check_name(bad)
    for bad in ("", ".1", "1.0;rm", "a b"):
        with pytest.raises(ab.AppError):
            ab.check_tag(bad)


def test_version_suivante():
    assert ab.next_tag(None) == "1.0"
    assert ab.next_tag("1.0") == "1.1"
    assert ab.next_tag("1.9") == "1.10"
    assert ab.next_tag("v3") == "v4"
    assert ab.next_tag("latest") == "latest.1"


def test_expose():
    assert ab.parse_expose("FROM x\nEXPOSE 3000\n") == 3000
    assert ab.parse_expose("FROM x\n# EXPOSE 1\n") is None


def test_chemin_windows_et_refus(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "allowed_roots", lambda: [str(tmp_path.resolve())])
    d = tmp_path / "monapp"
    d.mkdir()
    assert ab.resolve_source(str(d)) == str(d.resolve())
    with pytest.raises(ab.AppError):
        ab.resolve_source("/etc")
    with pytest.raises(ab.AppError):
        ab.resolve_source(str(tmp_path / "absent"))
    with pytest.raises(ab.AppError):                 # C:\… → /mnt/c/…, hors des racines du test
        ab.resolve_source(r"C:\Windows")


def test_inspection(tmp_path):
    (tmp_path / "index.html").write_text("<h1>ok</h1>")
    assert ab.inspect_dir(str(tmp_path))["kind"] == "static"
    (tmp_path / "Dockerfile").write_text("FROM node:20\nEXPOSE 3000\n")
    i = ab.inspect_dir(str(tmp_path))
    assert i["kind"] == "dockerfile" and i["port"] == 3000
    vide = tmp_path / "vide"
    vide.mkdir()
    (vide / "package.json").write_text("{}")
    i = ab.inspect_dir(str(vide))
    assert i["kind"] is None and "Node.js" in i["message"]


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_zip_descend_dans_le_dossier_unique(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "APPS_DIR", str(tmp_path))
    src = ab.extract_zip(_zip({"site/index.html": "<h1>ok</h1>"}), "site")
    assert os.path.exists(os.path.join(src, "index.html"))


def test_zip_dangereux_refuse(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "APPS_DIR", str(tmp_path))
    with pytest.raises(ab.AppError):
        ab.extract_zip(_zip({"../../evil.txt": "x"}), "site")
    with pytest.raises(ab.AppError):
        ab.extract_zip(b"pas un zip", "site")


def test_copie_statique_ajoute_un_dockerfile_sans_toucher_la_source(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "APPS_DIR", str(tmp_path / "apps"))
    src = tmp_path / "monsite"
    src.mkdir()
    (src / "index.html").write_text("<h1>ok</h1>")
    (src / ".git").mkdir()
    ctx = ab._copy_source(str(src), "monsite", "static")
    assert os.path.exists(os.path.join(ctx, "Dockerfile"))
    assert not os.path.exists(os.path.join(ctx, ".git"))
    assert not (src / "Dockerfile").exists()


def test_manifeste():
    y = ab.manifest("monapp", "1.0", 80, 2)
    assert "image: monapp:1.0" in y and "imagePullPolicy: IfNotPresent" in y
    assert "kind: Service" in y and "containerPort: 80" in y and "kind: Namespace" not in y
    y = ab.manifest("monapp", "1.1", 3000, 1, "demo")
    assert y.startswith("apiVersion: v1\nkind: Namespace") and "namespace: demo" in y
    with pytest.raises(ab.AppError):
        ab.manifest("monapp", "1.0", 0)
    with pytest.raises(ab.AppError):
        ab.manifest("monapp", "1.0", 80, 50)


# ── Espace de travail ────────────────────────────────────────────────────────

@pytest.fixture
def apps(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "APPS_DIR", str(tmp_path / "apps"))
    monkeypatch.setattr(ab, "REGISTRY", str(tmp_path / "apps" / "apps.json"))
    monkeypatch.setattr(ab, "allowed_roots", lambda: [str(tmp_path.resolve())])
    return tmp_path


def test_import_dossier_copie_sans_toucher_l_original(apps):
    src = apps / "monapp"
    (src / "node_modules" / "x").mkdir(parents=True)
    (src / "index.html").write_text("<h1>v1</h1>")
    info = ab.import_path("monapp", str(src))
    assert info["kind"] == "static"
    assert [f["path"] for f in ab.list_files("monapp")] == ["index.html"]     # node_modules ignoré
    ab.write_file("monapp", "index.html", "<h1>v2</h1>")
    assert (src / "index.html").read_text() == "<h1>v1</h1>"                   # original intact
    assert ab.read_file("monapp", "index.html")["content"] == "<h1>v2</h1>"


def test_reimport_garde_la_version_precedente(apps):
    src = apps / "monapp"
    src.mkdir()
    (src / "index.html").write_text("a")
    ab.import_path("monapp", str(src))
    ab.write_file("monapp", "notes.txt", "à garder")
    ab.import_path("monapp", str(src))
    assert os.path.exists(os.path.join(ab.ws_dir("monapp") + ".precedent", "notes.txt"))


def test_import_fichier_par_fichier(apps):
    ab.import_begin("site")
    ab.import_file("site", "index.html", b"<h1>ok</h1>")
    ab.import_file("site", "css/style.css", b"body{}")
    ab.import_file("site", "node_modules/lib.js", b"x")                          # ignoré
    with pytest.raises(ab.AppError):
        ab.import_file("site", "../evil.txt", b"x")
    info = ab.import_finish("site")
    assert info["kind"] == "static"
    assert {f["path"] for f in ab.list_files("site")} == {"index.html", "css/style.css"}


def test_import_zip_dans_l_espace_de_travail(apps):
    info = ab.import_zip("zipapp", _zip({"app/Dockerfile": "FROM x\nEXPOSE 3000\n", "app/main.py": "print(1)"}))
    assert info["port"] == 3000
    assert {f["path"] for f in ab.list_files("zipapp")} == {"Dockerfile", "main.py"}


def test_chemins_et_fichiers_binaires(apps):
    ab.write_file("monapp", "a/b/c.txt", "x")
    for bad in ("../x", "/etc/passwd", "C:/x", ""):
        with pytest.raises(ab.AppError):
            ab.write_file("monapp", bad, "x")
    with open(os.path.join(ab.ws_dir("monapp"), "logo.png"), "wb") as f:
        f.write(b"\x89PNG\x00\x00")
    assert ab.read_file("monapp", "logo.png")["editable"] is False
    ab.delete_file("monapp", "a/b/c.txt")
    assert not os.path.exists(os.path.join(ab.ws_dir("monapp"), "a"))           # dossiers vides retirés


def test_export_et_liste(apps):
    ab.write_file("monapp", "index.html", "<h1>ok</h1>")
    z = zipfile.ZipFile(io.BytesIO(ab.export_zip("monapp")))
    assert z.namelist() == ["monapp/index.html"]
    assert [a["name"] for a in ab.list_apps()] == ["monapp"]


def test_reponse_de_l_ia():
    r = ab.parse_ai_edit('Voici :\n```json\n{"explication": "Ajout", "fichiers": [{"chemin": "a.html", "contenu": "x"}]}\n```')
    assert r == {"explanation": "Ajout", "files": [{"path": "a.html", "content": "x"}]}
    with pytest.raises(ab.AppError):
        ab.parse_ai_edit("pas de json ici")

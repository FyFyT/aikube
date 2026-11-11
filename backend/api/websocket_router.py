"""
backend/api/websocket_router.py
================================================================================
Endpoints WebSocket d'AI-Kube.

  /ws/logs/{namespace}/{pod}    streaming des logs (existant)
  /ws/metrics/live              métriques cluster (existant)
  /ws/exec/{namespace}/{pod}    terminal interactif dans un conteneur (NOUVEAU)

--------------------------------------------------------------------------------
NOTES D'IMPLÉMENTATION — /ws/exec
--------------------------------------------------------------------------------

1. Pont synchrone → asynchrone
   `kubernetes.stream.stream()` ouvre un canal SPDY/WebSocket vers l'API server,
   mais son interface est BLOQUANTE. L'appeler dans une coroutine gèlerait la
   boucle asyncio de FastAPI : plus aucune autre requête ne serait servie
   pendant l'exécution de la commande.
   Solution : l'exécution vit dans un thread dédié, qui pousse ses chunks vers
   la coroutine via `loop.call_soon_threadsafe(queue.put_nowait, ...)` — la seule
   méthode sûre pour alimenter une asyncio.Queue depuis un thread externe.
   Une sentinelle marque la fin du flux et libère la coroutine consommatrice.

2. Émulation d'un shell persistant
   Chaque appel exec crée un processus neuf : `cd /tmp` puis `pwd` retournerait
   `/` car les deux commandes n'ont aucun lien. Plutôt qu'un vrai PTY persistant
   (coûteux, et sans délimiteur fiable pour détecter la fin d'une commande), on
   maintient le `cwd` côté serveur : chaque commande est préfixée d'un `cd` vers
   le répertoire courant, et un marqueur en fin de script renvoie le nouveau
   `$PWD` ainsi que le code de sortie. Le marqueur est retiré avant affichage.
   Résultat : l'utilisateur perçoit une session continue, sans état côté cluster.

3. Surface d'attaque
   Un exec non filtré = exécution de code arbitraire dans le cluster. Trois
   couches : filtrage par motifs des commandes destructrices, plafonds
   (timeout, taille de sortie, longueur, nombre de commandes par session), et
   un mode lecture seule optionnel (AIKUBE_EXEC_READONLY=1) restreint à une
   liste blanche de binaires d'inspection.
================================================================================
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from core.kubernetes_manager import k8s_manager

import asyncio
import subprocess
import threading
import time
import os
import re
import shlex

router = APIRouter()

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION SÉCURITÉ
# ══════════════════════════════════════════════════════════════════════════════

EXEC_TIMEOUT          = int(os.getenv("AIKUBE_EXEC_TIMEOUT", "15"))       # secondes
MAX_OUTPUT_BYTES      = int(os.getenv("AIKUBE_EXEC_MAX_OUTPUT", "262144"))  # 256 Ko
MAX_COMMAND_LENGTH    = 2000
MAX_COMMANDS_PER_SESS = 200
READONLY_MODE         = os.getenv("AIKUBE_EXEC_READONLY", "0") == "1"

# Marqueur de fin de script : transporte le cwd et le code de sortie.
META_PREFIX = "__AIKUBE_META__"
META_SUFFIX = "__END__"
META_RE     = re.compile(
    re.escape(META_PREFIX) + r"(?P<cwd>.*?)\|(?P<rc>-?\d+)" + re.escape(META_SUFFIX),
    re.DOTALL,
)

# Commandes refusées quel que soit le mode. Le libellé est renvoyé à
# l'utilisateur : un refus explicite vaut mieux qu'un échec silencieux.
BLOCKED_PATTERNS = [
    (r"\brm\s+(-[a-zA-Z]+\s+)*-{1,2}[rf]{1,2}\b[^|;&]*\s+/\s*($|[;&|])", "suppression récursive de la racine"),
    (r"\bmkfs(\.\w+)?\b",                     "formatage de système de fichiers"),
    (r"\bdd\b[^|;&]*\bof=/dev/",              "écriture directe sur périphérique bloc"),
    (r">\s*/dev/(sd|nvme|hd|xvd)",            "écriture directe sur disque"),
    (r"\b(shutdown|reboot|halt|poweroff|init\s+0)\b", "arrêt du système"),
    (r"\bkill\s+(-\w+\s+)*1\b",               "arrêt du PID 1 (détruirait le conteneur)"),
    (r":\s*\(\s*\)\s*\{.*\}\s*;\s*:",         "fork bomb"),
    (r"\bchmod\s+(-[a-zA-Z]+\s+)*[0-7]{3,4}\s+/\s*($|[;&|])", "modification des permissions de la racine"),
    (r"\b(curl|wget)\b[^|;&]*\|\s*(ba|z|k|d)?sh", "exécution d'un script distant non vérifié"),
    (r"\bnc\b[^|;&]*\s-e\b",                  "reverse shell netcat"),
    (r"/etc/(shadow|sudoers|gshadow)",        "accès aux secrets système"),
    (r"\b(iptables|ip6tables|nft)\s+(-F|flush)", "purge des règles réseau"),
    (r"\b(useradd|usermod|passwd)\b",         "modification des comptes système"),
    (r"\bhistory\s+-c\b",                     "effacement de traces"),
]
COMPILED_BLOCKED = [(re.compile(p, re.IGNORECASE), reason) for p, reason in BLOCKED_PATTERNS]

# Mode lecture seule : liste blanche de binaires d'inspection.
READONLY_ALLOWED = {
    "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "find", "stat",
    "env", "printenv", "ps", "top", "df", "du", "free", "uptime", "date", "id",
    "whoami", "hostname", "uname", "pwd", "echo", "which", "whereis", "file",
    "netstat", "ss", "ip", "ifconfig", "nslookup", "dig", "ping", "sort", "uniq",
    "cut", "awk", "sed", "tr", "basename", "dirname", "readlink", "md5sum",
    "sha256sum", "diff", "less", "more", "history", "cd", "exit", "clear",
}

SHELL_CANDIDATES = ["/bin/sh", "/bin/bash", "/bin/ash", "/usr/bin/sh"]


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT EXISTANT — LOGS
# ══════════════════════════════════════════════════════════════════════════════

@router.websocket("/logs/{namespace}/{pod_name}")
async def stream_pod_logs(websocket: WebSocket, namespace: str, pod_name: str):
    await websocket.accept()
    proc = None
    try:
        check = subprocess.run(
            ['kubectl', 'get', 'pod', pod_name, '-n', namespace,
             '-o', 'jsonpath={.status.phase}'],
            capture_output=True, text=True, timeout=5
        )
        phase = check.stdout.strip()

        if phase == "Pending" or not phase:
            await websocket.send_json({"type": "log", "line": f"⏳ Pod en état {phase or 'inconnu'} — logs non disponibles"})
            await websocket.send_json({"type": "log", "line": "   Attente que le pod démarre..."})
            await asyncio.sleep(2)
            return

        proc = await asyncio.create_subprocess_exec(
            'kubectl', 'logs', pod_name, '-n', namespace,
            '--tail=50', '-f',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def send_logs():
            try:
                while True:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=30.0)
                    if not line:
                        break
                    decoded = line.decode('utf-8', errors='replace').rstrip()
                    if decoded:
                        await websocket.send_json({"type": "log", "line": decoded, "pod": pod_name})
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "log", "line": "[timeout — pas de nouveaux logs]"})
            except Exception:
                pass

        log_task = asyncio.create_task(send_logs())

        try:
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    if log_task.done():
                        break
                    continue
        except WebSocketDisconnect:
            pass
        finally:
            log_task.cancel()
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT EXISTANT — MÉTRIQUES
# ══════════════════════════════════════════════════════════════════════════════

@router.websocket("/metrics/live")
async def stream_live_metrics(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            try:
                result = subprocess.run(
                    ['kubectl', 'get', 'pods', '-A', '--no-headers'],
                    capture_output=True, text=True, timeout=5
                )
                lines = [l for l in result.stdout.strip().split('\n') if l]
                running = sum(1 for l in lines if 'Running' in l)
                pending = sum(1 for l in lines if 'Pending' in l)
                failed  = sum(1 for l in lines if 'Error' in l or 'CrashLoop' in l)
                await websocket.send_json({
                    "type": "metrics",
                    "pods": {"total": len(lines), "running": running, "pending": pending, "failed": failed}
                })
            except Exception:
                pass
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION DES COMMANDES
# ══════════════════════════════════════════════════════════════════════════════

def validate_command(cmd: str):
    """Retourne (True, None) si la commande est autorisée, sinon (False, raison)."""
    stripped = cmd.strip()

    if not stripped:
        return False, "commande vide"

    if len(stripped) > MAX_COMMAND_LENGTH:
        return False, f"commande trop longue ({len(stripped)} caractères, maximum {MAX_COMMAND_LENGTH})"

    if "\x00" in stripped:
        return False, "caractère nul interdit"

    for pattern, reason in COMPILED_BLOCKED:
        if pattern.search(stripped):
            return False, reason

    if READONLY_MODE:
        # On isole chaque segment d'un pipeline pour vérifier son binaire.
        segments = re.split(r"[|;&]+|&&|\|\|", stripped)
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            try:
                tokens = shlex.split(seg)
            except ValueError:
                return False, "syntaxe shell invalide (guillemets non fermés)"
            if not tokens:
                continue
            binary = os.path.basename(tokens[0])
            # On ignore les affectations de variables en préfixe (VAR=x cmd)
            if "=" in binary and not binary.startswith("="):
                continue
            if binary not in READONLY_ALLOWED:
                return False, f"'{binary}' non autorisé en mode lecture seule"

    return True, None


def build_script(cwd: str, user_command: str) -> str:
    """
    Construit le script réellement transmis au conteneur.

    Le marqueur final renvoie $PWD et le code de sortie, ce qui permet de
    simuler une session persistante sans maintenir de processus côté cluster.
    """
    safe_cwd = cwd.replace("'", "'\\''")
    return (
        f"cd '{safe_cwd}' 2>/dev/null || cd /\n"
        f"{user_command}\n"
        f"__aikube_rc=$?\n"
        f"printf '\\n{META_PREFIX}%s|%s{META_SUFFIX}' \"$(pwd)\" \"$__aikube_rc\"\n"
    )


# ══════════════════════════════════════════════════════════════════════════════
# EXÉCUTION — pont thread bloquant ↔ boucle asyncio
# ══════════════════════════════════════════════════════════════════════════════

_SENTINEL = object()


def _push(loop, queue, item):
    """Alimente une asyncio.Queue depuis un thread externe, sans data race."""
    try:
        loop.call_soon_threadsafe(queue.put_nowait, item)
    except RuntimeError:
        # La boucle est fermée (client déconnecté pendant l'exécution).
        pass


def _blocking_exec(v1, namespace, pod, container, shell, script, loop, queue):
    """
    Tourne dans un thread dédié. Ne touche JAMAIS au WebSocket directement :
    tout passe par la queue, consommée côté coroutine.
    """
    from kubernetes.stream import stream

    resp = None
    total = 0
    try:
        resp = stream(
            v1.connect_get_namespaced_pod_exec,
            pod, namespace,
            container=container,
            command=[shell, "-c", script],
            stderr=True, stdin=False, stdout=True, tty=False,
            _preload_content=False,
        )

        deadline = time.monotonic() + EXEC_TIMEOUT

        while resp.is_open():
            if time.monotonic() > deadline:
                _push(loop, queue, ("error", f"⏱ Commande interrompue après {EXEC_TIMEOUT}s"))
                break

            resp.update(timeout=1)

            if resp.peek_stdout():
                chunk = resp.read_stdout()
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    _push(loop, queue, ("stdout", chunk[:MAX_OUTPUT_BYTES]))
                    _push(loop, queue, ("error", f"✂ Sortie tronquée à {MAX_OUTPUT_BYTES // 1024} Ko"))
                    break
                _push(loop, queue, ("stdout", chunk))

            if resp.peek_stderr():
                _push(loop, queue, ("stderr", resp.read_stderr()))

    except Exception as e:
        _push(loop, queue, ("fatal", str(e)))
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        _push(loop, queue, _SENTINEL)


async def run_command(websocket, v1, namespace, pod, container, shell, cwd, command):
    """
    Lance la commande, draine la queue, envoie au client.
    Retourne le nouveau cwd (émulation de session persistante).
    """
    loop  = asyncio.get_running_loop()
    queue = asyncio.Queue()
    script = build_script(cwd, command)

    threading.Thread(
        target=_blocking_exec,
        args=(v1, namespace, pod, container, shell, script, loop, queue),
        daemon=True,
    ).start()

    # stdout est bufferisé : le marqueur de fin peut être scindé entre deux
    # chunks, le reconstituer à la volée serait fragile. stderr passe en direct.
    stdout_buffer = []
    fatal = None

    while True:
        item = await queue.get()
        if item is _SENTINEL:
            break
        kind, data = item
        if kind == "stdout":
            stdout_buffer.append(data)
        elif kind == "stderr":
            await websocket.send_json({"error": data.rstrip("\n")})
        elif kind == "error":
            await websocket.send_json({"error": data})
        elif kind == "fatal":
            fatal = data

    if fatal:
        await websocket.send_json({"error": f"✖ Exec impossible : {fatal}"})
        return cwd, False

    raw = "".join(stdout_buffer)

    # Extraction du marqueur → nouveau cwd + code de sortie
    new_cwd, rc = cwd, 0
    match = META_RE.search(raw)
    if match:
        new_cwd = match.group("cwd").strip() or cwd
        try:
            rc = int(match.group("rc"))
        except ValueError:
            rc = 0
        raw = raw[:match.start()] + raw[match.end():]

    # Le client met à jour son prompt : la session paraît continue.
    await websocket.send_json({"type": "cwd", "cwd": new_cwd})

    output = raw.rstrip("\n")
    if output:
        await websocket.send_json({"output": output})
    elif rc == 0:
        await websocket.send_json({"output": "(aucune sortie)"})

    if rc != 0:
        await websocket.send_json({"error": f"[code de sortie {rc}]"})

    return new_cwd, True


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT — TERMINAL INTERACTIF
# ══════════════════════════════════════════════════════════════════════════════

@router.websocket("/exec/{namespace}/{pod_name}")
async def exec_in_pod(websocket: WebSocket, namespace: str, pod_name: str):
    """
    Terminal interactif dans un conteneur.

    Protocole client → serveur : {"command": "ls -la", "container": "nginx"?}
    Protocole serveur → client : {"output": "..."} | {"error": "..."}
    """
    await websocket.accept()

    v1 = getattr(k8s_manager, "v1", None)
    if v1 is None:
        await websocket.send_json({"error": "✖ Client Kubernetes non initialisé côté serveur."})
        await websocket.close()
        return

    cwd            = "/"
    command_count  = 0
    container      = None
    shell          = None

    try:
        # ── Résolution du conteneur cible ──────────────────────────────────────
        try:
            pod = await asyncio.to_thread(v1.read_namespaced_pod, pod_name, namespace)
            containers = [c.name for c in (pod.spec.containers or [])]
            phase = pod.status.phase
        except Exception as e:
            await websocket.send_json({"error": f"✖ Pod introuvable : {e}"})
            await websocket.close()
            return

        if phase != "Running":
            await websocket.send_json({"error": f"✖ Pod en état {phase} — exec impossible (le conteneur doit être Running)."})
            await websocket.close()
            return

        if not containers:
            await websocket.send_json({"error": "✖ Aucun conteneur dans ce pod."})
            await websocket.close()
            return

        container = containers[0]

        # ── Détection du shell disponible ──────────────────────────────────────
        # Les images distroless ou scratch n'ont pas /bin/sh : on teste les
        # candidats un par un plutôt que d'échouer sur une erreur cryptique.
        for candidate in SHELL_CANDIDATES:
            probe_queue = asyncio.Queue()
            loop = asyncio.get_running_loop()
            threading.Thread(
                target=_blocking_exec,
                args=(v1, namespace, pod_name, container, candidate, "echo ok", loop, probe_queue),
                daemon=True,
            ).start()
            ok = False
            while True:
                item = await probe_queue.get()
                if item is _SENTINEL:
                    break
                kind, data = item
                if kind == "stdout" and "ok" in data:
                    ok = True
            if ok:
                shell = candidate
                break

        if shell is None:
            await websocket.send_json({
                "error": "✖ Aucun shell trouvé dans ce conteneur (image distroless ou scratch ?)."
            })
            await websocket.close()
            return

        # ── Session prête ──────────────────────────────────────────────────────
        banner = (
            f"✓ Session ouverte — conteneur '{container}' · shell {shell}"
            + (f"\n  Conteneurs disponibles : {', '.join(containers)}" if len(containers) > 1 else "")
            + (f"\n  ⚠ Mode lecture seule actif" if READONLY_MODE else "")
            + f"\n  Timeout {EXEC_TIMEOUT}s par commande · sortie plafonnée à {MAX_OUTPUT_BYTES // 1024} Ko"
        )
        await websocket.send_json({"output": banner})

        # ── Boucle principale ──────────────────────────────────────────────────
        while True:
            raw_msg = await websocket.receive_json()
            command = (raw_msg or {}).get("command", "")

            # Changement de conteneur à la volée pour les pods multi-conteneurs
            requested = (raw_msg or {}).get("container")
            if requested and requested in containers and requested != container:
                container = requested
                cwd = "/"
                await websocket.send_json({"output": f"✓ Conteneur actif : {container}"})
                continue

            command_count += 1
            if command_count > MAX_COMMANDS_PER_SESS:
                await websocket.send_json({"error": f"✖ Limite de {MAX_COMMANDS_PER_SESS} commandes atteinte. Rouvrez une session."})
                break

            allowed, reason = validate_command(command)
            if not allowed:
                await websocket.send_json({"error": f"⛔ Commande refusée — {reason}"})
                continue

            # `clear` est purement côté client, inutile de solliciter le cluster
            if command.strip() in ("clear", "cls"):
                await websocket.send_json({"type": "clear", "output": ""})
                continue

            cwd, ok = await run_command(
                websocket, v1, namespace, pod_name, container, shell, cwd, command
            )
            if not ok:
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"error": f"✖ Erreur session : {e}"})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass

#!/usr/bin/env python3
"""
scripts/build_corpus_v5_self.py — Corpus de KubeLLM v4 : les erreurs PROPRES de l'élève
================================================================================
Hypothèse testée (mémoire, C5-j) : le second entraînement n'a pas appris à corriger
parce que les erreurs montrées n'étaient pas les siennes (altérations simulées d'un
manifeste valide). Ici, les erreurs viennent de l'élève lui-même :

  1. KubeLLM v3 (Ollama, même format qu'en application) répond à des demandes NOUVELLES
     (plan déterministe, graines 44 et 45 ; les 36 demandes du banc C1 restent exclues).
  2. Ses réponses passent les mêmes arbitres que dans l'application (dry-run serveur,
     capacité du profil montré, namespaces) ET les contrôles d'intention (port de
     l'image, sondes, conteneur non demandé, objet demandé absent).
  3. Pour chaque réponse fautive, l'enseignant (gpt-oss-120b, poids ouverts) reçoit la
     demande + la réponse rejetée de l'élève + les erreurs, et propose une correction.
     La correction n'est retenue que si elle passe arbitres ET contrôles d'intention.
  4. Deux exemples par erreur retenue :
       · « premier essai » : demande → manifeste corrigé (éviter l'erreur d'emblée) ;
       · « correction »    : demande + SA réponse rejetée + erreurs des arbitres →
                             manifeste corrigé (format exact de la 2e tentative de
                             l'application). Seulement si les arbitres l'ont rejetée :
                             l'application ne renvoie pas les avertissements d'intention.

Aucune sortie de Claude n'entre dans le corpus. Les réponses justes de l'élève ne sont
pas réapprises (elles sont seulement comptées dans le journal).

Mesure secondaire (mémoire) : erreurs propres de KubeLLM v3 sur des demandes nouvelles,
par catégorie → data/observations/corpus_v5self_log.jsonl

USAGE (terminal Ubuntu WSL, dans ~/aikube ; minikube démarré ; Ollama lancé avec
ollama-start ; CEREBRAS_API_KEY ou GROQ_API_KEY dans .env)
  python3 scripts/build_corpus_v5_self.py --plan-only
  python3 scripts/build_corpus_v5_self.py --n 10 --n-cap 5          # essai (~10 min)
  python3 scripts/build_corpus_v5_self.py --provider cerebras        # complet (~2 h, reprise possible)
  python3 scripts/build_corpus_v5_self.py --stats                    # bilan des erreurs de l'élève
  python3 scripts/build_corpus_v5_self.py --provider cerebras --retry-failed   # retraiter les non corrigés
  → data/datasets/corpus_v5self.jsonl (nouveau fichier ; les corpus existants ne sont pas touchés)
================================================================================
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

OUT = os.path.join(ROOT, "data", "datasets", "corpus_v5self.jsonl")
LOG = os.path.join(ROOT, "data", "observations", "corpus_v5self_log.jsonl")


def done_ids():
    ids = set()
    if os.path.exists(LOG):
        for line in open(LOG, encoding="utf-8"):
            try:
                ids.add(json.loads(line)["id"])
            except (ValueError, KeyError):
                pass
    return ids


def parse(text):
    try:
        return [d for d in yaml.safe_load_all(text or "") if isinstance(d, dict)]
    except yaml.YAMLError:
        return []


async def student(ollama_url, model, user):
    """Même appel que l'application (llm_client._ollama_manifest)."""
    import httpx
    from ai_module.kubellm_prompt import STUDENT_SYSTEM, parse_student_output
    async with httpx.AsyncClient(timeout=900) as c:
        r = await c.post(f"{ollama_url}/api/chat", json={
            "model": model, "stream": False,
            "messages": [{"role": "system", "content": STUDENT_SYSTEM}, {"role": "user", "content": user}],
            "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 1200},
            "keep_alive": "60m"})
    r.raise_for_status()
    return parse_student_output((r.json().get("message") or {}).get("content", ""))


def judge(Y, B, intent_warnings, request, yaml_text, prof):
    """Arbitres de l'application + namespaces du profil + contrôles d'intention."""
    check = Y.validate_text(yaml_text, prof)
    ns_err = B.namespace_errors(yaml_text, prof)
    if ns_err:
        check = {**check, "valid": False, "errors": check["errors"] + ns_err}
    intent = intent_warnings(request, parse(yaml_text)) if check.get("valid") or check.get("server_validated") else []
    return check, intent


async def run(a):
    import build_corpus_v3 as B
    from ai_module import host_context, yaml_pipeline as Y
    from ai_module.intent_checks import intent_warnings
    from ai_module.kubellm_prompt import STUDENT_SYSTEM, student_target, student_user
    from campaign_yaml import PROMPTS as C1
    from core.config import settings
    host_context.host_history = lambda *x, **k: {"available": False}   # profils synthétiques

    real = await host_context.collect(force=True)
    if not real.get("api_kinds"):
        sys.exit("Cluster injoignable : démarre minikube puis relance (kubectl get nodes).")
    holdout = [B.words(p[2]) for p in C1]
    plan, close1 = B.build_plan(a.seed, a.n, real, holdout, 0.5, "all", "v5self")
    plan_cap, close2 = B.build_plan(a.seed + 1, a.n_cap, real, holdout, 0.5, "capacite_plus", "v5selfcap") \
        if a.n_cap else ([], 0)
    plan += plan_cap
    print(f"Plan : {len(plan)} demandes nouvelles ({Counter(p['category'] for p in plan).most_common()})")
    print(f"Écartées car trop proches du banc C1 : {close1 + close2}")
    if a.plan_only:
        return

    # Élève disponible ?
    import httpx
    try:
        tags = httpx.get(f"{settings.OLLAMA_URL}/api/tags", timeout=5).json()
        if not any(m["name"].startswith(a.student) for m in tags.get("models", [])):
            sys.exit(f"Modèle {a.student} absent d'Ollama (ollama list).")
    except httpx.HTTPError:
        sys.exit("Ollama injoignable : lance ollama-start.")

    # Enseignant (même configuration que build_corpus_v3)
    prov = B.PROVIDERS[a.provider]
    key = os.environ.get(prov["key"])
    if not key and os.path.exists(os.path.join(ROOT, ".env")):
        for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
            if line.startswith(prov["key"] + "="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit(f"{prov['key']} introuvable : ajoute la ligne {prov['key']}=… dans ~/aikube/.env")
    if a.provider == "groq":
        from groq import AsyncGroq
        client = AsyncGroq(api_key=key)
    else:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=key, base_url=prov["base_url"])
    model = prov["model"]
    rpm = prov["rpm"]
    teacher_id = f"{a.provider}/{model}"
    system = re.sub(r"\n7\. Dans « choices ».*$", "", Y.SYSTEM_PROMPT, flags=re.S) + (
        "\n\nRéponds UNIQUEMENT par un objet JSON, sans markdown : "
        '{"yaml": "<manifeste complet, documents séparés par --->", "warnings": []}  — warnings reste '
        "VIDE sauf si tu as dû réduire ou modifier la demande pour la faire tenir dans la capacité ; "
        "dans ce cas, une phrase par réduction avec le calcul.\n"
        "On te montre la réponse REJETÉE d'un autre modèle et ses erreurs : corrige-les toutes. "
        "N'ajoute aucun conteneur que la demande ne mentionne pas ; les ports (containerPort, Service, "
        "sondes) doivent être ceux sur lesquels l'image écoute réellement (ex. nginx-unprivileged : 8080) ; "
        "n'utilise que des images qui existent réellement.")

    for ns in B.NS_POOL:
        subprocess.run(["kubectl", "create", "namespace", ns], capture_output=True, text=True, timeout=45)

    last_call = [0.0]

    async def ask_teacher(user):
        if rpm:
            gap = 60 / rpm - (time.time() - last_call[0])
            if gap > 0:
                await asyncio.sleep(gap)
            last_call[0] = time.time()
        while True:
            try:
                return await B.teacher(client, model, system, user)
            except B.RateLimited as e:
                if e.wait > a.max_wait:
                    raise
                print(f"   … limite de débit, pause {round(e.wait)} s")
                await asyncio.sleep(e.wait + 1)

    already = done_ids()
    todo = [p for p in plan if p["id"] not in already]
    print(f"Élève : {a.student} · enseignant : {teacher_id}")
    print(f"À faire : {len(todo)} (déjà traités : {len(plan) - len(todo)})\n")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    loop = asyncio.get_running_loop()
    stats = Counter()

    for k, item in enumerate(todo, 1):
        t0 = time.time()
        prof = item["profile"]
        ctx_text = host_context.to_prompt(prof)
        user0 = student_user(ctx_text, item["request"])
        try:
            st = await student(settings.OLLAMA_URL, a.student, user0)
        except Exception as e:  # noqa: BLE001
            print(f"[{k}/{len(todo)}] ⏭ élève indisponible ({str(e)[:80]}) : repris au prochain lancement")
            await asyncio.sleep(10)
            continue
        check, intent = await loop.run_in_executor(None, judge, Y, B, intent_warnings,
                                                   item["request"], st["yaml"], prof)
        if check.get("server_validated") is None and not check.get("valid"):
            print(f"[{k}/{len(todo)}] ⏭ serveur Kubernetes injoignable : repris au prochain lancement")
            continue
        ok = bool(check["valid"]) and not intent
        rec = {"id": item["id"], "category": item["category"], "request": item["request"],
               "profile": prof["profile_id"], "student": a.student,
               "student_valid": bool(check["valid"]), "student_intent": intent,
               "student_errors": [[e["source"], e["category"]] for e in check["errors"]],
               "student_ok": ok, "teacher": None, "kept": 0,
               # réponse brute de l'élève : permet de rejuger le journal si un contrôle est corrigé
               "student_yaml": st["yaml"][:8000]}

        if not ok:
            # erreurs données à l'enseignant : arbitres + intention (signalée comme telle)
            errs = list(check["errors"]) + [{"source": "intention", "category": "écart avec la demande",
                                             "message": w} for w in intent]
            user_t = user0 + Y._feedback(st["yaml"], {"errors": errs})
            final, tries = None, []
            for n in range(1, a.max_attempts + 1):
                try:
                    out = await ask_teacher(user_t)
                except B.RateLimited:
                    print("\nLimite de l'API atteinte : relance plus tard, la génération reprendra ici.")
                    return
                except Exception as e:  # noqa: BLE001
                    tries.append({"n": n, "error": str(e)[:200]})
                    await asyncio.sleep(15)
                    continue
                if not out["yaml"].strip():
                    tries.append({"n": n, "error": "réponse vide"})
                    continue
                t_check, t_intent = await loop.run_in_executor(None, judge, Y, B, intent_warnings,
                                                               item["request"], out["yaml"], prof)
                tries.append({"n": n, "valid": bool(t_check["valid"]), "intent": t_intent,
                              "errors": [[e["source"], e["category"]] for e in t_check["errors"]]})
                if t_check["valid"] and not t_intent:
                    final = out
                    break
                errs = list(t_check["errors"]) + [{"source": "intention", "category": "écart avec la demande",
                                                   "message": w} for w in t_intent]
                user_t = user0 + Y._feedback(out["yaml"], {"errors": errs})
            rec["teacher"] = {"id": teacher_id, "attempts": tries, "valid": bool(final)}

            if final:
                target = student_target(final["yaml"], B.reduction_warnings(final["warnings"]))
                meta = {"category": item["category"], "profile": prof["profile_id"], "teacher": teacher_id,
                        "student_errors": rec["student_errors"], "student_intent": intent}
                rows = [{"id": f"{item['id']}-first",
                         "messages": [{"role": "system", "content": STUDENT_SYSTEM},
                                      {"role": "user", "content": user0},
                                      {"role": "assistant", "content": target}],
                         "meta": {**meta, "kind": "premier_essai"}}]
                if not check["valid"] and check["errors"]:
                    # format exact de la 2e tentative dans l'application (erreurs des arbitres seules)
                    rows.append({"id": f"{item['id']}-fix",
                                 "messages": [{"role": "system", "content": STUDENT_SYSTEM},
                                              {"role": "user", "content": user0 + Y._feedback(st["yaml"], check)},
                                              {"role": "assistant", "content": target}],
                                 "meta": {**meta, "kind": "correction"}})
                with open(OUT, "a", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
                rec["kept"] = len(rows)

        rec["duration_s"] = round(time.time() - t0, 1)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        stats["élève juste" if ok else ("corrigé" if rec["kept"] else "non corrigé")] += 1
        why = "" if ok else " | " + "; ".join(sorted({c for _, c in rec["student_errors"]} |
                                                    {"intention" for _ in intent}))[:70]
        mark = "✅" if ok else ("🔧" if rec["kept"] else "❌")
        print(f"[{k}/{len(todo)}] {mark} {item['category']:11} ({rec['duration_s']} s){why}  {item['request'][:55]}")

    print(f"\nTerminé : {dict(stats)}")
    print(f"Corpus : {OUT}\nJournal (erreurs propres de l'élève) : {LOG}")


def stats():
    """Bilan du journal : erreurs propres de l'élève, et pourquoi certaines n'ont pas été corrigées."""
    from collections import Counter as C
    rows = [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]
    ok = [r for r in rows if r["student_ok"]]
    bad = [r for r in rows if not r["student_ok"]]
    print(f"Demandes : {len(rows)} · élève juste : {len(ok)} ({len(ok) / max(1, len(rows)):.0%})")
    print("\nErreurs propres de l'élève (une demande peut en avoir plusieurs) :")
    kinds = C()
    for r in bad:
        for k in {c for _, c in r["student_errors"]}:
            kinds[k] += 1
        for w in r["student_intent"]:
            kinds["intention : " + ("port" if "écoute" in w or "Probe" in w else
                                    "conteneur non demandé" if "conteneur" in w else "objet absent")] += 1
    for k, n in kinds.most_common():
        print(f"  {n:4}  {k}")
    print("\nPar catégorie de demande (erreurs / total) :")
    by = C(r["category"] for r in rows)
    byb = C(r["category"] for r in bad)
    for cat, n in by.most_common():
        print(f"  {cat:12} {byb[cat]:3} / {n}")
    why = C()
    for r in bad:
        if r["kept"]:
            why["corrigé"] += 1
            continue
        t = (r.get("teacher") or {}).get("attempts") or []
        if not t or all("error" in x for x in t):
            why["non corrigé : API injoignable ou en erreur"] += 1
        elif any(x.get("valid") and x.get("intent") for x in t):
            why["non corrigé : seul l'avertissement d'intention bloque"] += 1
        else:
            why["non corrigé : l'enseignant n'a pas trouvé de version valide"] += 1
    print("\nSort des erreurs :")
    for k, n in why.most_common():
        print(f"  {n:4}  {k}")


def forget_failed():
    """Retire du journal les demandes fautives NON corrigées, pour les retraiter (copie conservée)."""
    import shutil
    rows = [l for l in open(LOG, encoding="utf-8") if l.strip()]
    keep = [l for l in rows if (lambda r: r["student_ok"] or r["kept"])(json.loads(l))]
    bak = LOG + time.strftime(".avant-reprise-%Y%m%d-%H%M%S")
    shutil.copy(LOG, bak)
    open(LOG, "w", encoding="utf-8").writelines(keep)
    print(f"{len(rows) - len(keep)} demandes non corrigées remises à faire (journal d'origine : {bak})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=220, help="demandes courantes (graine --seed)")
    ap.add_argument("--n-cap", type=int, default=80, help="demandes de capacité (graine --seed + 1)")
    ap.add_argument("--seed", type=int, default=44)
    ap.add_argument("--student", default="kubellm-v3")
    ap.add_argument("--provider", choices=["groq", "cerebras", "openrouter"], default="cerebras")
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--max-wait", type=int, default=900)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--stats", action="store_true", help="bilan du journal, sans rien générer")
    ap.add_argument("--retry-failed", action="store_true",
                    help="retraiter les erreurs non corrigées (coupure réseau, contrôle corrigé…)")
    a = ap.parse_args()
    if a.stats:
        return stats()
    if a.retry_failed:
        forget_failed()
    asyncio.run(run(a))


if __name__ == "__main__":
    main()

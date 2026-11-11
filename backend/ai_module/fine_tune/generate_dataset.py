"""
Génère 200 paires (description → YAML) via Groq
Pour le fine-tuning de microsoft/phi-2
"""
import asyncio, json, random, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

TEMPLATES = [
    "Déployer {app} avec {r} replicas et limite {m} RAM",
    "App {app} exposée sur le port {p} avec {r} replicas",
    "Déploiement {app} avec CPU limit {cpu}m et mémoire {m}",
    "HPA pour {app}: min {r} max {mx} replicas si CPU>{c}%",
    "Service LoadBalancer pour {app} sur port {p}",
    "CronJob {app} quotidien à {h}h du matin",
    "Déployer {app} avec PersistentVolume {vol}Gi",
    "Job de migration pour {app}",
    "Déploiement {app} avec readinessProbe HTTP sur /{endpoint}",
    "Déploiement {app} avec SecurityContext non-root",
    "ConfigMap pour {app} avec config database",
    "NetworkPolicy deny-all pour namespace {ns}",
    "RBAC role lecture seule pour {app} dans namespace {ns}",
    "Secret pour {app} avec credentials base de données",
    "Ingress pour {app} sur domaine {app}.example.com",
    "DaemonSet pour {app} sur tous les noeuds",
    "StatefulSet pour {app} avec stockage persistant",
    "Deployment {app} avec anti-affinité entre replicas",
    "Deployment {app} avec livenessProbe TCP port {p}",
    "ResourceQuota pour namespace {ns}",
]

APPS = ["flask-api","nginx","redis","postgres","node-app","ml-worker",
        "rabbitmq","mongodb","grafana","prometheus","kafka","elasticsearch"]
NS   = ["default","production","staging","ai-ns","monitoring"]

async def generate_pair(description: str, groq_key: str) -> dict:
    from groq import AsyncGroq
    import re
    client = AsyncGroq(api_key=groq_key)
    system = """Tu génères des exemples d'entraînement pour fine-tuner un LLM sur Kubernetes.
Réponds UNIQUEMENT en JSON valide sans markdown:
{"instruction": "description courte", "input": "contexte cluster", "output": "YAML Kubernetes complet et valide"}"""

    response = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role":"system","content":system},
            {"role":"user","content":f"Génère pour: {description}"}
        ],
        temperature=0.7,
        max_tokens=800,
    )
    content = response.choices[0].message.content
    clean = re.sub(r'```json|```','',content).strip()
    return json.loads(clean)

async def main():
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        # Lire depuis .env
        with open('/home/tamft/aikube/.env') as f:
            for line in f:
                if line.startswith('GROQ_API_KEY='):
                    groq_key = line.split('=',1)[1].strip()

    if not groq_key:
        print("❌ GROQ_API_KEY non trouvé dans .env")
        return

    output = "/home/tamft/aikube/data/datasets/kubernetes_pairs.jsonl"
    os.makedirs(os.path.dirname(output), exist_ok=True)

    n = 200
    print(f"🔄 Génération de {n} paires YAML avec Groq...")
    examples, errors = [], 0

    for i in range(n):
        tmpl = random.choice(TEMPLATES)
        desc = tmpl.format(
            app=random.choice(APPS),
            r=random.randint(1,5),
            m=random.choice(["256Mi","512Mi","1Gi","2Gi"]),
            mx=random.randint(5,10),
            c=random.choice([50,60,70,80]),
            p=random.choice([5000,8000,8080,3000,80,443]),
            cpu=random.choice([100,200,500,1000]),
            h=random.randint(0,6),
            ns=random.choice(NS),
            vol=random.choice([1,5,10,20]),
            endpoint=random.choice(["health","ready","api/v1/status"]),
        )

        try:
            ex = await generate_pair(desc, groq_key)
            examples.append(ex)
            print(f"  [{i+1:3d}/{n}] ✅ {desc[:55]}")
        except Exception as e:
            errors += 1
            print(f"  [{i+1:3d}/{n}] ❌ {str(e)[:50]}")

        # Rate limiting Groq (30 req/min)
        if (i+1) % 10 == 0:
            print(f"  ⏸ Pause 3s (rate limiting)...")
            await asyncio.sleep(3)

    with open(output, 'w', encoding='utf-8') as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + '\n')

    print(f"\n✅ {len(examples)} paires générées ({errors} erreurs)")
    print(f"   Fichier: {output}")
    print(f"   Taille:  {os.path.getsize(output)/1024:.1f} KB")

if __name__ == "__main__":
    asyncio.run(main())

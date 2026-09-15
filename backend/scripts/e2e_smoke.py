"""E2E smoke test against the running stack (localhost:8001).

Covers the fixes landed this session: auth real-JWT path, upload rate
limit + content dedup 409, progress SSE via Authorization header, search,
streaming chat (truncation marker plumbing), graph entity extraction.
"""
import json
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://localhost:8001"
# Fixed identity: registration is rate-limited per IP (10/h) — reuse the
# same account across runs; register only on first use.
USER = "e2e_smoke_user"
PASS = "E2ePassw0rd1"


def req(method, path, data=None, headers=None):
    url = BASE + path
    body = None
    h = dict(headers or {})
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
        elif isinstance(data, bytes):
            body = data
        else:
            body = str(data).encode()
    r = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name} — {detail}")


print(f"=== E2E user: {USER} ===")

# 1. register (first run) + duplicate; fall back to login when the per-IP
# registration limiter has been exhausted by earlier runs.
s, b = req("POST", "/api/auth/register", {"username": USER, "password": PASS})
if s in (200, 400, 429):
    ok += 1
    print(f"  [PASS] register 200/400/429 (fresh: {s == 200}, reused: {s != 200})")
else:
    check("register 200", False, f"{s}: {b[:200]}")

# 2. login
form = urllib.parse.urlencode({"username": USER, "password": PASS}).encode()
s, b = req("POST", "/api/auth/login", form,
           {"Content-Type": "application/x-www-form-urlencoded"})
check("login 200", s == 200, f"{s}: {b[:200]}")
token = json.loads(b)["access_token"]
check("token non-empty", bool(token))
AUTH = {"Authorization": f"Bearer {token}"}

# 3. /me real + forged
s, b = req("GET", "/api/auth/me", headers=AUTH)
check("me 200", s == 200 and json.loads(b)["username"] == USER, str(s))
s, b = req("GET", "/api/auth/me", headers={"Authorization": "Bearer forged.token"})
check("me forged token 401", s == 401, str(s))

# 4. upload
_run_tag = uuid.uuid4().hex[:6]
content = (f"# Knowledge Graph E2E {_run_tag}\n"
           "The NEXUS knowledge graph system extracts entities and relations from documents. "
           "OpenAI develops the GPT language model. Anthropic builds Claude, an AI assistant. "
           "Neo4j stores the graph database and ChromaDB stores the vector embeddings.\n"
           "* RAG retrieval combines BM25 with vector search.\n"
           "* Graph traversal finds related entities.\n") * 8
boundary = "----E2E" + uuid.uuid4().hex
mp = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
      f"filename=\"e2e.txt\"\r\nContent-Type: text/plain\r\n\r\n{content}\r\n"
      f"--{boundary}--\r\n").encode()
s, b = req("POST", "/api/documents/upload", mp,
           {**AUTH, "Content-Type": f"multipart/form-data; boundary={boundary}"})
check("upload 200/201", s in (200, 201), f"{s}: {b[:300]}")
doc = json.loads(b)
doc_id = doc["id"]
print(f"  doc_id={doc_id} status={doc.get('status')}")

# 5. duplicate upload -> 409
s, b = req("POST", "/api/documents/upload", mp,
           {**AUTH, "Content-Type": f"multipart/form-data; boundary={boundary}"})
check("duplicate upload 409", s == 409, f"{s}: {b[:200]}")

# 6. progress SSE via Authorization header (fetch-style, no ?token=)
deadline = time.time() + 240
status = None
while time.time() < deadline:
    try:
        r = urllib.request.Request(BASE + f"/api/progress/{doc_id}", headers=AUTH)
        # read() blocks until the stream closes; the socket timeout must
        # comfortably span a long LLM-extraction window with no status
        # transition (progress only emits on status changes).
        with urllib.request.urlopen(r, timeout=120) as resp:
            data = resp.read().decode()
    except (TimeoutError, urllib.error.URLError):
        # Stream still open, processing slow — re-open and keep waiting.
        time.sleep(5)
        continue
    events = [json.loads(l[6:]) for l in data.splitlines() if l.startswith("data:")]
    last = events[-1] if events else {}
    status = last.get("type")
    if status in ("complete", "error"):
        break
    time.sleep(10)
check("progress stream via header auth", status == "complete",
      f"last={status} {data[-200:]}")
s, b = req("GET", "/api/documents", headers=AUTH)
docs = json.loads(b)
if isinstance(docs, dict):
    docs = docs.get("documents") or []
check("documents list non-empty", len(docs) >= 1, str(b[:200]))

# 7. search
s, b = req("POST", "/api/search",
           {"query": "what database stores the knowledge graph?", "top_k": 5}, AUTH)
check("search 200", s == 200, f"{s}: {b[:300]}")
search = json.loads(b)
check("search returns chunks", len(search.get("chunks", [])) > 0, str(b[:300]))

# 8. chat stream (SSE)
payload = json.dumps({"message": "Which company builds the Claude model?"}).encode()
r = urllib.request.Request(BASE + "/api/chat/stream", data=payload,
                           headers={**AUTH, "Content-Type": "application/json"},
                           method="POST")
with urllib.request.urlopen(r, timeout=120) as resp:
    raw = resp.read().decode()
chunks = [json.loads(l[6:]) for l in raw.splitlines() if l.startswith("data:")]
kinds = set()
answer = ""
for c in chunks:
    if "chunk" in c:
        answer += c["chunk"]
        kinds.add("chunk")
    if "sources" in c:
        kinds.add("sources")
    if "error" in c:
        kinds.add("error")
check("chat stream has chunk frames", "chunk" in kinds, raw[:200])
check("chat stream has sources/done frame", "sources" in kinds, raw[-300:])
check("chat answer non-empty", len(answer) > 5, f"answer={answer[:100]!r}")

# 9. graph entities
s, b = req("GET", "/api/graph/entities", headers=AUTH)
check("graph entities 200", s == 200, f"{s}: {b[:200]}")
try:
    ents = json.loads(b)
    n = len(ents) if isinstance(ents, list) else len(ents.get("entities", []))
    check("graph has extracted entities", n > 0, f"count={n}")
except Exception as e:
    check("graph has extracted entities", False, str(e))

# 10. merge entities (FEAT-025 duplicates page). Regression gate for the
# AsyncBoltDriver.execute_write AttributeError: the async driver only
# exposes execute_write on the SESSION, so a driver-level call 500s on
# the live stack while unit fakes (which mirrored the wrong API) stayed
# green. Deterministic: seed two probe entities directly in Neo4j (the
# e2e corpus may not produce duplicate groups), then merge through the
# exact endpoint the page calls, then verify the source is gone.
MERGE_SRC, MERGE_TGT = "MergeProbeAlpha", "MergeProbeBeta"


def _seed_probes(uid):
    import asyncio
    import sys
    from pathlib import Path
    # Anchor backend/ on sys.path so the script runs from any cwd.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.services.neo4j_client import Neo4jClient

    async def _go():
        c = Neo4jClient()
        await c.connect()
        try:
            await c.create_entities_batch([
                {"name": MERGE_SRC, "type": "CONCEPT", "description": "e2e probe"},
                {"name": MERGE_TGT, "type": "CONCEPT", "description": "e2e probe"},
            ], user_id=uid)
        finally:
            await c.close()

    asyncio.run(_go())


s, b = req("GET", "/api/auth/me", headers=AUTH)
uid = json.loads(b)["id"]
_seed_probes(uid)
s, b = req("POST", "/api/graph/entities/merge",
           {"source": MERGE_SRC, "target": MERGE_TGT}, AUTH)
try:
    summary = json.loads(b)
    check("merge endpoint 200 + source deleted", s == 200 and summary.get("source_deleted") == 1,
          f"{s}: {b[:200]}")
except Exception as e:
    check("merge endpoint 200 + source deleted", False, str(e))

print(f"\n=== RESULT: {ok} passed, {fail} failed ===")
raise SystemExit(1 if fail else 0)

import os
import json
import logging
import asyncpg
import httpx
from fastapi import HTTPException
from fastmcp import FastMCP

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Elkro.MCP")

mcp = FastMCP("Elkro Cognitive Layer")

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/elkro")
INFERENCE_URL = os.environ.get("INFERENCE_URL", "http://127.0.0.1:8091")

_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=10)
    return _pool

async def get_embedding(text: str) -> list[float]:
    """Fetches text embeddings asynchronously from the local container backend."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(
                f"{INFERENCE_URL}/v1/embeddings",
                json={"model": "nomic-embed-text", "input": text}
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]
        except Exception as e:
            logger.error(f"Failed to fetch embedding from inference engine: {str(e)}")
            raise HTTPException(status_code=500, detail="Embedding service unavailable")

INFERENCE_CHAT_URL = os.environ.get("INFERENCE_CHAT_URL", "http://127.0.0.1:8090/v1/chat/completions")
INFERENCE_CHAT_MODEL = os.environ.get("INFERENCE_CHAT_MODEL", "gpt-oss-20b")

async def run_extraction_llm(prompt: str, max_tokens: int = 768) -> str:
    """Calls the chat model (via llama-swap) to run an extraction prompt."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            INFERENCE_CHAT_URL,
            json={
                "model": INFERENCE_CHAT_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": max_tokens,
            }
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()


def _clean_json_array(text: str) -> list:
    """Strips markdown fences and parses a JSON array, returns [] on failure."""
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        logger.error(f"Failed to parse extraction JSON: {text[:200]}")
        return []


def _format_transcript(messages: list, char_limit: int = 8000) -> str:
    """Flattens a Hermes messages array into a plain transcript for prompting."""
    lines = []
    for m in messages:
        role = m.get("role", "unknown").upper()
        content = m.get("content", "")
        if isinstance(content, list):
            # handle content blocks (tool calls etc.) — extract text parts only
            content = " ".join(
                c.get("text", "") for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            )
        if isinstance(content, str) and content.strip():
            lines.append(f"{role}: {content.strip()}")
    transcript = "\n".join(lines)
    return transcript[-char_limit:]

# ── INITIALIZATION & SCOPE RESOLUTION ────────────────────────────────────────

@mcp.tool()
async def resolve_context_scopes(platform: str, platform_id: str, channel_id: str, active_agent: str = "orchestrator") -> dict:
    """Translates raw platform identities into secure, canonical project scopes."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT canonical_id FROM identities WHERE platform = $1 AND platform_id = $2",
            platform, platform_id
        )
    
    canonical_id = row["canonical_id"] if row else f"user_{platform}_{platform_id}"
    
    return {
        "private_project_id": f"{active_agent}:private:{canonical_id}",
        "shared_project_id": f"{active_agent}:shared:{channel_id}"
    }

# ── TIER 1: SEMANTIC VECTOR MEMORIES ──────────────────────────────────────────

@mcp.tool()
async def search_memories_scoped(query: str, private_project_id: str, shared_project_id: str, limit: int = 5) -> str:
    """Performs a hybrid vector search spanning both private personal context and community channels."""
    pool = await get_pool()
    embedding = await get_embedding(query)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT content, scope, embedding <=> $1::vector AS distance
            FROM memories
            WHERE project_id IN ($2, $3) AND scope != 'pending_share'
            ORDER BY distance ASC
            LIMIT $4
            """,
            str(embedding), private_project_id, shared_project_id, limit
        )

    if not rows:
        return "No relevant matching semantic memories located in either active scope."

    formatted_results = []
    for r in rows:
        score = 1 - float(r['distance'])
        formatted_results.append(f"[{r['scope'].upper()} Scope][Match Accuracy: {score:.2%}]\n{r['content']}")

    return "\n\n---\n\n".join(formatted_results)

@mcp.tool()
async def save_memory(content: str, project_id: str, scope: str = "private", metadata: dict = None) -> str:
    """Saves a raw text snippet or contextual insight directly into a specific project context."""
    pool = await get_pool()
    embedding = await get_embedding(content)
    meta_json = json.dumps(metadata or {})

    async with pool.acquire() as conn:
        memory_id = await conn.fetchval(
            """
            INSERT INTO memories (project_id, content, embedding, metadata, scope)
            VALUES ($1, $2, $3::vector, $4::jsonb, $5)
            RETURNING id
            """,
            project_id, content, str(embedding), meta_json, scope
        )
    return f"Successfully saved memory [{scope.upper()}] with ID: {memory_id}"

@mcp.tool()
async def propose_shared_memory(content: str, private_project_id: str, shared_project_id: str) -> str:
    """Flags a discovery or rule during conversation as pending community knowledge."""
    pool = await get_pool()
    embedding = await get_embedding(content)

    async with pool.acquire() as conn:
        async with conn.transaction():
            memory_id = await conn.fetchval(
                """
                INSERT INTO memories (project_id, content, embedding, scope)
                VALUES ($1, $2, $3::vector, 'pending_share')
                RETURNING id
                """,
                private_project_id, content, str(embedding)
            )
            await conn.execute(
                """
                INSERT INTO share_requests (memory_id, project_id, target_project_id, content)
                VALUES ($1, $2, $3, $4)
                """,
                memory_id, private_project_id, shared_project_id, content
            )
    return f"SIGNAL_TRIGGER_SHARE_REQUEST:{memory_id}:{shared_project_id}"

# ── TIER 2: SUMMARIES (FIXED: Corrected function references) ───────────────────

@mcp.tool()
async def save_summary(content: str, project_id: str) -> str:
    """Save a rolled-up session or project summary."""
    pool = await get_pool()
    vector = await get_embedding(content)
    
    await pool.execute("""
        INSERT INTO summaries (project_id, content, embedding)
        VALUES ($1, $2, $3::vector)
    """, project_id, content, str(vector))
    return "Summary saved."

@mcp.tool()
async def search_summaries(query: str, project_id: str, limit: int = 3) -> str:
    """Search high-level project summaries."""
    pool = await get_pool()
    vector = await get_embedding(query)
    
    rows = await pool.fetch("""
        SELECT content FROM summaries
        WHERE project_id = $2
        ORDER BY embedding <=> $1::vector LIMIT $3
    """, str(vector), project_id, limit)
    return "\n\n".join([r["content"] for r in rows])

# ── TIER 3: ATOMIC FACTS ──────────────────────────────────────────────────────

@mcp.tool()
async def get_fact(private_project_id: str, shared_project_id: str, key: str) -> str:
    """Retrieves an exact fact, checking private overrides before shared constants."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT value, project_id FROM facts
            WHERE project_id IN ($1, $2) AND key = $3
            ORDER BY CASE WHEN project_id = $1 THEN 1 ELSE 2 END ASC
            """,
            private_project_id, shared_project_id, key
        )
    if row:
        scope_type = "PRIVATE" if row['project_id'] == private_project_id else "SHARED"
        return f"[{scope_type} FACT FOUND] {key}: {row['value']}"
    return f"No exact matching fact values located under key '{key}'."

@mcp.tool()
async def list_facts(project_id: str) -> str:
    """List all exact configuration facts tracked for a project context."""
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT key, value FROM facts WHERE project_id = $1 ORDER BY key
    """, project_id)
    return "\n".join([f"- {r['key']}: {r['value']}" for r in rows])

# ── TIER 4: SESSION-LEVEL EXTRACTION (LLM-powered) ───────────────────────────

@mcp.tool()
async def extract_session_summary(messages: list, project_id: str) -> str:
    """Runs LLM extraction over a full session transcript, saving a narrative
    summary to `summaries` and atomic facts to `memories`."""
    transcript = _format_transcript(messages)
    if len(transcript) < 100:
        return "Skipped: transcript too short."

    # --- Narrative summary ---
    summary_prompt = (
        "Summarize this conversation in 2-4 sentences, focused on what the user "
        "was working on, decided, or asked about. Write in third person, past tense. "
        "Return ONLY the summary text, no preamble.\n\n"
        f"CONVERSATION:\n{transcript}"
    )
    try:
        summary_text = await run_extraction_llm(summary_prompt, max_tokens=256)
        if summary_text:
            await save_summary(content=summary_text, project_id=project_id)
    except Exception as e:
        logger.error(f"Session summary extraction failed: {e}")

    # --- Atomic facts ---
    facts_prompt = (
        "Extract 3-7 specific, durable facts worth remembering long-term about "
        "the user from this conversation. Focus on: personal details, preferences, "
        "project names, technical setup, goals. Ignore small talk.\n"
        "Return ONLY a JSON array of strings, no explanation, no markdown.\n"
        "Example: [\"User's homelab is called Runes\", \"User prefers Python\"]\n\n"
        f"CONVERSATION:\n{transcript}"
    )
    saved = 0
    try:
        facts_raw = await run_extraction_llm(facts_prompt, max_tokens=512)
        facts = _clean_json_array(facts_raw)
        for fact in facts:
            if isinstance(fact, str) and len(fact) > 10:
                await save_memory(content=fact, project_id=project_id, scope="private")
                saved += 1
    except Exception as e:
        logger.error(f"Session fact extraction failed: {e}")

    return f"Session extraction complete: summary={'yes' if summary_text else 'no'}, facts_saved={saved}"


@mcp.tool()
async def rescue_compressed_memories(messages: list, project_id: str) -> str:
    """Extracts atomic facts from messages about to be discarded by context
    compression. Lighter-weight than full session extraction — facts only."""
    transcript = _format_transcript(messages, char_limit=6000)
    if len(transcript) < 100:
        return "Skipped: transcript too short."

    prompt = (
        "These messages are about to be discarded from context. Extract any "
        "durable facts about the user worth keeping (preferences, decisions, "
        "technical details, project context). Ignore tool noise and small talk.\n"
        "Return ONLY a JSON array of strings, no explanation, no markdown. "
        "Return [] if nothing is worth keeping.\n\n"
        f"MESSAGES:\n{transcript}"
    )
    saved = 0
    try:
        facts_raw = await run_extraction_llm(prompt, max_tokens=512)
        facts = _clean_json_array(facts_raw)
        for fact in facts:
            if isinstance(fact, str) and len(fact) > 10:
                await save_memory(content=fact, project_id=project_id, scope="private")
                saved += 1
    except Exception as e:
        logger.error(f"Pre-compress rescue failed: {e}")

    return f"Rescued {saved} fact(s) before compression."


# ── TIER 3b: FACT WRITES ──────────────────────────────────────────────────────

@mcp.tool()
async def set_fact(project_id: str, key: str, value: str) -> str:
    """Upserts a structured key-value fact (e.g. turn counters, active state)."""
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO facts (project_id, key, value)
        VALUES ($1, $2, $3)
        ON CONFLICT (project_id, key) DO UPDATE SET value = EXCLUDED.value
        """,
        project_id, key, value
    )
    return f"Fact '{key}' set."

# ── REST SHIM FOR HERMES PLUGIN ───────────────────────────────────────────────

from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import JSONResponse

async def rest_resolve_context_scopes(request: Request):
    body = await request.json()
    result = await resolve_context_scopes(
        platform=body["platform"],
        platform_id=body["platform_id"],
        channel_id=body["channel_id"],
        active_agent=body.get("active_agent", "orchestrator")
    )
    return JSONResponse({"result": result})

async def rest_search_memories_scoped(request: Request):
    body = await request.json()
    result = await search_memories_scoped(
        query=body["query"],
        private_project_id=body["private_project_id"],
        shared_project_id=body["shared_project_id"],
        limit=body.get("limit", 4)
    )
    return JSONResponse({"result": result})

async def rest_save_memory(request: Request):
    body = await request.json()
    result = await save_memory(
        content=body["content"],
        project_id=body["project_id"],
        scope=body.get("scope", "private"),
        metadata=body.get("metadata")
    )
    return JSONResponse({"result": result})

async def rest_propose_shared_memory(request: Request):
    body = await request.json()
    result = await propose_shared_memory(
        content=body["content"],
        private_project_id=body["private_project_id"],
        shared_project_id=body["shared_project_id"]
    )
    return JSONResponse({"result": result})

async def rest_extract_session_summary(request: Request):
    body = await request.json()
    result = await extract_session_summary(
        messages=body["messages"],
        project_id=body["project_id"]
    )
    return JSONResponse({"result": result})

async def rest_rescue_compressed_memories(request: Request):
    body = await request.json()
    result = await rescue_compressed_memories(
        messages=body["messages"],
        project_id=body["project_id"]
    )
    return JSONResponse({"result": result})

async def rest_set_fact(request: Request):
    body = await request.json()
    result = await set_fact(
        project_id=body["project_id"],
        key=body["key"],
        value=body["value"]
    )
    return JSONResponse({"result": result})

rest_routes = [
    Route("/tools/resolve_context_scopes", rest_resolve_context_scopes, methods=["POST"]),
    Route("/tools/search_memories_scoped", rest_search_memories_scoped, methods=["POST"]),
    Route("/tools/save_memory", rest_save_memory, methods=["POST"]),
    Route("/tools/propose_shared_memory", rest_propose_shared_memory, methods=["POST"]),
    Route("/tools/extract_session_summary", rest_extract_session_summary, methods=["POST"]),
    Route("/tools/rescue_compressed_memories", rest_rescue_compressed_memories, methods=["POST"]),
    Route("/tools/set_fact", rest_set_fact, methods=["POST"]),
]
# ── SERVING APPLICATION ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    from starlette.routing import Mount
    mcp_app = mcp.http_app(transport="streamable-http")
    app = Starlette(routes=[
        Mount("/mcp", app=mcp_app),
        *rest_routes,
    ])
    uvicorn.run(app, host="0.0.0.0", port=8000)
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
INFERENCE_CHAT_MODEL = os.environ.get("INFERENCE_CHAT_MODEL", "qwen-3.5-9b")
INFERENCE_API_KEY = os.environ.get("INFERENCE_API_KEY", "")
EXTRACTION_ENABLED = os.environ.get("EXTRACTION_ENABLED", "true").lower() == "true"
EXTRACTION_MAX_RETRIES = int(os.environ.get("EXTRACTION_MAX_RETRIES", "3"))

from models import ExtractionResult

async def run_extraction_llm(prompt: str, max_tokens: int = 4096, assistant_prefill: str = None) -> str:
    """Helper to run the inference model. assistant_prefill adds a prefill string."""
    headers = {
        "Content-Type": "application/json"
    }
    if INFERENCE_API_KEY:
        headers["Authorization"] = f"Bearer {INFERENCE_API_KEY}"
        
    messages = [{"role": "user", "content": prompt}]
    if assistant_prefill:
        messages.append({"role": "assistant", "content": assistant_prefill})
    
    payload = {
        "model": INFERENCE_CHAT_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            INFERENCE_CHAT_URL,
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        content = message.get("content", "").strip()
        if not content:
            content = message.get("reasoning_content", "").strip()
        
        if assistant_prefill:
            return assistant_prefill + content
        return content

async def extract_structured_facts(prompt: str, max_tokens: int = 4096) -> ExtractionResult:
    """Uses LLM to extract facts from a transcript into Pydantic models."""
    try:
        raw_json = await run_extraction_llm(prompt, max_tokens=max_tokens, assistant_prefill="```json\n{\n  \"facts\": [\n")
        return ExtractionResult.parse_raw_json(raw_json)
    except Exception as e:
        logger.error(f"Failed LLM extraction call: {str(e)}")
        return ExtractionResult(facts=[])


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
async def search_memories_scoped(query: str, private_project_id: str, shared_project_id: str, limit: int = 5, source_filter: str = None) -> str:
    """Performs a hybrid vector+keyword search spanning both private personal context and community channels."""
    pool = await get_pool()
    embedding = await get_embedding(query)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH vector_search AS (
                SELECT id,
                       embedding <=> $1::vector AS distance,
                       RANK() OVER (ORDER BY embedding <=> $1::vector ASC) AS rank
                FROM memories
                WHERE project_id IN ($2, $3) AND scope != 'pending_share'
                  AND ($6::text IS NULL OR metadata->>'source' = $6::text)
                ORDER BY distance ASC
                LIMIT 30
            ),
            text_search AS (
                SELECT id,
                       ts_rank_cd(tsv, websearch_to_tsquery('english', $4)) AS bm25_score,
                       RANK() OVER (ORDER BY ts_rank_cd(tsv, websearch_to_tsquery('english', $4)) DESC) AS rank
                FROM memories
                WHERE project_id IN ($2, $3) AND scope != 'pending_share'
                  AND ($6::text IS NULL OR metadata->>'source' = $6::text)
                  AND tsv @@ websearch_to_tsquery('english', $4)
                ORDER BY bm25_score DESC
                LIMIT 30
            )
            SELECT m.id, m.content, m.scope,
                   (COALESCE(1.0 / (60 + v.rank), 0.0) +
                    COALESCE(1.0 / (60 + t.rank), 0.0)) * m.confidence AS rrf_score,
                   v.distance
            FROM memories m
            LEFT JOIN vector_search v ON m.id = v.id
            LEFT JOIN text_search t ON m.id = t.id
            WHERE m.id IN (SELECT id FROM vector_search UNION SELECT id FROM text_search)
            ORDER BY rrf_score DESC
            LIMIT $5
            """,
            str(embedding), private_project_id, shared_project_id, query, limit, source_filter
        )

    filtered_rows = [r for r in rows if r['rrf_score'] > 0.005]

    if not filtered_rows:
        return "No relevant matching memories located in either active scope."

    # Update access counts
    if filtered_rows:
        memory_ids = [r['id'] for r in filtered_rows]
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE memories SET access_count = access_count + 1, last_accessed = NOW() WHERE id = ANY($1)",
                memory_ids
            )

    formatted_results = []
    for r in filtered_rows:
        dist_str = f"{(1 - float(r['distance'])):.2%}" if r['distance'] is not None else "Keyword Match"
        formatted_results.append(f"[{r['scope'].upper()} Scope][RRF: {r['rrf_score']:.4f} | Semantic: {dist_str}]\n{r['content']}")

    return "\n\n---\n\n".join(formatted_results)

@mcp.tool()
async def list_memories(project_id: str, scope: str = None, source: str = None, limit: int = 50, offset: int = 0) -> str:
    """Lists raw memories with optional filtering by scope and source. Use for debugging/browsing."""
    pool = await get_pool()
    query = "SELECT id, content, scope, metadata, created_at, confidence FROM memories WHERE project_id = $1"
    args = [project_id]
    param_idx = 2
    
    if scope:
        query += f" AND scope = ${param_idx}"
        args.append(scope)
        param_idx += 1
        
    if source:
        query += f" AND metadata->>'source' = ${param_idx}"
        args.append(source)
        param_idx += 1
        
    query += f" ORDER BY created_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
    args.extend([limit, offset])

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        
    if not rows:
        return "No memories found matching the criteria."
        
    results = []
    for r in rows:
        meta_str = json.dumps(r['metadata']) if r['metadata'] else "{}"
        results.append(f"[{r['created_at'].strftime('%Y-%m-%d %H:%M')}] {r['scope'].upper()} (Conf: {r['confidence']:.1f}) {meta_str}\n{r['content']}")
        
    return "\n---\n".join(results)

@mcp.tool()
async def save_memory(content: str, project_id: str, scope: str = "private", metadata: dict = None) -> str:
    """Saves a raw text snippet or contextual insight directly into a specific project context."""
    pool = await get_pool()
    metadata = metadata or {}
    
    # Try to extract a timestamp from metadata for temporal reasoning
    session_timestamp = metadata.get("session_date") or metadata.get("session_X_date_time")
    
    # Prefix content with timestamp if available to help temporal retrieval
    if session_timestamp and not content.startswith("["):
        content = f"[{session_timestamp}] {content}"

    embedding = await get_embedding(content)
    meta_json = json.dumps(metadata)

    async with pool.acquire() as conn:
        # Dedup check
        existing = await conn.fetchrow(
            """
            SELECT id, embedding <=> $1::vector AS distance
            FROM memories
            WHERE project_id = $2
            ORDER BY distance ASC
            LIMIT 1
            """,
            str(embedding), project_id
        )
        if existing and float(existing['distance']) < 0.05:
            return f"Skipped saving memory (duplicate of {existing['id']}) [{scope.upper()}]"

        # We insert the session_timestamp if valid, else fallback to NOW() happens via DEFAULT or COALESCE in SQL.
        # But to avoid Postgres casting errors from weird strings, we just rely on DEFAULT NOW() if it's missing.
        try:
            memory_id = await conn.fetchval(
                """
                INSERT INTO memories (project_id, content, embedding, metadata, scope, timestamp)
                VALUES ($1, $2, $3::vector, $4::jsonb, $5, COALESCE($6::timestamptz, NOW()))
                RETURNING id
                """,
                project_id, content, str(embedding), meta_json, scope, session_timestamp if session_timestamp else None
            )
        except asyncpg.exceptions.DataError:
            # If session_timestamp cannot be cast to timestamptz, ignore it for the DB column
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
async def remove_memory(content: str, project_id: str) -> str:
    """Removes a memory from the semantic store by close semantic distance."""
    pool = await get_pool()
    embedding = await get_embedding(content)
    async with pool.acquire() as conn:
        deleted = await conn.execute(
            """
            DELETE FROM memories
            WHERE id IN (
                SELECT id FROM memories
                WHERE project_id = $1
                  AND embedding <=> $2::vector < 0.15
                ORDER BY embedding <=> $2::vector ASC
                LIMIT 1
            )
            """,
            project_id, str(embedding)
        )
    return f"Memory removed: {deleted}"

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
            SELECT value, project_id FROM legacy_facts
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
        SELECT key, value FROM legacy_facts WHERE project_id = $1 ORDER BY key
    """, project_id)
    return "\n".join([f"- {r['key']}: {r['value']}" for r in rows])

# ── TIER 4: SESSION-LEVEL EXTRACTION (LLM-powered) ───────────────────────────

async def save_fact(
    project_id: str, fact_text: str, fact_kind: str = 'state',
    who: list = None, what: str = None, when_description: str = None,
    when_start=None, when_end=None, where_location: str = None, why: str = None,
    source_chunk: str = None, source_session_id: str = None, source_session_date=None,
    source_memory_id: str = None
) -> str:
    """Internal helper to save a structured fact to the database."""
    pool = await get_pool()
    embedding = await get_embedding(fact_text)
    who = who or []
    
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("""
            SELECT id, fact_text, confidence, embedding <=> $1::vector AS distance
            FROM facts
            WHERE project_id = $2
            ORDER BY distance ASC
            LIMIT 1
        """, str(embedding), project_id)
        
        if existing and float(existing['distance']) < 0.08:
            # Update confidence of existing fact instead of inserting duplicate
            await conn.execute(
                "UPDATE facts SET confidence = LEAST(confidence + 0.1, 1.0), updated_at = NOW() WHERE id = $1",
                existing['id']
            )
            return str(existing['id'])

        fact_id = await conn.fetchval(
            """
            INSERT INTO facts (
                project_id, fact_text, fact_kind, who, what, when_description, 
                when_start, when_end, where_location, why, source_chunk, 
                source_session_id, source_session_date, source_memory_id, embedding
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::vector
            )
            RETURNING id
            """,
            project_id, fact_text, fact_kind, who, what, when_description,
            when_start, when_end, where_location, why, source_chunk,
            source_session_id, source_session_date, source_memory_id, str(embedding)
        )
    return str(fact_id)

async def save_entity_relationship(
    project_id: str, subject: str, predicate: str, object_name: str, source_fact_id: str
):
    """Internal helper to save an entity relationship."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Simple dedup
        existing = await conn.fetchval(
            "SELECT id FROM entity_relationships WHERE project_id = $1 AND subject = $2 AND predicate = $3 AND object = $4",
            project_id, subject, predicate, object_name
        )
        if not existing:
            await conn.execute(
                """
                INSERT INTO entity_relationships (project_id, subject, predicate, object, source_fact_id)
                VALUES ($1, $2, $3, $4, $5)
                """,
                project_id, subject, predicate, object_name, source_fact_id
            )

async def save_entity_profile(project_id: str, entity_name: str, summary: str):
    """Internal helper to save or update an entity profile."""
    pool = await get_pool()
    embedding = await get_embedding(summary)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO entity_profiles (project_id, entity_name, summary, embedding)
            VALUES ($1, $2, $3, $4::vector)
            ON CONFLICT (project_id, entity_name) 
            DO UPDATE SET summary = EXCLUDED.summary, embedding = EXCLUDED.embedding, updated_at = NOW()
            """,
            project_id, entity_name, summary, str(embedding)
        )

async def queue_entity_consolidation(project_id: str, entity_name: str):
    """Queues an entity consolidation task for the worker."""
    if not EXTRACTION_ENABLED:
        return
    pool = await get_pool()
    payload = json.dumps({"entity_name": entity_name})
    await pool.execute(
        """
        INSERT INTO extraction_queue (project_id, payload, task_type)
        VALUES ($1, $2::jsonb, 'entity_consolidation')
        """,
        project_id, payload
    )

@mcp.tool()
async def search_facts(query: str, project_id: str, limit: int = 5) -> str:
    """Searches structured facts via vector similarity."""
    pool = await get_pool()
    embedding = await get_embedding(query)
    
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, fact_text, fact_kind, who, what, when_description, when_start, where_location, why, source_chunk,
                   embedding <=> $1::vector AS distance
            FROM facts
            WHERE project_id = $2
            ORDER BY distance ASC
            LIMIT $3
            """,
            str(embedding), project_id, limit
        )
        
    if not rows:
        return "No relevant facts found."
        
    formatted = []
    for r in rows:
        # Build 5W components
        who_str = f", Who: {', '.join(r['who'])}" if r['who'] else ""
        what_str = f", What: {r['what']}" if r['what'] else ""
        when_str = ""
        if r['when_description']:
            when_str += f", When: {r['when_description']}"
        elif r['when_start']:
            when_str += f", When: {r['when_start'].strftime('%Y-%m-%d')}"
        where_str = f", Where: {r['where_location']}" if r['where_location'] else ""
        why_str = f", Why: {r['why']}" if r['why'] else ""
        source_str = f", Source: \"{r['source_chunk']}\"" if r['source_chunk'] else ""
        
        # Assemble string
        metadata_str = f"Distance: {float(r['distance']):.2f}{who_str}{what_str}{when_str}{where_str}{why_str}{source_str}"
        formatted.append(f"[FACT: {r['fact_kind'].upper()}] {r['fact_text']} ({metadata_str})")
        
    return "\n\n".join(formatted)

@mcp.tool()
async def search_relationships(project_id: str, entity: str) -> str:
    """Searches for relationships involving a specific entity (as subject or object)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT subject, predicate, object
            FROM entity_relationships
            WHERE project_id = $1 AND (subject ILIKE $2 OR object ILIKE $2)
            LIMIT 20
            """,
            project_id, f"%{entity}%"
        )
        
    if not rows:
        return ""
        
    return "\n".join([f"- {r['subject']} {r['predicate']} {r['object']}" for r in rows])

@mcp.tool()
async def search_entity_profiles(query: str, project_id: str, limit: int = 2) -> str:
    """Searches consolidated entity profiles via vector similarity."""
    pool = await get_pool()
    embedding = await get_embedding(query)
    
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT entity_name, summary,
                   embedding <=> $1::vector AS distance
            FROM entity_profiles
            WHERE project_id = $2
            ORDER BY distance ASC
            LIMIT $3
            """,
            str(embedding), project_id, limit
        )
        
    if not rows:
        return ""
        
    formatted = []
    for r in rows:
        # Only return profiles that are somewhat relevant
        if float(r['distance']) < 0.8:
            formatted.append(f"--- PROFILE: {r['entity_name']} ---\n{r['summary']}\n-----------------")
        
    return "\n\n".join(formatted)

@mcp.tool()
async def retrieve_context(query: str, project_id: str, limit: int = 5) -> str:
    """Dynamic context assembly: fetches synthesized entity profiles + atomic facts."""
    
    # 1. Fetch synthesized profiles (Anti-Soup layer)
    profiles_str = await search_entity_profiles(query, project_id, limit=2)
    
    # 2. Fetch specific atomic facts (for temporal or highly specific lookups)
    facts_str = await search_facts(query, project_id, limit=limit)
    
    # Combine context
    context_parts = []
    if profiles_str:
        context_parts.append("[SYNTHESIZED ENTITY PROFILES]\n" + profiles_str)
    
    if facts_str:
        context_parts.append("[RECENT ATOMIC FACTS]\n" + facts_str)
        
    if not context_parts:
        return "No relevant context found."
        
    return "\n\n".join(context_parts)

@mcp.tool()
async def extract_session_summary(messages: list, project_id: str) -> str:
    """Queues LLM extraction over a full session transcript for background processing."""
    if not EXTRACTION_ENABLED:
        return "Skipped: extraction disabled via config."
        
    pool = await get_pool()
    payload = json.dumps(messages)
    await pool.execute(
        """
        INSERT INTO extraction_queue (project_id, payload, task_type)
        VALUES ($1, $2::jsonb, 'session_summary')
        """,
        project_id, payload
    )
    return "Session summary extraction queued successfully."

@mcp.tool()
async def extract_session_sync(transcript: str, project_id: str, session_date_str: str) -> str:
    """Synchronously extracts facts and relationships from a transcript and saves them. Used for benchmarking."""
    from worker import STRUCTURED_FACT_PROMPT
    
    # 1. Structured Facts
    facts_prompt = STRUCTURED_FACT_PROMPT.format(today=session_date_str, session_date=session_date_str, transcript=transcript)
    result = await extract_structured_facts(facts_prompt, max_tokens=2048)
    
    count = 0
    for fact_obj in result.facts:
        # Save Fact
        fact_id = await save_fact(
            project_id=project_id,
            fact_text=fact_obj.fact_text,
            fact_kind=fact_obj.fact_kind,
            who=fact_obj.who,
            what=fact_obj.what,
            when_description=fact_obj.when_description,
            when_start=fact_obj.when_resolved,
            where_location=fact_obj.where,
            why=fact_obj.why,
            source_chunk=fact_obj.source_quote
        )
        count += 1
        
        # Save Relationships
        for ent in fact_obj.entities:
            if ent.subject and ent.predicate and ent.object:
                await save_entity_relationship(
                    project_id=project_id,
                    subject=ent.subject,
                    predicate=ent.predicate,
                    object_name=ent.object,
                    source_fact_id=fact_id
                )
                
    # Queue consolidation for any entity discovered
    entities_to_consolidate = set()
    for fact_obj in result.facts:
        if fact_obj.who:
            entities_to_consolidate.update(fact_obj.who)
        for ent in fact_obj.entities:
            if ent.subject: entities_to_consolidate.add(ent.subject)
            if ent.object: entities_to_consolidate.add(ent.object)
            
    for entity in entities_to_consolidate:
        # Don't consolidate generic placeholders
        if entity.lower() not in ('user', 'unknown', 'someone'):
            await queue_entity_consolidation(project_id, entity)

    return f"Extracted and saved {count} facts synchronously. Queued {len(entities_to_consolidate)} entities for consolidation."

@mcp.tool()
async def rescue_compressed_memories(messages: list, project_id: str) -> str:
    """Queues pre-compression fact extraction for background processing."""
    if not EXTRACTION_ENABLED:
        return "Skipped: extraction disabled via config."
        
    pool = await get_pool()
    payload = json.dumps(messages)
    await pool.execute(
        """
        INSERT INTO extraction_queue (project_id, payload, task_type)
        VALUES ($1, $2::jsonb, 'rescue_compress')
        """,
        project_id, payload
    )
    return "Pre-compress rescue queued successfully."


# ── TIER 3b: FACT WRITES ──────────────────────────────────────────────────────

@mcp.tool()
async def set_fact(project_id: str, key: str, value: str) -> str:
    """Upserts a structured key-value fact (e.g. turn counters, active state)."""
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO legacy_facts (project_id, key, value)
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

async def rest_retrieve_context(request: Request):
    body = await request.json()
    result = await retrieve_context(
        query=body["query"],
        project_id=body["project_id"],
        limit=body.get("limit", 5)
    )
    return JSONResponse({"result": result})

async def rest_extract_session_sync(request: Request):
    body = await request.json()
    result = await extract_session_sync(
        transcript=body["transcript"],
        project_id=body["project_id"],
        session_date_str=body["session_date_str"]
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

async def rest_remove_memory(request: Request):
    body = await request.json()
    result = await remove_memory(
        content=body["content"],
        project_id=body["project_id"]
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

# ── SHARE APPROVAL FLOW ──────────────────────────────────────────────────────

async def rest_approve_share_request(request: Request):
    """Transitions a pending_share memory to shared scope."""
    request_id = request.path_params["request_id"]

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, memory_id, project_id, target_project_id FROM share_requests WHERE id = $1::uuid AND status = 'pending'",
            request_id
        )
        if not row:
            return JSONResponse({"error": "Share request not found or already resolved"}, status_code=404)

        async with conn.transaction():
            # Move memory to shared scope under the target project
            await conn.execute(
                "UPDATE memories SET scope = 'shared', project_id = $1 WHERE id = $2",
                row["target_project_id"], row["memory_id"]
            )
            await conn.execute(
                "UPDATE share_requests SET status = 'approved', resolved_at = NOW() WHERE id = $1::uuid",
                request_id
            )
            await conn.execute(
                "INSERT INTO audit_log (project_id, memory_id, action, description) VALUES ($1, $2, 'share_approved', $3)",
                row["project_id"], row["memory_id"], f"Approved share to {row['target_project_id']}"
            )

    return JSONResponse({"result": f"Share request {request_id} approved. Memory moved to shared scope."})


async def rest_reject_share_request(request: Request):
    """Rejects a pending_share memory."""
    request_id = request.path_params["request_id"]

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, memory_id, project_id FROM share_requests WHERE id = $1::uuid AND status = 'pending'",
            request_id
        )
        if not row:
            return JSONResponse({"error": "Share request not found or already resolved"}, status_code=404)

        async with conn.transaction():
            await conn.execute(
                "UPDATE share_requests SET status = 'rejected', resolved_at = NOW() WHERE id = $1::uuid",
                request_id
            )
            await conn.execute(
                "UPDATE memories SET scope = 'private' WHERE id = $1",
                row["memory_id"]
            )
            await conn.execute(
                "INSERT INTO audit_log (project_id, memory_id, action, description) VALUES ($1, $2, 'share_rejected', $3)",
                row["project_id"], row["memory_id"], "Rejected, memory reverted to private"
            )

    return JSONResponse({"result": f"Share request {request_id} rejected."})

async def rest_link_identity(request: Request):
    """Links a platform user ID to a canonical identity."""
    body = await request.json()
    platform = body["platform"]
    platform_id = body["platform_id"]
    canonical_id = body["canonical_id"]

    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO identities (platform, platform_id, canonical_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (platform, platform_id) DO UPDATE SET canonical_id = EXCLUDED.canonical_id
        """,
        platform, platform_id, canonical_id
    )
    return JSONResponse({"result": f"Identity {platform}:{platform_id} linked to {canonical_id} successfully."})

async def rest_list_pending_share_requests(request: Request):
    """Returns all pending share requests for a project."""
    project_id = request.query_params.get("project_id", "")
    if not project_id:
        return JSONResponse({"error": "project_id query parameter required"}, status_code=400)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sr.id, sr.memory_id, sr.target_project_id, sr.content, sr.proposed_at,
                   m.content AS memory_content
            FROM share_requests sr
            LEFT JOIN memories m ON sr.memory_id = m.id
            WHERE sr.project_id = $1 AND sr.status = 'pending'
            ORDER BY sr.proposed_at ASC
            """,
            project_id
        )

    requests = [
        {
            "id": str(r["id"]),
            "memory_id": str(r["memory_id"]),
            "target_project_id": r["target_project_id"],
            "content": r["content"],
            "proposed_at": r["proposed_at"].isoformat() if r["proposed_at"] else None,
        }
        for r in rows
    ]
    return JSONResponse({"requests": requests, "count": len(requests)})

async def rest_recall(request: Request):
    """Unified recall: memories + facts + relationships in one call with LLM reranking."""
    body = await request.json()
    query = body.get("query", "")
    private_pid = body.get("private_project_id", "")
    shared_pid = body.get("shared_project_id", "")
    
    # Stage 1: Retrieve broad candidate set
    profiles_str = await search_entity_profiles(query, private_pid, limit=2)
    memories_str = await search_memories_scoped(query, private_pid, shared_pid, limit=15)
    facts_str = await search_facts(query, private_pid, limit=10)
    
    # Stage 2: LLM rerank
    rerank_prompt = f"""Given the question: "{query}"

Rate each of the following facts by relevance (KEEP or DROP):

{facts_str}

Return ONLY the facts rated KEEP, in order of relevance, exactly as they are formatted above. If none are relevant, return an empty string."""

    if "No relevant facts found." not in facts_str:
        reranked_facts = await run_extraction_llm(rerank_prompt, max_tokens=1024)
        if not reranked_facts.strip():
            reranked_facts = "No relevant facts found."
    else:
        reranked_facts = facts_str
        
    result = {
        "profiles": profiles_str,
        "memories": memories_str,
        "facts": reranked_facts,
    }
    return JSONResponse({"result": result})


rest_routes = [
    Route("/tools/resolve_context_scopes", rest_resolve_context_scopes, methods=["POST"]),
    Route("/tools/search_memories_scoped", rest_search_memories_scoped, methods=["POST"]),
    Route("/tools/retrieve_context", rest_retrieve_context, methods=["POST"]),
    Route("/tools/extract_session_sync", rest_extract_session_sync, methods=["POST"]),
    Route("/tools/save_memory", rest_save_memory, methods=["POST"]),
    Route("/tools/remove_memory", rest_remove_memory, methods=["POST"]),
    Route("/tools/propose_shared_memory", rest_propose_shared_memory, methods=["POST"]),
    Route("/tools/extract_session_summary", rest_extract_session_summary, methods=["POST"]),
    Route("/tools/rescue_compressed_memories", rest_rescue_compressed_memories, methods=["POST"]),
    Route("/tools/set_fact", rest_set_fact, methods=["POST"]),
    Route("/tools/recall", rest_recall, methods=["POST"]),
    Route("/share_requests/{request_id}/approve", rest_approve_share_request, methods=["POST"]),
    Route("/share_requests/{request_id}/reject", rest_reject_share_request, methods=["POST"]),
    Route("/share_requests/pending", rest_list_pending_share_requests, methods=["GET"]),
    Route("/tools/link_identity", rest_link_identity, methods=["POST"]),
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
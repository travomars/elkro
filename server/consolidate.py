import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

from server import get_pool, run_extraction_llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] Consolidator: %(message)s")
logger = logging.getLogger(__name__)

async def run_dedup_sweep(pool):
    """Finds near-duplicate memories (distance < 0.08) and merges them."""
    logger.info("Starting Dedup Sweep...")
    async with pool.acquire() as conn:
        # Find pairs of memories that are very similar
        rows = await conn.fetch("""
            SELECT a.id as id1, b.id as id2, a.content as content1, b.content as content2,
                   a.project_id, a.metadata as meta1, b.metadata as meta2,
                   a.confidence as conf1, b.confidence as conf2
            FROM memories a
            JOIN memories b ON a.project_id = b.project_id AND a.scope = b.scope AND a.id < b.id
            WHERE a.embedding <=> b.embedding < 0.08
        """)
        
        merged_count = 0
        deleted_ids = set()
        
        for row in rows:
            id1, id2 = row['id1'], row['id2']
            if id1 in deleted_ids or id2 in deleted_ids:
                continue
                
            # Merge logic: keep the longer content, bump confidence, merge metadata
            content = row['content1'] if len(row['content1']) > len(row['content2']) else row['content2']
            conf = min(row['conf1'] + 0.1, 1.0)
            
            meta1 = row['meta1'] or {}
            meta2 = row['meta2'] or {}
            merged_meta = {**meta1, **meta2}
            
            async with conn.transaction():
                # Update id1
                await conn.execute("""
                    UPDATE memories SET content = $1, confidence = $2, metadata = $3::jsonb
                    WHERE id = $4
                """, content, conf, json.dumps(merged_meta), id1)
                
                # Delete id2
                await conn.execute("DELETE FROM memories WHERE id = $1", id2)
                
                # Log audit
                await conn.execute("""
                    INSERT INTO audit_log (project_id, memory_id, action, description)
                    VALUES ($1, $2, 'MERGE', $3)
                """, row['project_id'], id1, f"Merged duplicate {id2}")
                
            deleted_ids.add(id2)
            merged_count += 1
            
    logger.info(f"Dedup Sweep complete. Merged {merged_count} duplicates.")

async def run_staleness_sweep(pool, days=30):
    """Decrements confidence of memories not accessed in a while."""
    logger.info("Starting Staleness Sweep...")
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE memories
            SET confidence = GREATEST(confidence - 0.1, 0.1)
            WHERE last_accessed < NOW() - INTERVAL '1 day' * $1
               OR (last_accessed IS NULL AND created_at < NOW() - INTERVAL '1 day' * $1)
            RETURNING id
        """, days)
        
        # result is a string like "UPDATE 5"
        count = int(result.split()[-1])
        if count > 0:
            # We don't log every single decay row to audit_log to save space, but we log the sweep.
            logger.info(f"Decayed confidence for {count} stale memories.")
    logger.info("Staleness Sweep complete.")

async def run_contradiction_sweep(pool):
    """Finds semantic neighbors and uses LLM to check for contradictions."""
    logger.info("Starting Contradiction Sweep...")
    async with pool.acquire() as conn:
        # Find pairs of memories that are somewhat similar (distance < 0.25)
        # to see if they contradict.
        rows = await conn.fetch("""
            SELECT a.id as id1, b.id as id2, a.content as content1, b.content as content2,
                   a.project_id, a.created_at as created1, b.created_at as created2
            FROM memories a
            JOIN memories b ON a.project_id = b.project_id AND a.scope = b.scope AND a.id < b.id
            WHERE a.embedding <=> b.embedding < 0.25
            LIMIT 50  -- limit per run to save LLM tokens
        """)
        
        superseded_count = 0
        for row in rows:
            prompt = (
                "Do these two facts explicitly contradict each other? "
                "Answer ONLY 'YES' or 'NO'.\n\n"
                f"Fact A: {row['content1']}\n"
                f"Fact B: {row['content2']}"
            )
            try:
                response = await run_extraction_llm(prompt, max_tokens=10)
                if "YES" in response.upper():
                    # Decide which is newer
                    if row['created1'] > row['created2']:
                        older_id, newer_id = row['id2'], row['id1']
                    else:
                        older_id, newer_id = row['id1'], row['id2']
                        
                    async with conn.transaction():
                        # Mark older as superseded (drop confidence significantly)
                        await conn.execute("""
                            UPDATE memories SET confidence = 0.1
                            WHERE id = $1
                        """, older_id)
                        
                        await conn.execute("""
                            INSERT INTO audit_log (project_id, memory_id, action, description)
                            VALUES ($1, $2, 'SUPERSEDE', $3)
                        """, row['project_id'], older_id, f"Superseded by {newer_id}")
                    superseded_count += 1
            except Exception as e:
                logger.error(f"LLM contradiction check failed: {e}")
                
    logger.info(f"Contradiction Sweep complete. Superseded {superseded_count} memories.")

async def run_fact_dedup_sweep(pool):
    """Finds near-duplicate facts (distance < 0.08) and merges them."""
    logger.info("Starting Fact Dedup Sweep...")
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.id as id1, b.id as id2, a.fact_text as text1, b.fact_text as text2,
                   a.project_id, a.source_chunk as chunk1, b.source_chunk as chunk2,
                   a.confidence as conf1, b.confidence as conf2
            FROM facts a
            JOIN facts b ON a.project_id = b.project_id AND a.id < b.id
            WHERE a.embedding <=> b.embedding < 0.08
        """)
        
        merged_count = 0
        deleted_ids = set()
        
        for row in rows:
            id1, id2 = row['id1'], row['id2']
            if id1 in deleted_ids or id2 in deleted_ids:
                continue
                
            conf = min(row['conf1'] + 0.1, 1.0)
            # Keep richer source chunk
            chunk = row['chunk1'] if len(row['chunk1'] or "") > len(row['chunk2'] or "") else row['chunk2']
            
            async with conn.transaction():
                await conn.execute("""
                    UPDATE facts SET confidence = $1, source_chunk = $2, updated_at = NOW()
                    WHERE id = $3
                """, conf, chunk, id1)
                
                # Transfer relationships from id2 to id1
                await conn.execute("UPDATE entity_relationships SET source_fact_id = $1 WHERE source_fact_id = $2", id1, id2)
                await conn.execute("DELETE FROM facts WHERE id = $1", id2)
                
            deleted_ids.add(id2)
            merged_count += 1
            
    logger.info(f"Fact Dedup Sweep complete. Merged {merged_count} duplicates.")

async def run_fact_contradiction_sweep(pool):
    """Finds semantic neighbor facts and uses LLM to check for contradictions."""
    logger.info("Starting Fact Contradiction Sweep...")
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.id as id1, b.id as id2, a.fact_text as text1, b.fact_text as text2,
                   a.project_id, a.created_at as created1, b.created_at as created2
            FROM facts a
            JOIN facts b ON a.project_id = b.project_id AND a.id < b.id
            WHERE a.embedding <=> b.embedding < 0.25
            LIMIT 50
        """)
        
        superseded_count = 0
        for row in rows:
            prompt = (
                "Do these two facts explicitly contradict each other? "
                "Answer ONLY 'YES' or 'NO'.\\n\\n"
                f"Fact A: {row['text1']}\\n"
                f"Fact B: {row['text2']}"
            )
            try:
                response = await run_extraction_llm(prompt, max_tokens=10)
                if "YES" in response.upper():
                    if row['created1'] > row['created2']:
                        older_id, newer_id = row['id2'], row['id1']
                    else:
                        older_id, newer_id = row['id1'], row['id2']
                        
                    async with conn.transaction():
                        await conn.execute("UPDATE facts SET confidence = 0.1 WHERE id = $1", older_id)
                    superseded_count += 1
            except Exception as e:
                logger.error(f"LLM fact contradiction check failed: {e}")
                
    logger.info(f"Fact Contradiction Sweep complete. Superseded {superseded_count} facts.")

async def main():
    pool = await get_pool()
    await run_dedup_sweep(pool)
    await run_fact_dedup_sweep(pool)
    await run_staleness_sweep(pool, days=30)
    
    if os.environ.get("EXTRACTION_ENABLED", "true").lower() == "true":
        await run_contradiction_sweep(pool)
        await run_fact_contradiction_sweep(pool)
    else:
        logger.info("Skipping Contradiction Sweeps (EXTRACTION_ENABLED=false)")
        
    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import os
from collections import defaultdict
from datetime import datetime
from server import get_pool, save_fact, save_entity_relationship, extract_structured_facts
from worker import STRUCTURED_FACT_PROMPT

async def backfill():
    print("Starting backfill of facts from existing memories (grouped by session)...")
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        # Group memories by session, ordered by timestamp
        memories = await conn.fetch("""
            SELECT id, project_id, content, metadata, timestamp,
                   COALESCE(metadata->>'session_key', 'unknown') AS session_key
            FROM memories
            WHERE scope IN ('private', 'shared')
            ORDER BY project_id, session_key, timestamp
        """)
    
    # Group by (project_id, session_key)
    sessions = defaultdict(list)
    for row in memories:
        key = (row['project_id'], row['session_key'])
        sessions[key].append(row)
        
    print(f"Found {len(sessions)} sessions to process (from {len(memories)} memories).")
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    for (project_id, session_key), rows in sessions.items():
        # Build full session transcript
        transcript = "\\n".join(row['content'] for row in rows)
        if len(transcript) < 50:
            continue
            
        session_date_str = today_str
        if rows[0]['timestamp']:
            session_date_str = rows[0]['timestamp'].strftime("%Y-%m-%d")
        elif rows[0]['metadata'] and rows[0]['metadata'].get('session_date'):
            session_date_str = rows[0]['metadata'].get('session_date')
            
        print(f"Processing session {session_key} for {project_id} ({len(rows)} messages)...")
        
        prompt = STRUCTURED_FACT_PROMPT.format(today=today_str, session_date=session_date_str, transcript=transcript)
        
        try:
            result = await extract_structured_facts(prompt, max_tokens=4096)
            
            count = 0
            for fact_obj in result.facts:
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
                    source_chunk=fact_obj.source_quote,
                    source_session_id=session_key,
                    source_memory_id=str(rows[0]['id']) # Associate with the first memory in the session
                )
                
                for ent in fact_obj.entities:
                    if ent.subject and ent.predicate and ent.object:
                        await save_entity_relationship(
                            project_id=project_id,
                            subject=ent.subject,
                            predicate=ent.predicate,
                            object_name=ent.object,
                            source_fact_id=fact_id
                        )
                count += 1
            print(f"  Extracted {count} facts.")
        except Exception as e:
            print(f"  Failed to extract facts for session {session_key}: {e}")

    print("Backfill complete.")

if __name__ == "__main__":
    asyncio.run(backfill())

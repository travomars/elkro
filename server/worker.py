import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from server import (
    get_pool,
    run_extraction_llm,
    extract_structured_facts,
    save_summary,
    save_fact,
    save_entity_relationship,
    save_entity_profile,
    queue_entity_consolidation,
    _format_transcript,
    EXTRACTION_MAX_RETRIES
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] Worker: %(message)s")
logger = logging.getLogger(__name__)

STRUCTURED_FACT_PROMPT = """
Extract factual memories from this conversation transcript. 

CRITICAL RULES:
1. DO NOT extract meta-conversational facts (e.g., "A conversation occurred", "User participated in session_7", "User asked a question").
2. DO NOT extract trivial pleasantries, greetings, or filler words.
3. ONLY extract substantive facts about the world, people, states, events, preferences, or relationships.
4. If a speaker is talking to an unnamed person, refer to the unnamed person as "User". DO NOT use terms like "addressee" or "unnamed person".
5. Resolve all pronouns (he/she/they/it) to the actual entity they refer to.
6. Return an empty JSON array `[]` if there are no substantive facts to extract.

For each substantive fact, provide:
{{
  "fact_text": "One-sentence statement of the fact",
  "fact_kind": "event|state|preference|relationship",
  "who": ["list of specific people/entities involved, using 'User' for the other person if unnamed"],
  "what": "the action or state being described",
  "when_description": "temporal reference as stated (e.g. 'last Friday')",
  "when_resolved": "absolute date if determinable (YYYY-MM-DD), else null",
  "where": "location if mentioned, else null",
  "why": "reason/cause if mentioned, else null",
  "source_quote": "the exact 1-3 sentence excerpt from the conversation that supports this fact",
  "entities": [
    {{"subject": "Person A", "predicate": "relationship_type", "object": "Person B"}}
  ]
}}

Today's date is {today}. The conversation session date is {session_date}.
Use the session date to resolve relative temporal references.

EXAMPLE INPUT CONVERSATION:
Melanie: I went to the zoo yesterday!
Caroline: Oh wow, what did you see?
Melanie: Mostly monkeys. But my dog Rex was waiting for me at home.

EXAMPLE OUTPUT:
{{
  "facts": [
    {{"fact_text": "Melanie went to the zoo on 2026-06-27.", "fact_kind": "event", "who": ["Melanie"], "what": "went to zoo", "when_description": "yesterday", "when_resolved": "2026-06-27", "where": "zoo", "why": null, "source_quote": "I went to the zoo yesterday!", "entities": []}},
    {{"fact_text": "Melanie owns a dog named Rex.", "fact_kind": "relationship", "who": ["Melanie", "Rex"], "what": "owns dog", "when_description": null, "when_resolved": null, "where": null, "why": null, "source_quote": "my dog Rex was waiting for me at home", "entities": [{{"subject": "Melanie", "predicate": "owns_pet", "object": "Rex"}}]}}
  ]
}}

CONVERSATION:
{transcript}

Return ONLY a JSON object containing a 'facts' key mapped to a list of fact objects. Extract 5-15 facts.
DO NOT output any "Thinking Process" or reasoning steps. Your ENTIRE response must be valid parseable JSON.
"""

async def process_task(conn, task):
    task_id = task['id']
    project_id = task['project_id']
    payload = json.loads(task['payload'])
    task_type = task['task_type']
    
    logger.info(f"Processing task {task_id} of type {task_type}")

    if task_type == 'session_summary':
        transcript = _format_transcript(payload)
        if len(transcript) < 100:
            return True, "Transcript too short"

        # Try to infer session date
        # Assuming payload has messages with metadata or we can infer it. For now, assume it's today if not specified.
        # Ideally, we should pass session_date in the payload wrapper, but let's just use today for the prompt.
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # In a real implementation, extract session_date from the first message's metadata if available.
        # For Elkro, the messages format from Hermes is just roles and contents. 
        session_date_str = today_str 
        
        # 1. Summary
        summary_prompt = (
            "Summarize this conversation in 2-4 sentences, focused on what the user "
            "was working on, decided, or asked about. Write in third person, past tense. "
            "Return ONLY the summary text, no preamble.\n\n"
            f"CONVERSATION:\n{transcript}"
        )
        summary_text = await run_extraction_llm(summary_prompt, max_tokens=256)
        if summary_text:
            await save_summary(content=summary_text, project_id=project_id)

        # 2. Structured Facts
        facts_prompt = STRUCTURED_FACT_PROMPT.format(today=today_str, session_date=session_date_str, transcript=transcript)
        result = await extract_structured_facts(facts_prompt, max_tokens=2048)
        
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
            if entity.lower() not in ('user', 'unknown', 'someone'):
                await queue_entity_consolidation(project_id, entity)

        return True, "Session summary and structured facts extracted"

    elif task_type == 'rescue_compress':
        transcript = _format_transcript(payload, char_limit=6000)
        if len(transcript) < 100:
            return True, "Transcript too short"

        prompt = (
            "These messages are about to be discarded from context. Extract any "
            "durable facts about the user worth keeping (preferences, decisions, "
            "technical details, project context). Ignore tool noise and small talk.\n"
            "Return ONLY a JSON array of strings, no explanation, no markdown. "
            "Return [] if nothing is worth keeping.\n\n"
            f"MESSAGES:\n{transcript}"
        )
        facts_raw = await run_extraction_llm(prompt, max_tokens=512)
        facts = _clean_json_array(facts_raw)
        for fact in facts:
            if isinstance(fact, str) and len(fact) > 10:
                await save_memory(content=fact, project_id=project_id, scope="private")
                
        return True, "Pre-compress facts extracted"
        
    elif task_type == 'entity_consolidation':
        entity_name = payload.get("entity_name")
        if not entity_name:
            return False, "Missing entity_name in payload"
            
        # Fetch all facts related to this entity
        rows = await conn.fetch(
            """
            SELECT fact_text, fact_kind, who, when_start, source_chunk
            FROM facts
            WHERE project_id = $1 AND ($2 = ANY(who))
            ORDER BY created_at ASC
            """,
            project_id, entity_name
        )
        
        # Also fetch relationships
        rels = await conn.fetch(
            """
            SELECT subject, predicate, object
            FROM entity_relationships
            WHERE project_id = $1 AND (subject = $2 OR object = $2)
            """,
            project_id, entity_name
        )
        
        if not rows and not rels:
            return True, f"No facts found for entity {entity_name}"
            
        context_lines = []
        for r in rows:
            when = r['when_start'].strftime('%Y-%m-%d') if r['when_start'] else 'unknown'
            context_lines.append(f"[{when}] {r['fact_kind'].upper()}: {r['fact_text']}")
        for r in rels:
            context_lines.append(f"RELATIONSHIP: {r['subject']} {r['predicate']} {r['object']}")
            
        context_str = "\n".join(context_lines)
        
        prompt = (
            f"You are a memory consolidation agent. Below is a list of facts and relationships about '{entity_name}'.\n"
            "Your task is to synthesize this information into a cohesive, highly structured profile for this entity.\n"
            "Resolve any contradictions (prefer newer facts if timestamps exist) and group information logically (e.g., Preferences, Relationships, Recent Events).\n"
            "Format the output as clean Markdown text.\n\n"
            f"FACTS ABOUT {entity_name}:\n{context_str}\n\n"
            "Write the comprehensive entity profile now. Return ONLY the markdown profile, no preamble."
        )
        
        profile_text = await run_extraction_llm(prompt, max_tokens=2048)
        if profile_text:
            await save_entity_profile(project_id, entity_name, profile_text)
            
        return True, f"Entity profile for {entity_name} consolidated and saved"
        
    else:
        return False, f"Unknown task type: {task_type}"

async def poll_queue(batch_mode=False):
    pool = await get_pool()
    logger.info(f"Worker started. Batch mode: {batch_mode}")
    
    while True:
        async with pool.acquire() as conn:
            # Claim a pending task
            # We use SKIP LOCKED to allow multiple workers without race conditions
            task = await conn.fetchrow(
                """
                UPDATE extraction_queue
                SET status = 'processing', attempts = attempts + 1
                WHERE id = (
                    SELECT id FROM extraction_queue
                    WHERE status IN ('pending', 'failed') AND attempts < $1
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, project_id, payload::text, task_type, attempts
                """,
                EXTRACTION_MAX_RETRIES
            )

            if not task:
                if batch_mode:
                    logger.info("No more tasks. Batch processing complete.")
                    break
                await asyncio.sleep(10)
                continue

            # Process the task
            try:
                success, msg = await process_task(conn, task)
                if success:
                    await conn.execute(
                        "UPDATE extraction_queue SET status = 'done', processed_at = NOW(), last_error = NULL WHERE id = $1",
                        task['id']
                    )
                    logger.info(f"Task {task['id']} completed: {msg}")
                else:
                    raise Exception(msg)
            except Exception as e:
                logger.error(f"Task {task['id']} failed (Attempt {task['attempts']}/{EXTRACTION_MAX_RETRIES}): {e}")
                # We mark it as failed so it can be retried, unless it hit max retries.
                # The query above only picks up 'failed' tasks if attempts < max.
                await conn.execute(
                    "UPDATE extraction_queue SET status = 'failed', last_error = $2 WHERE id = $1",
                    task['id'], str(e)
                )

if __name__ == "__main__":
    batch_mode = "--batch" in sys.argv
    try:
        asyncio.run(poll_queue(batch_mode=batch_mode))
    except KeyboardInterrupt:
        logger.info("Worker stopped.")

import pytest
import asyncio
from datetime import date
# test_dedup.py will test the dedup logic which we haven't written yet.
# It expects a real database connection.
from server import save_fact, get_pool
from models import ExtractedFact

import server

@pytest.fixture
async def db_pool():
    # Setup test DB or truncate facts
    pool = await server.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE facts CASCADE;")
        await conn.execute("TRUNCATE entity_relationships CASCADE;")
    yield pool
    # Teardown
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE facts CASCADE;")
    await pool.close()
    server._pool = None

@pytest.mark.asyncio
@pytest.mark.integration
async def test_exact_duplicate_is_rejected(db_pool):
    fact = ExtractedFact(
        fact_text="Caroline went hiking",
        fact_kind="event",
        who=["Caroline"]
    )
    # Insert once
    id1 = await save_fact("project_test", fact.fact_text, fact.fact_kind, fact.who, source_chunk="source_1", source_session_id="session_1")
    
    # Insert exact duplicate
    id2 = await save_fact("project_test", fact.fact_text, fact.fact_kind, fact.who, source_chunk="source_2", source_session_id="session_1")
    
    # Should be the same ID, or at least only 1 row in DB
    assert id1 == id2
    
    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM facts WHERE project_id = 'project_test'")
        assert count == 1
        
        # Confidence should be incremented, capped at 1.0
        confidence = await conn.fetchval("SELECT confidence FROM facts WHERE id = $1", id1)
        assert confidence == 1.0

@pytest.mark.asyncio
@pytest.mark.integration
async def test_near_duplicate_is_merged(db_pool):
    fact1 = ExtractedFact(fact_text="Caroline went hiking", fact_kind="event", who=["Caroline"])
    fact2 = ExtractedFact(fact_text="Caroline went hiking.", fact_kind="event", who=["Caroline"])
    
    id1 = await save_fact("project_test", fact1.fact_text, fact1.fact_kind, fact1.who, source_chunk="source_1", source_session_id="session_1")
    id2 = await save_fact("project_test", fact2.fact_text, fact2.fact_kind, fact2.who, source_chunk="source_2", source_session_id="session_2")
    
    assert id1 == id2
    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM facts WHERE project_id = 'project_test'")
        assert count == 1

@pytest.mark.asyncio
@pytest.mark.integration
async def test_different_facts_are_kept(db_pool):
    fact1 = ExtractedFact(fact_text="Caroline went hiking", fact_kind="event", who=["Caroline"])
    fact2 = ExtractedFact(fact_text="Melanie went camping", fact_kind="event", who=["Melanie"])
    
    id1 = await save_fact("project_test", fact1.fact_text, fact1.fact_kind, fact1.who, source_chunk="source_1", source_session_id="session_1")
    id2 = await save_fact("project_test", fact2.fact_text, fact2.fact_kind, fact2.who, source_chunk="source_2", source_session_id="session_2")
    
    assert id1 != id2
    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM facts WHERE project_id = 'project_test'")
        assert count == 2

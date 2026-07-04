import pytest
import asyncio
from datetime import date
from conftest import SAMPLE_SESSION_TRANSCRIPT
from server import extract_structured_facts, save_fact, search_facts, get_pool
# Assuming rest_recall will be tested if we set up a FastAPI TestClient

import server

@pytest.fixture
async def db_pool():
    pool = await server.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE facts CASCADE;")
        await conn.execute("TRUNCATE entity_relationships CASCADE;")
    yield pool
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE facts CASCADE;")
    await pool.close()
    server._pool = None

from mock_llm import mock_setup
from worker import STRUCTURED_FACT_PROMPT

@pytest.fixture
def mock_llm(monkeypatch):
    mock_setup(monkeypatch)

@pytest.mark.asyncio
@pytest.mark.e2e
async def test_e2e_extract_and_retrieve(db_pool, mock_llm):
    prompt = STRUCTURED_FACT_PROMPT.format(
        today=date.today().strftime("%Y-%m-%d"),
        session_date="2026-06-28",
        transcript=SAMPLE_SESSION_TRANSCRIPT
    )
    result = await extract_structured_facts(prompt)
    
    assert len(result.facts) == 3
    
    # Save facts and relationships
    for fact in result.facts:
        fact_id = await save_fact(
            "project_e2e", fact.fact_text, fact.fact_kind, fact.who, 
            when_start=fact.when_resolved, source_chunk="source_quote", source_session_id="session_test"
        )
        for ent in fact.entities:
            if ent.subject and ent.predicate and ent.object:
                await server.save_entity_relationship(
                    project_id="project_e2e",
                    subject=ent.subject,
                    predicate=ent.predicate,
                    object_name=ent.object,
                    source_fact_id=fact_id
                )
        
    # Give DB a moment to index if needed (though pgvector is sync)
    await asyncio.sleep(0.5)
    
    # 1. Retrieve Facts
    hiking_results = await search_facts("hiking", "project_e2e", limit=5)
    assert "hiking" in hiking_results.lower()
    
    # 2. Retrieve Relationships
    rel_results = await server.search_relationships("project_e2e", "Caroline")
    assert "Caroline roommate Emily" in rel_results or "Caroline" in rel_results
    
    # 3. Test retrieving with temporal info (if implemented later)
    # This just ensures we don't crash
    unrelated_results = await search_facts("quantum physics", "project_e2e", limit=1)
    assert isinstance(unrelated_results, str)

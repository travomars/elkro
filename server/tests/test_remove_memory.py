import pytest
import asyncio
from server import save_memory, remove_memory, get_pool
from datetime import date
import server

@pytest.fixture
async def memory_db_pool():
    pool = await server.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE memories CASCADE;")
    yield pool
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE memories CASCADE;")
    await pool.close()
    server._pool = None

from mock_llm import mock_setup

@pytest.fixture
def mock_llm(monkeypatch):
    mock_setup(monkeypatch)

@pytest.mark.asyncio
async def test_remove_memory_exact(memory_db_pool, mock_llm):
    project_id = "test_project"
    
    # Add two memories
    mem1 = await save_memory("User likes cats", project_id, scope="private")
    mem2 = await save_memory("User lives in New York", project_id, scope="private")
    
    # Allow DB indexing just in case
    await asyncio.sleep(0.1)
    
    # Remove one memory
    result = await remove_memory("User likes cats", project_id)
    assert "Memory removed: DELETE 1" in result
    
    # Verify it is removed
    pool = await server.get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE project_id = $1", project_id)
        assert count == 1
        
        remaining = await conn.fetchval("SELECT content FROM memories WHERE project_id = $1", project_id)
        assert "User lives in New York" in remaining

@pytest.mark.asyncio
async def test_remove_memory_semantic(memory_db_pool, mock_llm):
    project_id = "test_project_sem"
    
    # Add a memory
    mem1 = await save_memory("Jordan loves hiking and camping in the mountains.", project_id, scope="private")
    
    # Allow DB indexing
    await asyncio.sleep(0.1)
    
    # Remove with exact text which is what hermes passes in old_text
    result = await remove_memory("Jordan loves hiking and camping in the mountains.", project_id)
    assert "Memory removed: DELETE 1" in result
    
    pool = await server.get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE project_id = $1", project_id)
        assert count == 0

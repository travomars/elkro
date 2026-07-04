import pytest
import pytest_asyncio
import asyncpg
import asyncio
import os
from unittest.mock import patch
import asyncpg
import asyncio
import os

# Use a specific test database, or just the default one. For simplicity in this suite,
# we'll use the main one but generate unique project_ids for isolation.
DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/elkro")

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest_asyncio.fixture
async def db_pool():
    pool = await asyncpg.create_pool(DB_URL)
    yield pool
    await pool.close()

@pytest_asyncio.fixture(autouse=True)
def mock_llm_services(db_pool):
    """Mocks out the LLM, Embedding, and get_pool services for fast, deterministic unit testing."""
    async def fake_embedding(text: str):
        return [0.0] * 768

    async def fake_extraction_llm(prompt: str, **kwargs):
        if "memory consolidation agent" in prompt:
            return "# Project Alpha\nThe deadline is September 1st."
        return "Dummy extraction result."
        
    with patch('server.get_embedding', side_effect=fake_embedding), \
         patch('worker.run_extraction_llm', side_effect=fake_extraction_llm):
        yield

@pytest_asyncio.fixture
async def conn(db_pool):
    """Provides a database connection for tests."""
    async with db_pool.acquire() as connection:
        yield connection

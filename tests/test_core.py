import pytest
from server import save_memory, search_memories_scoped, set_fact, get_fact, save_fact
from worker import process_task
import json
import uuid
from datetime import datetime, timezone

@pytest.mark.asyncio
async def test_scope_isolation(conn):
    """
    The Scope Isolation Test (Privacy):
    Create a highly specific private memory. Then, simulate a query from 
    another user's account asking for that fact. Assert it fails to answer.
    """
    private_travis = f"private_travis_{uuid.uuid4()}"
    private_annika = f"private_annika_{uuid.uuid4()}"
    shared_global = f"shared_global_{uuid.uuid4()}"

    await save_memory("Travis's favorite color is crimson red", private_travis)
    
    # Query from Annika's context
    result = await search_memories_scoped(
        "What is Travis's favorite color?", 
        private_project_id=private_annika, 
        shared_project_id=shared_global, 
        limit=5
    )
    
    assert "crimson red" not in result.lower()
    assert "No relevant matching" in result


@pytest.mark.asyncio
async def test_override_niah(conn):
    """
    The Override Test (NIAH):
    Bury a specific, immutable rule in Tier 3 atomic facts (Max_Budget = 50000).
    Fill semantic memories with fuzzy, contradictory conversational data.
    Elkro bypasses semantic vector math and scores a flawless exact-match retrieval.
    """
    project_id = f"test_override_{uuid.uuid4()}"
    shared_id = f"test_override_shared_{uuid.uuid4()}"

    # Inject fuzzy noise into semantic memories
    for i in range(10):
        await save_memory(f"I think the budget was around {10000 + i*1000} maybe?", project_id)
        await save_memory(f"Someone said the budget is {60000 - i*500} next year.", project_id)
        
    # Inject exact immutable rule
    await set_fact(project_id, "Max_Budget", "50000")
    
    # In Hermes, exact facts are retrieved via `get_fact` tool or direct lookup
    result = await get_fact(private_project_id=project_id, shared_project_id=shared_id, key="Max_Budget")
    
    # It should bypass all the vector noise and exactly return 50000
    assert "50000" in result
    assert "FACT FOUND" in result


@pytest.mark.asyncio
async def test_state_mutation_consolidation(conn):
    """
    The State Mutation Test (Consolidation):
    Simulate a user changing their mind 5 times over a month about a deadline.
    Verify the background worker successfully overwrites the Entity Profile to
    reflect only the final state.
    """
    project_id = f"test_mutation_{uuid.uuid4()}"
    entity_name = "Project Alpha"
    
    # Inject sequential facts with advancing timestamps
    dates = ["2026-06-01", "2026-06-05", "2026-06-12", "2026-06-20", "2026-06-28"]
    deadlines = ["July 1st", "July 15th", "August 1st", "August 15th", "September 1st"]
    
    for i in range(5):
        # We explicitly set sequential timestamps (when_start) so consolidation knows timeline
        dt = datetime.strptime(dates[i], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        await save_fact(
            project_id=project_id,
            fact_text=f"The deadline for Project Alpha is {deadlines[i]}.",
            fact_kind="state",
            who=[entity_name],
            when_start=dt
        )

    # Manually create the task payload
    task = {
        "id": uuid.uuid4(),
        "project_id": project_id,
        "payload": json.dumps({"entity_name": entity_name}),
        "task_type": "entity_consolidation"
    }
    
    # Trigger the consolidation worker synchronously for the test
    success, msg = await process_task(conn, task)
    assert success is True
    
    # Check the database for the consolidated Entity Profile
    row = await conn.fetchrow(
        "SELECT summary FROM entity_profiles WHERE project_id = $1 AND entity_name = $2",
        project_id, entity_name
    )
    
    assert row is not None
    summary = row["summary"].lower()
    
    # The profile should reflect the final state (September 1st) and ideally NOT the oldest one
    assert "september" in summary or "sep" in summary
    assert "july 1st" not in summary # Oldest state should be resolved away

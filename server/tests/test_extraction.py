import pytest
import asyncio
from conftest import MALFORMED_JSON_RESPONSES, POISON_DATE_STRINGS, SAMPLE_SINGLE_TURN, SAMPLE_SESSION_TRANSCRIPT
from models import ExtractionResult
from server import extract_structured_facts

@pytest.mark.asyncio
@pytest.mark.integration
async def test_malformed_json_does_not_crash():
    # Because extract_structured_facts calls the LLM, we should mock the httpx client or the llm call itself
    # For now, let's assume we test a helper function `parse_extraction_result` that takes raw strings.
    # Or we can patch the LLM call.
    for bad_json in MALFORMED_JSON_RESPONSES:
        result = ExtractionResult.parse_raw_json(bad_json)
        assert isinstance(result, ExtractionResult)
        # Should not raise an exception

@pytest.mark.asyncio
@pytest.mark.integration
async def test_poison_dates_do_not_hang():
    # If dateparser is removed, there are no poison dates hanging.
    # But if it's there, this will hang.
    # We test the model instantiation directly here since it handles dates now.
    from models import ExtractedFact
    
    for bad_date in POISON_DATE_STRINGS:
        raw = {
            "fact_text": "A fact",
            "fact_kind": "event",
            "when_resolved": bad_date
        }
        fact = ExtractedFact.model_validate(raw)
        # It should complete instantly and either raise ValidationError or set to None
        assert fact.when_resolved is None

from worker import STRUCTURED_FACT_PROMPT
from datetime import date

@pytest.mark.asyncio
@pytest.mark.e2e
async def test_session_transcript_extracts_real_facts():
    # This requires the real LLM endpoint
    prompt = STRUCTURED_FACT_PROMPT.format(
        today=date.today().strftime("%Y-%m-%d"),
        session_date="2026-06-28",
        transcript=SAMPLE_SESSION_TRANSCRIPT
    )
    result = await extract_structured_facts(prompt)
    
    facts = [f.fact_text.lower() for f in result.facts]
    
    # Assert substantive facts exist
    assert any("hiking" in f for f in facts)
    assert any("rocky mountains" in f for f in facts)
    assert any("birthday" in f for f in facts)
    assert any("camping" in f for f in facts)
    
    # Assert no junk
    for f in result.facts:
        text = f.fact_text.lower()
        assert "addressee" not in text
        assert "unnamed person" not in text
        assert "a conversation occurred" not in text
        assert bool(f.source_quote), "Missing source quote"

@pytest.mark.asyncio
@pytest.mark.e2e
async def test_who_uses_real_names():
    prompt = f"Extract facts from:\n{SAMPLE_SESSION_TRANSCRIPT}"
    result = await extract_structured_facts(prompt)
    
    for f in result.facts:
        for person in f.who:
            assert person.lower() not in ["addressee", "unnamed person", "speaker"]

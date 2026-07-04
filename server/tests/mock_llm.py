import pytest
import server
import json

MOCK_EXTRACTION_JSON = """```json
{
  "facts": [
    {
      "fact_text": "Caroline went hiking in the Rocky Mountains.",
      "fact_kind": "event",
      "who": ["Caroline"],
      "what": "went hiking",
      "when_description": "Last Friday",
      "when_resolved": "2026-06-20",
      "where": "Rocky Mountains",
      "why": "to celebrate Emily's birthday",
      "source_quote": "I went hiking in the Rocky Mountains with my roommate Emily. Last Friday.",
      "entities": []
    },
    {
      "fact_text": "Caroline went hiking with Emily.",
      "fact_kind": "event",
      "who": ["Caroline", "Emily"],
      "what": "went hiking together",
      "when_description": "Last Friday",
      "when_resolved": "2026-06-20",
      "where": "Rocky Mountains",
      "why": "to celebrate Emily's birthday",
      "source_quote": "I went hiking in the Rocky Mountains with my roommate Emily.",
      "entities": [
        {
          "subject": "Caroline",
          "predicate": "roommate",
          "object": "Emily"
        }
      ]
    },
    {
      "fact_text": "Melanie owns a cat.",
      "fact_kind": "relationship",
      "who": ["Melanie"],
      "what": "owns cat",
      "when_description": null,
      "when_resolved": null,
      "where": null,
      "why": null,
      "source_quote": "My cat was sleeping.",
      "entities": [
        {
          "subject": "Melanie",
          "predicate": "owns",
          "object": "cat"
        }
      ]
    }
  ]
}
```"""

def mock_setup(monkeypatch):
    """Mocks out the LLM calls in the server for testing."""
    async def fake_run_extraction_llm(prompt: str, max_tokens: int = 4096, assistant_prefill: str = None) -> str:
        if assistant_prefill == "```json\n{\n  \"facts\": [\n":
            return MOCK_EXTRACTION_JSON
        elif assistant_prefill == "Answer: ":
            return "Answer: Mocked concise answer."
        return "Mocked response"
        
    monkeypatch.setattr(server, "run_extraction_llm", fake_run_extraction_llm)
    
    # Optionally mock embeddings if we want fully offline tests
    # async def fake_get_embedding(text: str):
    #     return [0.1] * 768
    # monkeypatch.setattr(server, "get_embedding", fake_get_embedding)

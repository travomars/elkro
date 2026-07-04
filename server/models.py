import json
import logging
from pydantic import BaseModel, Field, ValidationError, field_validator
from typing import Optional, Any
from datetime import date

logger = logging.getLogger(__name__)

class EntityRelation(BaseModel):
    subject: str
    predicate: str
    object: str

class ExtractedFact(BaseModel):
    fact_text: str = Field(description="One-sentence statement of the fact")
    fact_kind: str = Field(description="event|state|preference|relationship")
    who: list[str] = Field(default_factory=list, description="People/entities involved, using 'User' for unnamed")
    what: Optional[str] = None
    when_description: Optional[str] = Field(default=None, description="Temporal reference as stated, e.g. 'last Friday'")
    when_resolved: Optional[date] = Field(default=None, description="Absolute date if determinable, YYYY-MM-DD")
    where: Optional[str] = None
    why: Optional[str] = None
    source_quote: Optional[str] = Field(default=None, description="1-3 sentence excerpt supporting this fact")
    entities: list[EntityRelation] = Field(default_factory=list)

    @field_validator('when_resolved', mode='before')
    @classmethod
    def validate_when_resolved(cls, v: Any) -> Optional[date]:
        if not v:
            return None
        if isinstance(v, date):
            return v
        try:
            return date.fromisoformat(str(v).strip())
        except (ValueError, TypeError):
            # If the LLM generates a poison string like "during session_7", gracefully return None
            return None

    @field_validator('who', mode='before')
    @classmethod
    def validate_who(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return [str(x) for x in v]
        return []

class ExtractionResult(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)

    @classmethod
    def parse_raw_json(cls, raw: str) -> "ExtractionResult":
        """Helper to parse potentially malformed JSON from the LLM."""
        try:
            return cls.model_validate_json(raw)
        except ValidationError:
            # Try to parse as bare array
            try:
                # Cleanup markdown blocks if any
                cleaned = raw.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned.split("```json", 1)[1]
                if cleaned.endswith("```"):
                    cleaned = cleaned.rsplit("```", 1)[0]
                cleaned = cleaned.strip()
                
                data = json.loads(cleaned)
                if isinstance(data, list):
                    facts = []
                    for item in data:
                        if isinstance(item, dict):
                            try:
                                facts.append(ExtractedFact.model_validate(item))
                            except ValidationError:
                                pass
                    return cls(facts=facts)
            except Exception:
                pass
            
            # Improved JSON parsing fallback
            cleaned = raw.strip()
            # Remove duplicated prefills if present
            while "```json\n{\n  \"facts\": [\n```json\n{\n" in cleaned:
                cleaned = cleaned.replace("```json\n{\n  \"facts\": [\n```json\n{\n", "```json\n{\n")
                
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            try:
                data = json.loads(cleaned)
                if isinstance(data, dict) and "facts" in data:
                    data = data["facts"]
                if isinstance(data, list):
                    facts = []
                    for item in data:
                        if isinstance(item, dict):
                            try:
                                facts.append(ExtractedFact.model_validate(item))
                            except ValidationError:
                                pass
                    if facts:
                        return cls(facts=facts)
            except Exception:
                # If it still fails, try finding the first [ and last ]
                start = cleaned.find('[')
                end = cleaned.rfind(']')
                if start != -1 and end != -1 and start < end:
                    try:
                        data = json.loads(cleaned[start:end+1])
                        if isinstance(data, list):
                            facts = []
                            for item in data:
                                if isinstance(item, dict):
                                    try:
                                        facts.append(ExtractedFact.model_validate(item))
                                    except ValidationError:
                                        pass
                            if facts:
                                return cls(facts=facts)
                    except Exception:
                        pass
            
            logger.warning(f"Failed to parse extraction response:\n{raw}")
            return cls(facts=[])

import pytest

# Sample transcripts that represent real LoCoMo data at different granularities
SAMPLE_SINGLE_TURN = '[session_7] Caroline: That\'s so nice! What pet do you have?'

SAMPLE_SESSION_TRANSCRIPT = """[session_3] Melanie: Hey Caroline! How was your weekend?
[session_3] Caroline: It was great! I went hiking in the Rocky Mountains with my roommate Emily.
[session_3] Melanie: Oh nice! When was that?
[session_3] Caroline: Last Friday. We wanted to celebrate her birthday.
[session_3] Melanie: That sounds amazing. I went camping with my family two weekends ago.
[session_3] Caroline: Oh fun! Where did you go?
[session_3] Melanie: We went to Yellowstone. The kids loved it."""

SAMPLE_MULTI_SESSION = """[session_1] Caroline: I just adopted a golden retriever puppy! His name is Max.
[session_1] Melanie: That's adorable! How old is he?
[session_1] Caroline: He's 3 months old.
---
[session_5] Melanie: How's Max doing?
[session_5] Caroline: He's getting so big! He's 6 months now. We're starting obedience classes next week.
[session_5] Melanie: Oh that's great. My cat would never."""

# Known-bad inputs that have crashed the pipeline before
MALFORMED_JSON_RESPONSES = [
    '```json\n[{"fact_text": "truncated response...',       # Truncated mid-object
    '[{"fact_text": "missing bracket"',                     # Missing closing bracket
    'Here are the facts:\n[{"fact_text": "preamble"}]',    # LLM preamble before JSON
    '[]',                                                   # Empty array (valid, should not crash)
    '{"facts": []}',                                        # Wrapped object (valid alternate format)
    'I could not extract any facts from this conversation.', # Refusal (not JSON at all)
]

# Dates that caused dateparser to freeze (ReDoS)
POISON_DATE_STRINGS = [
    "during session_7 on 2026-06-28",
    "on the session date",
    "during the conversation on session date",
    "2026-06-28T00:00:00.000000+00:00 approximately",
    "",
    None,
]

Problem:
Docker container cannot connect to PostgreSQL

Solution:
Bridge network misconfiguration.
Fixed by connecting containers to same compose network.

Confidence: high
Last verified: 2026-06-15

experiences
-----------
problem TEXT
solution TEXT
context TEXT
embedding VECTOR
success BOOLEAN
last_used TIMESTAMPTZ
usage_count INTEGER


What I would not build yet

Honestly:

❌ Full relationship graph

❌ Complex ontology

❌ Advanced belief system

❌ Memory versioning on every table

❌ ACL inheritance trees

chatgpt: 
facts
memories
summaries
experiences
identities
permissions


Epistemic Layer (Beliefs & Opinions)
Unlike atomic facts (e.g., location: Berlin), beliefs are subjective, fluctuate over time, and can conflict between the user and the agent. They require a tracking mechanism for confidence and ownership.

CREATE TABLE IF NOT EXISTS beliefs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL,
    holder_type TEXT NOT NULL, -- 'user' or 'agent'
    topic TEXT NOT NULL,       -- E.g., 'Artificial General Intelligence'
    content TEXT NOT NULL,     -- E.g., 'Believes true AGI will emerge by 2027'
    confidence FLOAT NOT NULL DEFAULT 1.0, -- Scale of 0.0 to 1.0
    embedding vector(768),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_beliefs_project_holder ON beliefs (project_id, holder_type);


Episodic Layer (Experiences)
An experience is more than a summary; it is a high-fidelity record of a notable milestone or continuous arc within a session. It should capture emotional valence, execution success, and subjective impact.

CREATE TABLE IF NOT EXISTS experiences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    origin_type TEXT NOT NULL, -- 'user' (something they did) or 'agent' (task execution)
    narrative TEXT NOT NULL,    -- Detailed retrospective story of the interaction
    impact_score INT DEFAULT 5, -- Scale 1-10 (How critical was this milestone?)
    metadata JSONB,            -- Task outcomes, emotional tone, failure/success states
    embedding vector(768),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_experiences_project ON experiences (project_id);

Local/Homelab Inference: If you are running compute nodes locally (e.g., as part of a Dockerized setup), models like Llama-3-8B-Instruct or Gemma-2-9B are perfect. They are fast, cheap, and can be heavily prompted to output strict JSON schemas or RDF triples without being chatty.

API Inference: Claude 3.5 Haiku or GPT-4o-mini excel at this. They are heavily optimized for instruction following and classification.

Atomic Facts (The "What"): These are objective, mutable states. Think of them as a strict key-value store.

Examples: OS: Arch Linux, Primary_Shell: fish, Location: Leipzig.

Rule: Facts get overwritten when the state changes. You don't want the agent guessing your default shell based on 10 different vector chunks; you want it to look up the single source of truth.

Semantic Memories (The "Context"): These are conversational snippets that provide color, nuance, and history. They are immutable and stored as embeddings.
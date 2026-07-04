CREATE EXTENSION IF NOT EXISTS vector;

-- Tier 1: Semantic Memories
CREATE TABLE IF NOT EXISTS memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768),
    metadata JSONB,
    scope TEXT NOT NULL DEFAULT 'private',
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    confidence FLOAT DEFAULT 1.0,
    access_count INT DEFAULT 0,
    last_accessed TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_memories_project_scope ON memories (project_id, scope);
CREATE INDEX IF NOT EXISTS idx_memories_hnsw ON memories USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_memories_fts ON memories USING gin(tsv);

-- Phase 5: Audit Log
CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL,
    memory_id UUID,
    action TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Phase 2: Async Extraction Queue
CREATE TABLE IF NOT EXISTS extraction_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    attempts INT DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

-- Tier 1 Verification Buffer
CREATE TABLE IF NOT EXISTS share_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id UUID REFERENCES memories(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    target_project_id TEXT NOT NULL,
    content TEXT NOT NULL,
    proposed_at TIMESTAMPTZ DEFAULT NOW(),
    status TEXT DEFAULT 'pending',
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_share_requests_status ON share_requests (status);

-- Tier 2: Summaries (FIXED: Added missing table)

CREATE TABLE IF NOT EXISTS summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_summaries_project ON summaries (project_id);
CREATE INDEX IF NOT EXISTS idx_summaries_hnsw ON summaries USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- Tier 3: Atomic Facts (Legacy)
ALTER TABLE IF EXISTS facts RENAME TO legacy_facts;
ALTER INDEX IF EXISTS facts_pkey RENAME TO legacy_facts_pkey;
ALTER INDEX IF EXISTS facts_project_id_key_key RENAME TO legacy_facts_project_id_key_key;

-- Structured atomic facts with provenance
CREATE TABLE IF NOT EXISTS facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL,

    -- Core fact content
    fact_text TEXT NOT NULL,            -- "Caroline went hiking in the mountains"
    fact_kind TEXT NOT NULL DEFAULT 'state',  -- event | state | conversation | preference | relationship
    
    -- 5W structured fields
    who TEXT[],                         -- ["Caroline", "Emily (Caroline's roommate)"]
    what TEXT,                          -- "went hiking"
    when_description TEXT,              -- "last Friday" (original text)
    when_start TIMESTAMPTZ,            -- 2023-07-07 (resolved absolute date)
    when_end TIMESTAMPTZ,              -- NULL for point events
    where_location TEXT,               -- "Rocky Mountains"
    why TEXT,                           -- "to celebrate her birthday"

    -- Provenance & source
    source_chunk TEXT,                  -- Raw conversation snippet the fact was extracted from
    source_session_id TEXT,            -- Session key (e.g., "session_3")
    source_session_date TIMESTAMPTZ,   -- When the conversation happened
    source_memory_id UUID REFERENCES memories(id) ON DELETE SET NULL,

    -- Quality & lifecycle
    confidence FLOAT DEFAULT 1.0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT,                   -- "extraction_worker" | "consolidation" | "user_correction"
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Embedding for semantic search over facts
    embedding vector(768)
);

CREATE INDEX IF NOT EXISTS idx_facts_project ON facts (project_id);
CREATE INDEX IF NOT EXISTS idx_facts_kind ON facts (project_id, fact_kind);
CREATE INDEX IF NOT EXISTS idx_facts_temporal ON facts (project_id, when_start, when_end);
CREATE INDEX IF NOT EXISTS idx_facts_hnsw ON facts USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_facts_who ON facts USING gin (who);

-- Entity relationships extracted from facts
CREATE TABLE IF NOT EXISTS entity_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL,
    subject TEXT NOT NULL,             -- "Caroline"
    predicate TEXT NOT NULL,           -- "is_roommate_of"
    object TEXT NOT NULL,              -- "Emily"
    source_fact_id UUID REFERENCES facts(id) ON DELETE CASCADE,
    confidence FLOAT DEFAULT 1.0,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,             -- NULL = still current
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rel_subject ON entity_relationships (project_id, subject);
CREATE INDEX IF NOT EXISTS idx_rel_object ON entity_relationships (project_id, object);

-- Identity Resolution Mapping
CREATE TABLE IF NOT EXISTS identities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    platform_id TEXT NOT NULL,
    UNIQUE(platform, platform_id)
);
CREATE INDEX IF NOT EXISTS idx_identities_lookup ON identities (platform, platform_id);


--- Decaying beliefs/opinions if not reinforced


--- Relationships? UUID, Subject, predicate, Object, confidence?

--- agent_experiences // knowledge_entries

--- ACLs?

---{
 -- "created_by": "agent",
  --"source": "telegram",
  --"session": "abc123"
--}

--Version memories
-- Tier 1a: Atomic Entity Profiles (Consolidated state)
CREATE TABLE IF NOT EXISTS entity_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    summary TEXT NOT NULL,
    embedding vector(768),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, entity_name)
);
CREATE INDEX IF NOT EXISTS idx_entity_profiles_project ON entity_profiles (project_id);
CREATE INDEX IF NOT EXISTS idx_entity_profiles_hnsw ON entity_profiles USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE EXTENSION IF NOT EXISTS vector;

-- Tier 1: Semantic Memories
CREATE TABLE IF NOT EXISTS memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768),
    metadata JSONB,
    scope TEXT NOT NULL DEFAULT 'private',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_memories_project_scope ON memories (project_id, scope);

-- Tier 1 Verification Buffer
CREATE TABLE IF NOT EXISTS share_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id UUID REFERENCES memories(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    target_project_id TEXT NOT NULL,
    content TEXT NOT NULL,
    proposed_at TIMESTAMPTZ DEFAULT NOW(),
    status TEXT DEFAULT 'pending'
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

-- Tier 3: Atomic Facts
CREATE TABLE IF NOT EXISTS facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, key)

    --source_memory_id UUID,
    --confidence FLOAT,
    --updated_by TEXT
);

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
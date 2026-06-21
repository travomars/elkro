# Elkro Roadmap

> **Extensible Local Knowledge Routing Overlay**
> Last updated: 2026-06-21

---

## Where Elkro stands today

Elkro is a working, deployed multi-tenant memory layer with a genuinely differentiated scope model (private/shared/pending_share) that none of the top-of-leaderboard systems (agentmemory, Mem0, Letta, Cognee) have natively. The core plumbing — pgvector-backed 3-tier store, MCP+REST dual interface, Hermes plugin with lifecycle hooks — is functional and already serving real conversations.

The first LoCoMo pilot (conversation 0, 199 questions) surfaced exactly the right gaps: temporal reasoning is weak (cat 2: 0.12 F1), inference is blocked by over-cautious prompting (cat 3: 0.12 F1), and the adversarial scorer was broken (cat 5, now fixed). The overall 0.2432 F1 baseline is honest but far below agentmemory's claimed 95.2% R@5 on LongMemEval-S.

### Key strengths (keep and build on)
- **Scope model** — private/shared/pending_share is a real differentiator. Nobody else does multi-user memory routing at the schema level.
- **Hook surface** — 7 Hermes lifecycle hooks already wired (`sync_turn`, `on_memory_write`, `on_session_end`, `on_pre_compress`, `on_delegation`, `on_turn_start`, `prefetch`).
- **Portability** — MCP + REST means any agent can consume Elkro, not just Hermes.
- **Local-first** — pgvector + local embeddings, no cloud dependency.

### Key gaps (honest assessment)
- **No dedup** — near-identical memories pile up unchecked.
- **No BM25 / keyword retrieval** — pure vector search misses exact-match and lexical recall.
- **No HNSW index** — sequential scan on every query; will degrade as memory count grows.
- **No temporal anchoring** — memories have `created_at` but no session date in content; temporal reasoning is broken.
- **No consolidation loop** — no hourly/periodic sweep to merge, decay, or promote memories.
- **Synchronous LLM extraction** — extraction fails silently when LLM is down; no queue, no retry.
- **No structured metadata** — `metadata JSONB` column exists but nothing populates it consistently.
- **No tests** — no unit tests, no integration tests, no CI.
- **Single-file server** — 420-line monolith; sustainable for now but will need splitting.

---

## The phases

### Phase 0 — Stabilize & stop losing data (1-2 days)
*Goal: fix the bugs that actively lose or corrupt memories today.*

- [ ] **Fix `extract_session_summary` UnboundLocalError** — initialize `summary_text = ""` before `try` block. One-line fix.
- [ ] **Wire `INFERENCE_CHAT_URL` in docker-compose / systemd** — document the env var requirement, add to compose file.
- [ ] **Add dedup check to `save_memory`** — before insert, query nearest neighbor for same `project_id`; skip if cosine distance < 0.05. This is the single highest-value data hygiene fix.
- [ ] **Clean up junk test rows** — one-time SQL cleanup of stray backtick entries and placeholder identity rows.
- [ ] **Add HNSW index** on `memories.embedding` and `summaries.embedding`:
  ```sql
  CREATE INDEX IF NOT EXISTS idx_memories_hnsw ON memories
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
  CREATE INDEX IF NOT EXISTS idx_summaries_hnsw ON summaries
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
  ```

---

### Phase 1 — Temporal context & benchmark fix (2-3 days)
*Goal: close the biggest F1 gap and establish a trustworthy benchmark loop.*

- [ ] **Prepend timestamps to ingested memories.** When `save_memory` is called, if metadata includes a session date or message timestamp, prefix the content: `[2026-06-15 14:32] User prefers Python`. For the LoCoMo harness, pull `session_X_date_time` from the conversation JSON.
- [ ] **Add `timestamp` column to `memories`** (nullable `TIMESTAMPTZ`) — distinct from `created_at` (when the row was inserted). This is the "when did this event actually happen" field.
- [ ] **Soften eval prompt** for category 3 (inferential) — allow hedged reasoning instead of refusal.
- [ ] **Re-run full LoCoMo conversation 0** with both fixes and compare against 0.2432 baseline.
- [ ] **Run 3-5 LoCoMo conversations** to get a multi-conversation average, not just conversation 0.
- [ ] **Create a `benchmarks/` directory** with a reproducible harness script (ingest → query → score → report).

---

### Phase 2 — Async extraction pipeline (3-4 days)
*Goal: decouple memory writes from LLM availability. Never silently drop an extraction again.*

- [ ] **Create `extraction_queue` table:**
  ```sql
  CREATE TABLE IF NOT EXISTS extraction_queue (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      project_id TEXT NOT NULL,
      payload JSONB NOT NULL,        -- raw messages array
      task_type TEXT NOT NULL,        -- 'session_summary' | 'rescue_compress'
      status TEXT DEFAULT 'pending',  -- pending | processing | done | failed
      attempts INT DEFAULT 0,
      last_error TEXT,
      created_at TIMESTAMPTZ DEFAULT NOW(),
      processed_at TIMESTAMPTZ
  );
  ```
- [ ] **Modify `on_session_end` / `on_pre_compress`** to INSERT into queue instead of calling LLM inline.
- [ ] **Build extraction worker** — standalone script (`worker.py`) that polls queue, runs LLM extraction, marks rows done/failed. Exponential backoff on LLM unavailability. Max 3 retries before marking `failed`.
- [ ] **Add systemd timer or simple supervisor** to keep the worker running alongside the server.

---

### Phase 3 — Hybrid retrieval (5-7 days)
*Goal: add BM25 lexical search alongside vector search. This is the single biggest retrieval quality lever.*

This is where agentmemory gets its 95.2% R@5 — triple-stream retrieval (BM25 + vector + graph, reranked). Elkro can close a large chunk of that gap with just the first two.

- [ ] **Add `pg_trgm` extension** and a GIN trigram index on `memories.content` for fast full-text/keyword search.
- [ ] **Alternatively, add `tsvector` column** with GIN index for proper PostgreSQL full-text search:
  ```sql
  ALTER TABLE memories ADD COLUMN tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
  CREATE INDEX idx_memories_fts ON memories USING gin(tsv);
  ```
- [ ] **Implement hybrid search in `search_memories_scoped`:**
  1. Run vector search (top-K by cosine distance) — existing path.
  2. Run BM25/FTS search (top-K by `ts_rank`) — new path.
  3. Merge results with reciprocal rank fusion (RRF) or simple weighted combination.
  4. Return fused top-N.
- [ ] **Add a relevance threshold** — don't return results below a minimum score. Currently Elkro returns the top-N regardless of quality.
- [ ] **Re-benchmark** after hybrid search and compare R@5 to vector-only baseline.

---

### Phase 4 — Structured metadata & provenance (3-4 days)
*Goal: make every memory traceable and filterable.*

- [ ] **Populate `metadata` consistently** on every write path:
  ```json
  {
    "source": "discord|telegram|cli|hermes",
    "created_by": "agent|user",
    "session_id": "abc123",
    "speaker": "travis|partner|agent",
    "extraction_method": "inline|queue|rescue"
  }
  ```
- [ ] **Update Hermes plugin** to pass source/session metadata through all `_call_mcp_tool_bg` calls.
- [ ] **Add `source` filter to `search_memories_scoped`** — optional parameter to restrict search to memories from a specific source or speaker.
- [ ] **Add `list_memories` endpoint** — paginated listing with filters (project_id, scope, source, date range). Currently there's no way to browse what's stored without raw SQL.

---

### Phase 5 — Consolidation loop (4-5 days)
*Goal: keep the memory store lean and high-signal over time. This is what prevents memory systems from becoming noise.*

- [ ] **Build consolidation worker** (can share infrastructure with extraction worker):
  1. **Dedup sweep** — find clusters of memories with cosine distance < 0.08 within same project_id. Merge into single entry, preserving the best/latest content and combining metadata.
  2. **Staleness decay** — memories not accessed/reinforced in N days get a `confidence` score decrement. Optionally archive below a threshold.
  3. **Contradiction detection** — when a new fact contradicts an existing one (high similarity + semantic negation), flag the old one as superseded.
  4. **Audit log** — every merge/decay/deletion emits a row to an `audit_log` table for debugging.
- [ ] **Add `confidence` column to `memories`** (FLOAT, default 1.0). Factor into search ranking.
- [ ] **Add `access_count` and `last_accessed` columns** — increment on retrieval. Feed into decay scoring.
- [ ] **Schedule consolidation** — hourly or daily cron, configurable.

---

### Phase 6 — Multi-user ACLs & scope hardening (3-4 days)
*Goal: make the private/shared boundary actually enforced, not just conventional. Critical before real multi-user deployment.*

- [ ] **Review the agentmemory cross-agent memory leakage CVE** (mentioned in your TODO) — understand the attack vector and ensure Elkro's scope model isn't vulnerable to the same class of bug.
- [ ] **Add `owner_id` column to `memories`** — who created this memory. Currently all private memories under a `project_id` are accessible to anyone who knows the project_id string.
- [ ] **Implement `approve_share_request` / `reject_share_request` endpoints** — the `share_requests` table exists but there's no approval flow. Currently `propose_shared_memory` inserts as `pending_share` but nothing transitions it to `shared`.
- [ ] **Add notification hook** — when a share request is proposed, notify the target scope's owner(s) for approval.
- [ ] **Scope isolation test suite** — automated tests verifying that private memories from user A never leak into user B's search results.

---

### Phase 7 — Lightweight knowledge graph (5-7 days)
*Goal: enable relational queries ("who does X work with", "what tools does Y use") without a full Neo4j dependency. This is the Graphiti-equivalent feature.*

- [ ] **Create `relationships` table:**
  ```sql
  CREATE TABLE IF NOT EXISTS relationships (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      project_id TEXT NOT NULL,
      subject TEXT NOT NULL,
      predicate TEXT NOT NULL,
      object TEXT NOT NULL,
      confidence FLOAT DEFAULT 1.0,
      source_memory_id UUID REFERENCES memories(id),
      valid_from TIMESTAMPTZ DEFAULT NOW(),
      valid_to TIMESTAMPTZ,           -- NULL = still true
      created_at TIMESTAMPTZ DEFAULT NOW()
  );
  CREATE INDEX idx_rel_subject ON relationships (project_id, subject);
  CREATE INDEX idx_rel_object ON relationships (project_id, object);
  ```
- [ ] **Add relationship extraction to the consolidation/extraction pipeline** — after extracting facts from a session, also extract (subject, predicate, object) triples. This can use the same LLM call with an extended prompt.
- [ ] **Add `search_relationships` MCP tool** — query by subject or object, return related triples.
- [ ] **Integrate graph results into `search_memories_scoped`** — when vector/BM25 retrieval finds a memory mentioning an entity, also pull related triples as supplementary context.

---

### Phase 8 — Experiences & beliefs (future, 5+ days)
*Goal: implement the higher-level cognitive layers from your ideas.md.*

- [ ] **Create `experiences` table** (as designed in ideas.md) — high-fidelity episodic records with impact scores and emotional valence.
- [ ] **Create `beliefs` table** (as designed in ideas.md) — subjective, time-decaying beliefs with holder_type (user vs agent) and confidence tracking.
- [ ] **Wire experiences into `on_session_end`** — after extraction, if the session involved a notable milestone (deployment, debugging breakthrough, architectural decision), promote it to an experience.
- [ ] **Wire beliefs into fact extraction** — when the LLM extracts something subjective ("user thinks X is better than Y"), store it as a belief rather than a fact.

---

### Phase 9 — Portability, observability, polish (ongoing)
*Goal: make Elkro production-ready for multi-agent, multi-platform deployment.*

- [ ] **Refactor `server.py` into modules** — split into `routes/`, `services/`, `models/` once it exceeds ~600 lines.
- [ ] **Add health endpoint** (`/health`) — DB connectivity, embedding engine reachability, queue depth.
- [ ] **Add basic observability** — structured JSON logging, latency tracking per endpoint. Consider OpenTelemetry spans if you want parity with agentmemory's OTEL support.
- [ ] **Dockerize properly** — Dockerfile + docker-compose.yml with all services (db, inference, server, worker).
- [ ] **Write integration tests** — at minimum: save → search round-trip, scope isolation, dedup, share request flow.
- [ ] **MCP schema documentation** — auto-generate from FastMCP tool decorators or manually maintain an OpenAPI-compatible spec.
- [ ] **Second agent plugin** — write a plugin for at least one non-Hermes agent (e.g., Claude Code MCP config, or a generic MCP client example) to prove portability.
- [ ] **LongMemEval harness** — port the benchmark harness to LongMemEval-S to get numbers directly comparable to agentmemory's 95.2% R@5 claim.

---

## Suggested priority order

| Order | Phase | Impact | Effort | Why this order |
|-------|-------|--------|--------|----------------|
| 1 | **Phase 0** | 🔴 Critical | ~1-2 days | You're actively losing data from the UnboundLocalError and silent LLM failures. Fix before anything else. |
| 2 | **Phase 1** | 🔴 High | ~2-3 days | Temporal context is your single biggest F1 lever according to the LoCoMo results. Quick win. |
| 3 | **Phase 2** | 🟠 High | ~3-4 days | Async extraction removes the fragile LLM coupling. Once this is in, you stop worrying about extraction reliability forever. |
| 4 | **Phase 3** | 🔴 High | ~5-7 days | Hybrid retrieval is the biggest retrieval quality jump. This is where you close the gap to agentmemory/Mem0. |
| 5 | **Phase 5** | 🟠 Medium | ~4-5 days | Consolidation prevents noise accumulation. Do this before the memory count gets large enough to cause problems. |
| 6 | **Phase 4** | 🟡 Medium | ~3-4 days | Provenance makes debugging and filtering possible. Nice quality-of-life for you as a developer. |
| 7 | **Phase 6** | 🟠 Medium | ~3-4 days | ACLs are essential before real multi-user (you + partner on Discord). Do this before deploying shared scopes. |
| 8 | **Phase 7** | 🟡 Medium | ~5-7 days | Knowledge graph is the long-term play for relational reasoning. Highest-effort, highest-ceiling. |
| 9 | **Phase 8** | 🔵 Low | ~5+ days | Experiences/beliefs are research territory. Build when the foundation is solid. |
| 10 | **Phase 9** | 🟢 Ongoing | continuous | Polish, observability, portability — always happening in parallel. |

---

## Competitive positioning

| Capability | Elkro (today) | Elkro (after Phase 7) | agentmemory | Mem0 | Letta |
|---|---|---|---|---|---|
| Multi-user scopes | ✅ native | ✅ native | ❌ | ❌ | ❌ |
| Private/shared routing | ✅ | ✅ | ❌ | ❌ | ❌ |
| Share proposal flow | 🟡 partial | ✅ with approval | ❌ | ❌ | ❌ |
| BM25 + vector hybrid | ❌ | ✅ | ✅ | ✅ | ❌ |
| Knowledge graph | ❌ | ✅ lightweight | ✅ | ❌ (needs Neo4j) | ❌ |
| Consolidation/decay | ❌ | ✅ | ✅ | ❌ | ✅ |
| Local-first | ✅ | ✅ | ✅ | ❌ | ❌ |
| MCP native | ✅ | ✅ | ✅ | ❌ | ❌ |
| External DB deps | 1 (Postgres) | 1 (Postgres) | 0 | 2 | 1 |
| Agent-agnostic | ✅ via REST/MCP | ✅ | ✅ | ✅ | ❌ |

**Elkro's moat is the scope model.** No other memory system natively supports "this memory is mine, that memory is ours, and I'd like to propose sharing this one." That's your differentiator — lean into it.

---

## Resolved decisions

1. **Worker architecture**: Separate process. A lightweight `while True: poll, sleep(30)` loop running as a systemd unit alongside the server. ~20-40MB RAM idle on the Oracle Ampere box. No measurable impact on the 2 OCPU / 12GB host.

2. **Share approval UX**: Phase 1 → Hermes agent command (inline trigger like `APPROVE_SHARE: <id>`, or the agent proactively suggests sharing). Phase 2 → UI dashboard for browsing and managing shared memories, with visual recommendations for what to share and where.

3. **Graph scope**: Yes — relationships inherit the same private/shared scope model via `project_id`. No additional complexity; the extraction pipeline already runs in a project context.

4. **Benchmark strategy**: Start small. Fix temporal+prompt issues → re-run 1 LoCoMo conversation → validate improvement → run 3-5 more → port to LongMemEval-S for comparable numbers to agentmemory's 95.2% R@5.

5. **Multi-user**: Immediately via correct scope resolution. No ACL layer needed — the `project_id IN ($1, $2)` query pattern already provides isolation. Phase 6 (ACLs) moved to future/optional.

## Deployment context

- **Current**: Oracle Ampere A1 — 2 OCPU, 12GB RAM (ARM64)
- **Potential upgrade**: Netcup ARM server
- **Services on host**: Postgres+pgvector, llama-swap (LLM inference), nomic-embed-text, Elkro server, Hermes agent
- **Future**: If successful, consider public infrastructure hosting for open-source community use

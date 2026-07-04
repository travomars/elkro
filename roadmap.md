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

### Key gaps (Remaining & Addressed)
- [x] ~~**No tests**~~ — *Addressed: Built the robust "Anti-Soup" Pytest suite.*
- [x] ~~**Synchronous LLM extraction**~~ — *Addressed: Built the `extraction_queue` and async background worker.*
- [x] ~~**No BM25 / keyword retrieval**~~ — *Addressed: Postgres `tsvector` and `GIN` indexes are fully implemented for hybrid RRF search.*
- [x] ~~**No temporal anchoring**~~ — *Addressed: `timestamp` columns and prepended dates are now native.*
- [x] ~~**No HNSW index**~~ — *Addressed: Added `idx_memories_hnsw` to `schema.sql`.*
- [ ] **No consolidation loop** — *Still remaining: We need an hourly/periodic sweep to merge, decay, or promote near-identical memories and archive old ones.*

---

## The phases

### Phase 0 — Stabilize & stop losing data (1-2 days)
*Goal: fix the bugs that actively lose or corrupt memories today.*

- [x] **Fix `extract_session_summary` UnboundLocalError** — initialize `summary_text = ""` before `try` block. One-line fix.
- [x] **Wire `INFERENCE_CHAT_URL` in docker-compose / systemd** — document the env var requirement, add to compose file.
- [x] **Add dedup check to `save_memory`** — before insert, query nearest neighbor for same `project_id`; skip if cosine distance < 0.05. This is the single highest-value data hygiene fix.
- [x] **Clean up junk test rows** — one-time SQL cleanup of stray backtick entries and placeholder identity rows.
- [x] **Add HNSW index** on `memories.embedding` and `summaries.embedding`:
  ```sql
  CREATE INDEX IF NOT EXISTS idx_memories_hnsw ON memories
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
  CREATE INDEX IF NOT EXISTS idx_summaries_hnsw ON summaries
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
  ```

---

### Phase 1 — "Anti-Soup" TDD Suite (Completed)
*Goal: Enforce strict boundaries and logic to prevent memory leakage and ensure accurate temporal conflict resolution.*

- [x] **Prepend timestamps to ingested memories.**
- [x] **Add `timestamp` column to `memories`**.
- [x] **Scope Isolation (Privacy) Tests**: Ensure private memories cannot bleed into or be queried from a shared context.
- [x] **Override (NIAH) Tests**: Guarantee that private overrides correctly supersede global baseline facts.
- [x] **State Mutation Tests**: Ensure the consolidation worker correctly resolves temporal conflicts.
- [x] **Automated Pytest Suite**: Scaffolding built in `tests/test_core.py` using `pytest-asyncio`.

---

### Phase 2 — Async extraction pipeline & Benchmarking (Completed)
*Goal: decouple memory writes from LLM availability, and build an automated metrics pipeline.*

- [x] **Make extraction optional via config**
- [x] **Create `extraction_queue` table**
- [x] **Modify `on_session_end` / `on_pre_compress`** to INSERT into queue instead of calling LLM inline.
- [x] **Build extraction worker** (`worker.py`) that polls the queue.
- [x] **Automated Pipeline Benchmarks**: Built `run_pipeline_tests.sh` and `evaluate_metrics.py` to test Retrieval Latency, Token Efficiency, Context Precision, and Context Recall.

---

### Phase 3 — Production Governance & Hermes Plugin (Completed)
*Goal: Integrate interactive UI governance into the Hermes Discord bot for multi-tenant deployments.*

- [x] **Discord UI Governance Buttons**: Modified Hermes `adapter.py` to intercept `SHARE_REQUEST:` strings and render interactive Approve/Reject buttons.
- [x] **UUID Resolution Hook**: Discord adapter natively queries Elkro to resolve context scopes and fetch `share_request` UUIDs prior to rendering buttons.
- [x] **Hermes Memory Plugin**: Integrated zero-latency `prefetch`, background writes (`on_memory_write`), and async extraction.
- [x] **Feature Flag**: Wrapped Phase 3 logic under `ELKRO_GOVERNANCE_ENABLED` in `docker-compose.yml`.
### Phase 4 — Hybrid retrieval (Completed)
*Goal: add BM25 lexical search alongside vector search. This is the single biggest retrieval quality lever.*

This is where agentmemory gets its 95.2% R@5 — triple-stream retrieval (BM25 + vector + graph, reranked). Elkro can close a large chunk of that gap with just the first two.

- [x] **Add `pg_trgm` extension** and a GIN trigram index on `memories.content` for fast full-text/keyword search.
- [x] **Alternatively, add `tsvector` column** with GIN index for proper PostgreSQL full-text search:
  ```sql
  ALTER TABLE memories ADD COLUMN tsv tsvector

  ```
- [x] **Implement hybrid search in `search_memories_scoped`:**
  1. Run vector search (top-K by cosine distance) — existing path.
  2. Run BM25/FTS search (top-K by `ts_rank`) — new path.
  3. Merge results with reciprocal rank fusion (RRF) or simple weighted combination.
  4. Return fused top-N.
- [x] **Add a relevance threshold** — don't return results below a minimum score. Currently Elkro returns the top-N regardless of quality.

---

### Phase 4 — Structured metadata & provenance (3-4 days)
*Goal: make every memory traceable and filterable.*

- [x] **Populate `metadata` consistently** on every write path:
  ```json
  {
    "source": "discord|telegram|cli|hermes",
    "created_by": "agent|user",
    "session_id": "abc123",
    "speaker": "travis|partner|agent",
    "extraction_method": "inline|queue|rescue"
  }
  ```
- [x] **Update Hermes plugin** to pass source/session metadata through all `_call_mcp_tool_bg` calls.
- [x] **Add `source` filter to `search_memories_scoped`** — optional parameter to restrict search to memories from a specific source or speaker.
- [x] **Add `list_memories` endpoint** — paginated listing with filters (project_id, scope, source, date range). Currently there's no way to browse what's stored without raw SQL.

---

### Phase 5 — Consolidation loop (4-5 days)
*Goal: keep the memory store lean and high-signal over time. This is what prevents memory systems from becoming noise.*

- [x] **Build consolidation worker** (can share infrastructure with extraction worker):
  1. **Dedup sweep** — find clusters of memories with cosine distance < 0.08 within same project_id. Merge into single entry, preserving the best/latest content and combining metadata.
  2. **Staleness decay** — memories not accessed/reinforced in N days get a `confidence` score decrement. Optionally archive below a threshold.
  3. **Contradiction detection** — when a new fact contradicts an existing one (high similarity + semantic negation), flag the old one as superseded.
  4. **Audit log** — every merge/decay/deletion emits a row to an `audit_log` table for debugging.
- [x] **Add `confidence` column to `memories`** (FLOAT, default 1.0). Factor into search ranking.
- [x] **Add `access_count` and `last_accessed` columns** — increment on retrieval. Feed into decay scoring.
- [x] **Schedule consolidation** — hourly or daily cron, configurable.

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

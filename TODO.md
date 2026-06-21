CopyElkro TODO

Bugs


 extract_session_summary UnboundLocalError — summary_text is referenced
in the final return statement but only assigned inside the try block.
If run_extraction_llm() raises before assignment, the function crashes
with UnboundLocalError instead of failing gracefully.
Fix: initialize summary_text = "" before the try: block in server.py.
 INFERENCE_CHAT_URL not set in test environment — causes
"All connection attempts failed" on extract_session_summary and
rescue_compressed_memories. Needs to be exported wherever the server
runs (shell, docker-compose, systemd unit), pointing at llama-swap's
gpt-oss-20b endpoint (default http://127.0.0.1:8090/v1/chat/completions).


Architecture / Design


 Move LLM-dependent extraction to batch/deferred processing.
extract_session_summary and rescue_compressed_memories currently run
synchronously, inline, triggered by Hermes plugin hooks (on_session_end,
on_pre_compress). This couples memory writes to local LLM availability —
if llama-swap / gpt-oss-20b isn't running, these calls just fail and log
an error, silently dropping the extraction.
Proposed redesign:
1. Add an `extraction_queue` table (raw messages + project_id + status +
   created_at).
2. `on_session_end` / `on_pre_compress` just INSERT into the queue —
   fast, no LLM dependency, never fails.
3. A separate worker process (cron job, systemd timer, or simple
   `while True` + sleep loop) polls the queue, runs extraction when the
   LLM is available, and marks rows processed.
4. This also makes it trivial to batch multiple sessions into fewer,
   larger LLM calls later if desired.


 Add env vars to docker-compose.yml for Elkro server:
- DATABASE_URL
- INFERENCE_URL (embeddings — nomic-embed)
- INFERENCE_CHAT_URL (chat completions — gpt-oss-20b via llama-swap)
- INFERENCE_CHAT_MODEL
 Decide retry/backoff policy for the extraction worker — e.g. exponential
backoff if llama-swap is unreachable, max retry count before marking a
queue row as permanently failed (for visibility, not silent loss).
 Confirm facts upsert behavior (ON CONFLICT (project_id, key)) is the
desired semantics for current_session_turn — currently overwrites on
every turn, which is correct for a counter but worth a comment in schema.


Nice to have


 Add a status column to summaries / memories to mark entries that
came from the (potentially noisier) async extraction queue vs. direct
on_memory_write mirroring, for easier debugging/auditing later.

# Elkro TODO

Updated after first LoCoMo pilot run (conversation 0, 199 questions, DeepSeek eval-LLM).

## 🔴 High priority — benchmark findings

- [ ] **Add temporal context to ingested memories.** Category 2 (temporal
      reasoning) scored 0.12 F1, but inspection showed the model retrieved
      the *right* turn and gave a *relatively* correct answer ("yesterday",
      "last year") — it just had no absolute date to anchor against. Gold
      answers are absolute dates ("7 May 2023").
      Fix: prepend session date when ingesting into Elkro, e.g.
      `content = f"[{session_key} — {session_date}] {speaker}: {text}"`
      pulling `session_X_date_time` from the LoCoMo conversation JSON (or,
      in production, the real message timestamp from Hermes/Discord/etc).
      This is the same gap that gives Zep's Graphiti its edge — worth
      treating as a first step toward closing that distance without a full
      graph rewrite.

- [ ] **Soften the eval/answer prompt to allow reasoned inference.** Category
      3 (hypothetical/inferential questions, e.g. "would Caroline likely...")
      scored 0.12 F1 but most "failures" were the model refusing to
      speculate ("I don't know") rather than reasoning from retrieved facts.
      This is a benchmark-harness prompt issue, not a memories/retrieval
      issue. Update the eval prompt to explicitly permit reasoned, hedged
      inference ("Likely yes/no, because...") instead of defaulting to
      refusal whenever the answer isn't stated verbatim.

- [x] **Fix F1 scoring for adversarial (empty-gold) questions.** Category 5
      scored 0.04 F1 because token-overlap F1 between "I don't know" and an
      empty gold string is always 0 — even though the model was answering
      correctly every time. DONE: `f1_score()` now treats empty gold as a
      refusal-detection check instead of token overlap.

- [ ] **Re-run full F1 summary after both fixes above** and compare against
      the original 0.2432 baseline to quantify the real improvement from
      genuine architecture/retrieval changes vs. scoring/prompting fixes.
      Keep these two categories of improvement clearly separated when
      reporting results — don't conflate "we fixed our scorer" with "we
      improved retrieval."

## 🟠 Bugs (from earlier server.py work)

- [ ] **`extract_session_summary` UnboundLocalError** — `summary_text` is
      referenced in the final return statement but only assigned inside the
      `try` block. Fix: initialize `summary_text = ""` before the `try:`
      block in `server.py`.

- [ ] **`INFERENCE_CHAT_URL` not set in test/server environment** — causes
      "All connection attempts failed" on `extract_session_summary` and
      `rescue_compressed_memories`. Needs to be exported wherever the
      server runs (shell, docker-compose, systemd unit), pointing at
      llama-swap's `gpt-oss-20b` endpoint or DeepSeek, depending on which
      is being used for extraction at the time.

## 🟡 Data hygiene

- [ ] **Add dedup logic to `save_memory`.** No similarity check exists
      before insert — near-identical memories get stored multiple times
      (seen in testing: duplicate "favorite liquor" and "homelab" entries).
      Skip insert if cosine distance to nearest existing memory for the
      same project_id is below a threshold (e.g. 0.05).

- [ ] **Clean up junk/test entries already in `memories`.** A few rows are
      leftover meta-commentary from early SAVE_MEMORY trigger testing
      (fragments like a stray backtick followed by "lines actually
      persisted."). One-time cleanup:
      ```sql
      DELETE FROM memories WHERE content LIKE '`%';
      ```

- [ ] **Remove/replace placeholder identity row** — a placeholder Discord
      user ID string is sitting in `identities`, left over from seed/test
      data. Harmless until real Discord integration is wired up, but worth
      tracking so it isn't mistaken for a real mapping.

## 🟢 Architecture / design (deferred / batch processing)

- [ ] **Move LLM-dependent extraction off the synchronous hot path.**
      `extract_session_summary` and `rescue_compressed_memories` currently
      run inline, triggered by Hermes plugin hooks (`on_session_end`,
      `on_pre_compress`). This couples memory durability to local LLM
      availability — if llama-swap / gpt-oss-20b isn't running, the call
      just fails and logs an error, silently dropping the extraction.

      Proposed redesign:
      1. Add an `extraction_queue` table (raw messages + project_id +
         status + created_at).
      2. `on_session_end` / `on_pre_compress` just INSERT into the queue —
         fast, no LLM dependency, never fails.
      3. A separate worker process (cron job, systemd timer, or a simple
         `while True` + sleep loop) polls the queue and runs extraction
         whenever the LLM is actually available.
      4. Also makes it trivial to batch multiple sessions into fewer,
         larger LLM calls later if desired.

- [ ] **Add env vars to `docker-compose.yml`** for the Elkro server:
      - `DATABASE_URL`
      - `INFERENCE_URL` (embeddings — nomic-embed)
      - `INFERENCE_CHAT_URL` (chat completions — gpt-oss-20b via llama-swap,
        or DeepSeek for higher-quality extraction)
      - `INFERENCE_CHAT_MODEL`

- [ ] **Decide retry/backoff policy for the extraction worker** — e.g.
      exponential backoff if the LLM endpoint is unreachable, max retry
      count before marking a queue row as permanently failed (for
      visibility, not silent loss).

## 🔵 Future / research

- [ ] **Decaying beliefs/opinions if not reinforced** — facts that haven't
      been mentioned/confirmed in a long time could have confidence decay
      over time, distinct from facts that are explicitly contradicted.

- [ ] **Relationships table** — UUID, subject, predicate, object, confidence.
      Would enable graph-style queries ("who does Caroline work with") on
      top of the existing flat vector memories. This is the closest analog
      to Zep's Graphiti approach and the most direct way to meaningfully
      close the temporal/relational reasoning gap long-term (vs. the
      lighter date-prefix fix above, which is a stopgap).

- [ ] **Provenance / metadata schema** for memories — `created_by` (agent
      vs user), `source` (telegram/discord/cli), `session` id. Partial
      support exists via the `metadata JSONB` column on `memories`, but
      nothing currently populates it consistently.

- [ ] **ACLs** — access control for shared/multi-user scopes, especially
      before any real multi-user (Discord channel, etc.) deployment. Worth
      reviewing agentmemory's isolated-agent-scope CVE (cross-agent memory
      leakage via memory_recall) as a cautionary reference before shipping
      shared scopes.

- [ ] **Memory versioning** — track edits/corrections to a fact over time
      rather than just inserting a new contradictory row, to support
      "what did we used to believe vs. what's current" queries.

## ⚪ Benchmarking process

- [ ] Run full 10-conversation LoCoMo set with DeepSeek once the temporal
      and prompt fixes above are in, to get a clean comparable F1 number.
- [ ] Run the same harness with `--skip-ingest` reusing the same ingested
      memories, swapping only the eval-LLM, to isolate retrieval quality
      (constant) from reader/generator quality (variable) — e.g. DeepSeek
      vs gpt-oss-20b on identical retrieved context.
- [ ] Consider porting the harness to LongMemEval (v1, not v2) once LoCoMo
      numbers are trusted, reusing ~90% of the existing ingest/query/score
      structure.
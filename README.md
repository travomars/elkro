E.L.K.R.O.
==========

**Extensible Local Knowledge Routing Overlay**

Elkro is a fast, multi-tenant, locally-hosted cognitive routing layer for AI agents. Built on the **Model Context Protocol (MCP)**, it acts as a secure external memory bank that dynamically separates private user memories from shared community knowledge contexts.

Instead of relying on cloud providers or monolithic, tightly coupled agent frameworks, Elkro provides a portable, async API powered by **PostgreSQL (pgvector)** and local embedding engines.

🧠 Core Architecture: The Three-Tier Memory System
--------------------------------------------------

Elkro structures context retrieval across three distinct cognitive layers to balance fuzzy reasoning with absolute accuracy.

| **Tier** | **Concept** | **Storage Mechanism** | **Best Used For** |
| --- | --- | --- | --- |
| **Tier 1** | **Semantic Vectors** | `pgvector` Cosine Distance | Fluid conversational history, contextual preferences, and fuzzy recall. |
| **Tier 2** | **Summaries** | `pgvector` Context Roll-ups | Long-term session compression and high-level project status. |
| **Tier 3** | **Atomic Facts** | Strict Key-Value SQL | Rigid rules, system constants, user IDs, and non-negotiable instructions. |

### The Boundary Model (Scopes)

Every memory operation in Elkro requires a defined **Scope**.

-   **Private Scope:** Bound to a specific user's canonical ID. Memories here are strictly isolated.

-   **Shared Scope:** Bound to a channel, group, or server. Agents query this as a secondary fallback if private context is insufficient.

🛠️ Prerequisites
-----------------

Before spinning up Elkro, ensure your host machine has the following:

-   **Python 3.10+**

-   **Podman** (Rootless container management)

-   A local embedding engine serving OpenAI-compatible endpoints (e.g., `llama.cpp` or `Ollama` running `nomic-embed-text`).

🚀 Quickstart Guide
-------------------

Elkro relies on host-networked Podman containers to minimize synchronous latency between the Python API, the database, and the inference engine.

### 1\. Spin up the Infrastructure

Start the Postgres database with the `pgvector` extension enabled.

Bash

```
podman run -d --name elkro-db\
  --net=host\
  -e POSTGRES_USER=postgres\
  -e POSTGRES_PASSWORD=postgres\
  -e POSTGRES_DB=elkro\
  pgvector/pgvector:pg16

```

### 2\. Seed the Database Schema

Wait 3 seconds for the database to initialize, then inject the schema.

Bash

```
podman cp schema.sql elkro-db:/schema.sql
podman exec -it elkro-db psql -U postgres -d elkro -f /schema.sql

```

### 3\. Start Local Embeddings

*(Example using a containerized `llama.cpp` server for `nomic-embed-text`)*

Bash

```
podman run -d --name elkro-inference\
  --net=host\
  -v ~/.cache/lm-studio/models:/models:ro\
  ghcr.io/ggerganov/llama.cpp:server\
  -m /models/nomic-embed-text.gguf\
  --port 8080\
  --embedding

```

### 4\. Run the Elkro MCP Server

Initialize your Python environment and start the asynchronous FastAPI backend.

Bash

```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5432/elkro"
export INFERENCE_URL="http://127.0.0.1:8080"
export INFERENCE_CHAT_URL="http://127.0.0.1:8090/v1/chat/completions"
export INFERENCE_CHAT_MODEL="gpt-oss-20b"

python server.py

```

The MCP server is now actively listening on `http://0.0.0.0:8000`.

🤖 MCP Tool Reference for Agents
--------------------------------

Agents connecting to Elkro via MCP will automatically discover the following tools.

### Scope Resolution

-   `resolve_context_scopes(platform, platform_id, channel_id, active_agent)`

    -   **Action:** Translates raw external identities (like Discord User IDs) into strict canonical project IDs.

### Tier 1 Tools

-   `search_memories_scoped(query, private_project_id, shared_project_id, limit)`

    -   **Action:** Executes a hybrid vector search prioritizing private context, appending relevant shared context underneath.

-   `save_memory(content, project_id, scope, metadata)`

    -   **Action:** Persists a conversational observation.

-   `propose_shared_memory(content, private_project_id, shared_project_id)`

    -   **Action:** Flags a private discovery for community review. Saves immediately as `pending_share`.

### Tier 2 Tools

-   `search_summaries(query, project_id, limit)`

    -   **Action:** Retrieves rolled-up project overviews based on semantic proximity.

-   `save_summary(content, project_id)`

    -   **Action:** Commits a synthesized block of text to the project's macro-memory.

### Tier 3 Tools

-   `get_fact(private_project_id, shared_project_id, key)`

    -   **Action:** Retrieves an absolute system string. Overrides channel-wide rules with personal preferences if a conflict exists.

-   `list_facts(project_id)`

    -   **Action:** Returns a complete directory of exact configurations mapped to the agent's current scope.

🔌 Connecting an Agent Framework (Hermes/LangChain)
---------------------------------------------------

To integrate an agent, point its MCP client initialization at your local server.

Python

```
# Example Client Integration Environment Variable
export RUNES_MCP_URL="http://127.0.0.1:8000"

```

Ensure your agent's memory provider or `sync_turn` hooks handle HTTP timeouts gracefully (recommended: `<3.0s` for reads) to prevent blocking the LLM generation cycle if the database is under load. Offload all `save` operations to background threads.

The Symlink Trick (Connecting it to Hermes)
Because Hermes expects memory plugins to live inside its own internal configuration folder (usually ~/.hermes/plugins/memory/), you don't want to constantly copy and paste your code from ~/projects/elkro/hermes-plugin every time you make an edit.

Instead, create a symbolic link. This creates a shortcut so Hermes reads directly from your organized project folder:

Bash
# Ensure the Hermes memory plugin directory exists
mkdir -p ~/.hermes/plugins/memory/

# Create a symlink pointing Hermes to your new plugin folder
ln -s ~/projects/elkro/hermes-plugin ~/.hermes/plugins/memory/elkro
Now, your workspace is perfectly clean. When you are editing the server, you stay in the `server/` folder. When you tweak how Hermes formats memories, you edit the `hermes-plugin/` folder, and Hermes will see the updates instantly.

## 📊 Automated Benchmarks
Elkro includes a fully automated evaluation pipeline that rigorously tests Retrieval Latency, Token Efficiency, Context Precision, and Context Recall using authentic Hermes trajectories.
To run the automated pipeline tests:
```bash
./run_pipeline_tests.sh
```

## ⚖️ Discord UI Governance
Elkro supports an interactive memory promotion lifecycle. When an agent proposes a global rule using `SHARE_REQUEST: <rule>`, the Elkro backend generates a secure UUID and the Hermes Discord adapter renders interactive `Approve` and `Reject` buttons inline in Discord.
This feature can be toggled by passing `ELKRO_GOVERNANCE_ENABLED=true` into the agent's environment.
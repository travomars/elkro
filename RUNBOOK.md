# Elkro Runbook

This runbook documents how to run the various services required for Elkro local development and testing.

## Prerequisites

Ensure Docker (or Podman) is running, and that the `elkro-db` container is active:
```bash
docker start elkro-db
```

## 1. Start the Embedding Service (llama-server)

Elkro uses `nomic-embed-text` for creating embeddings. If you are not using Ollama, you can run the model directly with `llama-server`.

```bash
llama-server \
  --model /home/travis/models/nomic-embed-text-v1.5.f16.gguf \
  --embedding \
  --n-gpu-layers 0 \
  --threads 8 \
  --batch-size 2048 \
  --ubatch-size 2048 \
  --ctx-size 8192 \
  --port 8080
```
> [!NOTE]
> Ensure the port matches `INFERENCE_URL` (usually 8080 or 8091). If you use `8091`, update the export below!

## 2. Start the Inference Service (LLM for Extraction)

For session extraction, Elkro requires an LLM serving OpenAI-compatible chat completions. You have two options:

**Option A: Local LLM (e.g., `llama-swap` / `vLLM`)**
Start your local inference server pointing at a lightweight model (e.g., `gpt-oss-20b`) on port `8090`.

**Option B: External API (No Local LLM Required)**
If you prefer not to run a local LLM, Elkro can seamlessly route extraction tasks to an external API (like OpenAI, Groq, or TogetherAI). You will just need to set the `INFERENCE_CHAT_URL`, `INFERENCE_CHAT_MODEL`, and `INFERENCE_API_KEY` environment variables.

*(Note: Your local `nomic-embed-text` server from Step 1 handles all vector embeddings perfectly. You do **not** need any additional embedding service.)*

## 3. Start the Elkro Server

Activate the virtual environment and run the server.

```bash
export DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5432/elkro"
export INFERENCE_URL="http://127.0.0.1:8080" # Points to your Nomic embedding server

# -- If using a Local LLM --
export INFERENCE_CHAT_URL="http://127.0.0.1:8090/v1/chat/completions"
export INFERENCE_CHAT_MODEL="gpt-oss-20b"

# -- OR: If using an External API (e.g., OpenAI) --
# export INFERENCE_CHAT_URL="https://api.openai.com/v1/chat/completions"
# export INFERENCE_CHAT_MODEL="gpt-4o-mini"
# export INFERENCE_API_KEY="sk-..."

export EXTRACTION_ENABLED="true"

source server/venv/bin/activate
cd server
python server.py
```

## 4. Run the Background Worker

With async extraction (Phase 2), memory extractions are queued in the database. You must run the worker to process them.

**Daemon mode (continuous polling):**
```bash
source server/venv/bin/activate
cd server
python worker.py
```

**Batch mode (process all pending tasks and exit):**
```bash
source server/venv/bin/activate
cd server
python worker.py --batch
```

## 5. Running Automated Pipeline Benchmarks

To automatically spin up the Mock Inference Server, Elkro API, Elkro Worker, and run the complete benchmark suite against Hermes, use the pipeline script:

```bash
./run_pipeline_tests.sh
```
*(This evaluates Context Precision, Recall, Token Efficiency, and Latency. To test vector search accurately, ensure `INFERENCE_URL` points to a genuine embedding model like `nomic-embed-text` rather than the mock server.)*

## 6. Hermes Discord Governance (Phase 3)

To enable the interactive Discord Governance UI (Approve/Reject buttons for shared memories), ensure the following flag is set in your Hermes agent's `docker-compose.yml`:

```yaml
    environment:
      - ELKRO_GOVERNANCE_ENABLED=true
```

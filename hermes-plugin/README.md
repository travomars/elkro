# Elkro Memory Provider for Hermes

This plugin integrates Hermes Agent with the **Elkro Cognitive Layer**—a local, multi-tenant memory routing engine backed by PostgreSQL and `pgvector`. 

It enables Hermes to:
1. Automatically fetch vector-embedded context (Private & Shared) before generating a response.
2. Silently save user memories in the background via trigger strings.
3. Keep user boundaries strictly scoped, preventing data leakage across different Discord/CLI sessions.

## ⚙️ Setup Instructions

### 1. Start the Elkro Backend
Ensure your Elkro MCP server and `pgvector` container are actively running on your host machine (default: `http://127.0.0.1:8000`).

### 2. Activate the Plugin in Hermes
Tell Hermes to use this provider instead of the built-in standard memory.

Run the interactive setup:
```bash
hermes memory setup
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
import httpx

from agent.memory_provider import MemoryProvider

logger = logging.getLogger("Hermes.Plugin.Elkro")

class ElkroMemoryProvider(MemoryProvider):
    """Elkro Cognitive Layer - Multi-tenant memory routing engine via pgvector."""

    def __init__(self):
        self._mcp_url = "http://127.0.0.1:8000"
        self._active_agent = "orchestrator"
        self._session_id = ""
        self._private_pid = ""
        self._shared_pid = ""
        self._raw_user_id = "unknown_user"
        self._platform = "discord"

    @property
    def name(self) -> str:
        return "elkro"

    def is_available(self) -> bool:
        """Always return True so it is visible and selectable in the setup CLI menu."""
        return True

    def get_tool_schemas(self) -> list:
        """Elkro injects memory via prefetch/system_prompt_block — no agent-callable tools needed."""
        return []

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    def get_config_schema(self):
        """Generates the interactive CLI questions during setup."""
        return [
            {
                "key": "mcp_url",
                "description": "Elkro MCP Server URL",
                "default": "http://127.0.0.1:8000",
                "env_var": "ELKRO_MCP_URL",
                "required": True
            },
            {
                "key": "active_agent",
                "description": "Profile namespace to segregate agent memories",
                "default": "orchestrator",
                "env_var": "ACTIVE_AGENT_PROFILE"
            }
        ]

    def save_config(self, values: dict, hermes_home: str) -> None:
        """Saves values received from the interactive CLI to the configuration space."""
        config_path = Path(hermes_home) / "elkro.json"
        try:
            config_path.write_text(json.dumps(values, indent=2))
            logger.info(f"Elkro config written successfully to {config_path}")
        except Exception as e:
            logger.error(f"Failed to write Elkro config: {e}")
    

    def initialize(self, session_id: str, **kwargs) -> None:
        """Pre-warms connections and resolves multi-tenant semantic boundaries."""
        self._session_id = session_id
        hermes_home = kwargs.get("hermes_home", os.path.expanduser("~/.hermes"))
        
        # Load config from disk, falling back to env vars or defaults
        config_path = Path(hermes_home) / "elkro.json"
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text())
                self._mcp_url = cfg.get("mcp_url", self._mcp_url)
                self._active_agent = cfg.get("active_agent", self._active_agent)
            except Exception as e:
                logger.debug(f"Failed parsing elkro.json: {e}")

        # Override with environment variables if explicitly passed
        self._mcp_url = os.environ.get("ELKRO_MCP_URL", self._mcp_url)
        self._active_agent = os.environ.get("ACTIVE_AGENT_PROFILE", self._active_agent)

        # Pull real user telemetry out of the session DB
        self._fetch_hermes_session_data(hermes_home)
        
        # Build initial fallback IDs
        self._private_pid = f"{self._active_agent}:private:{self._raw_user_id}"
        self._shared_pid = f"{self._active_agent}:shared:global"
        
        # Map IDs against pgvector UUIDs via the FastMCP resolution engine
        self._resolve_context_scopes()

    def _fetch_hermes_session_data(self, hermes_home: str):
        db_path = Path(hermes_home) / "state.db"
        if not db_path.exists():
            return
        try:
            with sqlite3.connect(str(db_path)) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id, source FROM sessions WHERE id = ?", (self._session_id,))
                row = cursor.fetchone()
                if row:
                    self._raw_user_id = row[0] or self._raw_user_id
                    self._platform = row[1] or self._platform
        except Exception as e:
            logger.error(f"Failed reading state.db: {e}")

    def _resolve_context_scopes(self):
        try:
            with httpx.Client(timeout=3.0) as client:
                r = client.post(
                    f"{self._mcp_url}/tools/resolve_context_scopes",
                    json={
                        "platform": self._platform,
                        "platform_id": str(self._raw_user_id),
                        "channel_id": "global_channel",
                        "active_agent": self._active_agent
                    }
                )
                if r.status_code == 200:
                    scopes = r.json().get("result", {})
                    if isinstance(scopes, dict):
                        self._private_pid = scopes.get("private_project_id", self._private_pid)
                        self._shared_pid = scopes.get("shared_project_id", self._shared_pid)
        except Exception as e:
            logger.warning(f"Database scope resolution bypassed; defaulting to system keys: {e}")

    def system_prompt_block(self) -> str:
        """Injects instructions into the core system prompt (cache friendly)."""
        return (
            "# Elkro Memory Activation\n"
            "Active. Relevant historical vector context is being auto-injected dynamically.\n"
            "To propose community rules or project constants, use:\n"
            "SHARE_REQUEST: <shared team context or rule>"
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Synchronously blocks turning cycles to grab rich contexts from Elkro."""
        try:
            payload = {
                "query": query,
                "private_project_id": self._private_pid,
                "shared_project_id": self._shared_pid
            }
            with httpx.Client(timeout=3.0) as client:
                r = client.post(f"{self._mcp_url}/tools/recall", json=payload)
                if r.status_code == 200:
                    data = r.json().get("result", {})
                    
                    profiles = data.get("profiles", "")
                    memories = data.get("memories", "")
                    facts = data.get("facts", "")
                    
                    context = []
                    
                    # Explicit preamble (Hindsight-style) to enforce grounding across all models
                    context.append(
                        "## Elkro Persistent Memory\n"
                        "Use this cross-session context to answer questions about the user and prior sessions. "
                        "Do NOT call memory search tools to look up information that is already provided below. "
                        "Prioritize this information when answering."
                    )
                    
                    if profiles:
                        context.append(f"### Synthesized Entity Profiles\n{profiles}")
                    
                    if memories and "No relevant matching" not in memories:
                        context.append(f"### Retrieved Memories\n{memories}")
                    
                    if facts and "No relevant facts" not in facts:
                        context.append(f"### Structured Facts\n{facts}")
                        
                    if len(context) > 1:
                        # Append session context if we actually retrieved anything
                        context.append(f"### Session Context\nSession ID: {session_id}")
                        return "\n\n".join(context)
                    
                    return ""
        except Exception as e:
            logger.error(f"Recall fetch timeout/failure: {e}")
        return ""

    # =========================================================================
    # THE WRITE PIPELINE (Background Hooks)
    # =========================================================================

    def _call_mcp_tool_bg(self, tool_name: str, payload: dict):
        """Generic background worker to fire and forget JSON payloads to Elkro MCP."""
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(f"{self._mcp_url}/tools/{tool_name}", json=payload)
        except Exception as e:
            logger.error(f"Background write to {tool_name} failed: {e}")

    def sync_turn(self, user_content, assistant_content, *, session_id="", messages=None) -> None:
        """Intercepts model responses out-of-band to catch explicit inline triggers."""
        pass

    def on_memory_write(self, action: str, target: str, content: str, metadata: dict = None) -> None:
        """Catches when the agent uses the internal memory tool natively -> Routes to Atomic Memories."""
        logger.error(f"DEBUG_ELKRO on_memory_write called with action={action}, target={target}, content={content}, metadata={metadata}")
        merged_meta = metadata or {}
        
        if action in ["replace", "remove"]:
            old_text = merged_meta.get("old_text", "")
            target_content = old_text if old_text else content
            if target_content:
                threading.Thread(target=self._call_mcp_tool_bg, args=("remove_memory", {
                    "content": target_content,
                    "project_id": self._private_pid,
                })).start()

        if action in ["add", "replace"] and content:
            merged_meta.update({"source": "hermes", "extraction_method": "native_tool", "speaker": "agent"})
            threading.Thread(target=self._call_mcp_tool_bg, args=("save_memory", {
                "content": content,
                "project_id": self._private_pid,
                "scope": "private",
                "metadata": merged_meta
            })).start()

    def on_session_end(self, messages: list) -> None:
        """Passes the raw transcript array to FastMCP for extraction into Narratives & Facts."""
        threading.Thread(target=self._call_mcp_tool_bg, args=("extract_session_summary", {
            "messages": messages, # We send the raw JSON array to FastMCP to do the LLM extraction
            "project_id": self._private_pid
        })).start()

    def on_pre_compress(self, messages: list) -> str:
        """Rescues context before it is pruned by the context window limit -> Routes to Memories."""
        threading.Thread(target=self._call_mcp_tool_bg, args=("rescue_compressed_memories", {
            "messages": messages,
            "project_id": self._private_pid
        })).start()
        
        # This string is injected into the prompt so the agent knows the memory was saved
        return "[Elkro Cognitive Layer: Prior context has been securely extracted and archived.]"

    def on_delegation(self, task: str, result: str, **kwargs) -> None:
        """Captures completed subagent task outcomes as semantic memories."""
        content = f"Delegated Task: {task}\nOutcome: {result}"
        threading.Thread(target=self._call_mcp_tool_bg, args=("save_memory", {
            "content": content,
            "project_id": self._private_pid,
            "scope": "private",
            "metadata": {"source": "hermes", "extraction_method": "delegation", "speaker": "agent"}
        })).start()

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> str:
        """Tracks structured, non-semantic state like the current turn counter in the Facts tier.
        Also surfaces pending share requests to the agent's context."""
        threading.Thread(target=self._call_mcp_tool_bg, args=("set_fact", {
            "project_id": self._private_pid,
            "key": "current_session_turn",
            "value": str(turn_number)
        })).start()

        # Check for pending share requests and return them to be injected into context
        pending_msg = ""
        try:
            with httpx.Client(timeout=3.0) as client:
                r = client.get(
                    f"{self._mcp_url}/share_requests/pending",
                    params={"project_id": self._private_pid}
                )
                if r.status_code == 200:
                    pending = r.json().get("requests", [])
                    if pending:
                        logger.info(f"[Elkro] {len(pending)} pending share requests found")
                        pending_list = "\n".join([f"- Request {p['id']}: {p['content']}" for p in pending])
                        pending_msg = f"\n[Elkro System] You have {len(pending)} pending share requests waiting for your approval. You can approve or reject them using the API if you had one, but for now just inform the user. Requests:\n{pending_list}\n"
        except Exception as e:
            logger.debug(f"Share request check failed: {e}")
        
        return pending_msg


def register(ctx):
    
    ctx.register_memory_provider(ElkroMemoryProvider())
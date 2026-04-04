#!/usr/bin/env python3
"""
RLM MCP Server - Recursive Language Model patterns for massive context handling.

Implements the core insight from https://arxiv.org/html/2512.24601v1:
Treat context as external variable, chunk programmatically, sub-call recursively.
"""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from claude_agent_sdk import ClaudeAgentOptions, query as claude_query
    HAS_CLAUDE_SDK = True
except ImportError:
    HAS_CLAUDE_SDK = False

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

# Storage directories
DATA_DIR = Path(os.environ.get("RLM_DATA_DIR", "/tmp/rlm"))
CONTEXTS_DIR = DATA_DIR / "contexts"
CHUNKS_DIR = DATA_DIR / "chunks"
RESULTS_DIR = DATA_DIR / "results"

for directory in [CONTEXTS_DIR, CHUNKS_DIR, RESULTS_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# In-memory context storage (also persisted to disk)
contexts: dict[str, dict] = {}

server = Server("rlm")

# Default models per provider
DEFAULT_MODELS = {
    "ollama": "olmo-3.1:32b",
    "claude-sdk": "claude-haiku-4-5-20251001",
    "openai": "gpt-5.4-mini",
    "openrouter": "openai/gpt-5.4-mini",  # Recommended hosted default
}

# ============================================================================
# SANDBOX SECURITY: Blocked imports for rlm_exec
# ============================================================================
# These modules are blocked because they could allow:
# - File system access beyond the context (os, pathlib, shutil)
# - Network access (socket, requests, urllib, http, httpx, aiohttp)
# - Process spawning (subprocess, multiprocessing)
# - Code execution (importlib, __import__, exec, eval - handled separately)
# - System modification (ctypes, cffi)
BLOCKED_IMPORTS = frozenset({
    # File system
    "os", "pathlib", "shutil", "glob", "fnmatch",
    # Network
    "socket", "requests", "urllib", "http", "httpx", "aiohttp", "websocket",
    # Process/threading
    "subprocess", "multiprocessing", "threading", "concurrent",
    # Dynamic code loading
    "importlib", "imp", "pkgutil", "zipimport",
    # System-level
    "ctypes", "cffi", "sysconfig",
    # Potentially dangerous
    "pickle", "shelve", "marshal",  # Arbitrary code execution via deserialization
    "pty", "tty", "termios",  # Terminal manipulation
    "resource",  # Resource limit manipulation
})


def _check_blocked_imports(code: str) -> Optional[str]:
    """
    Scan code for blocked imports. Returns error message if blocked import found.

    Checks for:
    - import <blocked>
    - from <blocked> import ...
    - __import__('<blocked>')
    """
    # Normalize code for checking
    lines = code.split('\n')

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Skip comments and empty lines
        if stripped.startswith('#') or not stripped:
            continue

        # Check for direct imports: "import os" or "import os, sys"
        if stripped.startswith('import '):
            # Extract module names
            import_part = stripped[7:].split('#')[0]  # Remove inline comments
            modules = [m.strip().split('.')[0].split(' ')[0] for m in import_part.split(',')]
            for module in modules:
                if module in BLOCKED_IMPORTS:
                    return f"Line {i}: Blocked import '{module}' - not allowed in sandbox"

        # Check for from imports: "from os import path"
        if stripped.startswith('from '):
            # Extract the base module
            match = re.match(r'from\s+(\w+)', stripped)
            if match:
                module = match.group(1)
                if module in BLOCKED_IMPORTS:
                    return f"Line {i}: Blocked import 'from {module}' - not allowed in sandbox"

        # Check for __import__ calls
        if '__import__' in stripped:
            for blocked in BLOCKED_IMPORTS:
                if f"__import__('{blocked}" in stripped or f'__import__("{blocked}' in stripped:
                    return f"Line {i}: Blocked __import__('{blocked}') - not allowed in sandbox"

    return None  # No blocked imports found


@dataclass
class RecursionState:
    """Track recursion depth to prevent infinite loops in sub-queries."""
    current_depth: int = 0
    max_depth: int = 3
    call_trace: list[str] = field(default_factory=list)

    def can_recurse(self) -> bool:
        """Check if further recursion is allowed."""
        return self.current_depth < self.max_depth

    def descend(self, call_id: str) -> "RecursionState":
        """Create a new state for a deeper recursion level."""
        return RecursionState(
            current_depth=self.current_depth + 1,
            max_depth=self.max_depth,
            call_trace=self.call_trace + [call_id],
        )


@dataclass
class ProviderCallResult:
    """Structured provider call result with resolved backend metadata."""
    result: Optional[str]
    error: Optional[str]
    state: Optional["RecursionState"]
    provider: str
    model: str


def _bound_recursive_tool_args(
    tool_name: str,
    tool_args: dict[str, Any],
    state: Optional["RecursionState"],
) -> dict[str, Any]:
    """Bound recursive tool arguments to remaining depth budget.

    Prevent sub-model tool calls from requesting deeper recursion than the
    current recursion state allows.
    """
    if not state:
        return tool_args

    bounded = dict(tool_args)

    if tool_name in {"rlm_sub_query", "rlm_sub_query_batch"}:
        requested_depth = bounded.get("max_depth", 0)
        try:
            requested_depth = int(requested_depth)
        except (TypeError, ValueError):
            requested_depth = 0

        remaining_depth = max(0, state.max_depth - (state.current_depth + 1))
        bounded["max_depth"] = min(max(requested_depth, 0), remaining_depth)

    return bounded


def _coerce_int(value: Any, default: int) -> int:
    """Coerce a value to int, falling back to default on invalid input."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _hash_content(content: str) -> str:
    """Create short hash for content identification."""
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def _load_context_from_disk(name: str) -> Optional[dict]:
    """Load context from disk if it exists."""
    meta_path = CONTEXTS_DIR / f"{name}.meta.json"
    content_path = CONTEXTS_DIR / f"{name}.txt"

    if not (meta_path.exists() and content_path.exists()):
        return None

    meta = json.loads(meta_path.read_text())
    meta["content"] = content_path.read_text()
    return meta


def _save_context_to_disk(name: str, content: str, meta: dict) -> None:
    """Persist context to disk."""
    (CONTEXTS_DIR / f"{name}.txt").write_text(content)
    meta_without_content = {k: v for k, v in meta.items() if k != "content"}
    (CONTEXTS_DIR / f"{name}.meta.json").write_text(
        json.dumps(meta_without_content, indent=2)
    )


def _ensure_context_loaded(name: str) -> Optional[str]:
    """Ensure context is loaded into memory. Returns error message if not found."""
    if name in contexts:
        return None

    disk_context = _load_context_from_disk(name)
    if disk_context:
        content = disk_context.pop("content")
        contexts[name] = {"meta": disk_context, "content": content}
        return None

    return f"Context '{name}' not found"


def _text_response(data: Any) -> list[TextContent]:
    """Create a JSON text response."""
    if isinstance(data, str):
        return [TextContent(type="text", text=data)]
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


def _error_response(code: str, message: str) -> list[TextContent]:
    """Create a structured error response."""
    return _text_response({"error": code, "message": message})


def _context_summary(name: str, content: str, **extra: Any) -> dict:
    """Build a common context summary dict."""
    summary = {
        "name": name,
        "length": len(content),
        "lines": content.count("\n") + 1,
    }
    summary.update(extra)
    return summary


# Shared schema fragments for tool definitions
PROVIDER_SCHEMA = {
    "type": "string",
    "enum": ["auto", "ollama", "claude-sdk", "openai", "openrouter"],
    "description": "LLM provider for sub-call. Use auto to pick the best available backend.",
    "default": "auto",
}

AUTO_PROVIDER_ORDER_SIMPLE = ("openrouter", "ollama", "openai", "claude-sdk")
AUTO_PROVIDER_ORDER_RECURSIVE = ("openrouter", "ollama", "openai")
PREFERRED_OLLAMA_MODELS = (
    "olmo-3.1:32b",
    "qwen2.5:32b",
    "qwen2.5:32b-instruct",
    "gemma3:27b",
    "qwen2.5:latest",
    "llama3.3:70b",
    "llama3:latest",
    "llama3.2:latest",
)


def _default_provider() -> str:
    """Return the configured default provider, falling back to auto."""
    provider = os.environ.get("RLM_DEFAULT_PROVIDER", "auto")
    if provider in PROVIDER_SCHEMA["enum"]:
        return provider
    return "auto"


def _provider_supports_recursive_tools(provider: str) -> bool:
    """Whether a provider can currently handle recursive tool loops."""
    return provider in {"ollama", "openai", "openrouter"}


async def _probe_ollama_models() -> tuple[list[str], Optional[str]]:
    """Return locally available Ollama models, or an error string."""
    if not HAS_HTTPX:
        return [], "httpx required for Ollama calls"

    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{ollama_url}/api/tags")
            response.raise_for_status()
            payload = response.json()
    except Exception as e:
        return [], str(e)

    models = payload.get("models", [])
    names = [m.get("name") for m in models if m.get("name")]
    return names, None


async def _collect_provider_status(include_models: bool = False) -> dict[str, dict[str, Any]]:
    """Collect availability and setup status for each provider."""
    statuses: dict[str, dict[str, Any]] = {
        "claude-sdk": {
            "available": HAS_CLAUDE_SDK,
            "reason": "claude-agent-sdk import available" if HAS_CLAUDE_SDK else "claude-agent-sdk package missing",
            "recursive_tool_calls": _provider_supports_recursive_tools("claude-sdk"),
        },
        "openai": {
            "available": HAS_OPENAI and bool(os.environ.get("OPENAI_API_KEY")),
            "reason": (
                "OPENAI_API_KEY configured"
                if HAS_OPENAI and os.environ.get("OPENAI_API_KEY")
                else ("OPENAI_API_KEY environment variable not set" if HAS_OPENAI else "openai package missing")
            ),
            "recursive_tool_calls": _provider_supports_recursive_tools("openai"),
        },
        "openrouter": {
            "available": HAS_OPENAI and bool(os.environ.get("OPENROUTER_API_KEY")),
            "reason": (
                "OPENROUTER_API_KEY configured"
                if HAS_OPENAI and os.environ.get("OPENROUTER_API_KEY")
                else ("OPENROUTER_API_KEY environment variable not set" if HAS_OPENAI else "openai package missing")
            ),
            "recursive_tool_calls": _provider_supports_recursive_tools("openrouter"),
        },
    }

    ollama_models, ollama_error = await _probe_ollama_models()
    statuses["ollama"] = {
        "available": bool(ollama_models),
        "reason": f"{len(ollama_models)} model(s) available" if ollama_models else (ollama_error or "no Ollama models found"),
        "recursive_tool_calls": _provider_supports_recursive_tools("ollama"),
    }
    if include_models:
        statuses["ollama"]["models"] = ollama_models

    return statuses


async def _resolve_ollama_model(requested_model: str | None) -> tuple[str, Optional[str]]:
    """Resolve a usable Ollama model without assuming the default is installed."""
    if requested_model:
        return requested_model, None

    models, error = await _probe_ollama_models()
    if not models:
        return DEFAULT_MODELS["ollama"], error or "no Ollama models available"

    preferred_override = os.environ.get("RLM_OLLAMA_MODEL")
    if preferred_override and preferred_override in models:
        return preferred_override, None

    for candidate in PREFERRED_OLLAMA_MODELS:
        if candidate in models:
            return candidate, None

    return models[0], None


async def _resolve_auto_provider_candidates(
    requested_model: str | None,
    tools: list[dict] | None,
    state: Optional["RecursionState"],
) -> tuple[list[tuple[str, str]], dict[str, dict[str, Any]]]:
    """Return candidate providers/models for auto mode, in preference order."""
    recursive = bool(tools and state)
    order = AUTO_PROVIDER_ORDER_RECURSIVE if recursive else AUTO_PROVIDER_ORDER_SIMPLE
    statuses = await _collect_provider_status(include_models=True)
    candidates: list[tuple[str, str]] = []

    for provider in order:
        status = statuses.get(provider, {})
        if not status.get("available"):
            continue
        if recursive and not status.get("recursive_tool_calls", _provider_supports_recursive_tools(provider)):
            continue

        if provider == "ollama":
            model, _ = await _resolve_ollama_model(requested_model)
        else:
            model = requested_model or DEFAULT_MODELS.get(provider, "claude-haiku-4-5-20251001")

        candidates.append((provider, model))

    return candidates, statuses


def _format_auto_provider_error(statuses: dict[str, dict[str, Any]], attempted: list[str]) -> str:
    """Format an actionable auto-provider failure message."""
    availability = "; ".join(
        f"{provider}: {info.get('reason', 'unavailable')}"
        for provider, info in statuses.items()
    )
    attempted_summary = ", ".join(attempted) if attempted else "none"
    return (
        "auto provider could not find a usable backend. "
        f"Attempted: {attempted_summary}. "
        f"Status: {availability}"
    )



async def _handle_ollama_tool_calls(
    client: httpx.AsyncClient,
    ollama_url: str,
    model: str,
    messages: list[dict],
    tools: list[dict],
    state: "RecursionState",
    max_iterations: int = 5,
) -> tuple[Optional[str], Optional[str], "RecursionState"]:
    """Handle Ollama tool calls in an agent loop.

    Implements the agent loop pattern:
    1. Get tool_calls from last message
    2. Execute each tool call via TOOL_HANDLERS
    3. Append tool result as {"role": "tool", "content": result_text}
    4. Send follow-up request to continue conversation
    5. Repeat until no more tool calls or max_iterations reached
    """
    current_messages = messages.copy()
    current_state = state

    for iteration in range(max_iterations):
        # Send request to Ollama
        request_body = {
            "model": model,
            "messages": current_messages,
            "stream": False,
        }

        # Only include tools if we can still recurse
        if tools and current_state.can_recurse():
            request_body["tools"] = tools

        response = await client.post(
            f"{ollama_url}/api/chat",
            json=request_body,
        )
        response.raise_for_status()
        result = response.json()

        message = result.get("message", {})
        tool_calls = message.get("tool_calls", [])

        # If no tool calls, return the content
        if not tool_calls:
            return message.get("content", ""), None, current_state

        # Append assistant message with tool calls
        current_messages.append(message)

        # Process each tool call
        for tool_call in tool_calls:
            function_info = tool_call.get("function", {})
            tool_name = function_info.get("name", "")
            tool_args = function_info.get("arguments", {})

            # Parse arguments if they're a string
            if isinstance(tool_args, str):
                try:
                    tool_args = json.loads(tool_args)
                except json.JSONDecodeError:
                    tool_args = {}

            # Execute the tool
            handler = TOOL_HANDLERS.get(tool_name)
            if handler:
                # Descend recursion state for the tool call
                call_id = f"{tool_name}:{iteration}"
                child_state = current_state.descend(call_id)
                bounded_args = _bound_recursive_tool_args(tool_name, tool_args, current_state)

                try:
                    tool_result = await handler(bounded_args)
                    result_text = tool_result[0].text if tool_result else ""
                except Exception as e:
                    result_text = json.dumps({"error": str(e)})

                current_state = child_state
            else:
                result_text = json.dumps({"error": f"Unknown tool: {tool_name}"})

            # Append tool result
            current_messages.append({
                "role": "tool",
                "content": result_text,
            })

    # Max iterations reached - return last content or error
    last_message = current_messages[-1] if current_messages else {}
    content = last_message.get("content", "")
    if last_message.get("role") == "tool":
        # Last message was a tool result, need to get final response
        return content, "max_iterations_reached", current_state

    return content, None, current_state


async def _call_ollama(
    query: str,
    context_content: str,
    model: str,
    tools: list[dict] | None = None,
    state: Optional["RecursionState"] = None,
) -> tuple[Optional[str], Optional[str], Optional["RecursionState"]]:
    """Make a sub-call to Ollama using /api/chat endpoint.

    Returns (result, error, updated_state).
    When tools and state are provided, enables recursive tool calling.
    """
    if not HAS_HTTPX:
        return None, "httpx required for Ollama calls", state

    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")

    messages = [{"role": "user", "content": f"{query}\n\nContext:\n{context_content}"}]

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            # Build request body
            request_body = {
                "model": model,
                "messages": messages,
                "stream": False,
            }

            # Include tools if provided and recursion is allowed
            if tools and state and state.can_recurse():
                request_body["tools"] = tools

            response = await client.post(
                f"{ollama_url}/api/chat",
                json=request_body,
            )
            response.raise_for_status()
            result = response.json()

            message = result.get("message", {})
            tool_calls = message.get("tool_calls", [])

            # If there are tool calls and we have state, handle them
            if tool_calls and state:
                messages.append(message)
                return await _handle_ollama_tool_calls(
                    client, ollama_url, model, messages, tools or [], state, max_iterations=5
                )

            # No tool calls - return content directly
            return message.get("content", ""), None, state

    except Exception as e:
        return None, str(e), state


async def _call_claude_sdk(
    query: str,
    context_content: str,
    model: str,
    tools: list[dict] | None = None,
    state: Optional["RecursionState"] = None,
) -> tuple[Optional[str], Optional[str], Optional["RecursionState"]]:
    """Make a sub-call to Claude SDK (single-turn only).

    Returns (result, error, updated_state).

    Recursive tool-calling is NOT supported via claude-sdk because each call
    spawns a full Claude Code CLI subprocess.  This makes it 10-100x slower
    and far more expensive than OpenRouter/Ollama API calls.  Use
    provider="openrouter" or provider="ollama" for recursive work.
    """
    if not HAS_CLAUDE_SDK:
        return None, "claude-agent-sdk required for claude-sdk provider", state

    # Fail fast if caller expects recursion
    if tools and state and state.max_depth > 0:
        return (
            None,
            "claude-sdk does not support recursive tool-calling. "
            "Use provider='openrouter' or 'ollama' for recursive sub-queries.",
            state,
        )

    try:
        prompt = f"{query}\n\nContext:\n{context_content}"
        options = ClaudeAgentOptions(max_turns=1, model=model or None)

        texts = []
        async for message in claude_query(prompt=prompt, options=options):
            if hasattr(message, "content"):
                content = message.content
                # Extract text from TextBlock objects
                if isinstance(content, list):
                    for block in content:
                        if hasattr(block, "text"):
                            texts.append(block.text)
                elif hasattr(content, "text"):
                    texts.append(content.text)
                else:
                    texts.append(str(content))

        result = "\n".join(texts) if texts else ""
        return result, None, state
    except Exception as e:
        return None, str(e), state


async def _call_openrouter(
    query: str,
    context_content: str,
    model: str,
    tools: list[dict] | None = None,
    state: Optional["RecursionState"] = None,
) -> tuple[Optional[str], Optional[str], Optional["RecursionState"]]:
    """Make a sub-call via OpenRouter API (OpenAI-compatible)."""
    return await _call_openai_compatible(
        query=query,
        context_content=context_content,
        model=model,
        tools=tools,
        state=state,
        provider_label="openrouter",
        api_key_env="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        extra_headers={
            "HTTP-Referer": "https://github.com/alexkamysz/rlm-fork",
            "X-Title": "rlm-fork",
        },
    )


async def _call_openai_api(
    query: str,
    context_content: str,
    model: str,
    tools: list[dict] | None = None,
    state: Optional["RecursionState"] = None,
) -> tuple[Optional[str], Optional[str], Optional["RecursionState"]]:
    """Make a sub-call directly to OpenAI (no OpenRouter required)."""
    return await _call_openai_compatible(
        query=query,
        context_content=context_content,
        model=model,
        tools=tools,
        state=state,
        provider_label="openai",
        api_key_env="OPENAI_API_KEY",
    )


async def _call_openai_compatible(
    query: str,
    context_content: str,
    model: str,
    tools: list[dict] | None,
    state: Optional["RecursionState"],
    provider_label: str,
    api_key_env: str,
    base_url: str | None = None,
    extra_headers: dict[str, str] | None = None,
    max_iterations: int = 5,
) -> tuple[Optional[str], Optional[str], Optional["RecursionState"]]:
    """Run an OpenAI-compatible tool-calling loop for recursion-safe sub-queries."""
    if not HAS_OPENAI:
        return None, f"openai package required for {provider_label} provider (pip install openai)", state

    api_key = os.environ.get(api_key_env)
    if not api_key:
        return None, f"{api_key_env} environment variable not set", state

    try:
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = openai.AsyncOpenAI(**client_kwargs)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"Context:\n{context_content}"},
            {"role": "user", "content": query},
        ]

        current_state = state
        latest_content = ""

        for iteration in range(max_iterations):
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": 4096,
                "temperature": 0.2,
            }
            if extra_headers:
                kwargs["extra_headers"] = extra_headers

            can_recurse = bool(tools and current_state and current_state.can_recurse())
            if can_recurse:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            response = await client.chat.completions.create(**kwargs)
            message = response.choices[0].message
            content = message.content or ""
            if content:
                latest_content = content

            tool_calls = message.tool_calls or []
            if not tool_calls:
                return latest_content, None, current_state

            if not can_recurse:
                # Model requested tools after recursion was disabled/budget-exhausted.
                return latest_content, "recursion_budget_exhausted", current_state

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": content or "",
                "tool_calls": [],
            }
            for tool_call in tool_calls:
                assistant_message["tool_calls"].append({
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments or "{}",
                    },
                })
            messages.append(assistant_message)

            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                tool_args_raw = tool_call.function.arguments or "{}"
                tool_args: dict[str, Any]

                if isinstance(tool_args_raw, str):
                    try:
                        parsed = json.loads(tool_args_raw)
                        tool_args = parsed if isinstance(parsed, dict) else {}
                    except json.JSONDecodeError:
                        tool_args = {}
                elif isinstance(tool_args_raw, dict):
                    tool_args = tool_args_raw
                else:
                    tool_args = {}

                handler = TOOL_HANDLERS.get(tool_name)
                if handler:
                    call_id = f"{tool_name}:{iteration}"
                    child_state = current_state.descend(call_id) if current_state else None
                    bounded_args = _bound_recursive_tool_args(tool_name, tool_args, current_state)

                    try:
                        tool_result = await handler(bounded_args)
                        result_text = tool_result[0].text if tool_result else ""
                    except Exception as e:
                        result_text = json.dumps({"error": str(e)})

                    if child_state:
                        current_state = child_state
                else:
                    result_text = json.dumps({"error": f"Unknown tool: {tool_name}"})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_text,
                })

        return latest_content, "max_iterations_reached", current_state

    except Exception as e:
        return None, f"{provider_label} error: {str(e)}", state


def _get_rlm_tools_for_subcall() -> list[dict]:
    """Generate RLM tool definitions in OpenAI-compatible format for sub-calls.

    Returns a subset of tools suitable for recursive calls. This includes
    tools for context manipulation and querying, but excludes tools that
    would be redundant or dangerous in recursive contexts.
    """
    # Tools to expose in recursive sub-calls
    recursive_tool_names = {
        "rlm_chunk_context",
        "rlm_get_chunk",
        "rlm_filter_context",
        "rlm_sub_query",
        "rlm_inspect_context",
        "rlm_list_contexts",
    }

    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema,
            }
        }
        for tool in TOOL_DEFINITIONS
        if tool.name in recursive_tool_names
    ]


async def _call_explicit_provider(
    provider: str,
    model: str,
    query: str,
    context_content: str,
    tools: list[dict] | None = None,
    state: Optional["RecursionState"] = None,
) -> ProviderCallResult:
    """Route a sub-call to the appropriate provider.

    Returns (result, error, updated_state).
    When tools and state are provided, enables recursive tool calling.
    """
    if provider == "ollama":
        resolved_model, model_error = await _resolve_ollama_model(model)
        if model_error and not model:
            return ProviderCallResult(None, model_error, state, provider, resolved_model)
        result, error, updated_state = await _call_ollama(query, context_content, resolved_model, tools, state)
        return ProviderCallResult(result, error, updated_state, provider, resolved_model)
    elif provider == "claude-sdk":
        result, error, updated_state = await _call_claude_sdk(query, context_content, model, tools, state)
        return ProviderCallResult(result, error, updated_state, provider, model)
    elif provider == "openai":
        result, error, updated_state = await _call_openai_api(query, context_content, model, tools, state)
        return ProviderCallResult(result, error, updated_state, provider, model)
    elif provider == "openrouter":
        result, error, updated_state = await _call_openrouter(query, context_content, model, tools, state)
        return ProviderCallResult(result, error, updated_state, provider, model)
    else:
        return ProviderCallResult(
            None,
            f"Unknown provider: {provider}. Supported: auto, ollama, claude-sdk, openai, openrouter",
            state,
            provider,
            model,
        )


async def _make_provider_call(
    provider: str,
    model: str | None,
    query: str,
    context_content: str,
    tools: list[dict] | None = None,
    state: Optional["RecursionState"] = None,
) -> ProviderCallResult:
    """Route a sub-call to the appropriate provider, with auto fallback support."""
    if provider != "auto":
        # Ollama handles its own model resolution (probing installed models),
        # so don't pre-fill a default that may not be installed.
        if provider == "ollama":
            explicit_model = model  # let _resolve_ollama_model handle None
        else:
            explicit_model = model or DEFAULT_MODELS.get(provider, "claude-haiku-4-5-20251001")
        return await _call_explicit_provider(provider, explicit_model, query, context_content, tools, state)

    candidates, statuses = await _resolve_auto_provider_candidates(model, tools, state)
    attempted: list[str] = []

    if not candidates:
        return ProviderCallResult(
            None,
            _format_auto_provider_error(statuses, attempted),
            state,
            "auto",
            model or "",
        )

    last_error: Optional[str] = None
    current_state = state
    for candidate_provider, candidate_model in candidates:
        attempted.append(f"{candidate_provider}:{candidate_model}")
        call_result = await _call_explicit_provider(
            candidate_provider,
            candidate_model,
            query,
            context_content,
            tools,
            current_state,
        )
        if not call_result.error:
            return call_result

        last_error = f"{candidate_provider}: {call_result.error}"
        current_state = call_result.state

    return ProviderCallResult(
        None,
        _format_auto_provider_error(statuses, attempted + ([last_error] if last_error else [])),
        current_state,
        "auto",
        model or "",
    )


def _chunk_content(content: str, strategy: str, size: int) -> list[str]:
    """Chunk content using the specified strategy."""
    if strategy == "lines":
        lines = content.split("\n")
        return ["\n".join(lines[i : i + size]) for i in range(0, len(lines), size)]
    elif strategy == "chars":
        return [content[i : i + size] for i in range(0, len(content), size)]
    elif strategy == "paragraphs":
        paragraphs = re.split(r"\n\s*\n", content)
        return [
            "\n\n".join(paragraphs[i : i + size])
            for i in range(0, len(paragraphs), size)
        ]
    return []


def _detect_content_type(content: str) -> dict:
    """Detect content type from first 1000 chars. Returns type and confidence."""
    sample = content[:1000]

    # Python detection
    python_patterns = ["import ", "def ", "class ", "if __name__"]
    python_score = sum(1 for p in python_patterns if p in sample)

    # JSON detection
    json_score = 0
    stripped = sample.strip()
    if stripped.startswith(("{", "[")):
        try:
            json.loads(content[:10000])  # Try parsing first 10K
            json_score = 10
        except json.JSONDecodeError:
            json_score = 3 if stripped.startswith(("{", "[")) else 0

    # Markdown detection
    md_patterns = ["# ", "## ", "**", "```"]
    md_score = sum(1 for p in md_patterns if p in sample)

    # Log detection
    log_patterns = ["ERROR", "INFO", "DEBUG", "WARN"]
    log_score = sum(1 for p in log_patterns if p in sample)
    if re.search(r"\d{4}-\d{2}-\d{2}", sample):  # Date pattern
        log_score += 2

    # Generic code detection
    code_indicators = ["{", "}", ";", "=>", "->"]
    code_score = sum(sample.count(c) for c in code_indicators) / 10

    # Prose detection
    sentence_count = len(re.findall(r"[.!?]\s+[A-Z]", sample))
    prose_score = sentence_count

    scores = {
        "python": python_score,
        "json": json_score,
        "markdown": md_score,
        "logs": log_score,
        "code": code_score,
        "prose": prose_score,
    }

    detected_type = max(scores, key=scores.get)
    max_score = scores[detected_type]
    confidence = min(1.0, max_score / 10.0) if max_score > 0 else 0.5

    return {"type": detected_type, "confidence": round(confidence, 2)}


def _select_chunking_strategy(content_type: str) -> dict:
    """Select chunking strategy based on content type."""
    strategies = {
        "python": {"strategy": "lines", "size": 150},
        "code": {"strategy": "lines", "size": 150},
        "json": {"strategy": "chars", "size": 10000},
        "markdown": {"strategy": "paragraphs", "size": 20},
        "logs": {"strategy": "lines", "size": 500},
        "prose": {"strategy": "paragraphs", "size": 30},
    }
    return strategies.get(content_type, {"strategy": "lines", "size": 100})


def _adapt_query_for_goal(goal: str, content_type: str) -> str:
    """Generate appropriate sub-query based on goal and content type."""
    if goal.startswith("answer:"):
        return goal[7:].strip()

    goal_templates = {
        "find_bugs": {
            "python": "Identify bugs, issues, or potential errors in this Python code. Look for: syntax errors, logic errors, unhandled exceptions, type mismatches, missing imports.",
            "code": "Identify bugs, issues, or potential errors in this code. Look for: syntax errors, logic errors, unhandled exceptions.",
            "default": "Identify any errors, issues, or problems in this content.",
        },
        "summarize": {
            "python": "Summarize what this Python code does. List main functions/classes and their purpose.",
            "code": "Summarize what this code does. List main functions and their purpose.",
            "markdown": "Summarize the main points of this documentation in 2-3 sentences.",
            "prose": "Summarize the main points of this text in 2-3 sentences.",
            "logs": "Summarize the key events and errors in these logs.",
            "json": "Summarize the structure and key data in this JSON.",
            "default": "Summarize the main points of this content in 2-3 sentences.",
        },
        "extract_structure": {
            "python": "Extract the code structure: list all classes, functions, and their signatures.",
            "code": "Extract the code structure: list all functions/classes and their signatures.",
            "json": "Extract the JSON schema: list top-level keys and their types.",
            "markdown": "Extract the document structure: list all headings and hierarchy.",
            "default": "Extract the main structural elements of this content.",
        },
        "security_audit": {
            "python": "Find security vulnerabilities: SQL injection, command injection, eval(), exec(), unsafe deserialization, hardcoded secrets, path traversal.",
            "code": "Find security vulnerabilities: injection flaws, unsafe functions, hardcoded credentials.",
            "default": "Identify potential security issues or sensitive information.",
        },
    }

    templates = goal_templates.get(goal, {})
    return templates.get(content_type, templates.get("default", f"Analyze this content for: {goal}"))


# Tool definitions
TOOL_DEFINITIONS = [
    Tool(
        name="rlm_load_context",
        description="Load a large context as an external variable. Returns metadata without the content itself.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Identifier for this context"},
                "content": {"type": "string", "description": "The full context content"},
            },
            "required": ["name", "content"],
        },
    ),
    Tool(
        name="rlm_inspect_context",
        description="Inspect a loaded context - get structure info without loading full content into prompt.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Context identifier"},
                "preview_chars": {
                    "type": "integer",
                    "description": "Number of chars to preview (default 500)",
                    "default": 500,
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="rlm_chunk_context",
        description="Chunk a loaded context by strategy. Returns chunk metadata, not full content.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Context identifier"},
                "strategy": {
                    "type": "string",
                    "enum": ["lines", "chars", "paragraphs"],
                    "description": "Chunking strategy",
                    "default": "lines",
                },
                "size": {
                    "type": "integer",
                    "description": "Chunk size (lines/chars depending on strategy)",
                    "default": 100,
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="rlm_get_chunk",
        description="Get a specific chunk by index. Use after chunking to retrieve individual pieces.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Context identifier"},
                "chunk_index": {"type": "integer", "description": "Index of chunk to retrieve"},
            },
            "required": ["name", "chunk_index"],
        },
    ),
    Tool(
        name="rlm_filter_context",
        description="Filter context using regex/string operations. Creates a new filtered context.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Source context identifier"},
                "output_name": {"type": "string", "description": "Name for filtered context"},
                "pattern": {"type": "string", "description": "Regex pattern to match"},
                "mode": {
                    "type": "string",
                    "enum": ["keep", "remove"],
                    "description": "Keep or remove matching lines",
                    "default": "keep",
                },
            },
            "required": ["name", "output_name", "pattern"],
        },
    ),
    Tool(
        name="rlm_sub_query",
        description="Make a sub-LLM call on a chunk or filtered context. Core of recursive pattern. Set max_depth > 0 to allow the sub-LLM to use RLM tools for hierarchical decomposition.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Question/instruction for the sub-call"},
                "context_name": {"type": "string", "description": "Context identifier to query against"},
                "chunk_index": {"type": "integer", "description": "Optional: specific chunk index"},
                "provider": PROVIDER_SCHEMA,
                "model": {
                    "type": "string",
                    "description": "Model to use (provider-specific defaults apply)",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum recursion depth. 0 = no recursion (default), 1-5 = allow sub-queries to use RLM tools",
                    "default": 0,
                    "minimum": 0,
                    "maximum": 5,
                },
            },
            "required": ["query", "context_name"],
        },
    ),
    Tool(
        name="rlm_store_result",
        description="Store a sub-call result for later aggregation.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Result set identifier"},
                "result": {"type": "string", "description": "Result content to store"},
                "metadata": {"type": "object", "description": "Optional metadata about this result"},
            },
            "required": ["name", "result"],
        },
    ),
    Tool(
        name="rlm_get_results",
        description="Retrieve stored results for aggregation.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Result set identifier"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="rlm_list_contexts",
        description="List all loaded contexts and their metadata.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="rlm_provider_status",
        description="Report which sub-call providers are configured and usable right now, including local Ollama models when requested.",
        inputSchema={
            "type": "object",
            "properties": {
                "include_models": {
                    "type": "boolean",
                    "description": "Include discovered Ollama model names",
                    "default": False,
                },
            },
        },
    ),
    Tool(
        name="rlm_sub_query_batch",
        description="Process multiple chunks in parallel. Respects concurrency limit to manage system resources. Set max_depth > 0 to allow sub-LLMs to use RLM tools for hierarchical decomposition.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Question/instruction for each sub-call"},
                "context_name": {"type": "string", "description": "Context identifier"},
                "chunk_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of chunk indices to process",
                },
                "provider": PROVIDER_SCHEMA,
                "model": {
                    "type": "string",
                    "description": "Model to use (provider-specific defaults apply)",
                },
                "concurrency": {
                    "type": "integer",
                    "description": "Max parallel requests (default 4, max 8)",
                    "default": 4,
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum recursion depth. 0 = no recursion (default), 1-5 = allow sub-queries to use RLM tools",
                    "default": 0,
                    "minimum": 0,
                    "maximum": 5,
                },
            },
            "required": ["query", "context_name", "chunk_indices"],
        },
    ),
    Tool(
        name="rlm_auto_analyze",
        description="Automatically detect content type and analyze with optimal chunking strategy. One-step analysis for common tasks.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Context identifier"},
                "content": {"type": "string", "description": "The content to analyze"},
                "goal": {
                    "type": "string",
                    "description": "Analysis goal: 'summarize', 'find_bugs', 'extract_structure', 'security_audit', or 'answer:<your question>'",
                },
                "provider": PROVIDER_SCHEMA,
                "concurrency": {
                    "type": "integer",
                    "description": "Max parallel requests (default 4, max 8)",
                    "default": 4,
                },
            },
            "required": ["name", "content", "goal"],
        },
    ),
    Tool(
        name="rlm_exec",
        description="Execute Python code against a loaded context in a sandboxed subprocess. Set result variable for output.",
        inputSchema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute. User sets result variable for output.",
                },
                "context_name": {
                    "type": "string",
                    "description": "Name of previously loaded context",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max execution time in seconds (default 30)",
                    "default": 30,
                },
            },
            "required": ["code", "context_name"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available RLM tools."""
    return TOOL_DEFINITIONS


# Tool handlers
async def _handle_load_context(arguments: dict) -> list[TextContent]:
    """Load a large context as an external variable."""
    ctx_name = arguments["name"]
    content = arguments["content"]

    content_hash = _hash_content(content)
    meta = _context_summary(ctx_name, content, hash=content_hash, chunks=None)
    contexts[ctx_name] = {"meta": meta, "content": content}
    _save_context_to_disk(ctx_name, content, meta)

    return _text_response({
        "status": "loaded",
        "name": ctx_name,
        "length": meta["length"],
        "lines": meta["lines"],
        "hash": content_hash,
    })


async def _handle_inspect_context(arguments: dict) -> list[TextContent]:
    """Inspect a loaded context."""
    ctx_name = arguments["name"]
    preview_chars = arguments.get("preview_chars", 500)

    error = _ensure_context_loaded(ctx_name)
    if error:
        return _error_response("context_not_found", error)

    ctx = contexts[ctx_name]
    content = ctx["content"]
    chunk_meta = ctx["meta"].get("chunks")

    summary = _context_summary(
        ctx_name,
        content,
        preview=content[:preview_chars],
        has_chunks=chunk_meta is not None,
        chunk_count=len(chunk_meta) if chunk_meta else 0,
    )
    return _text_response(summary)


async def _handle_chunk_context(arguments: dict) -> list[TextContent]:
    """Chunk a loaded context by strategy."""
    ctx_name = arguments["name"]
    strategy = arguments.get("strategy", "lines")
    size = arguments.get("size", 100)

    error = _ensure_context_loaded(ctx_name)
    if error:
        return _error_response("context_not_found", error)

    content = contexts[ctx_name]["content"]
    chunks = _chunk_content(content, strategy, size)

    chunk_meta = [
        {"index": i, "length": len(chunk), "preview": chunk[:100]}
        for i, chunk in enumerate(chunks)
    ]

    contexts[ctx_name]["meta"]["chunks"] = chunk_meta
    contexts[ctx_name]["chunks"] = chunks

    chunk_dir = CHUNKS_DIR / ctx_name
    chunk_dir.mkdir(exist_ok=True)
    for i, chunk in enumerate(chunks):
        (chunk_dir / f"{i}.txt").write_text(chunk)

    return _text_response({
        "status": "chunked",
        "name": ctx_name,
        "strategy": strategy,
        "chunk_count": len(chunks),
        "chunks": chunk_meta,
    })


async def _handle_get_chunk(arguments: dict) -> list[TextContent]:
    """Get a specific chunk by index."""
    ctx_name = arguments["name"]
    chunk_index = arguments["chunk_index"]

    error = _ensure_context_loaded(ctx_name)
    if error:
        return _error_response("context_not_found", error)

    chunks = contexts[ctx_name].get("chunks")
    if not chunks:
        chunk_path = CHUNKS_DIR / ctx_name / f"{chunk_index}.txt"
        if chunk_path.exists():
            return _text_response(chunk_path.read_text())
        return _error_response(
            "context_not_chunked",
            f"Context '{ctx_name}' has not been chunked yet",
        )

    if chunk_index >= len(chunks):
        return _error_response(
            "chunk_out_of_range",
            f"Chunk index {chunk_index} out of range (max {len(chunks) - 1})",
        )

    return _text_response(chunks[chunk_index])


async def _handle_filter_context(arguments: dict) -> list[TextContent]:
    """Filter context using regex."""
    src_name = arguments["name"]
    out_name = arguments["output_name"]
    pattern = arguments["pattern"]
    mode = arguments.get("mode", "keep")

    error = _ensure_context_loaded(src_name)
    if error:
        return _error_response("context_not_found", error)

    content = contexts[src_name]["content"]
    lines = content.split("\n")
    regex = re.compile(pattern)

    if mode == "keep":
        filtered = [line for line in lines if regex.search(line)]
    else:
        filtered = [line for line in lines if not regex.search(line)]

    new_content = "\n".join(filtered)
    meta = _context_summary(
        out_name,
        new_content,
        hash=_hash_content(new_content),
        source=src_name,
        filter_pattern=pattern,
        filter_mode=mode,
        chunks=None,
    )
    contexts[out_name] = {"meta": meta, "content": new_content}
    _save_context_to_disk(out_name, new_content, meta)

    return _text_response({
        "status": "filtered",
        "name": out_name,
        "original_lines": len(lines),
        "filtered_lines": len(filtered),
        "length": len(new_content),
    })


async def _handle_sub_query(arguments: dict) -> list[TextContent]:
    """Make a sub-LLM call on a chunk or context."""
    query = arguments["query"]
    ctx_name = arguments["context_name"]
    chunk_index = arguments.get("chunk_index")
    provider = arguments.get("provider", _default_provider())
    model = arguments.get("model")
    max_depth = max(0, min(_coerce_int(arguments.get("max_depth", 0), 0), 5))

    error = _ensure_context_loaded(ctx_name)
    if error:
        return _error_response("context_not_found", error)

    if chunk_index is not None:
        chunks = contexts[ctx_name].get("chunks")
        if not chunks or chunk_index >= len(chunks):
            return _error_response(
                "chunk_not_available", f"Chunk {chunk_index} not available"
            )
        context_content = chunks[chunk_index]
    else:
        context_content = contexts[ctx_name]["content"]

    # Set up recursion state and tools if max_depth > 0
    state = RecursionState(max_depth=max_depth) if max_depth > 0 else None
    tools = _get_rlm_tools_for_subcall() if max_depth > 0 else None

    call_result = await _make_provider_call(
        provider, model, query, context_content, tools, state
    )

    if call_result.error:
        return _text_response({
            "error": "provider_error",
            "provider": provider,
            "resolved_provider": call_result.provider,
            "model": call_result.model,
            "message": call_result.error,
        })

    response = {
        "provider": provider,
        "resolved_provider": call_result.provider,
        "model": call_result.model,
        "response": call_result.result,
    }

    # Include recursion info if state was used
    if call_result.state:
        response["recursion"] = {
            "max_depth": max_depth,
            "depth_reached": call_result.state.current_depth,
            "call_trace": call_result.state.call_trace,
        }

    return _text_response(response)


async def _handle_store_result(arguments: dict) -> list[TextContent]:
    """Store a sub-call result for later aggregation."""
    result_name = arguments["name"]
    result = arguments["result"]
    metadata = arguments.get("metadata", {})

    results_file = RESULTS_DIR / f"{result_name}.jsonl"
    with open(results_file, "a") as f:
        f.write(json.dumps({"result": result, "metadata": metadata}) + "\n")

    return _text_response(f"Result stored to '{result_name}'")


async def _handle_get_results(arguments: dict) -> list[TextContent]:
    """Retrieve stored results for aggregation."""
    result_name = arguments["name"]
    results_file = RESULTS_DIR / f"{result_name}.jsonl"

    if not results_file.exists():
        return _text_response(f"No results found for '{result_name}'")

    results = [json.loads(line) for line in results_file.read_text().splitlines()]

    return _text_response({
        "name": result_name,
        "count": len(results),
        "results": results,
    })


async def _handle_list_contexts(_arguments: dict) -> list[TextContent]:
    """List all loaded contexts and their metadata."""
    ctx_list = [
        {
            "name": name,
            "length": ctx["meta"]["length"],
            "lines": ctx["meta"]["lines"],
            "chunked": ctx["meta"].get("chunks") is not None,
        }
        for name, ctx in contexts.items()
    ]

    for meta_file in CONTEXTS_DIR.glob("*.meta.json"):
        disk_name = meta_file.stem.replace(".meta", "")
        if disk_name not in contexts:
            meta = json.loads(meta_file.read_text())
            ctx_list.append({
                "name": disk_name,
                "length": meta["length"],
                "lines": meta["lines"],
                "chunked": meta.get("chunks") is not None,
                "disk_only": True,
            })

    return _text_response({"contexts": ctx_list})


async def _handle_provider_status(arguments: dict) -> list[TextContent]:
    """Report provider availability and recommended defaults."""
    include_models = bool(arguments.get("include_models", False))
    statuses = await _collect_provider_status(include_models=include_models)
    simple_candidates = [
        provider for provider in AUTO_PROVIDER_ORDER_SIMPLE
        if statuses.get(provider, {}).get("available")
    ]
    recursive_candidates = [
        provider for provider in AUTO_PROVIDER_ORDER_RECURSIVE
        if statuses.get(provider, {}).get("available") and statuses.get(provider, {}).get("recursive_tool_calls")
    ]
    return _text_response({
        "default_provider": _default_provider(),
        "recommended_simple": simple_candidates[0] if simple_candidates else None,
        "recommended_recursive": recursive_candidates[0] if recursive_candidates else None,
        "providers": statuses,
        "env": {
            "RLM_DATA_DIR": str(DATA_DIR),
            "OLLAMA_URL": os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        },
    })


async def _handle_sub_query_batch(arguments: dict) -> list[TextContent]:
    """Process multiple chunks in parallel."""
    query = arguments["query"]
    ctx_name = arguments["context_name"]
    chunk_indices = arguments["chunk_indices"]
    provider = arguments.get("provider", _default_provider())
    model = arguments.get("model")
    concurrency = max(1, min(_coerce_int(arguments.get("concurrency", 4), 4), 8))
    max_depth = max(0, min(_coerce_int(arguments.get("max_depth", 0), 0), 5))

    error = _ensure_context_loaded(ctx_name)
    if error:
        return _error_response("context_not_found", error)

    chunks = contexts[ctx_name].get("chunks")
    if not chunks:
        return _error_response(
            "context_not_chunked",
            f"Context '{ctx_name}' has not been chunked yet",
        )

    invalid_indices = [idx for idx in chunk_indices if idx >= len(chunks)]
    if invalid_indices:
        return _error_response(
            "invalid_chunk_indices",
            f"Invalid chunk indices: {invalid_indices} (max: {len(chunks) - 1})",
        )

    # Set up tools for recursion if max_depth > 0
    tools = _get_rlm_tools_for_subcall() if max_depth > 0 else None

    # Track recursion stats across the batch
    max_depth_reached = 0
    total_recursive_calls = 0

    semaphore = asyncio.Semaphore(concurrency)

    async def process_chunk(chunk_idx: int) -> dict:
        nonlocal max_depth_reached, total_recursive_calls

        async with semaphore:
            chunk_content = chunks[chunk_idx]

            # Create fresh state for each chunk to avoid cross-contamination
            state = RecursionState(max_depth=max_depth) if max_depth > 0 else None

            call_result = await _make_provider_call(
                provider, model, query, chunk_content, tools, state
            )

            if call_result.error:
                return {
                    "chunk_index": chunk_idx,
                    "error": "provider_error",
                    "provider": provider,
                    "resolved_provider": call_result.provider,
                    "model": call_result.model,
                    "message": call_result.error,
                }

            chunk_result = {
                "chunk_index": chunk_idx,
                "response": call_result.result,
                "provider": provider,
                "resolved_provider": call_result.provider,
                "model": call_result.model,
            }

            # Track recursion stats
            if call_result.state:
                chunk_result["recursion"] = {
                    "depth_reached": call_result.state.current_depth,
                    "call_trace": call_result.state.call_trace,
                }
                max_depth_reached = max(max_depth_reached, call_result.state.current_depth)
                total_recursive_calls += len(call_result.state.call_trace)

            return chunk_result

    results = await asyncio.gather(*[process_chunk(idx) for idx in chunk_indices])

    successful = sum(1 for r in results if "response" in r)
    failed = len(results) - successful

    response = {
        "status": "completed",
        "total_chunks": len(chunk_indices),
        "successful": successful,
        "failed": failed,
        "concurrency": concurrency,
        "results": results,
    }

    # Include batch-level recursion stats if recursion was enabled
    if max_depth > 0:
        response["recursion"] = {
            "max_depth": max_depth,
            "max_depth_reached": max_depth_reached,
            "total_recursive_calls": total_recursive_calls,
        }

    return _text_response(response)


async def _handle_auto_analyze(arguments: dict) -> list[TextContent]:
    """Automatically detect content type and analyze with optimal strategy."""
    ctx_name = arguments["name"]
    content = arguments["content"]
    goal = arguments["goal"]
    provider = arguments.get("provider", _default_provider())
    concurrency = max(1, min(_coerce_int(arguments.get("concurrency", 4), 4), 8))

    # Load the content
    await _handle_load_context({"name": ctx_name, "content": content})

    # Detect content type
    detection = _detect_content_type(content)
    detected_type = detection["type"]
    confidence = detection["confidence"]

    # Select chunking strategy
    strategy_config = _select_chunking_strategy(detected_type)

    # Chunk the content
    chunk_result = await _handle_chunk_context({
        "name": ctx_name,
        "strategy": strategy_config["strategy"],
        "size": strategy_config["size"],
    })
    chunk_data = json.loads(chunk_result[0].text)
    chunk_count = chunk_data["chunk_count"]

    # Sample if too many chunks (max 20)
    chunk_indices = list(range(chunk_count))
    sampled = False
    if chunk_count > 20:
        step = max(1, chunk_count // 20)
        chunk_indices = list(range(0, chunk_count, step))[:20]
        sampled = True

    # Adapt query for goal and content type
    adapted_query = _adapt_query_for_goal(goal, detected_type)

    # Run batch query
    batch_result = await _handle_sub_query_batch({
        "query": adapted_query,
        "context_name": ctx_name,
        "chunk_indices": chunk_indices,
        "provider": provider,
        "concurrency": concurrency,
    })
    batch_data = json.loads(batch_result[0].text)

    return _text_response({
        "status": "completed",
        "detected_type": detected_type,
        "confidence": confidence,
        "strategy": strategy_config,
        "chunk_count": chunk_count,
        "chunks_analyzed": len(chunk_indices),
        "sampled": sampled,
        "goal": goal,
        "adapted_query": adapted_query,
        "provider": provider,
        "successful": batch_data["successful"],
        "failed": batch_data["failed"],
        "results": batch_data["results"],
    })


async def _handle_exec(arguments: dict) -> list[TextContent]:
    """Execute Python code against a loaded context in a sandboxed subprocess."""
    code = arguments["code"]
    ctx_name = arguments["context_name"]
    timeout = arguments.get("timeout", 30)

    # SECURITY: Check for blocked imports before execution
    blocked_error = _check_blocked_imports(code)
    if blocked_error:
        return _error_response("blocked_import", f"Security violation: {blocked_error}")

    # Ensure context is loaded
    error = _ensure_context_loaded(ctx_name)
    if error:
        return _error_response("context_not_found", error)

    content = contexts[ctx_name]["content"]

    # Create a temporary Python file with the execution environment
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        temp_file = f.name
        # Write the execution wrapper
        f.write("""
import sys
import json
import re
import collections

# Inject context as read-only variable
context = sys.stdin.read()

# User code execution
result = None
try:
""")
        # Indent user code
        for line in code.split("\n"):
            f.write(f"    {line}\n")

        # Capture result
        f.write("""
    # Output result
    if result is not None:
        print("__RESULT_START__")
        print(json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result))
        print("__RESULT_END__")
except Exception as e:
    print(f"__ERROR__: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
""")

    try:
        # Run the subprocess with minimal environment (no shell=True for security)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }

        process = subprocess.run(
            [sys.executable, temp_file],
            input=content,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

        # Parse output
        stdout = process.stdout
        stderr = process.stderr
        return_code = process.returncode

        # Extract result
        result = None
        if "__RESULT_START__" in stdout and "__RESULT_END__" in stdout:
            result_start = stdout.index("__RESULT_START__") + len("__RESULT_START__\n")
            result_end = stdout.index("__RESULT_END__")
            result_str = stdout[result_start:result_end].strip()
            try:
                result = json.loads(result_str)
            except json.JSONDecodeError:
                result = result_str

            # Clean stdout
            stdout = stdout[:stdout.index("__RESULT_START__")].strip()

        return _text_response({
            "result": result,
            "stdout": stdout,
            "stderr": stderr,
            "return_code": return_code,
            "timed_out": False,
        })

    except subprocess.TimeoutExpired:
        return _text_response({
            "result": None,
            "stdout": "",
            "stderr": f"Execution timed out after {timeout} seconds",
            "return_code": -1,
            "timed_out": True,
        })
    except Exception as e:
        return _error_response("execution_error", str(e))
    finally:
        # Clean up temp file
        try:
            os.unlink(temp_file)
        except Exception:
            pass


# Tool dispatch table
TOOL_HANDLERS = {
    "rlm_load_context": _handle_load_context,
    "rlm_inspect_context": _handle_inspect_context,
    "rlm_chunk_context": _handle_chunk_context,
    "rlm_get_chunk": _handle_get_chunk,
    "rlm_filter_context": _handle_filter_context,
    "rlm_sub_query": _handle_sub_query,
    "rlm_store_result": _handle_store_result,
    "rlm_get_results": _handle_get_results,
    "rlm_list_contexts": _handle_list_contexts,
    "rlm_provider_status": _handle_provider_status,
    "rlm_sub_query_batch": _handle_sub_query_batch,
    "rlm_auto_analyze": _handle_auto_analyze,
    "rlm_exec": _handle_exec,
}


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route tool calls to their handlers."""
    handler = TOOL_HANDLERS.get(name)
    if handler:
        return await handler(arguments)
    return _text_response(f"Unknown tool: {name}")


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())

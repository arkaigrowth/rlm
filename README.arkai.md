# RLM Fork for arkai Integration

> **Fork of**: [richardwhiteii/rlm](https://github.com/richardwhiteii/rlm)
> **Purpose**: MCP server for massive context analysis in arkai/Claude Code
> **Status**: Phase 0 Complete, Sandbox Hardening Pending

---

## What This Is

This is a forked version of richardwhiteii/rlm with additions for arkai integration:

1. **OpenRouter Provider** - Route sub-queries to GPT-4o-mini, GLM-4.7, Gemini via OpenRouter API
2. **Bash Wrapper** - `run_rlm.sh` for reliable Claude Code MCP startup
3. **arkai Integration** - Designed to work as a skill/sidecar for the arkai AIOS

---

## Changes from Upstream

| File | Change |
|------|--------|
| `src/rlm_mcp_server.py` | +`_call_openrouter()` function (lines 350-420) |
| `src/rlm_mcp_server.py` | +`openrouter` in PROVIDER_SCHEMA enum (line 152) |
| `src/rlm_mcp_server.py` | +`openrouter` in DEFAULT_MODELS dict |
| `pyproject.toml` | +`openai>=1.0.0` dependency |
| `run_rlm.sh` | NEW - bash wrapper for MCP startup |
| `src/__init__.py` | NEW - missing module init |

---

## Setup

### 1. Install Dependencies

```bash
cd ~/AI/rlm-fork
uv sync
```

### 2. Configure Environment

Set these environment variables (or they'll use defaults):

```bash
export RLM_DATA_DIR="$HOME/.rlm-data"
export OLLAMA_URL="http://localhost:11434"
export OPENROUTER_API_KEY="sk-or-..."  # Required for openrouter provider
```

### 3. Add to Claude Code

The MCP server was added via:

```bash
claude mcp add rlm ~/AI/rlm-fork/run_rlm.sh
```

Or manually in `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "rlm": {
      "command": "/Users/alexkamysz/AI/rlm-fork/run_rlm.sh",
      "args": []
    }
  }
}
```

### 4. Verify Connection

```bash
claude mcp list
# Should show: rlm: ... - ✓ Connected
```

---

## Usage

### Available Tools

| Tool | Purpose |
|------|---------|
| `rlm_load_context` | Load files as external variables |
| `rlm_filter_context` | Regex-based filtering |
| `rlm_chunk_context` | Split by lines/chars/paragraphs |
| `rlm_get_chunk` | Retrieve specific chunk |
| `rlm_sub_query` | LLM call on context (supports openrouter) |
| `rlm_sub_query_batch` | Parallel LLM calls |
| `rlm_exec` | Sandboxed Python execution |
| `rlm_auto_analyze` | One-step intelligent analysis |

### Provider Options

```python
# Claude SDK (default)
rlm_sub_query(query="...", context_name="...", provider="claude-sdk")

# OpenRouter (GPT, Gemini, GLM)
rlm_sub_query(query="...", context_name="...", provider="openrouter", model="openai/gpt-4o-mini")

# Ollama (local)
rlm_sub_query(query="...", context_name="...", provider="ollama", model="gemma3:12b")
```

---

## arkai Integration

This RLM server is designed as a **skill/sidecar** for arkai:

- **arkai does NOT call LLMs directly** - RLM handles LLM calls
- **RLM outputs artifacts** - findings.json, evidence_candidates.jsonl
- **arkai validates evidence** - resolves spans, places in library/

See: `~/AI/arkai/docs/RLM_INTEGRATION.md` for full architecture.

---

## Known Issues

### Sandbox Not Hardened (CRITICAL)

The `rlm_exec` sandbox currently allows dangerous imports:
- `import os` - ALLOWED (should be blocked)
- `import socket` - ALLOWED (should be blocked)

**TODO**: Add import blocklist before production use.

### rlm_exec stdin

Context not being passed to Python subprocess stdin correctly.

---

## Data Storage

```
~/.rlm-data/
├── contexts/     # Loaded context files
├── chunks/       # Cached chunks
└── results/      # Aggregated results
```

---

## References

- arkai repo: `~/AI/arkai/`
- arkai RLM design doc: `~/AI/arkai/docs/RLM_INTEGRATION.md`
- MIT RLM paper: https://arxiv.org/html/2512.24601v1
- Upstream repo: https://github.com/richardwhiteii/rlm

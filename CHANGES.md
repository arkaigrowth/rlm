# Fork Changes

> Fork of [richardwhiteii/rlm](https://github.com/richardwhiteii/rlm) for arkai integration

## 2026-01-18: OpenRouter Provider + arkai Integration

### Added
- **OpenRouter provider** in `src/rlm_mcp_server.py`:
  - `_call_openrouter()` async function (lines 350-420)
  - Supports all OpenRouter models: GPT, Gemini, GLM, etc.
  - Uses `openai` SDK with `base_url="https://openrouter.ai/api/v1"`

- **PROVIDER_SCHEMA update** (line 152):
  - Added `"openrouter"` to enum: `["ollama", "claude-sdk", "openrouter"]`

- **DEFAULT_MODELS update**:
  - Added `"openrouter": "openai/gpt-4o-mini"`

- **Dependencies** in `pyproject.toml`:
  - Added `openai>=1.0.0`

- **New files**:
  - `run_rlm.sh` - Bash wrapper for Claude Code MCP startup
  - `src/__init__.py` - Module init (was missing)
  - `README.arkai.md` - arkai-specific documentation
  - `CHANGES.md` - This file

### Environment Variables

```bash
OPENROUTER_API_KEY  # Required for openrouter provider
RLM_DATA_DIR        # Default: /tmp/rlm
OLLAMA_URL          # Default: http://localhost:11434
```

### TODO
- [ ] Sandbox hardening (block os/socket/subprocess imports)
- [ ] Fix rlm_exec stdin context passing
- [ ] Add budget/cost tracking middleware

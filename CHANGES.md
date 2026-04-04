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

## 2026-04-03: Keychain Contract + GPT-5.4 Mini Hosted Defaults

### Added
- Shared launcher helper: `scripts/lib/keychain_env.sh`
- Shared Keychain contract doc: `docs/keychain-env-contract.md`
- Keychain export helpers for wrapper scripts:
  - `keychain_export_secret_optional ENV_VAR KEY...`
  - `keychain_export_secret ENV_VAR KEY...`

### Changed
- `run_rlm.sh` now uses the shared helper instead of bespoke lookup logic.
- Hosted defaults now target the GPT-5.4 mini family:
  - `openai`: `gpt-5.4-mini`
  - `openrouter`: `openai/gpt-5.4-mini`
- `provider="auto"` now prefers:
  - simple calls: `openrouter -> ollama -> openai -> claude-sdk`
  - recursive calls: `openrouter -> ollama -> openai`
- Recursive auto-selection now skips providers that do not support recursive tool calls.

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

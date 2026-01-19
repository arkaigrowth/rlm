#!/bin/bash
# RLM MCP Server wrapper for Claude Code
export RLM_DATA_DIR="${RLM_DATA_DIR:-/Users/alexkamysz/.rlm-data}"
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export OPENROUTER_API_KEY="${OPENROUTER_API_KEY}"

cd /Users/alexkamysz/AI/rlm-fork
exec /Users/alexkamysz/Library/Python/3.9/bin/uv run python -m src.rlm_mcp_server

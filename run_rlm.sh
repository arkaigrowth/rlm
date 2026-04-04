#!/bin/bash
# RLM MCP Server wrapper for Claude Code
#
# Usage: Add to your Claude Code MCP config:
#   claude mcp add rlm /path/to/rlm/run_rlm.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEYCHAIN_PATH="${RLM_KEYCHAIN_PATH:-$HOME/Library/Keychains/login.keychain-db}"
KEY_OPENROUTER_PRIMARY="${RLM_KEYCHAIN_OPENROUTER:-rlm-openrouter}"
KEY_OPENROUTER_LEGACY="${RLM_KEYCHAIN_OPENROUTER_LEGACY:-openclaw-openrouter}"
LOCAL_KEYCHAIN_HELPER="$SCRIPT_DIR/scripts/lib/keychain_env.sh"
KEYCHAIN_HELPER_PATH="${KEYCHAIN_HELPER_PATH:-${XDG_DATA_HOME:-$HOME/.local/share}/agent-tools/keychain_env.sh}"
if [ -f "$KEYCHAIN_HELPER_PATH" ]; then
  source "$KEYCHAIN_HELPER_PATH"
else
  source "$LOCAL_KEYCHAIN_HELPER"
fi

export RLM_DATA_DIR="${RLM_DATA_DIR:-$HOME/.rlm-data}"
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export RLM_DEFAULT_PROVIDER="${RLM_DEFAULT_PROVIDER:-auto}"

keychain_openrouter_key=""
if keychain_export_secret_optional OPENROUTER_API_KEY "$KEY_OPENROUTER_PRIMARY" "$KEY_OPENROUTER_LEGACY"; then
  if [ "${RLM_LOG_STARTUP:-0}" = "1" ]; then
    if [ "${KEYCHAIN_LAST_SOURCE:-missing}" = "keychain" ]; then
      echo "INFO: Loaded OPENROUTER_API_KEY from Keychain item '${KEYCHAIN_LAST_MATCHED_KEY:-unknown}'." >&2
    elif [ "${KEYCHAIN_LAST_SOURCE:-missing}" = "env" ]; then
      echo "INFO: Using inherited OPENROUTER_API_KEY because no matching Keychain item was found." >&2
    fi
  fi
elif [ "${RLM_DEFAULT_PROVIDER}" = "openrouter" ]; then
  echo "WARN: No OpenRouter key found in Keychain items '$KEY_OPENROUTER_PRIMARY' / '$KEY_OPENROUTER_LEGACY' or inherited environment." >&2
fi

cd "$SCRIPT_DIR"
exec uv run python -m src.rlm_mcp_server

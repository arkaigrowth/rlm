#!/usr/bin/env bash
# Install or refresh the machine-level shared Keychain helper from this repo.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SOURCE_HELPER="$REPO_ROOT/scripts/lib/keychain_env.sh"
TARGET_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/agent-tools"
TARGET_HELPER="$TARGET_DIR/keychain_env.sh"
TARGET_README="$TARGET_DIR/README.md"

usage() {
  cat <<'EOF'
Usage: scripts/sync_shared_keychain_helper.sh [--check]

Installs or refreshes the canonical shared helper at:
  ${XDG_DATA_HOME:-$HOME/.local/share}/agent-tools/keychain_env.sh

Options:
  --check   Verify that the installed helper matches the repo copy.
EOF
}

ensure_source_exists() {
  if [ ! -f "$SOURCE_HELPER" ]; then
    echo "ERROR: source helper not found at $SOURCE_HELPER" >&2
    exit 1
  fi
}

write_readme() {
  cat >"$TARGET_README" <<'EOF'
# agent-tools

This directory contains machine-level helper files shared across local AI repos and launchers.

## keychain_env.sh

Canonical shared helper for loading secrets from macOS Keychain into wrapper-launched processes.

Default path:
- `${XDG_DATA_HOME:-$HOME/.local/share}/agent-tools/keychain_env.sh`

Projects should prefer this file first, then fall back to their vendored repo copy if it is missing.

Why this exists:
- one shared implementation across projects
- no secrets in shell dotfiles or launchd plists
- wrapper scripts stay deterministic for both CLI and launchd paths
EOF
}

check_mode() {
  ensure_source_exists
  if [ ! -f "$TARGET_HELPER" ]; then
    echo "missing: $TARGET_HELPER"
    exit 1
  fi

  if cmp -s "$SOURCE_HELPER" "$TARGET_HELPER"; then
    echo "ok: shared helper matches repo source"
    exit 0
  fi

  echo "drift: shared helper differs from repo source" >&2
  exit 1
}

main() {
  case "${1:-}" in
    "")
      ;;
    --check)
      check_mode
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac

  ensure_source_exists
  mkdir -p "$TARGET_DIR"
  install -m 0644 "$SOURCE_HELPER" "$TARGET_HELPER"
  write_readme
  echo "synced: $TARGET_HELPER"
}

main "$@"

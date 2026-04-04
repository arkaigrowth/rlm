#!/usr/bin/env bash
# Shared macOS Keychain helper for launcher scripts.
#
# Contract:
# - Secrets live in macOS Keychain as Generic Password items.
# - Callers set KEYCHAIN_PATH before sourcing if they want a custom keychain.
# - Required lookups fail fast with a create hint.
# - Optional lookups stay silent and return non-zero when nothing is found.
# - Launchers should treat this helper as the only Keychain integration layer.

if [[ -n "${_KEYCHAIN_ENV_HELPER_LOADED:-}" ]]; then
  return 0
fi
_KEYCHAIN_ENV_HELPER_LOADED=1

KEYCHAIN_PATH="${KEYCHAIN_PATH:-$HOME/Library/Keychains/login.keychain-db}"
KEYCHAIN_LAST_MATCHED_KEY=""
KEYCHAIN_LAST_SOURCE=""

_keychain_try_lookup() {
  local mode="$1"
  local key="$2"
  local value

  if value="$(security find-generic-password -w "$mode" "$key" "$KEYCHAIN_PATH" 2>/dev/null)"; then
    printf '%s' "$value"
    return 0
  fi
  if value="$(security find-generic-password -w "$mode" "$key" 2>/dev/null)"; then
    printf '%s' "$value"
    return 0
  fi

  return 1
}

keychain_get_secret_optional() {
  local key="$1"
  local value
  KEYCHAIN_LAST_MATCHED_KEY=""
  KEYCHAIN_LAST_SOURCE="missing"

  if value="$(_keychain_try_lookup -s "$key")"; then
    KEYCHAIN_LAST_MATCHED_KEY="$key"
    KEYCHAIN_LAST_SOURCE="keychain"
    printf '%s' "$value"
    return 0
  fi
  if value="$(_keychain_try_lookup -l "$key")"; then
    KEYCHAIN_LAST_MATCHED_KEY="$key"
    KEYCHAIN_LAST_SOURCE="keychain"
    printf '%s' "$value"
    return 0
  fi

  return 1
}

keychain_first_secret_optional() {
  local key
  local value
  for key in "$@"; do
    [[ -n "$key" ]] || continue
    if value="$(keychain_get_secret_optional "$key")"; then
      printf '%s' "$value"
      return 0
    fi
  done
  KEYCHAIN_LAST_MATCHED_KEY=""
  KEYCHAIN_LAST_SOURCE="missing"
  return 1
}

keychain_print_create_hint() {
  local key="$1"
  {
    echo "ERROR: Missing Keychain entry: $key" >&2
    echo "Create it with:" >&2
    echo "  security add-generic-password -a \"$USER\" -s \"$key\" -w \"...\" -U \"$KEYCHAIN_PATH\"" >&2
  } >&2
}

keychain_get_secret() {
  local key="$1"
  local value
  if value="$(keychain_get_secret_optional "$key")"; then
    printf '%s' "$value"
    return 0
  fi
  keychain_print_create_hint "$key"
  return 1
}

keychain_export_secret_optional() {
  local env_var="$1"
  shift
  local value=""

  if [[ $# -eq 0 ]]; then
    echo "ERROR: keychain_export_secret_optional requires an env var name and at least one Keychain item name." >&2
    return 2
  fi

  if value="$(keychain_first_secret_optional "$@")"; then
    export "$env_var=$value"
    return 0
  fi

  if [[ -n "${!env_var:-}" ]]; then
    KEYCHAIN_LAST_MATCHED_KEY=""
    KEYCHAIN_LAST_SOURCE="env"
    return 0
  fi

  KEYCHAIN_LAST_SOURCE="missing"
  return 1
}

keychain_export_secret() {
  local env_var="$1"
  shift

  if keychain_export_secret_optional "$env_var" "$@"; then
    return 0
  fi

  keychain_print_create_hint "${1:-$env_var}"
  return 1
}

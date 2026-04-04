# Keychain Env Contract

Use this contract when a launcher, daemon, or MCP wrapper needs secrets from macOS Keychain.

## Goals

- Keep API keys out of shell dotfiles and repo config.
- Make CLI and launchd paths behave the same way.
- Fail fast for required secrets.
- Keep agents ignorant of Keychain internals. Agents should only see environment variables.

## Storage Rules

- Store secrets as **Generic Password** items in macOS Keychain.
- Use a stable service/name such as `rlm-openrouter`.
- Prefer the login keychain unless a custom keychain path is intentionally configured.

## Canonical Install Location

Use one machine-level helper install so multiple repos can share the same implementation:

- `KEYCHAIN_HELPER_PATH`
  - optional override for unusual setups
- default canonical path:
  - `${XDG_DATA_HOME:-$HOME/.local/share}/agent-tools/keychain_env.sh`

Keep a repo-local copy under `scripts/lib/keychain_env.sh` only as a fallback for portability.
The maintained source of truth in this repo is `scripts/lib/keychain_env.sh`.
Install or refresh the machine-level copy with `scripts/sync_shared_keychain_helper.sh`.

## Helper API

Source:

```bash
source /path/to/scripts/lib/keychain_env.sh
```

Functions:

- `keychain_get_secret KEY`
  - required lookup
  - prints secret to stdout
  - returns non-zero and prints a create hint if missing

- `keychain_get_secret_optional KEY`
  - optional lookup
  - prints secret to stdout if found
  - returns non-zero and stays silent if missing

- `keychain_first_secret_optional KEY...`
  - tries multiple Keychain item names in order
  - prints the first match to stdout
  - returns non-zero if none exist

- `keychain_print_create_hint KEY`
  - prints a safe create/update hint to stderr

- `keychain_export_secret_optional ENV_VAR KEY...`
  - tries Keychain item names in order
  - exports the first match into `ENV_VAR`
  - if no Keychain item matches but `ENV_VAR` is already set, keeps the inherited env
  - sets `KEYCHAIN_LAST_SOURCE` to `keychain`, `env`, or `missing`

- `keychain_export_secret ENV_VAR KEY...`
  - required version of the export helper
  - fails fast with a create hint if neither Keychain nor env provides a value

State:

- `KEYCHAIN_PATH`
  - optional override before sourcing
  - defaults to `~/Library/Keychains/login.keychain-db`

- `KEYCHAIN_LAST_MATCHED_KEY`
  - set by successful lookups
  - useful for startup logging without printing secrets

- `KEYCHAIN_LAST_SOURCE`
  - one of `keychain`, `env`, or `missing`
  - useful for startup logging and health checks

## Usage Pattern

Preferred for wrappers and launchd jobs:

```bash
KEYCHAIN_PATH="${MY_KEYCHAIN_PATH:-$HOME/Library/Keychains/login.keychain-db}"
KEYCHAIN_HELPER_PATH="${KEYCHAIN_HELPER_PATH:-${XDG_DATA_HOME:-$HOME/.local/share}/agent-tools/keychain_env.sh}"
LOCAL_KEYCHAIN_HELPER="/path/to/repo/scripts/lib/keychain_env.sh"

if [ -f "$KEYCHAIN_HELPER_PATH" ]; then
  source "$KEYCHAIN_HELPER_PATH"
else
  source "$LOCAL_KEYCHAIN_HELPER"
fi

if keychain_export_secret_optional OPENROUTER_API_KEY "rlm-openrouter" "openclaw-openrouter"; then
  case "${KEYCHAIN_LAST_SOURCE:-missing}" in
    keychain) echo "INFO: OpenRouter key loaded from ${KEYCHAIN_LAST_MATCHED_KEY}" >&2 ;;
    env) echo "INFO: OpenRouter key already present in environment" >&2 ;;
  esac
else
  echo "WARN: no OpenRouter key found" >&2
fi

exec my-agent
```

## Runtime Guidance

- For CLI tools, a wrapper script is still the cleanest pattern. It keeps the shell profile out of the trust path.
- For launchd services, use a wrapper script. `launchd` does not source your shell config, so shell exports are not reliable.
- Avoid writing secrets back to `.env` files unless there is no safer option.
- Prefer the canonical shared helper path for all wrappers so updates propagate across repos.
- Keep the vendored fallback copy so a repo still works if the shared helper has not been installed yet.

## CLI vs Launchd

There are two common runtime contexts:

- Interactive CLI: the tool starts from your terminal session.
- Background service: macOS `launchd` starts the process at login or boot.

Only the first one sees your shell state. Both should go through the same wrapper so behavior is deterministic.

## Agent Guidance

Tell agents:

- secrets are injected by the launcher as env vars
- do not ask for raw keys
- fail fast and name the expected Keychain item if a required env var is missing

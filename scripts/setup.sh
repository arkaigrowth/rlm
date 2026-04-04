#!/bin/bash
# RLM Setup — Configure MCP server for Claude Code and/or Claude Desktop.
#
# Usage:
#   ./scripts/setup.sh              # interactive — asks which targets to configure
#   ./scripts/setup.sh --code       # Claude Code only
#   ./scripts/setup.sh --desktop    # Claude Desktop only
#   ./scripts/setup.sh --all        # both
#   ./scripts/setup.sh --check      # show current status without changing anything

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RLM_WRAPPER="$REPO_DIR/run_rlm.sh"

CC_SETTINGS="$HOME/.claude/settings.json"
CD_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
SKILL_LINK="$HOME/.claude/skills/rlm"
SKILL_TARGET="$REPO_DIR/skills/rlm"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "${RED}✗${NC} $*"; }

# ── Dependency check ──────────────────────────────────────────────────
check_deps() {
    local missing=()
    command -v uv   >/dev/null 2>&1 || missing+=("uv")
    command -v python3 >/dev/null 2>&1 || missing+=("python3")
    if [ ${#missing[@]} -gt 0 ]; then
        fail "Missing dependencies: ${missing[*]}"
        exit 1
    fi
}

# ── JSON helpers (python3 — no jq dependency) ─────────────────────────
json_has_key() {
    # json_has_key <file> <dot.path>   e.g. json_has_key file.json mcpServers.rlm
    python3 -c "
import json, sys
data = json.load(open(sys.argv[1]))
keys = sys.argv[2].split('.')
try:
    for k in keys:
        data = data[k]
    print('yes')
except (KeyError, TypeError):
    print('no')
" "$1" "$2"
}

json_add_mcp_server() {
    # json_add_mcp_server <file> <server_key> <wrapper_path> <servers_parent_key>
    python3 -c "
import json, sys
path, key, wrapper, parent = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(path) as f:
    data = json.load(f)
if parent not in data:
    data[parent] = {}
data[parent][key] = {'command': wrapper, 'args': []}
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
" "$1" "$2" "$3" "$4"
}

# ── Status check ──────────────────────────────────────────────────────
check_status() {
    echo ""
    info "RLM Setup Status"
    echo "  Repo:    $REPO_DIR"
    echo "  Wrapper: $RLM_WRAPPER"
    echo ""

    # Wrapper exists?
    if [ -x "$RLM_WRAPPER" ]; then
        ok "run_rlm.sh exists and is executable"
    elif [ -f "$RLM_WRAPPER" ]; then
        warn "run_rlm.sh exists but is not executable"
    else
        fail "run_rlm.sh not found at $RLM_WRAPPER"
    fi

    # uv sync
    if [ -f "$REPO_DIR/.venv/bin/python" ]; then
        ok "Python venv exists"
    else
        warn "Python venv missing — run 'uv sync' first"
    fi

    # Claude Code
    if [ -f "$CC_SETTINGS" ]; then
        if [ "$(json_has_key "$CC_SETTINGS" "mcpServers.rlm")" = "yes" ]; then
            ok "Claude Code: RLM MCP configured"
        else
            warn "Claude Code: RLM MCP not configured"
        fi
    else
        warn "Claude Code: settings.json not found"
    fi

    # Claude Desktop
    if [ -f "$CD_CONFIG" ]; then
        if [ "$(json_has_key "$CD_CONFIG" "mcpServers.rlm")" = "yes" ]; then
            ok "Claude Desktop: RLM MCP configured"
        else
            warn "Claude Desktop: RLM MCP not configured"
        fi
    else
        info "Claude Desktop: config not found (not installed?)"
    fi

    # Skill symlink
    if [ -L "$SKILL_LINK" ]; then
        local target
        target="$(readlink "$SKILL_LINK")"
        if [ "$target" = "$SKILL_TARGET" ]; then
            ok "Skill symlink: correct → $target"
        else
            warn "Skill symlink: points to $target (expected $SKILL_TARGET)"
        fi
    elif [ -d "$SKILL_LINK" ]; then
        warn "Skill directory exists but is NOT a symlink (will drift)"
    else
        warn "Skill not linked at $SKILL_LINK"
    fi

    echo ""
}

# ── Configure targets ─────────────────────────────────────────────────
setup_venv() {
    if [ ! -f "$REPO_DIR/.venv/bin/python" ]; then
        info "Running uv sync..."
        (cd "$REPO_DIR" && uv sync)
        ok "Dependencies installed"
    fi
}

setup_wrapper() {
    if [ ! -x "$RLM_WRAPPER" ]; then
        chmod +x "$RLM_WRAPPER"
        ok "Made run_rlm.sh executable"
    fi
}

setup_skill_symlink() {
    if [ -L "$SKILL_LINK" ]; then
        local target
        target="$(readlink "$SKILL_LINK")"
        if [ "$target" = "$SKILL_TARGET" ]; then
            return  # already correct
        fi
        rm "$SKILL_LINK"
    elif [ -d "$SKILL_LINK" ]; then
        warn "Replacing stale skill copy with symlink"
        rm -rf "$SKILL_LINK"
    fi

    mkdir -p "$(dirname "$SKILL_LINK")"
    ln -s "$SKILL_TARGET" "$SKILL_LINK"
    ok "Skill symlinked: $SKILL_LINK → $SKILL_TARGET"
}

setup_claude_code() {
    if [ ! -f "$CC_SETTINGS" ]; then
        warn "Claude Code settings.json not found — skipping"
        return
    fi

    if [ "$(json_has_key "$CC_SETTINGS" "mcpServers.rlm")" = "yes" ]; then
        ok "Claude Code: already configured"
    else
        json_add_mcp_server "$CC_SETTINGS" "rlm" "$RLM_WRAPPER" "mcpServers"
        ok "Claude Code: RLM MCP server added"
    fi
}

setup_claude_desktop() {
    if [ ! -f "$CD_CONFIG" ]; then
        warn "Claude Desktop config not found — skipping"
        return
    fi

    if [ "$(json_has_key "$CD_CONFIG" "mcpServers.rlm")" = "yes" ]; then
        ok "Claude Desktop: already configured"
    else
        json_add_mcp_server "$CD_CONFIG" "rlm" "$RLM_WRAPPER" "mcpServers"
        ok "Claude Desktop: RLM MCP server added"
        warn "Restart Claude Desktop to pick up the new MCP server"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────
main() {
    local do_code=false do_desktop=false do_check=false

    if [ $# -eq 0 ]; then
        # Interactive
        echo ""
        info "RLM Setup"
        echo ""
        echo "  1) Claude Code only"
        echo "  2) Claude Desktop only"
        echo "  3) Both"
        echo "  4) Check status"
        echo ""
        read -rp "Choose [1-4]: " choice
        case "$choice" in
            1) do_code=true ;;
            2) do_desktop=true ;;
            3) do_code=true; do_desktop=true ;;
            4) do_check=true ;;
            *) fail "Invalid choice"; exit 1 ;;
        esac
    else
        for arg in "$@"; do
            case "$arg" in
                --code)    do_code=true ;;
                --desktop) do_desktop=true ;;
                --all)     do_code=true; do_desktop=true ;;
                --check)   do_check=true ;;
                -h|--help)
                    echo "Usage: $0 [--code] [--desktop] [--all] [--check]"
                    exit 0
                    ;;
                *) fail "Unknown option: $arg"; exit 1 ;;
            esac
        done
    fi

    if $do_check; then
        check_status
        exit 0
    fi

    check_deps
    setup_venv
    setup_wrapper
    setup_skill_symlink

    if $do_code; then
        setup_claude_code
    fi
    if $do_desktop; then
        setup_claude_desktop
    fi

    echo ""
    check_status
}

main "$@"

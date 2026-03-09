#!/usr/bin/env bash
# FleetCommandAV addon manager
# Usage: ./addon.sh <command> [args]
#
# Commands:
#   sync [name]     Fetch/update addons from their declared sources
#   status          Show installed vs declared state, flag missing deps
#   list            List all declared addons with source and status
#   add <source>    Add a new addon to the manifest
#   remove <name>   Remove an addon from the manifest
#   freeze [name]   Pin ref in addons.toml to current HEAD
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANAGER="${SCRIPT_DIR}/framework/addon_manager.py"

# Find a Python 3.11+ interpreter (needed for tomllib)
find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" -c "import sys; print(sys.version_info >= (3, 11))" 2>/dev/null)
            if [[ "$ver" == "True" ]]; then
                echo "$cmd"
                return
            fi
        fi
    done
    echo "Error: Python 3.11+ required (for tomllib)" >&2
    exit 1
}

PYTHON="$(find_python)"
exec "$PYTHON" "$MANAGER" "$@"

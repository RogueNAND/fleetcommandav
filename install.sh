#!/usr/bin/env bash
set -euo pipefail

START_TS=$(date +%s)

# Traps ------------------------------------------------------------------------

on_error() {
  local exit_code=$?
  local line_no=${BASH_LINENO[0]:-?}
  local cmd=${BASH_COMMAND:-?}
  echo -e "\e[31mError: command failed (exit=$exit_code) at line $line_no: $cmd\e[0m" >&2
  exit "$exit_code"
}
on_exit() {
  local end_ts
  end_ts=$(date +%s)
  local elapsed=$(( end_ts - START_TS ))
  echo -e "\e[90mDone. Elapsed: ${elapsed}s\e[0m"
}
trap on_error ERR
trap on_exit EXIT

# UI helpers -------------------------------------------------------------------

msg()      { echo -e "\e[32m$1\e[0m"; }
die()      { echo -e "\e[31m$*\e[0m" >&2; exit 1; }

# Configuration ----------------------------------------------------------------

REPO_URL="https://github.com/RogueNAND/fleetcommandav.git"
PROFILES=""
TARGET_DIR=""

# Detect if running locally (in a git repo) or remotely (via curl)
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  # Running locally - use current directory
  TARGET_DIR="$(git rev-parse --show-toplevel)"
  msg "Detected local installation at ${TARGET_DIR}"
else
  # Remote install - use /srv
  TARGET_DIR="/srv/fleetcommandav"
fi

# Parse flags ------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--profiles) PROFILES="${2:-}"; shift 2;;
    *) die "Unknown flag: $1";;
  esac
done

# Functions --------------------------------------------------------------------

check_shell_and_user() {
  [[ -n "${BASH_VERSION:-}" ]] || die "Error: run with bash, not sh."
  [[ "$EUID" -ne 0 ]] || die "Do not run this script as root. Run it as a regular user with sudo privileges."

  sudo -v || die "This script requires sudo privileges."
}

ensure_repo() {
  if [[ ! -d "$TARGET_DIR/.git" ]]; then
    msg "Cloning repository to ${TARGET_DIR}..."
    sudo mkdir -p "$(dirname "$TARGET_DIR")"
    sudo git clone "$REPO_URL" "$TARGET_DIR"
    sudo chown -R "$(id -u):$(id -g)" "$TARGET_DIR"
  fi

  cd "$TARGET_DIR"
  # Ensure git trusts this directory (needed when cloned via sudo)
  git config --global --add safe.directory "$TARGET_DIR" 2>/dev/null || true
  git config --local core.autocrlf input
}

checkbox_select() {
  local -n _result=$1
  local prompt_text=$2
  local items_csv=$3
  local prechecked_csv=${4:-}

  # Parse items
  IFS=',' read -ra items <<< "$items_csv"
  local count=${#items[@]}
  [[ $count -eq 0 ]] && { _result=""; return; }

  # Parse pre-checked
  local -A pre=()
  if [[ -n "$prechecked_csv" ]]; then
    IFS=',' read -ra _pre <<< "$prechecked_csv"
    for p in "${_pre[@]}"; do pre["$p"]=1; done
  fi

  # Init checked state
  local checked=()
  for item in "${items[@]}"; do
    [[ -n "${pre[$item]+x}" ]] && checked+=(1) || checked+=(0)
  done

  local cursor=0

  # Hide cursor, ensure restore on exit
  printf '\e[?25l' >/dev/tty
  local _old_trap
  _old_trap=$(trap -p INT)
  trap 'printf "\e[?25h" >/dev/tty; exit 130' INT

  # Draw function
  _draw() {
    local i
    for (( i=0; i<count; i++ )); do
      local mark=" "
      [[ ${checked[$i]} -eq 1 ]] && mark="x"
      if [[ $i -eq $cursor ]]; then
        printf '\e[7m  [%s] %s\e[0m\e[K\n' "$mark" "${items[$i]}" >/dev/tty
      else
        printf '  [%s] %s\e[K\n' "$mark" "${items[$i]}" >/dev/tty
      fi
    done
    printf '\e[90m  ↑/↓: move  Space: toggle  Enter: confirm\e[0m\e[K' >/dev/tty
  }

  # Initial draw
  printf '\e[36m%s\e[0m\n' "$prompt_text" >/dev/tty
  _draw

  # Input loop
  while true; do
    local key
    IFS= read -rsN1 key </dev/tty

    case "$key" in
      $'\x1b')
        local seq
        read -rsN2 -t 0.1 seq </dev/tty || true
        case "$seq" in
          '[A') cursor=$(( (cursor - 1 + count) % count )) ;;
          '[B') cursor=$(( (cursor + 1) % count )) ;;
        esac
        ;;
      ' ')
        checked[$cursor]=$(( 1 - checked[$cursor] ))
        ;;
      $'\n'|$'\r'|'')
        break
        ;;
    esac

    # Redraw (move up count+1 lines for the items + footer)
    printf '\e[%dA\r' "$count" >/dev/tty
    _draw
  done

  # Show cursor
  printf '\e[?25h\n' >/dev/tty
  eval "$_old_trap" 2>/dev/null || trap - INT

  # Build result
  local result=""
  for (( i=0; i<count; i++ )); do
    if [[ ${checked[$i]} -eq 1 ]]; then
      [[ -n "$result" ]] && result+=","
      result+="${items[$i]}"
    fi
  done
  _result="$result"
}

select_profiles() {
  # Check if profiles already set in .env
  local existing_profiles=""
  if [[ -f .env ]] && grep -q "^COMPOSE_PROFILES=" .env; then
    existing_profiles=$(grep "^COMPOSE_PROFILES=" .env | cut -d'=' -f2-)
  fi

  # Only prompt if none provided via flag and we're interactive
  if [[ -z "$PROFILES" && -t 0 ]]; then
    # Dynamically extract available profiles from docker-compose.yml
    local available_profiles
    available_profiles=$(grep -A5 'profiles:' docker-compose.yml 2>/dev/null | awk '/^[[:space:]]+-/ {gsub(/^[[:space:]]*-[[:space:]]*|[[:space:]]*$|[\r]/, ""); if ($0 ~ /^[a-z]+$/) print}' | sort -u | paste -sd,)

    if [[ -n "$available_profiles" ]]; then
      checkbox_select PROFILES "Select profiles to enable:" "$available_profiles" "$existing_profiles"
    fi

    [[ -z "$PROFILES" && -n "$existing_profiles" ]] && PROFILES="$existing_profiles"
  elif [[ -z "$PROFILES" && -n "$existing_profiles" ]]; then
    PROFILES="$existing_profiles"
  fi

  # Normalize spaces
  PROFILES="${PROFILES//[[:space:]]/}"

  if [[ -n "$PROFILES" ]]; then
    # Update .env
    touch .env
    if grep -qE "^[[:space:]]*COMPOSE_PROFILES=" .env; then
      sed -i "s|^[[:space:]]*COMPOSE_PROFILES=.*|COMPOSE_PROFILES=${PROFILES}|" .env
    else
      echo "COMPOSE_PROFILES=${PROFILES}" >> .env
    fi
    msg "Updated .env: COMPOSE_PROFILES=${PROFILES}"
  else
    msg "No profiles selected. Core services only."
  fi
}

ensure_compose() {
  mkdir -p "./compose" "./addons"
  sudo chown -R "$(id -u):$(id -g)" "./compose" "./addons"

  # Sync addons from manifest (if addon.sh exists)
  if [[ -x "./addon.sh" ]]; then
    msg "Syncing addons..."
    ./addon.sh sync
  fi
}

deploy_services() {
  msg "Deploying services..."

  sudo docker compose down --remove-orphans 2>/dev/null || true
  sudo docker compose pull
  sudo docker compose up -d --build

  sudo chown -R "$(id -u):$(id -g)" "./compose"

  msg "Services deployed successfully."
}

show_access_info() {
  local ip
  ip=$(ip -4 -o addr show scope global 2>/dev/null | awk 'NR==1 { sub(/\/.*/, "", $4); print $4 }')

  echo
  msg "========================================="
  msg "FleetCommandAV is now running!"
  msg "========================================="
  echo
  msg "Access the dashboard at:"
  msg "  http://${ip:-localhost}/"
  echo
  msg "To update: cd ${TARGET_DIR} && git pull && ./install.sh"
  echo
}

main() {
  check_shell_and_user
  ensure_repo
  select_profiles
  ensure_compose
  deploy_services
  show_access_info
}

main "$@"

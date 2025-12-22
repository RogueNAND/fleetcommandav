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
    sudo chown -R "$USER:$USER" "$TARGET_DIR"
  fi

  cd "$TARGET_DIR"
  git config --local core.autocrlf input
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
      msg "Available profiles: ${available_profiles}"
    fi

    # Inline prompt
    printf "\e[36mProfiles to enable (comma-separated) [${existing_profiles}]: \e[0m" > /dev/tty
    read -r PROFILES < /dev/tty || PROFILES=""
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

ensure_datastore() {
  mkdir -p "./datastore"
  sudo chown -R "1000:1000" "./datastore"
  find "./datastore" -type d -exec chmod 755 {} +
  find "./datastore" -type f -exec chmod 644 {} +
}

deploy_services() {
  msg "Deploying services..."

  sudo docker compose down --remove-orphans 2>/dev/null || true
  sudo docker compose pull
  sudo docker compose up -d --build

  sudo chown -R "1000:1000" "./datastore"

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
  ensure_datastore
  deploy_services
  show_access_info
}

main "$@"

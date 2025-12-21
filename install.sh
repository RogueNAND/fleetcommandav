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

TARGET_DIR="/srv/fleetcommandav"
REPO_URL="https://github.com/RogueNAND/fleetcommandav.git"
PROFILES=""

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

  msg "Checking sudo access..."
  sudo -v || die "This script requires sudo privileges."
}

ensure_repo() {
  if [[ ! -d "$TARGET_DIR/.git" ]]; then
    # Backup existing non-git directory
    if [[ -d "$TARGET_DIR" ]] && [[ "$(ls -A "$TARGET_DIR" 2>/dev/null)" ]]; then
      local backup_dir="${TARGET_DIR}-backup-$(date +%Y%m%d-%H%M%S)"
      msg "Backing up existing directory to ${backup_dir}..."
      sudo mv "$TARGET_DIR" "$backup_dir"
    fi

    msg "Cloning repository to ${TARGET_DIR}..."
    sudo mkdir -p "$(dirname "$TARGET_DIR")"
    sudo git clone "$REPO_URL" "$TARGET_DIR"
    sudo chown -R "$USER:$USER" "$TARGET_DIR"
  else
    msg "Repository already exists at ${TARGET_DIR}"
    cd "$TARGET_DIR"
    git pull --ff-only 2>/dev/null || msg "Note: git pull failed or not on tracking branch (continuing anyway)"
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
    available_profiles=$(awk '/profiles:/{p=1;next} p && /^[[:space:]]+-/{sub(/^[[:space:]]*-[[:space:]]*/,""); print; next} p{p=0}' docker-compose.yml 2>/dev/null | sort -u | tr '\n' ',' | sed 's/,$//')

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
    msg "Set profiles: COMPOSE_PROFILES=${PROFILES}"
  else
    msg "No profiles selected. Core services only."
  fi
}

ensure_datastore() {
  mkdir -p "./datastore"
  sudo chown -R "1000:1000" "./datastore"
  chmod -R 755 "./datastore"
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

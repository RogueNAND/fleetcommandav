#!/bin/bash

# Ensure bash
if [ -z "$BASH_VERSION" ]; then
  echo "Error: run with bash, not sh." >&2
  exit 1
fi

# Robust mode: exit on error, unset vars, pipeline failures; better word-splitting rules
set -Eeuo pipefail
IFS=$'\n\t'

START_TS=$(date +%s)

# ---- Traps -------------------------------------------------------------------
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

# ---- UI helpers --------------------------------------------------------------
readonly COLOR1="\e[32m"
readonly COLOR_INPUT="\e[36m"
readonly ENDCOLOR="\e[0m"

msg()      { echo -e "${COLOR1}$1${ENDCOLOR}"; }
prompt()   { echo -ne "${COLOR_INPUT}$1${ENDCOLOR}"; }
die()      { echo -e "\e[31m$*\e[0m" >&2; exit 1; }
have()     { command -v "$1" >/dev/null 2>&1; }

# ---- Flags -------------------------------------------------------------------
PROFILES=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--profiles) PROFILES="${2:-}"; shift 2;;
    *) shift;;
  esac
done

# ---- Helpers -----------------------------------------------------------------
set_env_var() {
  local key="$1" val="$2" file="${3:-.env}"
  touch "$file"
  if grep -qE "^[[:space:]]*${key}=" "$file"; then
    sed -i "s|^[[:space:]]*${key}=.*|${key}=${val}|" "$file"
  else
    echo "${key}=${val}" >> "$file"
  fi
}

select_profiles_if_needed() {
  # Only prompt if none provided and we’re interactive
  if [[ -z "$PROFILES" && -t 0 ]]; then
    # Try to list available profiles (Compose v2+ supports this)
    local available=""
    if have docker; then
      available=$(docker compose config --profiles 2>/dev/null || true)
    fi
    if [[ -n "$available" ]]; then
      msg "Available profiles:"
      # print as a single line: profile1, profile2, ...
      echo "  $(echo "$available" | tr '\n' ',' | sed 's/,$//')"
    fi
    prompt "Profiles to enable (comma-separated): "
    read -r PROFILES || true
  fi

  # Normalize spaces
  PROFILES="${PROFILES//[[:space:]]/}"
  if [[ -n "$PROFILES" ]]; then
    set_env_var "COMPOSE_PROFILES" "$PROFILES" ".env"
    msg "Persisted profiles in .env: COMPOSE_PROFILES=${PROFILES}"
  else
    msg "You can change profiles later via COMPOSE_PROFILES in .env or by running this script again."
  fi
}

# ---- Steps -------------------------------------------------------------------

deploy() {
  local config_dir="./datastore"
  mkdir -p "$config_dir"

  sudo chown -R "1000:1000" "$config_dir"
  chmod -R 755 "$config_dir"

  select_profiles_if_needed

  msg "Starting services..."

  sudo docker compose down --remove-orphans || true
  sudo docker compose pull
  sudo docker compose up -d --build
}

check_tailscale() {
  local container="vpn"
  local state_dir="./datastore/tailscale"
  mkdir -p "$state_dir"
  sudo chown -R "1000:1000" "$state_dir"
  chmod 700 "$state_dir"

  # Wait briefly to ensure container is up
  sleep 2

  # Is container running?
  if ! sudo docker ps --format '{{.Names}}' | grep -q "^${container}$"; then
    echo -e "\e[33mTailscale container not running; skipping setup.\e[0m"
    return
  fi

  # Check if already logged in
  if sudo docker exec "$container" tailscale status 2>&1 | grep -q "Logged in as"; then
    return
  fi

  echo -e "\e[34mInitializing Tailscale...\e[0m"
  # Run tailscale up and capture output
  local output
  output=$(sudo docker exec "$container" tailscale up 2>&1 || true)

  if echo "$output" | grep -q "https://login.tailscale.com"; then
    local url
    url=$(echo "$output" | grep -Eo 'https://login\.tailscale\.com[^ ]+')
    echo -e "\e[33mAuthorize Tailscale for this device:\e[0m\n$url"
  else
    echo -e "\e[31mUnexpected output from tailscale up:\e[0m\n$output"
  fi
}

# ---- Main --------------------------------------------------------------------
msg "Run as a regular user (sudo will be used as needed)."

if [[ -t 0 ]]; then
  prompt "Run full system upgrade? [Y/n]: "
  read -r ans
  if [[ -z "$ans" || "$ans" =~ ^[Yy]$ ]]; then
    msg "Updating system packages..."
    sudo pacman -Syu --noconfirm
  else
    msg "Skipping system upgrade."
  fi
else
  msg "Non-interactive shell; skipping pacman -Syu."
fi

git config --global core.autocrlf input

deploy
check_tailscale

msg "Setup complete 👍"
ip=$(ip -4 -o addr show scope global 2>/dev/null | awk 'NR==1 { sub(/\/.*/, "", $4); print $4 }')
msg "FleetCommandAV is available on http://${ip:-localhost}/"

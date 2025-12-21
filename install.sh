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

  sudo chown -R "1000:1000" "$config_dir"
}

# ---- Main --------------------------------------------------------------------

# Enforce non-root
if [[ $EUID -eq 0 ]]; then
  die "Do not run this script as root. Run it as a regular user with sudo privileges."
fi

# Prime sudo password early
msg "Checking sudo access..."
if ! sudo -v; then
  die "This script requires sudo privileges."
fi

git config --global core.autocrlf input

# Deployment scripts
deploy

msg "Setup complete 👍"
ip=$(ip -4 -o addr show scope global 2>/dev/null | awk 'NR==1 { sub(/\/.*/, "", $4); print $4 }')
msg "FleetCommandAV is available on http://${ip:-localhost}/"

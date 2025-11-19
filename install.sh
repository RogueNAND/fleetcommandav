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
have_pkg() { dpkg -s "$1" >/dev/null 2>&1; }

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

# ---- NetworkManager ----------------------------------------------------------
ensure_networkmanager_for_cockpit() {
  # Skip on WSL to avoid fighting Docker Desktop networking
  if grep -qi microsoft /proc/version 2>/dev/null; then
    msg "WSL detected — skipping NetworkManager/Netplan setup."
    return 0
  fi

  local applied=0
  local netplan_file="/etc/netplan/01-network-manager.yaml"

  # 1) Install NetworkManager if missing
  if ! have_pkg network-manager; then
    msg "Installing NetworkManager..."
    sudo apt-get update -y
    sudo apt-get install -y network-manager
    applied=1
  fi

  # 2) Make NM ignore Docker/Tailscale/etc (but still see physical NICs)
  sudo mkdir -p /etc/NetworkManager/conf.d
  if ! grep -qs 'unmanaged-devices' /etc/NetworkManager/conf.d/99-unmanage-docker.conf 2>/dev/null; then
    msg "Configuring NetworkManager to ignore Docker/Tailscale interfaces..."
    sudo tee /etc/NetworkManager/conf.d/99-unmanage-docker.conf >/dev/null <<'CONF'
[keyfile]
unmanaged-devices=interface-name:docker0;interface-name:veth*;interface-name:br-*;interface-name:containerd*;interface-name:tailscale0
CONF
    applied=1
  fi

  # 3) Own netplan: backup any existing YAML once, then create a single minimal file
  if [[ ! -f "$netplan_file" ]]; then
    if ls /etc/netplan/*.yml /etc/netplan/*.yaml >/dev/null 2>&1; then
      msg "Backing up existing Netplan configs..."
      sudo mkdir -p /etc/netplan/backup
      for f in /etc/netplan/*.yml /etc/netplan/*.yaml; do
        [ -f "$f" ] && sudo mv "$f" "/etc/netplan/backup/$(basename "$f").bak"
      done
    fi

    msg "Creating minimal Netplan config for NetworkManager..."
    sudo tee "$netplan_file" >/dev/null <<'YAML'
network:
  version: 2
  renderer: NetworkManager
YAML
    applied=1
  else
    # If the file exists but does not specify NetworkManager as renderer, fix it
    if ! grep -Eq '^[[:space:]]*renderer:[[:space:]]*NetworkManager[[:space:]]*$' "$netplan_file"; then
      msg "Updating Netplan to use NetworkManager renderer..."
      sudo sed -i 's/^[[:space:]]*renderer:[[:space:]]*.*/  renderer: NetworkManager/' "$netplan_file"
      applied=1
    fi
  fi

  # Permissions (primarily to remove warning)
  sudo chown root:root "$netplan_file"
  sudo chmod 600 "$netplan_file"

  # 4) Switch services if needed
  if ! systemctl is-active --quiet NetworkManager || systemctl is-active --quiet systemd-networkd; then
    msg "Enabling NetworkManager and disabling systemd-networkd..."
    sudo systemctl disable --now systemd-networkd || true
    sudo systemctl enable --now NetworkManager
    applied=1
  fi

  # 5) Apply netplan (non-fatal), with a small wait/retry
  if [[ $applied -eq 1 ]]; then
    msg "Waiting for NetworkManager to stabilize before applying Netplan..."
    for i in {1..10}; do
      if systemctl is-active --quiet NetworkManager; then
        break
      fi
      sleep 0.5
    done
    sleep 1

    msg "Applying Netplan..."
    sudo netplan generate || msg "netplan generate failed (non-fatal)."

    if ! sudo netplan apply; then
      msg "Netplan apply failed once, retrying in 2s (non-fatal failure allowed)..."
      sleep 2
      if ! sudo netplan apply; then
        msg "Netplan apply still failing; continuing anyway. Cockpit/NetworkManager may still work once the system settles."
      fi
    fi
  fi

  # 6) Final check (non-fatal)
  if systemctl is-active --quiet NetworkManager; then
    msg "NetworkManager is active; physical interfaces will be manageable in Cockpit."
  else
    msg "⚠️ NetworkManager is not active. Check: journalctl -u NetworkManager"
  fi
}


# ---- Steps -------------------------------------------------------------------
install_cockpit() {
  if ! have_pkg cockpit; then
    msg "Installing Cockpit..."
    sudo apt-get install -y cockpit
  fi

  sudo systemctl enable --now cockpit.socket
  if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -qi "Status: active"; then
    sudo ufw allow 9090/tcp || true
  fi
}

install_docker() {
  # Installing as root since this installation is considered an appliance
  if ! have docker || ! docker version >/dev/null 2>&1; then
    msg "Installing Docker..."

    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
      https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
      sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    msg "Docker installed successfully."
  else
    msg "Docker already installed."
  fi
}

deploy() {
  local config_dir="./datastore/companion"
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
msg "Updating system packages..."
sudo apt-get update -y && sudo apt-get upgrade -y

git config --global core.autocrlf input

ensure_networkmanager_for_cockpit
install_cockpit
install_docker
deploy
check_tailscale

ip=$(hostname -I 2>/dev/null | awk '{print $1}')
msg "Setup complete 👍"
msg "FleetCommandAV is available on http://${ip:-localhost}/"

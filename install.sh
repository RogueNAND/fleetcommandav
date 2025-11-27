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

# Small helper to append a line to a file if it's not already present
append_if_missing() {
  local line="$1"
  local file="$2"

  sudo touch "$file"
  if ! grep -Fxq "$line" "$file" 2>/dev/null; then
    echo "$line" | sudo tee -a "$file" >/dev/null
  fi
}

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

# ---- Appliance hardening -----------------------------------------------------

harden_journald() {
  msg "Configuring journald for RAM-only, low-write logging..."
  sudo mkdir -p /etc/systemd/journald.conf.d
  if [[ ! -f /etc/systemd/journald.conf.d/10-appliance.conf ]]; then
    sudo tee /etc/systemd/journald.conf.d/10-appliance.conf >/dev/null <<'EOF'
[Journal]
# Keep logs in RAM only; avoid disk writes.
Storage=volatile
RuntimeMaxUse=32M
EOF
    sudo systemctl restart systemd-journald
  else
    msg "journald appliance config already present; skipping."
  fi
}

configure_tmpfs_mounts() {
  msg "Ensuring tmpfs mounts for /tmp and /var/tmp..."
  append_if_missing "tmpfs /tmp tmpfs defaults,noatime,mode=1777 0 0" /etc/fstab
  append_if_missing "tmpfs /var/tmp tmpfs defaults,noatime 0 0" /etc/fstab
}

configure_ext4_root_mount() {
  msg "Ensuring safer EXT4 mount options for / (noatime, errors=remount-ro)..."

  # Only touch /etc/fstab if / is ext4
  if grep -qE '^[^#].+\s+/\s+ext4' /etc/fstab; then
    sudo cp /etc/fstab /etc/fstab.bak_appliance || true

    # Rewrite the ext4 root line while preserving UUID and mountpoint
    sudo sed -i -E \
      's@^([^#].*\s/\s+)ext4\s+([^ ]*)@\1ext4 noatime,errors=remount-ro@' \
      /etc/fstab

    msg "Updated root mount options in /etc/fstab."
  else
    msg "Root filesystem is not ext4; skipping mount tuning."
  fi
}

configure_nm_ignore_docker() {
  msg "Configuring NetworkManager to ignore Docker interfaces..."

  sudo mkdir -p /etc/NetworkManager/conf.d

  sudo tee /etc/NetworkManager/conf.d/10-unmanaged-docker.conf >/dev/null <<'EOF'
[keyfile]
unmanaged-devices=interface-name:docker0;interface-name:br-*;interface-name:veth*
EOF

  sudo systemctl restart NetworkManager || true
}

configure_power_button() {
  msg "Configuring systemd-logind to ignore power button..."
  sudo mkdir -p /etc/systemd/logind.conf.d
  sudo tee /etc/systemd/logind.conf.d/10-ignore-power.conf >/dev/null <<'EOF'
[Login]
# Ignore physical power button presses (appliance behaviour).
HandlePowerKey=ignore
EOF
  sudo systemctl restart systemd-logind
}

disable_sleep_targets() {
  msg "Masking sleep/hibernate targets..."
  sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target || true
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

configure_tailscale() {
  msg "Configuring Tailscale"

  # 1) Ensure tailscale is installed
  if ! have tailscale; then
    msg "Installing tailscale via pacman..."
    sudo pacman --noconfirm --needed tailscale
  fi

  # 2) Enable and start the daemon
  sudo systemctl enable --now tailscaled.service

  # 3) Check current status and optionally reconfigure
  local already_logged_in=0
  if sudo tailscale status 2>&1 | grep -q "Logged in as"; then
    already_logged_in=1
    msg "Tailscale is already logged in."
    prompt "Reconfigure Tailscale on this host anyway? [y/N]: "
    local reconfigure
    read -r reconfigure
    if [[ ! "$reconfigure" =~ ^[Yy]$ ]]; then
      msg "Keeping existing Tailscale configuration."
      return
    fi
  fi

  msg ""
  msg "This host can be:"
  msg "  - just a normal Tailscale node"
  msg "  - a subnet router (LAN gateway)"
  msg "  - an exit node (full VPN gateway)"
  msg ""

  # 4) Optional: Headscale login server
  local login_server=""
  prompt "Use a custom Headscale login server? [y/N]: "
  local use_headscale
  read -r use_headscale
  if [[ "$use_headscale" =~ ^[Yy]$ ]]; then
    prompt "Headscale URL (e.g. https://headscale.example.com): "
    read -r login_server
    login_server=${login_server%%/}   # strip trailing slash
  fi

  # 5) Subnet routing: auto-detect LAN subnet and use as default
  local routes=""
  local detected_cidr=""
  detected_cidr=$(ip -4 addr show scope global | awk '/inet / {print $2}' | grep -v '^100\.' | head -n1 || true)

  prompt "Advertise this machine as a subnet router for local LAN(s)? [y/N]: "
  local use_subnet
  read -r use_subnet
  if [[ "$use_subnet" =~ ^[Yy]$ ]]; then
    msg "Enter comma-separated CIDR ranges reachable from this box."
    if [[ -n "$detected_cidr" ]]; then
      msg "Detected local IPv4 network: $detected_cidr"
      prompt "Subnets to advertise [default: $detected_cidr]: "
      read -r routes
      routes=${routes:-$detected_cidr}
    else
      msg "Example: 192.168.10.0/24,10.10.0.0/16"
      prompt "Subnets to advertise: "
      read -r routes
    fi

    if [[ -n "$routes" ]]; then
      msg "Enabling IP forwarding for subnet routing..."
      sudo tee /etc/sysctl.d/99-tailscale-ip-forward.conf >/dev/null <<EOF
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
      sudo sysctl --system >/dev/null
    fi
  fi

  # 6) Exit node
  local advertise_exit_flag=""
  prompt "Advertise this machine as an exit node (full VPN)? [y/N]: "
  local use_exit
  read -r use_exit
  if [[ "$use_exit" =~ ^[Yy]$ ]]; then
    advertise_exit_flag="--advertise-exit-node"
    msg "Note: clients still need to explicitly select this exit node."
  fi

  # 7) Build tailscale up arguments
  local args=(--ssh)

  # Authkey: use env if present, otherwise prompt
  local authkey="${TS_AUTHKEY:-}"
  if [[ -z "$authkey" ]]; then
    prompt "Enter Tailscale auth key (leave blank for URL/browser login): "
    read -r authkey
    msg ""
  fi
  if [[ -n "$authkey" ]]; then
    args+=(--authkey="$authkey")
  fi

  if [[ -n "$login_server" ]]; then
    args+=(--login-server="$login_server")
  fi

  if [[ -n "$routes" ]]; then
    args+=(--advertise-routes="$routes")
  fi

  if [[ -n "$advertise_exit_flag" ]]; then
    args+=("$advertise_exit_flag")
  fi

  msg ""

  # For logging, show args but redact auth key
  local pretty_args=()
  local a
  for a in "${args[@]}"; do
    if [[ "$a" == --authkey=* ]]; then
      pretty_args+=(--authkey=***redacted***)
    else
      pretty_args+=("$a")
    fi
  done

  msg "Running: sudo tailscale up ${pretty_args[*]}"
  msg ""

  # 8) Run tailscale up:
  # - If we have an authkey, it should be non-interactive; capture output.
  # - If we don't, let it print its URL / prompts directly and just check status.
  if [[ -n "$authkey" ]]; then
    local output
    output=$(sudo tailscale up "${args[@]}" 2>&1 || true)

    if echo "$output" | grep -q "Logged in as"; then
      msg "Tailscale authenticated successfully."
      return
    fi

    local url
    url=$(echo "$output" | grep -Eo 'https://[^ ]+' | head -n1 || true)

    if [[ -n "$url" ]]; then
      msg "Authorize this node by opening:"
      msg "  $url"
    else
      msg "tailscale up output:"
      echo "$output"
      msg "If this failed, you can re-run:"
      msg "  sudo tailscale up ${pretty_args[*]}"
    fi
  else
    # Interactive case: user sees URL and prompts directly
    msg "No auth key provided; 'tailscale up' may show a login URL and wait until you authenticate."
    msg "After completing authentication in your browser, this script will continue."
    sudo tailscale up "${args[@]}" || true

    # Give tailscale a few seconds to finish connecting / registering
    local ok=0
    for i in {1..10}; do
      if sudo tailscale status 2>&1 | grep -q "Logged in as"; then
        ok=1
        break
      fi
      sleep 2
    done

    if [[ $ok -eq 1 ]]; then
      msg "Tailscale authenticated successfully."
    else
      msg "Tailscale does not appear to be logged in (or status not yet updated)."
      msg "You can re-run manually with:"
      msg "  sudo tailscale up ${pretty_args[*]}"
    fi
  fi

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

# Appliance tweaks
harden_journald
configure_tmpfs_mounts
configure_ext4_root_mount
configure_nm_ignore_docker
configure_power_button
disable_sleep_targets

# Deployment scripts
configure_tailscale
deploy

msg "Setup complete 👍"
ip=$(ip -4 -o addr show scope global 2>/dev/null | awk 'NR==1 { sub(/\/.*/, "", $4); print $4 }')
msg "FleetCommandAV is available on http://${ip:-localhost}/"

#!/bin/bash
# Custom init script for OpenVSCode Server (LinuxServer)
# Runs at container startup via /custom-cont-init.d/

SERVER_JS="/app/openvscode-server/out/server-main.js"
SETTINGS="/config/.openvscode-server/data/User/settings.json"

# --- Default Settings ---
# VS Code Web stores user settings in the browser's IndexedDB. Fresh browser
# sessions start with built-in defaults (light theme, welcome page, etc.).
# The server sends a web config object to the browser; if it includes a
# top-level "configurationDefaults" key, the workbench applies those as
# overridable defaults for new sessions.
#
# Patch server-main.js to inject our settings.json as configurationDefaults
# in the web config object (alongside productConfiguration, not inside it).
if [ -f "$SERVER_JS" ] && [ -f "$SETTINGS" ]; then
    # Read settings.json, compact to single line
    DEFAULTS=$(jq -c '.' "$SETTINGS")
    sed -i "s|productConfiguration:k,callbackRoute:|productConfiguration:k,configurationDefaults:${DEFAULTS},callbackRoute:|" "$SERVER_JS"
    echo "Patched server-main.js with configurationDefaults from settings.json"
fi

# --- Docker CLI ---
# Provides terminal access to other containers (logs, exec, etc.)
if [ -S /var/run/docker.sock ]; then
    if ! command -v docker &>/dev/null; then
        curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-27.5.1.tgz" \
            | tar xz --strip-components=1 -C /usr/local/bin docker/docker
        echo "Installed Docker CLI"
    fi
    chmod 666 /var/run/docker.sock
fi

# --- Extensions ---
# Add extension IDs (publisher.name) to the list below.
# Available extensions: https://open-vsx.org/
EXTENSIONS=(
    ms-python.python
    ms-python.black-formatter
    redhat.vscode-yaml
    mhutchie.git-graph
    ms-azuretools.vscode-docker
    ms-pyright.pyright
)

for ext in "${EXTENSIONS[@]}"; do
    install-extension "$ext" --force
done

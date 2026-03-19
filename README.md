# FleetCommandAV
Built off the backs of open source broadcast ecosystems, this project attemps to glue it all together with code.

## Quick Start

Run this command to install FleetCommandAV:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/RogueNAND/fleetcommandav/main/install.sh)
```

Or clone and run locally:

```bash
git clone https://github.com/RogueNAND/fleetcommandav.git
cd fleetcommandav
./install.sh
```

The dashboard will be accessible at `http://<your-ip>/`

## Addon Management

All automation addons (libraries and modules) live in the `addons/` directory and are declared in `addons.toml`.

### CLI

```bash
./addon.sh list              # List all declared addons
./addon.sh status            # Show installed state, flag missing deps
./addon.sh sync              # Fetch/update all addons from their sources
./addon.sh sync <name>       # Sync a single addon
./addon.sh add <source>      # Add a new addon (e.g. github:User/repo)
./addon.sh remove <name>     # Remove an addon from the manifest
./addon.sh freeze            # Pin all refs to their current commit
```

### Adding an addon from GitHub

```bash
./addon.sh add github:SomeUser/my-addon --type module
./addon.sh sync my-addon
docker compose restart python
```

### Adding a local addon

Create a file or package directly in `addons/`, then register it in `addons.toml`:

```toml
[addons.my_automation]
type = "module"
source = "local"
```

### Addon types

- **`library`** — Has a `pyproject.toml`. Installed via `pip install -e` on container startup. Provides importable packages (e.g. cuelist, dmxld).
- **`module`** — A `.py` file or package with `__init__.py`. Auto-imported on startup. Interacts with Companion via decorators and button classes.

### Addon data storage

Addon data (timelines, uploads, configs) is stored separately from code in `./compose/python/`, mounted at `/data` inside the Python container. Use the framework helper to get a namespaced directory:

```python
from fleetcommand.storage import get_data_dir

data_dir = get_data_dir("my_addon")  # -> /data/my_addon/
```

This keeps code (`./addons/`) and data (`./compose/python/`) cleanly separated — back up `./compose/` to capture all service data.

### Source types

| Source | Example | Description |
|--------|---------|-------------|
| `local` | `source = "local"` | Manually managed, not fetched |
| `github:` | `source = "github:User/repo"` | Cloned from GitHub |
| `ssh://` | `source = "ssh://user@host:/path"` | Cloned via SSH (host sync) |
| Git URL | `source = "https://..."` | Any git remote |

### Load order

Addons declare dependencies via `requires` in `addons.toml`. Libraries are always installed first (pip), then modules are imported in topological order. Undeclared addons on disk load last, alphabetically.

## Adding Companion Modules

To add custom Bitfocus Companion modules (forks, unreleased packages, etc.):

```bash
cd /srv/fleetcommandav/companion/libraries
git clone https://github.com/example/companion-module-example.git
```

Modules are automatically installed on Companion container restart via `yarn install`. Restart with `docker compose restart companion`, then configure the new connection in the Companion UI.

## Updates

To update an existing installation:

```bash
cd /srv/fleetcommandav
git pull
./install.sh
```

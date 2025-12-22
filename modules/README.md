# FleetCommand Modules

This directory contains all automation modules for FleetCommandAV.

## Quick Start

1. Create a new file in `user/`: `my_automation.py`
2. Import companion: `from fleetcommand import companion`
3. Write your handlers using decorators
4. Save - the framework auto-reloads!

## Directory Structure

- **`base/`** - Built-in modules (shipped with FleetCommandAV)
- **`community/`** - Package manager installations (reserved for future use)
- **`user/`** - Your custom automations (user-written code)

## Simple Automation Example

**File**: `user/my_automation.py`

```python
from fleetcommand import companion

# Enable type casting for variables
companion.enable_cast()

# React to variable changes
@companion.on_change("internal", variable="time_s")
async def log_uptime(event):
    uptime = companion.var("internal", "uptime")
    print(f"Uptime: {uptime}s")

# Create custom buttons
class MyButton(companion.Button):
    async def on_init(self):
        await self.set_text("Ready")
        await self.set_bg_color(0.2, 0.2, 0.2)

    async def on_down(self):
        await self.set_bg_color(1, 0, 0)  # Red when pressed

    async def on_up(self):
        await self.set_bg_color(0, 0, 1)  # Blue when released
```

## Complex Package Example

For larger automations, create a package directory:

**Structure**: `user/my_package/`
```
my_package/
├── __init__.py       # Package entry point
├── handlers.py       # Event handlers
├── buttons.py        # Custom button classes
└── utils.py          # Helper functions
```

**File**: `user/my_package/__init__.py`
```python
from fleetcommand import companion
from .handlers import setup_handlers
from .buttons import MyButton

# Initialize
setup_handlers()
```

## Sharing Code Between Modules

Modules can import from each other since they're in the same PYTHONPATH:

**File**: `user/helpers.py`
```python
def hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert hex color to RGB tuple (0-1 range)"""
    hex_color = hex_color.lstrip('#')
    r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    return r/255, g/255, b/255
```

**Use in another module**: `user/colors.py`
```python
from fleetcommand import companion
from helpers import hex_to_rgb  # Direct import from same directory

class ColorButton(companion.Button):
    async def on_down(self):
        r, g, b = hex_to_rgb("#FF5733")
        await self.set_bg_color(r, g, b)
```

**Tip:** Name helper files with a leading underscore (e.g., `_helpers.py`) to prevent them from being loaded as automations.

## API Reference

See `user/demo.py` for comprehensive examples of all available APIs.

**Event Decorators:**
- `@companion.on_change(connection, variable="name")`
- `@companion.on_change(connection, prefix="prefix_")`
- `@companion.on_change(connection, suffix="_suffix")`
- `@companion.on_change(connection, regex=r"pattern")`
- `@companion.on_connect(connection)`

**Utility Decorators:**
- `@companion.requires("connection1", "connection2")`
- `@companion.debounce(min_delay=0.5, group_by="key")`
- `@companion.repeat_with_reset(attempts=3, delay=0.1)`

**API Methods:**
- `companion.var(connection, variable, default=None)`
- `companion.action(connection, action_id, options={})`
- `companion.action_multi(action1, action2, ...)`
- `companion.enable_cast(*connections)`

## Managing Modules

## Module Loading Order

Modules are loaded in alphabetical order:
1. `base/` - Built-in modules (git-tracked, shipped with project)
2. `community/` - Package manager installations (future)
3. `user/` - Your custom code

This ensures built-in modules are available first, followed by community packages, then your code can override or extend them.

## Future: Package Manager

The `community/` directory is reserved for future package manager integration. When implemented:

- Install community modules with a package manager command
- Modules auto-install to `community/` (never touches `base/` or `user/`)
- Track installed packages with metadata
- Easy updates and dependency management

For now, `community/` remains empty. Built-in modules go in `base/`, and your code goes in `user/`.

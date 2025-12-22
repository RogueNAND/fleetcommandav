# FleetCommand Framework

Core framework for the FleetCommandAV automation system.

## For Users

**Don't modify files in this directory.**

Put your automations in `/automations/enabled/` instead.

## Structure

```
framework/
├── fleetcommand/          # Core library package
│   ├── __init__.py        # Package exports
│   ├── companion.py       # Companion WebSocket client
│   └── utils/             # Framework utilities (future)
├── main.py                # Application entry point
├── Dockerfile             # Container build definition
└── requirements.txt       # Python dependencies
```

## For Contributors

This directory contains the core framework code that powers the FleetCommandAV automation system.

### Key Components

- **fleetcommand/companion.py** - WebSocket client for Bitfocus Companion with event-driven decorator API
- **main.py** - Application entry point that loads automations and starts the Companion client

### Making Changes

When modifying framework code:

1. Test changes don't break existing automations
2. Update version in `fleetcommand/__init__.py`
3. Update CLAUDE.md if APIs change
4. Consider backward compatibility

### Adding New Features

Framework utilities should go in `fleetcommand/utils/`:
- Logging utilities
- Type definitions
- Common decorators
- Helper functions

Keep the framework focused on core functionality. Feature-specific code belongs in automations.

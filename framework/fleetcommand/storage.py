"""Persistent storage for addons. Data lives outside the code volume."""

import os
from pathlib import Path

DATA_ROOT = Path(os.environ.get("FCAV_DATA_ROOT", "/data"))


def get_data_dir(addon_name: str) -> Path:
    """Return (and create) a persistent data directory for the named addon."""
    p = DATA_ROOT / addon_name
    p.mkdir(parents=True, exist_ok=True)
    return p

"""Simple file-based flags for runtime control.

Flags are stored as empty files in /data/.flags/ (or FCAV_DATA_ROOT/.flags/).
Any process can set/unset them — watcher, Companion buttons, CLI, future UI.
"""

import os
from pathlib import Path

_FLAGS_DIR = Path(os.environ.get("FCAV_DATA_ROOT", "/data")) / ".flags"


def _path(name):
    _FLAGS_DIR.mkdir(parents=True, exist_ok=True)
    return _FLAGS_DIR / name


def is_set(name):
    return _path(name).exists()


def set(name):
    _path(name).touch()


def unset(name):
    _path(name).unlink(missing_ok=True)


def toggle(name):
    if is_set(name):
        unset(name)
    else:
        set(name)


AUTORELOAD = "autoreload"

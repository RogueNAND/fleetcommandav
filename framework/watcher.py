"""File watcher — container PID 1.

Runs addon sync + library install once at startup, then watches for .py
changes and restarts main.py when the autoreload flag is enabled.
"""

import signal
import subprocess
import sys

from watchfiles import watch, PythonFilter

sys.path.insert(0, "/fleetcommand")
from main import sync_addons, install_libraries
from fleetcommand.flags import is_set, set, AUTORELOAD

WATCHED_PATHS = ["/fleetcommand", "/addons"]
MAIN_CMD = [sys.executable, "-Xfrozen_modules=off", "/fleetcommand/main.py"]


def run():
    sync_addons()
    install_libraries()

    # Default autoreload to enabled on first run
    if not is_set(AUTORELOAD):
        set(AUTORELOAD)

    proc = subprocess.Popen(MAIN_CMD)

    for changes in watch(*WATCHED_PATHS, watch_filter=PythonFilter(), debounce=500):
        if is_set(AUTORELOAD):
            print("🔄 File change detected, restarting...")
            proc.send_signal(signal.SIGINT)
            proc.wait()
            proc = subprocess.Popen(MAIN_CMD)

    proc.wait()


if __name__ == "__main__":
    run()

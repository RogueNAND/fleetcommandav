import asyncio
import subprocess
import sys
from pathlib import Path
from fleetcommand import companion


def install_libraries():
    """Install libraries from /fleetcommand/libraries/ using pip editable installs"""
    libraries_dir = Path("/fleetcommand/libraries")

    if not libraries_dir.exists():
        return

    for pkg_dir in libraries_dir.iterdir():
        if not pkg_dir.is_dir():
            continue
        if pkg_dir.name.startswith(("_", ".")):
            continue

        # Check if it's an installable package
        setup_files = [pkg_dir / f for f in ("pyproject.toml", "setup.py", "setup.cfg")]
        existing_setup_files = [f for f in setup_files if f.exists()]
        if not existing_setup_files:
            continue

        # Check if already installed using our own marker file
        marker = pkg_dir / ".fcav-installed"
        if marker.exists():
            # Check if any setup file is newer than the marker (package was updated)
            marker_mtime = marker.stat().st_mtime
            needs_reinstall = any(f.stat().st_mtime > marker_mtime for f in existing_setup_files)
            if not needs_reinstall:
                print(f"📦 Cached: {pkg_dir.name}")
                continue

        # Install with pip (uses cache, won't re-download existing deps)
        print(f"📦 Installing: {pkg_dir.name}")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(pkg_dir)],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"⚠️  Failed to install {pkg_dir.name}: {result.stderr}")
        else:
            # Create marker file on successful install
            marker.touch()


def load_automations():
    """Load automations from all module directories"""
    module_sources = [
        Path("/modules/base"),
        Path("/modules/community"),
        Path("/modules/user"),
    ]

    for source_dir in module_sources:
        if not source_dir.exists():
            print(f"⚠️  Module directory not found: {source_dir}")
            continue

        # Add to path for imports
        sys.path.insert(0, str(source_dir))

        # Load all .py files (except those starting with _)
        for file_path in source_dir.glob("*.py"):
            if file_path.name.startswith("_"):
                continue

            module_name = file_path.stem
            try:
                __import__(module_name)
                print(f"✅ Loaded: {module_name} (from {source_dir.name})")
            except Exception as e:
                print(f"❌ Failed to load {module_name}: {e}")
                import traceback
                traceback.print_exc()

        # Load package directories (directories with __init__.py)
        for dir_path in source_dir.iterdir():
            if not dir_path.is_dir():
                continue
            if dir_path.name.startswith("_"):
                continue
            if not (dir_path / "__init__.py").exists():
                continue

            module_name = dir_path.name
            try:
                __import__(module_name)
                print(f"✅ Loaded: {module_name}/ (from {source_dir.name})")
            except Exception as e:
                print(f"❌ Failed to load {module_name}/: {e}")
                import traceback
                traceback.print_exc()

async def main():
    install_libraries()  # Install libraries before loading automations
    load_automations()
    await companion.run()

if __name__ == "__main__":
    asyncio.run(main())

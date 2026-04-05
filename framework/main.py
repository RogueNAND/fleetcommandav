import asyncio
import hashlib
import subprocess
import sys
import tomllib
from pathlib import Path

from fleetcommand import companion

MARKER_DIR = Path("/tmp/fcav-lib-markers")
ADDONS_DIR = Path("/addons")
MANIFEST_PATH = ADDONS_DIR / "addons.toml"


def _resolve_source(source):
    """Convert shorthand source to a git-clonable URL. Returns None for local."""
    if source == "local":
        return None
    if source.startswith("github:"):
        return f"https://github.com/{source[7:]}.git"
    return source


def _git(args, cwd=None):
    """Run a git command, return (success, stdout)."""
    result = subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True
    )
    return result.returncode == 0, result.stdout.strip()


def sync_addons():
    """Clone missing remote addons and update existing ones to their declared ref."""
    if not MANIFEST_PATH.exists():
        return

    with open(MANIFEST_PATH, "rb") as f:
        manifest = tomllib.load(f)

    for name, config in manifest.get("addons", {}).items():
        url = _resolve_source(config.get("source", "local"))
        if url is None:
            continue

        ref = config.get("ref", "main")
        dest = ADDONS_DIR / name

        if not dest.exists():
            print(f"📥 Cloning {name} from {config['source']}...")
            ok, _ = _git(["clone", url, str(dest)])
            if not ok:
                print(f"⚠️  Failed to clone {name}")
                continue
            _git(["checkout", ref], cwd=dest)
        elif (dest / ".git").exists():
            print(f"🔄 Updating {name} to {ref}...")
            _git(["fetch", "origin"], cwd=dest)
            # If ref is a remote branch, pull; otherwise just checkout
            ok, _ = _git(["rev-parse", "--verify", f"origin/{ref}"], cwd=dest)
            if ok:
                _git(["checkout", ref], cwd=dest)
                _git(["pull", "--ff-only", "origin", ref], cwd=dest)
            else:
                _git(["checkout", ref], cwd=dest)


def _pkg_fingerprint(pkg_dir, setup_files):
    """Get a fingerprint for the current state of a library package.
    Uses git commit hash if available, otherwise hashes setup file contents."""
    git_dir = pkg_dir / ".git"
    if git_dir.exists():
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=pkg_dir, capture_output=True, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()

    # Fallback: hash setup file contents
    h = hashlib.sha256()
    for f in sorted(setup_files):
        h.update(f.read_bytes())
    return h.hexdigest()


def install_libraries():
    """Install library addons from /addons/ using pip editable installs."""
    if not ADDONS_DIR.exists():
        return

    MARKER_DIR.mkdir(exist_ok=True)
    installed_any = False

    for pkg_dir in ADDONS_DIR.iterdir():
        if not pkg_dir.is_dir():
            continue
        if pkg_dir.name.startswith(("_", ".")):
            continue

        # Check if it's an installable package
        setup_files = [pkg_dir / f for f in ("pyproject.toml", "setup.py", "setup.cfg")]
        existing_setup_files = [f for f in setup_files if f.exists()]
        if not existing_setup_files:
            continue

        # Check if already installed by comparing fingerprints
        # Markers live in container-local storage (survives restarts, wiped on recreate)
        marker = MARKER_DIR / pkg_dir.name
        fingerprint = _pkg_fingerprint(pkg_dir, existing_setup_files)
        if marker.exists() and marker.read_text() == fingerprint:
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
            marker.write_text(fingerprint)
            installed_any = True

    return installed_any


def _load_order():
    """Determine module load order from addons.toml, with topological sort on requires.
    Returns a list of addon names (module-type only). Addons on disk but not in
    manifest are appended alphabetically at the end."""
    # Discover module-type addons on disk (non-library: no pyproject.toml/setup.py/setup.cfg)
    on_disk = set()
    for p in ADDONS_DIR.iterdir():
        if p.name.startswith(("_", ".")):
            continue
        is_pkg = p.is_dir() and (p / "__init__.py").exists()
        is_file = p.is_file() and p.suffix == ".py"
        is_library = p.is_dir() and any(
            (p / f).exists() for f in ("pyproject.toml", "setup.py", "setup.cfg")
        )
        if (is_pkg or is_file) and not is_library:
            on_disk.add(p.stem if is_file else p.name)

    if not MANIFEST_PATH.exists():
        return sorted(on_disk)

    with open(MANIFEST_PATH, "rb") as f:
        manifest = tomllib.load(f)

    addons = manifest.get("addons", {})

    # Collect module-type addons from manifest that exist on disk
    manifest_modules = []
    requires_map = {}
    for name, config in addons.items():
        if config.get("type") == "library":
            continue
        if name in on_disk:
            manifest_modules.append(name)
            # Only include requires that are also modules (not libraries)
            module_requires = [
                r for r in config.get("requires", [])
                if r in addons and addons[r].get("type") != "library"
            ]
            if module_requires:
                requires_map[name] = module_requires

    # Topological sort (Kahn's algorithm)
    in_degree = {n: 0 for n in manifest_modules}
    dependents = {n: [] for n in manifest_modules}
    for name, deps in requires_map.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[name] += 1
                dependents[dep].append(name)

    queue = sorted(n for n in manifest_modules if in_degree[n] == 0)
    ordered = []
    while queue:
        node = queue.pop(0)
        ordered.append(node)
        for dep in sorted(dependents.get(node, [])):
            in_degree[dep] -= 1
            if in_degree[dep] == 0:
                queue.append(dep)

    # Append on-disk modules not in manifest (alphabetical)
    for name in sorted(on_disk - set(ordered)):
        ordered.append(name)

    return ordered


def load_addons():
    """Load module-type addons from /addons/ in dependency order."""
    if not ADDONS_DIR.exists():
        print(f"⚠️  Addons directory not found: {ADDONS_DIR}")
        return

    sys.path.insert(0, str(ADDONS_DIR))

    for module_name in _load_order():
        try:
            __import__(module_name)
            print(f"✅ Loaded: {module_name}")
        except Exception as e:
            print(f"❌ Failed to load {module_name}: {e}")
            import traceback
            traceback.print_exc()


async def main():
    import debugpy
    debugpy.listen(("0.0.0.0", 5678))

    load_addons()
    await companion.run()

if __name__ == "__main__":
    asyncio.run(main())

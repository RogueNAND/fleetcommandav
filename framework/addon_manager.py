"""FleetCommandAV addon manager — fetches, tracks, and manages addon sources."""

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "addons.toml"
ADDONS_DIR = ROOT / "addons"


def load_manifest():
    """Load and return the addons.toml manifest."""
    if not MANIFEST.exists():
        print(f"No manifest found at {MANIFEST}")
        sys.exit(1)
    with open(MANIFEST, "rb") as f:
        return tomllib.load(f)


def resolve_source(source):
    """Convert shorthand source to a git-clonable URL. Returns None for local."""
    if source == "local":
        return None
    if source.startswith("github:"):
        return f"https://github.com/{source[7:]}.git"
    # ssh://, https://, git:// — pass through as-is
    return source


def addon_dir(name, config):
    """Return the target directory for an addon."""
    return ADDONS_DIR / name


def git_run(args, cwd=None, check=True, capture=False):
    """Run a git command."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=capture,
        text=True,
    )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, ["git"] + args)
    return result


def get_current_ref(path):
    """Get the current HEAD sha of a git repo."""
    result = git_run(["rev-parse", "--short", "HEAD"], cwd=path, capture=True, check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def get_current_branch(path):
    """Get the current branch name of a git repo."""
    result = git_run(
        ["rev-parse", "--abbrev-ref", "HEAD"], cwd=path, capture=True, check=False
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def is_dirty(path):
    """Check if a git repo has uncommitted changes."""
    result = git_run(["status", "--porcelain"], cwd=path, capture=True, check=False)
    return bool(result.stdout.strip()) if result.returncode == 0 else False


# ─── Commands ────────────────────────────────────────────────────────────────


def cmd_sync(args):
    """Clone missing addons and update existing ones to their declared ref."""
    manifest = load_manifest()
    addons = manifest.get("addons", {})
    ADDONS_DIR.mkdir(exist_ok=True)

    target = args.name
    synced = 0

    for name, config in addons.items():
        if target and target != name:
            continue

        url = resolve_source(config.get("source", "local"))
        if url is None:
            if target:
                print(f"  {name}: local (nothing to sync)")
            continue

        ref = config.get("ref", "main")
        dest = addon_dir(name, config)

        if not dest.exists():
            print(f"  {name}: cloning from {config['source']}...")
            git_run(["clone", url, str(dest)])
            git_run(["checkout", ref], cwd=dest)
            synced += 1
        else:
            if is_dirty(dest):
                print(f"  {name}: dirty working tree, skipping (commit or stash first)")
                continue
            print(f"  {name}: fetching and checking out {ref}...")
            git_run(["fetch", "origin"], cwd=dest)
            # If ref is a branch, pull. If tag/sha, just checkout.
            result = git_run(
                ["rev-parse", "--verify", f"origin/{ref}"],
                cwd=dest, capture=True, check=False,
            )
            if result.returncode == 0:
                # It's a remote branch
                git_run(["checkout", ref], cwd=dest)
                git_run(["pull", "--ff-only", "origin", ref], cwd=dest)
            else:
                git_run(["checkout", ref], cwd=dest)
            synced += 1

    if target and not synced and target not in addons:
        print(f"  Unknown addon: {target}")
        sys.exit(1)

    print(f"\nSynced {synced} addon(s).")


def cmd_status(args):
    """Show the status of all declared addons."""
    manifest = load_manifest()
    addons = manifest.get("addons", {})

    # Collect all addon names for dependency checking
    declared_names = set(addons.keys())

    print(f"{'Name':<20} {'Type':<10} {'Source':<30} {'Status':<15} {'Ref'}")
    print("-" * 90)

    for name, config in addons.items():
        addon_type = config.get("type", "module")
        source = config.get("source", "local")
        ref = config.get("ref", "")
        dest = addon_dir(name, config)

        # For single-file modules, check if .py file exists
        is_single_file = not dest.exists() and (ADDONS_DIR / f"{name}.py").exists()

        if is_single_file:
            status = "installed"
            current = "file"
        elif dest.exists():
            if (dest / ".git").exists():
                current = get_current_ref(dest) or "?"
                dirty = " (dirty)" if is_dirty(dest) else ""
                status = f"installed{dirty}"
            else:
                status = "installed"
                current = "no-git"
        else:
            status = "MISSING"
            current = "-"

        display_source = source[:28] + ".." if len(source) > 30 else source
        print(f"  {name:<20} {addon_type:<10} {display_source:<30} {status:<15} {current}")

    # Check for missing dependencies
    print()
    issues = []
    for name, config in addons.items():
        requires = config.get("requires", [])
        for dep in requires:
            if dep not in declared_names:
                issues.append(f"  {name} requires '{dep}' which is not declared")

    # Check for addons on disk but not in manifest
    if ADDONS_DIR.exists():
        for path in sorted(ADDONS_DIR.iterdir()):
            if path.name.startswith((".", "_")):
                continue
            disk_name = path.stem if path.suffix == ".py" else path.name
            if disk_name not in declared_names:
                issues.append(f"  '{disk_name}' exists on disk but not in addons.toml")

    if issues:
        print("Issues:")
        for issue in issues:
            print(issue)
    else:
        print("No issues found.")


def cmd_list(args):
    """List all declared addons."""
    manifest = load_manifest()
    addons = manifest.get("addons", {})

    libraries = {k: v for k, v in addons.items() if v.get("type") == "library"}
    modules = {k: v for k, v in addons.items() if v.get("type", "module") == "module"}

    if libraries:
        print("Libraries:")
        for name, config in libraries.items():
            source = config.get("source", "local")
            ref = config.get("ref", "")
            requires = config.get("requires", [])
            parts = [f"  {name} ({source})"]
            if ref:
                parts.append(f"@ {ref}")
            if requires:
                parts.append(f"[requires: {', '.join(requires)}]")
            print(" ".join(parts))

    if modules:
        print("\nModules:")
        for name, config in modules.items():
            source = config.get("source", "local")
            ref = config.get("ref", "")
            requires = config.get("requires", [])
            parts = [f"  {name} ({source})"]
            if ref:
                parts.append(f"@ {ref}")
            if requires:
                parts.append(f"[requires: {', '.join(requires)}]")
            print(" ".join(parts))


def cmd_add(args):
    """Add a new addon to the manifest."""
    source = args.source
    name = args.name

    # Auto-detect name from source if not provided
    if not name:
        if source.startswith("github:"):
            name = source.split("/")[-1]
        elif "/" in source:
            name = source.rstrip("/").split("/")[-1]
            if name.endswith(".git"):
                name = name[:-4]
        else:
            print("Cannot auto-detect name from source. Use --name to specify.")
            sys.exit(1)

    # Check if already declared
    manifest = load_manifest()
    if name in manifest.get("addons", {}):
        print(f"Addon '{name}' already declared in addons.toml")
        sys.exit(1)

    addon_type = args.type or "module"

    # Read current file and append
    content = MANIFEST.read_text()
    content += f"\n[addons.{name}]\n"
    content += f'type = "{addon_type}"\n'
    content += f'source = "{source}"\n'
    if source != "local":
        ref = args.ref or "main"
        content += f'ref = "{ref}"\n'

    MANIFEST.write_text(content)
    print(f"Added '{name}' ({addon_type}) from {source}")
    print(f"Run './addon.sh sync {name}' to fetch it.")


def cmd_remove(args):
    """Remove an addon from the manifest."""
    name = args.name
    manifest = load_manifest()

    if name not in manifest.get("addons", {}):
        print(f"Addon '{name}' not found in addons.toml")
        sys.exit(1)

    # Remove from TOML by filtering lines (simple approach for TOML with no nested tables)
    lines = MANIFEST.read_text().splitlines(keepends=True)
    new_lines = []
    skip = False
    for line in lines:
        if line.strip() == f"[addons.{name}]":
            skip = True
            continue
        if skip and line.strip().startswith("["):
            skip = False
        if not skip:
            new_lines.append(line)

    # Clean up trailing blank lines from removed section
    content = "".join(new_lines).rstrip("\n") + "\n"
    MANIFEST.write_text(content)

    dest = addon_dir(name, {})
    single_file = ADDONS_DIR / f"{name}.py"
    if dest.exists():
        print(f"Removed '{name}' from manifest. Directory still exists at {dest}")
        print(f"Delete manually if desired: rm -rf {dest}")
    elif single_file.exists():
        print(f"Removed '{name}' from manifest. File still exists at {single_file}")
    else:
        print(f"Removed '{name}' from manifest.")


def cmd_freeze(args):
    """Pin ref in addons.toml to the current HEAD of each addon's git repo."""
    manifest = load_manifest()
    addons = manifest.get("addons", {})
    target = args.name

    content = MANIFEST.read_text()
    frozen = 0

    for name, config in addons.items():
        if target and target != name:
            continue
        if config.get("source") == "local":
            continue

        dest = addon_dir(name, config)
        if not dest.exists() or not (dest / ".git").exists():
            continue

        sha = get_current_ref(dest)
        if not sha:
            continue

        # Get the full sha for pinning
        full_result = git_run(["rev-parse", "HEAD"], cwd=dest, capture=True, check=False)
        if full_result.returncode != 0:
            continue
        full_sha = full_result.stdout.strip()

        old_ref = config.get("ref", "main")
        if old_ref == full_sha:
            continue

        # Replace ref in manifest content
        # Find the section and update the ref line
        section_header = f"[addons.{name}]"
        in_section = False
        lines = content.splitlines(keepends=True)
        new_lines = []
        for line in lines:
            if line.strip() == section_header:
                in_section = True
                new_lines.append(line)
            elif in_section and line.strip().startswith("["):
                in_section = False
                new_lines.append(line)
            elif in_section and line.strip().startswith("ref"):
                new_lines.append(f'ref = "{full_sha}"\n')
                frozen += 1
                print(f"  {name}: {old_ref} -> {full_sha[:12]}")
            else:
                new_lines.append(line)

        content = "".join(new_lines)

    if frozen:
        MANIFEST.write_text(content)
        print(f"\nFroze {frozen} addon(s).")
    else:
        print("Nothing to freeze.")


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="FleetCommandAV addon manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  ./addon.sh sync                  Fetch/update all addons
  ./addon.sh sync cuelist          Sync a specific addon
  ./addon.sh status                Show installed state and check deps
  ./addon.sh list                  List all declared addons
  ./addon.sh add github:User/repo  Add a new addon
  ./addon.sh remove my_addon       Remove an addon from manifest
  ./addon.sh freeze                Pin all refs to current HEAD
""",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("sync", help="Fetch/update addons from their declared sources")
    p.add_argument("name", nargs="?", help="Sync a specific addon by name")

    sub.add_parser("status", help="Show installed vs declared state")
    sub.add_parser("list", help="List all declared addons")

    p = sub.add_parser("add", help="Add a new addon to the manifest")
    p.add_argument("source", help="Addon source (github:owner/repo, git URL, or 'local')")
    p.add_argument("--name", help="Addon name (auto-detected from source if omitted)")
    p.add_argument("--type", choices=["library", "module"], help="Addon type (default: module)")
    p.add_argument("--ref", help="Git ref to track (default: main)")

    p = sub.add_parser("remove", help="Remove an addon from the manifest")
    p.add_argument("name", help="Addon name to remove")

    p = sub.add_parser("freeze", help="Pin ref to current HEAD sha")
    p.add_argument("name", nargs="?", help="Freeze a specific addon")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "sync": cmd_sync,
        "status": cmd_status,
        "list": cmd_list,
        "add": cmd_add,
        "remove": cmd_remove,
        "freeze": cmd_freeze,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()

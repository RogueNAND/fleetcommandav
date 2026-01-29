import asyncio
import sys
from pathlib import Path
from fleetcommand import companion

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
    load_automations()
    await companion.run()

if __name__ == "__main__":
    asyncio.run(main())

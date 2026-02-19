"""
MIGRATE_TO_PER_AGENT
====================

Migration script to move existing data from legacy structure to per-agent directories.

Legacy Structure:
    data/
    ├── CONFIG/agents/*.json          # Agent configurations
    ├── MEMORY/                        # Global memory (or agents/{id}/)
    │   ├── topics.json
    │   ├── index_*.json
    │   ├── {topic}/
    │   ├── sessions/
    │   └── shared/
    ├── OUTPUT/{agent_id}/{date}/      # Run outputs
    └── TASKS/                         # Scheduled tasks

New Per-Agent Structure:
    data/
    ├── SKILLS/                        # Global skills (unchanged)
    ├── AGENTS/
    │   └── {agent_id}/
    │       ├── config.json
    │       ├── skills/
    │       ├── tasks/
    │       ├── memory/
    │       ├── sessions/
    │       └── runs/
    ├── shared/                        # Global shared facts
    └── CONFIG/config.json             # Global configuration only

Usage:
    python -m loop_core.migration.migrate_to_per_agent [--dry-run] [--agent-id AGENT_ID]
"""

import json
import shutil
import argparse
from pathlib import Path
from datetime import datetime


def find_data_dir() -> Path:
    """Find the data directory."""
    current = Path(__file__).resolve().parent
    for _ in range(5):
        data_dir = current / "data"
        if data_dir.exists():
            return data_dir
        current = current.parent
    raise FileNotFoundError("Could not find data directory")


def migrate_agent_config(data_dir: Path, agent_id: str, dry_run: bool = False) -> bool:
    """
    Migrate agent configuration from CONFIG/agents/{id}.json to AGENTS/{id}/config.json
    """
    legacy_path = data_dir / "CONFIG" / "agents" / f"{agent_id}.json"
    new_dir = data_dir / "AGENTS" / agent_id
    new_path = new_dir / "config.json"

    if not legacy_path.exists():
        print(f"  [SKIP] No legacy config found: {legacy_path}")
        return False

    if new_path.exists():
        print(f"  [SKIP] Config already exists: {new_path}")
        return False

    print(f"  [MIGRATE] {legacy_path} -> {new_path}")

    if not dry_run:
        new_dir.mkdir(parents=True, exist_ok=True)
        # Copy (not move) to preserve original
        shutil.copy2(legacy_path, new_path)

    return True


def migrate_agent_memory(data_dir: Path, agent_id: str, dry_run: bool = False) -> bool:
    """
    Migrate memory from MEMORY/ to AGENTS/{id}/memory/

    Handles two legacy structures:
    1. MEMORY/agents/{agent_id}/ - per-agent isolation
    2. MEMORY/ (root level) - single agent mode
    """
    new_memory_dir = data_dir / "AGENTS" / agent_id / "memory"

    # Check for per-agent legacy structure first
    legacy_per_agent = data_dir / "MEMORY" / "agents" / agent_id
    if legacy_per_agent.exists():
        legacy_memory_dir = legacy_per_agent
        print(f"  [INFO] Found per-agent legacy structure: {legacy_per_agent}")
    else:
        # Use root MEMORY directory (single agent mode)
        legacy_memory_dir = data_dir / "MEMORY"
        print(f"  [INFO] Using root MEMORY directory: {legacy_memory_dir}")

    if not legacy_memory_dir.exists():
        print(f"  [SKIP] No legacy memory found")
        return False

    # Items to migrate (excluding sessions and shared)
    items_to_migrate = []

    # Topics file
    topics_file = legacy_memory_dir / "topics.json"
    if topics_file.exists():
        items_to_migrate.append(("topics.json", topics_file))

    # Index files
    for index_file in legacy_memory_dir.glob("index_*.json"):
        items_to_migrate.append((index_file.name, index_file))

    # Topic content directories (exclude sessions, shared, agents)
    skip_dirs = {"sessions", "shared", "agents"}
    for item in legacy_memory_dir.iterdir():
        if item.is_dir() and item.name not in skip_dirs:
            items_to_migrate.append((item.name, item))

    if not items_to_migrate:
        print(f"  [SKIP] No memory content to migrate")
        return False

    print(f"  [MIGRATE] Memory items: {[name for name, _ in items_to_migrate]}")

    if not dry_run:
        new_memory_dir.mkdir(parents=True, exist_ok=True)

        for name, source in items_to_migrate:
            dest = new_memory_dir / name
            if dest.exists():
                print(f"    [SKIP] Already exists: {dest}")
                continue

            if source.is_dir():
                shutil.copytree(source, dest)
            else:
                shutil.copy2(source, dest)
            print(f"    [COPIED] {name}")

    return True


def migrate_agent_sessions(data_dir: Path, agent_id: str, dry_run: bool = False) -> bool:
    """
    Migrate sessions from MEMORY/sessions/ to AGENTS/{id}/sessions/
    """
    new_sessions_dir = data_dir / "AGENTS" / agent_id / "sessions"

    # Check for per-agent legacy structure first
    legacy_per_agent = data_dir / "MEMORY" / "agents" / agent_id / "sessions"
    if legacy_per_agent.exists():
        legacy_sessions_dir = legacy_per_agent
    else:
        legacy_sessions_dir = data_dir / "MEMORY" / "sessions"

    if not legacy_sessions_dir.exists():
        print(f"  [SKIP] No legacy sessions found")
        return False

    session_files = list(legacy_sessions_dir.glob("session_*.json"))
    if not session_files:
        print(f"  [SKIP] No session files found")
        return False

    # Filter sessions by agent_id if possible
    sessions_to_migrate = []
    for session_file in session_files:
        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            session_agent = data.get("agent_id", agent_id)
            if session_agent == agent_id:
                sessions_to_migrate.append(session_file)
        except Exception:
            # If we can't read it, assume it belongs to this agent
            sessions_to_migrate.append(session_file)

    if not sessions_to_migrate:
        print(f"  [SKIP] No sessions for agent {agent_id}")
        return False

    print(f"  [MIGRATE] {len(sessions_to_migrate)} session files")

    if not dry_run:
        new_sessions_dir.mkdir(parents=True, exist_ok=True)

        for session_file in sessions_to_migrate:
            dest = new_sessions_dir / session_file.name
            if dest.exists():
                print(f"    [SKIP] Already exists: {session_file.name}")
                continue
            shutil.copy2(session_file, dest)
            print(f"    [COPIED] {session_file.name}")

    return True


def migrate_agent_runs(data_dir: Path, agent_id: str, dry_run: bool = False) -> bool:
    """
    Migrate runs from OUTPUT/{agent_id}/ to AGENTS/{id}/runs/
    """
    legacy_runs_dir = data_dir / "OUTPUT" / agent_id
    new_runs_dir = data_dir / "AGENTS" / agent_id / "runs"

    if not legacy_runs_dir.exists():
        print(f"  [SKIP] No legacy runs found: {legacy_runs_dir}")
        return False

    # Find date directories containing runs
    date_dirs = [d for d in legacy_runs_dir.iterdir() if d.is_dir()]
    if not date_dirs:
        print(f"  [SKIP] No date directories in {legacy_runs_dir}")
        return False

    print(f"  [MIGRATE] {len(date_dirs)} date directories with runs")

    if not dry_run:
        new_runs_dir.mkdir(parents=True, exist_ok=True)

        for date_dir in date_dirs:
            dest_date_dir = new_runs_dir / date_dir.name
            if dest_date_dir.exists():
                print(f"    [SKIP] Date dir already exists: {date_dir.name}")
                continue
            shutil.copytree(date_dir, dest_date_dir)
            run_count = len(list(dest_date_dir.glob("run_*")))
            print(f"    [COPIED] {date_dir.name} ({run_count} runs)")

    return True


def migrate_agent_tasks(data_dir: Path, agent_id: str, dry_run: bool = False) -> bool:
    """
    Migrate tasks from TASKS/ to AGENTS/{id}/tasks/

    Only migrates tasks that belong to this agent (based on execution.agent_id in task.json)
    """
    legacy_tasks_dir = data_dir / "TASKS"
    new_tasks_dir = data_dir / "AGENTS" / agent_id / "tasks"

    if not legacy_tasks_dir.exists():
        print(f"  [SKIP] No legacy tasks directory")
        return False

    # Find task folders belonging to this agent
    tasks_to_migrate = []
    for task_dir in legacy_tasks_dir.iterdir():
        if not task_dir.is_dir() or task_dir.name.startswith('.'):
            continue

        task_json = task_dir / "task.json"
        if not task_json.exists():
            continue

        try:
            with open(task_json, 'r', encoding='utf-8') as f:
                config = json.load(f)
            task_agent = config.get("execution", {}).get("agent_id", "main")
            if task_agent == agent_id:
                tasks_to_migrate.append(task_dir)
        except Exception:
            # If we can't read it, skip
            continue

    if not tasks_to_migrate:
        print(f"  [SKIP] No tasks for agent {agent_id}")
        return False

    print(f"  [MIGRATE] {len(tasks_to_migrate)} task folders")

    if not dry_run:
        new_tasks_dir.mkdir(parents=True, exist_ok=True)

        for task_dir in tasks_to_migrate:
            dest = new_tasks_dir / task_dir.name
            if dest.exists():
                print(f"    [SKIP] Already exists: {task_dir.name}")
                continue
            shutil.copytree(task_dir, dest)
            print(f"    [COPIED] {task_dir.name}")

    return True


def migrate_shared_facts(data_dir: Path, dry_run: bool = False) -> bool:
    """
    Migrate shared facts from MEMORY/shared/ to data/shared/
    """
    legacy_shared = data_dir / "MEMORY" / "shared"
    new_shared = data_dir / "shared"

    if not legacy_shared.exists():
        print(f"  [SKIP] No legacy shared directory")
        return False

    if new_shared.exists():
        print(f"  [SKIP] Shared directory already exists: {new_shared}")
        return False

    print(f"  [MIGRATE] {legacy_shared} -> {new_shared}")

    if not dry_run:
        shutil.copytree(legacy_shared, new_shared)

    return True


def create_agent_subdirs(data_dir: Path, agent_id: str, dry_run: bool = False) -> None:
    """Create all subdirectories for an agent."""
    agent_dir = data_dir / "AGENTS" / agent_id
    subdirs = ["skills", "tasks", "memory", "sessions", "runs"]

    if not dry_run:
        agent_dir.mkdir(parents=True, exist_ok=True)
        for subdir in subdirs:
            (agent_dir / subdir).mkdir(exist_ok=True)

    print(f"  [CREATED] Agent directory structure: AGENTS/{agent_id}/")


def discover_agents(data_dir: Path) -> list:
    """Discover all agents from legacy config."""
    agents = []

    # Check CONFIG/agents/*.json
    agents_config_dir = data_dir / "CONFIG" / "agents"
    if agents_config_dir.exists():
        for config_file in agents_config_dir.glob("*.json"):
            agents.append(config_file.stem)

    # Also check OUTPUT directory for agent IDs
    output_dir = data_dir / "OUTPUT"
    if output_dir.exists():
        for item in output_dir.iterdir():
            if item.is_dir() and item.name not in agents:
                # Check if it looks like an agent dir (has date subdirs)
                date_dirs = [d for d in item.iterdir() if d.is_dir() and len(d.name) == 10]
                if date_dirs:
                    agents.append(item.name)

    return sorted(set(agents))


def migrate_agent(data_dir: Path, agent_id: str, dry_run: bool = False) -> dict:
    """
    Migrate all data for a single agent.

    Returns:
        dict with migration results
    """
    print(f"\n{'='*60}")
    print(f"Migrating agent: {agent_id}")
    print(f"{'='*60}")

    results = {
        "agent_id": agent_id,
        "config": False,
        "memory": False,
        "sessions": False,
        "runs": False,
        "tasks": False
    }

    # Create directory structure
    print("\n[1/6] Creating directory structure...")
    create_agent_subdirs(data_dir, agent_id, dry_run)

    # Migrate config
    print("\n[2/6] Migrating agent configuration...")
    results["config"] = migrate_agent_config(data_dir, agent_id, dry_run)

    # Migrate memory
    print("\n[3/6] Migrating memory...")
    results["memory"] = migrate_agent_memory(data_dir, agent_id, dry_run)

    # Migrate sessions
    print("\n[4/6] Migrating sessions...")
    results["sessions"] = migrate_agent_sessions(data_dir, agent_id, dry_run)

    # Migrate runs
    print("\n[5/6] Migrating runs...")
    results["runs"] = migrate_agent_runs(data_dir, agent_id, dry_run)

    # Migrate tasks
    print("\n[6/6] Migrating tasks...")
    results["tasks"] = migrate_agent_tasks(data_dir, agent_id, dry_run)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Migrate data from legacy structure to per-agent directories"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--agent-id", "-a",
        help="Migrate specific agent only (default: all discovered agents)"
    )
    parser.add_argument(
        "--data-dir", "-d",
        help="Path to data directory (auto-detected if not specified)"
    )

    args = parser.parse_args()

    # Find data directory
    if args.data_dir:
        data_dir = Path(args.data_dir)
    else:
        try:
            data_dir = find_data_dir()
        except FileNotFoundError:
            print("ERROR: Could not find data directory. Use --data-dir to specify.")
            return 1

    print(f"Data directory: {data_dir}")
    print(f"Dry run: {args.dry_run}")

    if args.dry_run:
        print("\n" + "="*60)
        print("DRY RUN - No changes will be made")
        print("="*60)

    # Discover or use specified agent
    if args.agent_id:
        agents = [args.agent_id]
    else:
        agents = discover_agents(data_dir)

    if not agents:
        print("\nNo agents found to migrate.")
        return 0

    print(f"\nAgents to migrate: {agents}")

    # Migrate shared facts first
    print("\n" + "="*60)
    print("Migrating shared facts")
    print("="*60)
    migrate_shared_facts(data_dir, args.dry_run)

    # Migrate each agent
    all_results = []
    for agent_id in agents:
        results = migrate_agent(data_dir, agent_id, args.dry_run)
        all_results.append(results)

    # Summary
    print("\n" + "="*60)
    print("MIGRATION SUMMARY")
    print("="*60)

    for results in all_results:
        agent_id = results["agent_id"]
        migrated = [k for k, v in results.items() if v and k != "agent_id"]
        skipped = [k for k, v in results.items() if not v and k != "agent_id"]

        print(f"\nAgent: {agent_id}")
        if migrated:
            print(f"  Migrated: {', '.join(migrated)}")
        if skipped:
            print(f"  Skipped:  {', '.join(skipped)}")

    if args.dry_run:
        print("\n[DRY RUN] No changes were made. Remove --dry-run to apply changes.")
    else:
        print("\nMigration complete!")
        print("\nNote: Original data has been COPIED, not moved.")
        print("You can safely delete legacy directories after verifying the migration:")
        print("  - data/CONFIG/agents/")
        print("  - data/MEMORY/ (except shared/)")
        print("  - data/OUTPUT/")
        print("  - data/TASKS/")

    return 0


if __name__ == "__main__":
    exit(main())

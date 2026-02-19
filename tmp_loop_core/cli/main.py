"""
CLI_MAIN
========

Command-line interface for the Agentic Loop Framework.

Global Flags:
    --server            Start the API server (Admin Panel)
    --scheduler         Start the task scheduler daemon
    --port PORT         Port for API server (default: 8431)

Commands:
    run                 Run an agent with a message
    list-agents         List all configured agents
    sessions            List sessions for an agent
    skills              List available skills
    memory              Search memory
    memory-topics       List memory topics
    memory-consolidate  Consolidate related memories
    memory-decay        Apply decay to memory relevance scores
    runs                List recent runs
    fetch-skill         Fetch a skill from URL
    interactive         Run in interactive mode
    status              Show system status

    # Scheduler commands
    tasks               List scheduled tasks
    task-get            Get task details
    task-create         Create a new task
    task-trigger        Manually trigger a task
    task-enable         Enable a task
    task-disable        Disable a task
    task-delete         Delete a task
    task-runs           Get task run history
    scheduler-start     Start the scheduler daemon

Usage:
    python -m loop_core.cli --server                    # Start API server only
    python -m loop_core.cli --scheduler                 # Start scheduler only
    python -m loop_core.cli --server --scheduler        # Start both
    python -m loop_core.cli --server --port 9000        # Custom port
    python -m loop_core.cli run main "Hello!"
    python -m loop_core.cli list-agents
    python -m loop_core.cli interactive main
    python -m loop_core.cli memory-consolidate --dry-run
    python -m loop_core.cli memory-decay --dry-run
    python -m loop_core.cli tasks
    python -m loop_core.cli task-create my_task --interval 3600
    python -m loop_core.cli scheduler-start
"""

import argparse
import json
import sys
from typing import Optional


def get_manager():
    """Get the agent manager with error handling."""
    try:
        from ..agent_manager import get_agent_manager
        return get_agent_manager()
    except Exception as e:
        print(f"Error initializing agent manager: {e}")
        return None


# ============================================================================
# CLI COMMANDS
# ============================================================================

def cli_run(agent_id: str, message: str, session_id: Optional[str] = None,
            skill: Optional[str] = None, verbose: bool = False) -> dict:
    """
    Run an agent with a message.

    Args:
        agent_id: Agent ID to run
        message: Message to send
        session_id: Optional session ID
        skill: Optional skill to activate
        verbose: Show detailed output

    Returns:
        Result dictionary
    """
    manager = get_manager()
    if manager is None:
        return {"error": "Failed to initialize agent manager"}

    if verbose:
        print(f"Running agent: {agent_id}")
        print(f"Message: {message}")
        if session_id:
            print(f"Session: {session_id}")
        if skill:
            print(f"Skill: {skill}")
        print("-" * 40)

    try:
        if skill:
            result = manager.run_with_skill(agent_id, message, skill, session_id)
        else:
            result = manager.run_agent(agent_id, message, session_id)

        output = {
            "status": result.status,
            "response": result.final_response,
            "turns": result.turns,
            "tools_called": result.tools_called,
            "tokens": result.total_tokens,
            "duration_ms": result.total_duration_ms
        }

        if result.error:
            output["error"] = result.error

        return output

    except Exception as e:
        return {"error": str(e)}


def cli_list_agents() -> list:
    """List all configured agents."""
    manager = get_manager()
    if manager is None:
        return []

    agents = []
    for agent_id in manager.list_agents():
        info = manager.get_agent_info(agent_id)
        if info:
            agents.append({
                "id": agent_id,
                "name": info.get("name", agent_id),
                "description": info.get("description", ""),
                "model": info.get("model", "unknown")
            })
        else:
            agents.append({"id": agent_id, "name": agent_id})

    return agents


def cli_sessions(agent_id: Optional[str] = None) -> list:
    """List sessions for an agent."""
    manager = get_manager()
    if manager is None:
        return []

    return manager.list_sessions(agent_id)


def cli_skills() -> list:
    """List available skills."""
    manager = get_manager()
    if manager is None or manager.skill_loader is None:
        return []

    skills = []
    for skill_id in manager.skill_loader.list_skills():
        skill = manager.skill_loader.get_skill(skill_id)
        if skill:
            skills.append({
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "triggers": skill.triggers,
                "enabled": skill.enabled
            })

    return skills


def cli_memory_search(query: str, topic: Optional[str] = None,
                      limit: int = 10) -> list:
    """Search memory."""
    manager = get_manager()
    if manager is None or manager.memory_manager is None:
        return []

    return manager.memory_manager.search_memory(query, topic, limit)


def cli_runs(agent_id: Optional[str] = None, date: Optional[str] = None,
             limit: int = 20) -> list:
    """List recent runs."""
    manager = get_manager()
    if manager is None:
        return []

    return manager.list_runs(agent_id, date, limit)


def cli_fetch_skill(skill_id: str, url: str) -> dict:
    """Fetch a skill from URL."""
    manager = get_manager()
    if manager is None or manager.skill_loader is None:
        return {"error": "Skill loader not available"}

    skill = manager.skill_loader.fetch_from_url(skill_id, url)
    if skill:
        return {
            "success": True,
            "skill_id": skill.id,
            "name": skill.name
        }
    else:
        return {"error": f"Failed to fetch skill from {url}"}


def cli_interactive(agent_id: str):
    """Run in interactive mode."""
    from ..memory.decision import is_session_end_command

    manager = get_manager()
    if manager is None:
        print("Error: Failed to initialize agent manager")
        return

    # Check LLM client
    status = manager.get_status()
    if not status.get("llm_initialized"):
        print("Error: LLM client not initialized")
        print("Check that your API key is configured in apikeys/")
        return

    agent = manager.get_agent(agent_id)
    if agent is None:
        print(f"Error: Agent '{agent_id}' not found")
        return

    print(f"\nAgentic Loop Interactive Mode")
    print(f"=" * 50)
    print(f"Agent: {agent.name} ({agent_id})")
    print(f"Model: {agent.config.llm.model}")
    print(f"Tools: {', '.join(agent.enabled_tools) or 'none'}")
    print(f"\nType 'quit' to exit, 'help' for commands")
    print(f"Say 'goodbye' or '/end' to end session with memory review\n")

    session_id = None
    conversation_history = []

    while True:
        try:
            user_input = input("You: ").strip()

            if not user_input:
                continue

            # Handle commands
            if user_input.lower() in ('quit', 'q'):
                break

            if user_input.lower() == 'help':
                print("\nCommands:")
                print("  quit       - Exit interactive mode (no memory review)")
                print("  clear      - Clear conversation history")
                print("  session    - Show current session ID")
                print("  tools      - List available tools")
                print("  skills     - List available skills")
                print("  memories   - List stored memories")
                print("  help       - Show this help")
                print("\nSession end commands (with memory review):")
                print("  goodbye, bye, exit, /end")
                print()
                continue

            if user_input.lower() == 'clear':
                conversation_history = []
                print("Conversation cleared.\n")
                continue

            if user_input.lower() == 'session':
                print(f"Session ID: {session_id or 'none'}\n")
                continue

            if user_input.lower() == 'tools':
                print(f"Tools: {', '.join(agent.list_tools())}\n")
                continue

            if user_input.lower() == 'skills':
                if manager.skill_loader:
                    skills = manager.skill_loader.list_skills()
                    print(f"Skills: {', '.join(skills) or 'none'}\n")
                else:
                    print("No skills loaded.\n")
                continue

            if user_input.lower() == 'memories':
                if manager.memory_manager:
                    topics = manager.memory_manager.list_topics()
                    if topics:
                        print("\nStored Memories:")
                        for topic_data in topics:
                            topic_id = topic_data.get("id") if isinstance(topic_data, dict) else topic_data
                            index = manager.memory_manager.get_topic_index(topic_id)
                            if index and index.entries:
                                print(f"\n  [{topic_id}]:")
                                for entry in index.entries[:3]:
                                    print(f"    - {entry.get('summary', 'Untitled')[:50]}")
                                if len(index.entries) > 3:
                                    print(f"    ... and {len(index.entries) - 3} more")
                        print()
                    else:
                        print("No memories stored.\n")
                else:
                    print("Memory manager not available.\n")
                continue

            # Run agent
            result = agent.run(
                message=user_input,
                session_id=session_id,
                conversation_history=conversation_history
            )

            print(f"\nAgent: {result.final_response}\n")

            if result.tools_called:
                print(f"[Tools: {', '.join(result.tools_called)}]\n")

            # Update conversation history
            conversation_history.append({"role": "user", "content": user_input})
            conversation_history.append({
                "role": "assistant",
                "content": result.final_response or ""
            })

            # Check if session end command - exit after processing
            if is_session_end_command(user_input):
                print("Session ended.\n")
                break

        except KeyboardInterrupt:
            print("\n\nExiting...")
            break
        except Exception as e:
            print(f"\nError: {e}\n")

    print("\nGoodbye!")


def cli_status() -> dict:
    """Get system status."""
    manager = get_manager()
    if manager is None:
        return {"error": "Failed to initialize agent manager"}

    return manager.get_status()


def cli_memory_topics() -> list:
    """List all memory topics."""
    manager = get_manager()
    if manager is None or manager.memory_manager is None:
        return []

    return manager.memory_manager.list_topics()


def cli_memory_consolidate(topic: Optional[str] = None, dry_run: bool = False) -> dict:
    """
    Run memory consolidation.

    Args:
        topic: Specific topic to consolidate, or None for all
        dry_run: If True, only report what would be done

    Returns:
        Result dictionary with statistics
    """
    manager = get_manager()
    if manager is None:
        return {"error": "Failed to initialize agent manager"}

    if manager.memory_manager is None:
        return {"error": "Memory manager not available"}

    if manager.llm_client is None:
        return {"error": "LLM client not available (required for consolidation)"}

    try:
        from ..memory.decision import MemoryConsolidator

        consolidator = MemoryConsolidator(
            llm_client=manager.llm_client,
            memory_manager=manager.memory_manager
        )

        if dry_run:
            # For dry run, just count what would be consolidated
            topics = manager.memory_manager.list_topics()
            # Extract topic IDs
            topic_ids = []
            for t in topics:
                if isinstance(t, dict):
                    topic_ids.append(t.get("id", ""))
                else:
                    topic_ids.append(t)

            target_topics = topic_ids if topic is None else [topic]
            potential_groups = 0
            potential_merged = 0

            for tid in target_topics:
                index = manager.memory_manager.get_topic_index(tid)
                if index:
                    entries = index.entries
                    if len(entries) >= 3:
                        groups = consolidator._group_by_similarity(entries)
                        mergeable = [g for g in groups if len(g) >= 3]
                        potential_groups += len(mergeable)
                        potential_merged += sum(len(g) for g in mergeable)

            return {
                "dry_run": True,
                "topics_scanned": len(target_topics),
                "potential_groups": potential_groups,
                "potential_memories_merged": potential_merged
            }

        result = consolidator.run_consolidation(topic)
        return {
            "success": True,
            "topics_processed": result.topics_processed,
            "memories_merged": result.memories_merged,
            "memories_removed": result.memories_removed
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# SCHEDULER CLI FUNCTIONS
# ============================================================================

_scheduler = None


def get_scheduler():
    """Get or create the scheduler instance."""
    global _scheduler
    if _scheduler is None:
        manager = get_manager()
        if manager is None:
            return None

        try:
            from ..scheduler import TaskScheduler, create_task_executor

            agents_dir = manager.global_config.paths.agents_dir
            executor = create_task_executor(manager)

            _scheduler = TaskScheduler(
                agents_dir=agents_dir,
                executor=executor
            )
            # Load tasks but don't start the background loop
            _scheduler._load_all_tasks()

            # Register scheduler with agent manager for task tools
            manager.set_scheduler(_scheduler)
        except Exception as e:
            print(f"Warning: Could not initialize scheduler: {e}")
            return None

    return _scheduler


def cli_tasks_list() -> list:
    """List all scheduled tasks."""
    scheduler = get_scheduler()
    if scheduler is None:
        return []
    return scheduler.list_tasks()


def cli_task_get(task_id: str) -> dict:
    """Get task details."""
    scheduler = get_scheduler()
    if scheduler is None:
        return {"error": "Scheduler not available"}

    task = scheduler.get_task(task_id)
    if task is None:
        return {"error": f"Task not found: {task_id}"}
    return task


def cli_task_create(
    task_id: str,
    name: str,
    schedule_type: str,
    interval: int,
    cron: str,
    agent_id: str,
    content: str
) -> dict:
    """Create a new task."""
    scheduler = get_scheduler()
    if scheduler is None:
        return {"error": "Scheduler not available"}

    # Build schedule config
    if schedule_type == "interval":
        schedule = {"type": "interval", "interval_seconds": interval}
    elif schedule_type == "cron":
        if not cron:
            return {"error": "Cron expression required for cron schedule type"}
        schedule = {"type": "cron", "expression": cron}
    elif schedule_type == "once":
        schedule = {"type": "once"}
    else:
        schedule = {"type": "event_only"}

    try:
        task = scheduler.create_task(
            task_id=task_id,
            name=name or task_id,
            task_md_content=content or f"# {name or task_id}\n\nTask instructions go here.",
            schedule=schedule,
            agent_id=agent_id
        )
        return {
            "success": True,
            "task_id": task.task_id,
            "next_run": task.next_run.isoformat() if task.next_run else None
        }
    except Exception as e:
        return {"error": str(e)}


def cli_task_trigger(task_id: str) -> dict:
    """Manually trigger a task."""
    scheduler = get_scheduler()
    if scheduler is None:
        return {"error": "Scheduler not available"}

    return scheduler.trigger_task(task_id)


def cli_task_enable(task_id: str) -> dict:
    """Enable a task."""
    scheduler = get_scheduler()
    if scheduler is None:
        return {"error": "Scheduler not available"}

    success = scheduler.enable_task(task_id)
    if success:
        return {"success": True, "message": f"Task {task_id} enabled"}
    return {"error": f"Failed to enable task: {task_id}"}


def cli_task_disable(task_id: str) -> dict:
    """Disable a task."""
    scheduler = get_scheduler()
    if scheduler is None:
        return {"error": "Scheduler not available"}

    success = scheduler.disable_task(task_id)
    if success:
        return {"success": True, "message": f"Task {task_id} disabled"}
    return {"error": f"Failed to disable task: {task_id}"}


def cli_task_delete(task_id: str) -> dict:
    """Delete a task."""
    scheduler = get_scheduler()
    if scheduler is None:
        return {"error": "Scheduler not available"}

    success = scheduler.delete_task(task_id)
    if success:
        return {"success": True, "message": f"Task {task_id} deleted"}
    return {"error": f"Failed to delete task: {task_id}"}


def cli_task_runs(task_id: str, limit: int = 10) -> list:
    """Get task run history."""
    scheduler = get_scheduler()
    if scheduler is None:
        return []
    return scheduler.get_task_runs(task_id, limit=limit)


def cli_scheduler_start():
    """Start the scheduler daemon."""
    manager = get_manager()
    if manager is None:
        print("Error: Failed to initialize agent manager")
        return

    try:
        from ..scheduler import TaskScheduler, create_task_executor

        agents_dir = manager.global_config.paths.agents_dir
        executor = create_task_executor(manager)

        scheduler = TaskScheduler(
            agents_dir=agents_dir,
            executor=executor,
            check_interval=1.0
        )

        # Register scheduler with agent manager for task tools
        manager.set_scheduler(scheduler)

        print(f"\nScheduler starting...")
        print(f"Agents directory: {agents_dir}")
        print(f"Press Ctrl+C to stop\n")

        scheduler.start()

        # Show loaded tasks
        tasks = scheduler.list_tasks()
        if tasks:
            print(f"Loaded {len(tasks)} tasks:")
            for task in tasks:
                status = "+" if task["enabled"] else "-"
                next_run = task.get("next_run", "N/A")
                print(f"  [{status}] {task['task_id']}: next run {next_run}")
        else:
            print("No tasks loaded.")

        print("\nScheduler running...")

        # Keep running until interrupted or stopped via signal
        import time
        try:
            while scheduler.is_running():
                time.sleep(1)
            print("\nScheduler stopped.")
        except KeyboardInterrupt:
            print("\nStopping scheduler...")
            scheduler.stop()
            print("Scheduler stopped.")

    except Exception as e:
        print(f"Error starting scheduler: {e}")


def cli_memory_decay(dry_run: bool = False) -> dict:
    """
    Apply decay to memory relevance scores.

    Args:
        dry_run: If True, only report what would be decayed

    Returns:
        Result dictionary with statistics
    """
    manager = get_manager()
    if manager is None:
        return {"error": "Failed to initialize agent manager"}

    if manager.memory_manager is None:
        return {"error": "Memory manager not available"}

    try:
        from ..memory.decision import MemoryDecay

        decay = MemoryDecay(memory_manager=manager.memory_manager)

        if dry_run:
            # For dry run, count what would be decayed
            topics = manager.memory_manager.list_topics()
            would_decay = 0
            would_archive = 0

            for topic_data in topics:
                topic_id = topic_data.get("id") if isinstance(topic_data, dict) else topic_data
                index = manager.memory_manager.get_topic_index(topic_id)
                if index:
                    for entry in index.entries:
                        if entry.get("status") != "archived":
                            if decay._should_decay(entry):
                                would_decay += 1
                                score = entry.get("relevance_score", 1.0)
                                new_score = score * (1 - decay.DECAY_RATE)
                                if new_score < decay.MIN_RELEVANCE:
                                    would_archive += 1

            return {
                "dry_run": True,
                "topics_scanned": len(topics),
                "would_decay": would_decay,
                "would_archive": would_archive
            }

        result = decay.apply_decay()
        return {
            "success": True,
            "memories_decayed": result.memories_decayed,
            "memories_archived": result.memories_archived
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def cli_start_server(port: int = 8431):
    """Start the API server (with scheduler and runtime)."""
    try:
        from ..api.app import create_app, FASTAPI_AVAILABLE
        if not FASTAPI_AVAILABLE:
            print("FastAPI is not installed. Install with:")
            print("  pip install fastapi uvicorn")
            return

        import uvicorn
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Install with: pip install fastapi uvicorn")
        return

    # Note: scheduler and runtime are created lazily by create_app() via
    # get_scheduler() and get_runtime(). The import on line 709 triggers
    # module-level create_app() which eagerly initializes both.
    # We just need to reference them for the banner and cleanup.

    # Get references to runtime/scheduler created by create_app()
    manager = get_manager()
    runtime_ref = manager.get_runtime() if manager else None
    scheduler_ref = manager.get_scheduler() if manager else None

    from loop_core.api.app import API_VERSION
    print(f"\nAgentic Loop API Server  v{API_VERSION}")
    print(f"=" * 50)
    print(f"Admin Panel: http://localhost:{port}")
    print(f"API Docs:    http://localhost:{port}/docs")
    print(f"Runtime:     {'Running' if runtime_ref else 'Not started'}")
    print(f"Scheduler:   {'Running' if scheduler_ref and scheduler_ref.is_running() else 'Not started'}")
    print(f"\nPress Ctrl+C to stop\n")

    # Custom log config to filter out status polling endpoints
    import logging

    class StatusPollFilter(logging.Filter):
        """Filter out scheduler/runtime status and webhook polling from access logs."""
        def filter(self, record):
            message = record.getMessage()
            if 'GET' in message:
                if '/api/scheduler/status' in message:
                    return False
                if '/api/runtime/status' in message:
                    return False
            if '/hooks/' in message:
                return False
            return True

    # Apply filter to uvicorn access logger
    logging.getLogger("uvicorn.access").addFilter(StatusPollFilter())

    try:
        uvicorn.run(
            "loop_core.api.app:app",
            host="localhost",
            port=port,
            log_level="info"
        )
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        # Stop runtime and scheduler created by create_app()
        runtime_ref = manager.get_runtime() if manager else None
        if runtime_ref:
            runtime_ref.stop()
        scheduler_ref = manager.get_scheduler() if manager else None
        if scheduler_ref:
            scheduler_ref.stop()


def cli_start_scheduler_only():
    """Start only the scheduler daemon (no API server)."""
    cli_scheduler_start()


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="agentic-loop",
        description="Agentic Loop Framework - An AI agent orchestration system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
COMMAND CATEGORIES
==================

Agent Commands:
  run              Run an agent with a message
  list-agents      List all configured agents
  interactive      Start interactive chat mode with an agent
  status           Show system status

Session & Run Commands:
  sessions         List sessions (optionally filter by agent)
  runs             List recent agent runs

Memory Commands:
  memory           Search stored memories
  memory-topics    List all memory topics
  memory-consolidate  Merge related memories (reduce redundancy)
  memory-decay     Apply time-based decay to memory relevance

Skills Commands:
  skills           List available skills
  fetch-skill      Download and install a skill from URL

Scheduler Commands:
  tasks            List all scheduled tasks
  task-get         Get details of a specific task
  task-create      Create a new scheduled task
  task-trigger     Manually trigger a task execution
  task-enable      Enable a disabled task
  task-disable     Disable a task (pause scheduling)
  task-delete      Delete a task permanently
  task-runs        View execution history for a task
  scheduler-start  Start the background scheduler daemon

EXAMPLES
========

Run an agent:
  %(prog)s run main "Hello, what can you do?"
  %(prog)s run main "Research AI trends" --skill web_research
  %(prog)s run main "Summarize this" --session my_session --verbose

Interactive mode:
  %(prog)s interactive main
  %(prog)s interactive coding_agent

Memory operations:
  %(prog)s memory "python decorators" --topic programming
  %(prog)s memory-topics
  %(prog)s memory-consolidate --dry-run
  %(prog)s memory-decay --dry-run

Scheduler operations:
  %(prog)s tasks
  %(prog)s task-create daily_report --interval 86400 --agent main
  %(prog)s task-create cleanup --schedule-type cron --cron "0 0 * * *"
  %(prog)s task-trigger daily_report
  %(prog)s task-runs daily_report --limit 5
  %(prog)s scheduler-start

For command-specific help:
  %(prog)s <command> --help
        """
    )

    # Version argument
    parser.add_argument(
        "--version", "-V",
        action="version",
        version="%(prog)s 0.1.0"
    )

    # Global flags for server/scheduler mode
    parser.add_argument(
        "--server",
        action="store_true",
        help="Start the API server with Admin Panel"
    )
    parser.add_argument(
        "--scheduler",
        action="store_true",
        help="Start the task scheduler daemon"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8431,
        help="Port for API server (default: 8431)"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Use '%(prog)s <command> --help' for command-specific help",
        metavar="<command>"
    )

    # run command
    run_parser = subparsers.add_parser(
        "run",
        help="Run an agent with a message",
        description="Execute an agent with a given message and optional skill activation."
    )
    run_parser.add_argument("agent_id", help="Agent ID")
    run_parser.add_argument("message", help="Message to send")
    run_parser.add_argument("--session", "-s", help="Session ID")
    run_parser.add_argument("--skill", help="Skill to activate")
    run_parser.add_argument("--verbose", "-v", action="store_true",
                           help="Verbose output")
    run_parser.add_argument("--json", "-j", action="store_true",
                           help="Output as JSON")

    # list-agents command
    subparsers.add_parser(
        "list-agents",
        help="List configured agents",
        description="Display all agents defined in the configuration with their details."
    )

    # sessions command
    sessions_parser = subparsers.add_parser(
        "sessions",
        help="List sessions",
        description="List conversation sessions, optionally filtered by agent."
    )
    sessions_parser.add_argument("--agent", "-a", help="Filter by agent ID")

    # skills command
    subparsers.add_parser(
        "skills",
        help="List available skills",
        description="Display all loaded skills with their triggers and status."
    )

    # memory command
    memory_parser = subparsers.add_parser(
        "memory",
        help="Search memory",
        description="Search stored memories by keyword, optionally within a specific topic."
    )
    memory_parser.add_argument("query", help="Search query")
    memory_parser.add_argument("--topic", "-t", help="Topic to search in")
    memory_parser.add_argument("--limit", "-l", type=int, default=10,
                              help="Max results")

    # runs command
    runs_parser = subparsers.add_parser(
        "runs",
        help="List recent runs",
        description="List recent agent execution runs with filtering options."
    )
    runs_parser.add_argument("--agent", "-a", help="Filter by agent ID")
    runs_parser.add_argument("--date", "-d", help="Filter by date (YYYY-MM-DD)")
    runs_parser.add_argument("--limit", "-l", type=int, default=20,
                            help="Max results")

    # fetch-skill command
    fetch_parser = subparsers.add_parser(
        "fetch-skill",
        help="Fetch skill from URL",
        description="Download and install a skill from a remote URL."
    )
    fetch_parser.add_argument("skill_id", help="Skill ID to create")
    fetch_parser.add_argument("url", help="URL to fetch from")

    # interactive command
    interactive_parser = subparsers.add_parser(
        "interactive",
        help="Run in interactive mode",
        description="Start an interactive chat session with an agent. Type 'help' for in-session commands."
    )
    interactive_parser.add_argument("agent_id", nargs="?", default="main",
                                   help="Agent ID (default: main)")

    # status command
    subparsers.add_parser(
        "status",
        help="Show system status",
        description="Display system status including LLM, agents, skills, and memory info."
    )

    # memory-topics command
    subparsers.add_parser(
        "memory-topics",
        help="List memory topics",
        description="List all available memory topics (categories for stored memories)."
    )

    # memory-consolidate command
    consolidate_parser = subparsers.add_parser(
        "memory-consolidate",
        help="Consolidate related memories",
        description="Merge similar memories within topics to reduce redundancy. Use --dry-run to preview."
    )
    consolidate_parser.add_argument("--topic", "-t", help="Specific topic to consolidate")
    consolidate_parser.add_argument("--dry-run", "-n", action="store_true",
                                   help="Show what would be done without making changes")

    # memory-decay command
    decay_parser = subparsers.add_parser(
        "memory-decay",
        help="Apply decay to memory relevance scores",
        description="Apply time-based decay to memory relevance. Low-relevance memories get archived. Use --dry-run to preview."
    )
    decay_parser.add_argument("--dry-run", "-n", action="store_true",
                             help="Show what would be done without making changes")

    # ========================================================================
    # SCHEDULER COMMANDS
    # ========================================================================

    # tasks command - list all tasks
    subparsers.add_parser(
        "tasks",
        help="List scheduled tasks",
        description="List all scheduled tasks with their status and next run time."
    )

    # task-get command
    task_get_parser = subparsers.add_parser(
        "task-get",
        help="Get task details",
        description="Get detailed information about a specific task including schedule and history."
    )
    task_get_parser.add_argument("task_id", help="Task ID")

    # task-create command
    task_create_parser = subparsers.add_parser(
        "task-create",
        help="Create a new task",
        description="Create a new scheduled task with interval, cron, one-shot, or event-only scheduling."
    )
    task_create_parser.add_argument("task_id", help="Task ID")
    task_create_parser.add_argument("--name", "-n", help="Task name")
    task_create_parser.add_argument("--schedule-type", "-t", default="interval",
                                   choices=["interval", "cron", "once", "event_only"],
                                   help="Schedule type")
    task_create_parser.add_argument("--interval", "-i", type=int, default=3600,
                                   help="Interval in seconds (for interval type)")
    task_create_parser.add_argument("--cron", help="Cron expression (for cron type)")
    task_create_parser.add_argument("--agent", "-a", default="main",
                                   help="Agent ID to execute task")
    task_create_parser.add_argument("--content", "-c",
                                   help="Task content (or use --file)")
    task_create_parser.add_argument("--file", "-f",
                                   help="Read task content from file")

    # task-trigger command
    task_trigger_parser = subparsers.add_parser(
        "task-trigger",
        help="Manually trigger a task",
        description="Manually trigger immediate execution of a task, bypassing schedule."
    )
    task_trigger_parser.add_argument("task_id", help="Task ID")

    # task-enable command
    task_enable_parser = subparsers.add_parser(
        "task-enable",
        help="Enable a task",
        description="Enable a disabled task so it will be scheduled for execution."
    )
    task_enable_parser.add_argument("task_id", help="Task ID")

    # task-disable command
    task_disable_parser = subparsers.add_parser(
        "task-disable",
        help="Disable a task",
        description="Disable a task to pause its scheduling without deleting it."
    )
    task_disable_parser.add_argument("task_id", help="Task ID")

    # task-delete command
    task_delete_parser = subparsers.add_parser(
        "task-delete",
        help="Delete a task",
        description="Permanently delete a task and all its run history."
    )
    task_delete_parser.add_argument("task_id", help="Task ID")

    # task-runs command
    task_runs_parser = subparsers.add_parser(
        "task-runs",
        help="Get task run history",
        description="View the execution history for a specific task."
    )
    task_runs_parser.add_argument("task_id", help="Task ID")
    task_runs_parser.add_argument("--limit", "-l", type=int, default=10,
                                 help="Max runs to show")

    # scheduler-start command
    subparsers.add_parser(
        "scheduler-start",
        help="Start the scheduler daemon",
        description="Start the background scheduler daemon to automatically execute tasks on schedule. Press Ctrl+C to stop."
    )

    args = parser.parse_args()

    # Configure centralized logging before any command runs
    from loop_core.config.loader import get_config_manager
    from loop_core.logging_config import setup_logging
    _cfg = get_config_manager()
    setup_logging(level=_cfg.global_config.logging_level, log_file=_cfg.global_config.logging_file)

    # Handle --server and/or --scheduler flags
    if args.server or args.scheduler:
        if args.server:
            # Start API server (scheduler + runtime start automatically)
            cli_start_server(port=args.port)
        else:
            # Start scheduler only (no server)
            cli_start_scheduler_only()
        return

    if args.command is None:
        parser.print_help()
        return

    # Execute command
    if args.command == "run":
        result = cli_run(
            args.agent_id,
            args.message,
            args.session,
            args.skill,
            args.verbose
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if "error" in result:
                print(f"Error: {result['error']}")
            else:
                print(f"\nStatus: {result['status']}")
                print(f"Turns: {result['turns']}")
                if result['tools_called']:
                    print(f"Tools: {', '.join(result['tools_called'])}")
                print(f"Tokens: {result['tokens']}")
                print(f"Duration: {result['duration_ms']}ms")
                print(f"\n--- Response ---\n{result['response']}")

    elif args.command == "list-agents":
        agents = cli_list_agents()
        if agents:
            print("\nConfigured Agents:")
            for agent in agents:
                print(f"  {agent['id']}: {agent.get('name', '')} - {agent.get('description', '')[:50]}")
        else:
            print("No agents configured.")

    elif args.command == "sessions":
        sessions = cli_sessions(args.agent)
        if sessions:
            print(f"\nSessions{f' for {args.agent}' if args.agent else ''}:")
            for sess in sessions:
                print(f"  {sess['session_id']}: {sess.get('status', 'unknown')} ({sess.get('created_at', '')[:10]})")
        else:
            print("No sessions found.")

    elif args.command == "skills":
        skills = cli_skills()
        if skills:
            print("\nAvailable Skills:")
            for skill in skills:
                status = "+" if skill['enabled'] else "-"
                print(f"  [{status}] {skill['id']}: {skill['description'][:50]}")
        else:
            print("No skills loaded.")

    elif args.command == "memory":
        results = cli_memory_search(args.query, args.topic, args.limit)
        if results:
            print(f"\nMemory Search Results for '{args.query}':")
            for r in results:
                print(f"  [{r['topic_id']}] {r['summary']}")
        else:
            print("No results found.")

    elif args.command == "runs":
        runs = cli_runs(args.agent, args.date, args.limit)
        if runs:
            print(f"\nRecent Runs:")
            for run in runs:
                print(f"  {run['date']}/{run['run_id']}: {run.get('agent_id', '')} - {run.get('status', '')}")
        else:
            print("No runs found.")

    elif args.command == "fetch-skill":
        result = cli_fetch_skill(args.skill_id, args.url)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Successfully fetched skill: {result['skill_id']}")

    elif args.command == "interactive":
        cli_interactive(args.agent_id)

    elif args.command == "status":
        status = cli_status()
        if "error" in status:
            print(f"Error: {status['error']}")
        else:
            print("\nSystem Status:")
            print(f"  LLM Initialized: {status.get('llm_initialized', False)}")
            print(f"  LLM Provider: {status.get('llm_provider', 'N/A')}")
            print(f"  Configured Agents: {', '.join(status.get('configured_agents', []))}")
            print(f"  Active Agents: {', '.join(status.get('active_agents', []))}")
            print(f"  Skills Loaded: {status.get('skills_loaded', 0)}")
            print(f"  Memory Topics: {status.get('memory_topics', 0)}")

    elif args.command == "memory-topics":
        topics = cli_memory_topics()
        if topics:
            print("\nMemory Topics:")
            for topic in topics:
                print(f"  - {topic}")
        else:
            print("No memory topics found.")

    elif args.command == "memory-consolidate":
        result = cli_memory_consolidate(
            topic=getattr(args, 'topic', None),
            dry_run=getattr(args, 'dry_run', False)
        )
        if "error" in result:
            print(f"Error: {result['error']}")
        elif result.get("dry_run"):
            print("\nMemory Consolidation (DRY RUN):")
            print(f"  Topics scanned: {result.get('topics_scanned', 0)}")
            print(f"  Potential groups to merge: {result.get('potential_groups', 0)}")
            print(f"  Memories that would be merged: {result.get('potential_memories_merged', 0)}")
        else:
            print("\nMemory Consolidation Complete:")
            print(f"  Topics processed: {result.get('topics_processed', 0)}")
            print(f"  Groups merged: {result.get('memories_merged', 0)}")
            print(f"  Old entries removed: {result.get('memories_removed', 0)}")

    elif args.command == "memory-decay":
        result = cli_memory_decay(dry_run=getattr(args, 'dry_run', False))
        if "error" in result:
            print(f"Error: {result['error']}")
        elif result.get("dry_run"):
            print("\nMemory Decay (DRY RUN):")
            print(f"  Topics scanned: {result.get('topics_scanned', 0)}")
            print(f"  Memories that would decay: {result.get('would_decay', 0)}")
            print(f"  Memories that would be archived: {result.get('would_archive', 0)}")
        else:
            print("\nMemory Decay Applied:")
            print(f"  Memories decayed: {result.get('memories_decayed', 0)}")
            print(f"  Memories archived: {result.get('memories_archived', 0)}")

    # ========================================================================
    # SCHEDULER COMMAND HANDLERS
    # ========================================================================

    elif args.command == "tasks":
        tasks = cli_tasks_list()
        if tasks:
            print("\nScheduled Tasks:")
            for task in tasks:
                status = "+" if task["enabled"] else "-"
                next_run = task.get("next_run", "N/A")
                if next_run and next_run != "N/A":
                    next_run = next_run[:19]  # Trim to datetime
                print(f"  [{status}] {task['task_id']}")
                print(f"      Type: {task['schedule_type']}, Runs: {task.get('run_count', 0)}")
                print(f"      Next: {next_run}")
        else:
            print("No scheduled tasks.")

    elif args.command == "task-get":
        result = cli_task_get(args.task_id)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(json.dumps(result, indent=2))

    elif args.command == "task-create":
        # Read content from file if specified
        content = args.content
        if args.file:
            try:
                with open(args.file, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                print(f"Error reading file: {e}")
                return

        result = cli_task_create(
            task_id=args.task_id,
            name=args.name,
            schedule_type=args.schedule_type,
            interval=args.interval,
            cron=args.cron,
            agent_id=args.agent,
            content=content
        )
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Created task: {result['task_id']}")
            if result.get('next_run'):
                print(f"Next run: {result['next_run']}")

    elif args.command == "task-trigger":
        result = cli_task_trigger(args.task_id)
        if result.get("status") == "error":
            print(f"Error: {result.get('error')}")
        else:
            print(f"Task triggered: {args.task_id}")
            print(f"Status: {result.get('status')}")
            if result.get('response'):
                print(f"\n--- Response ---\n{result['response'][:500]}")

    elif args.command == "task-enable":
        result = cli_task_enable(args.task_id)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(result['message'])

    elif args.command == "task-disable":
        result = cli_task_disable(args.task_id)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(result['message'])

    elif args.command == "task-delete":
        result = cli_task_delete(args.task_id)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(result['message'])

    elif args.command == "task-runs":
        runs = cli_task_runs(args.task_id, args.limit)
        if runs:
            print(f"\nRun history for {args.task_id}:")
            for run in runs:
                status = run.get("result", {}).get("status", "unknown")
                started = run.get("started_at", "")[:19]
                duration = run.get("duration_ms", 0)
                trigger = run.get("trigger", {}).get("type", "unknown")
                print(f"  [{status}] {started} ({duration}ms) - {trigger}")
        else:
            print(f"No runs found for task: {args.task_id}")

    elif args.command == "scheduler-start":
        cli_scheduler_start()


if __name__ == "__main__":
    main()

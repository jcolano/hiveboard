"""
API_APP
=======

FastAPI REST API for the Agentic Loop Framework.

Endpoints:
    POST   /agents/{agent_id}/run     Run an agent
    GET    /agents                    List agents
    GET    /agents/{agent_id}         Get agent info
    GET    /agents/{agent_id}/sessions List agent sessions
    GET    /sessions/{session_id}     Get session details
    DELETE /sessions/{session_id}     Delete session
    GET    /skills                    List skills
    POST   /skills/fetch              Fetch skill from URL
    GET    /memory/search             Search memory
    GET    /runs                      List runs
    GET    /runs/{agent_id}/{date}/{run_id} Get run details
    GET    /health                    Health check
    GET    /status                    System status

Usage:
    uvicorn loop_core.api.app:app --reload --port 8431
"""

import json
import logging
from typing import Optional, List, Any, Dict
from datetime import datetime, timezone
from pathlib import Path

import hiveloop

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, HTTPException, Query, Header, Depends, Cookie, Body, Request
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, Field
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    # Create dummy classes for type hints
    class BaseModel:
        pass
    FastAPI = None

# Static files directory
STATIC_DIR = Path(__file__).parent.parent / "static"

# API Version
API_VERSION = "2026.02.07a"


# ============================================================================
# HELPERS
# ============================================================================

def _summarize_turns(turns: list) -> list:
    """Trim Turn objects for the API — keeps essentials, omits llm_text and tool params/results."""
    result = []
    for t in turns:
        d = {
            "number": t.number,
            "timestamp": t.timestamp,
            "duration_ms": t.duration_ms,
            "tokens": t.tokens_used.to_dict(),
            "tools": [
                {"name": tc.name, "success": tc.result.success,
                 "error": tc.result.error if not tc.result.success else None}
                for tc in t.tool_calls
            ],
            "has_text": bool(t.llm_text),
        }
        if t.plan_step_index is not None:
            d["plan_step_index"] = t.plan_step_index
            d["plan_step_description"] = t.plan_step_description
        result.append(d)
    return result


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

if FASTAPI_AVAILABLE:

    class RunAgentRequest(BaseModel):
        """Request body for running an agent."""
        message: str = Field(..., description="Message to send to the agent")
        session_id: Optional[str] = Field(None, description="Session ID for persistence")
        skill_id: Optional[str] = Field(None, description="Skill to activate")
        phase2_model: Optional[str] = Field(None, description="Model for Phase 2 (None = same as Phase 1)")

    class RunAgentResponse(BaseModel):
        """Response from running an agent."""
        agent_id: str
        session_id: str
        status: str
        response: Optional[str]
        turns: int
        tools_called: List[str]
        total_tokens: int
        duration_ms: int
        error: Optional[str] = None
        pending_events: Optional[List[dict]] = None
        input_tokens: Optional[int] = None
        output_tokens: Optional[int] = None
        execution_trace: Optional[List[dict]] = None
        plan: Optional[dict] = None
        step_stats: Optional[List[dict]] = None
        reflections: Optional[List[dict]] = None
        turn_details: Optional[List[dict]] = None
        journal: Optional[List[dict]] = None

    class AgentInfo(BaseModel):
        """Agent information."""
        agent_id: str
        name: str
        description: str
        model: str
        max_turns: int
        enabled_tools: List[str]
        enabled_skills: List[str] = []  # Optional, not all agents have skills
        registry_skills: List[str] = []  # Per-agent curated skills from skills/registry.json
        total_runs: int
        active_sessions: int
        heartbeat_context_count: int = 3  # Number of prior heartbeat summaries to inject
        active: bool = False             # Runtime: agent is started
        queue_depth: int = 0             # Runtime: pending events

    class SessionInfo(BaseModel):
        """Session information."""
        session_id: str
        agent_id: str
        status: str
        created_at: str
        updated_at: Optional[str] = None
        message_count: int

    class SkillInfo(BaseModel):
        """Skill information."""
        id: str
        name: str
        description: str
        triggers: List[str]
        enabled: bool

    class FetchSkillRequest(BaseModel):
        """Request to fetch a skill."""
        skill_id: str
        url: str

    class MemorySearchResult(BaseModel):
        """Memory search result."""
        topic_id: str
        summary: str
        keywords: List[str]
        score: float

    class RunInfo(BaseModel):
        """Run information."""
        run_id: str
        agent_id: str
        session_id: str
        timestamp: str
        status: str
        turns: int
        duration_ms: int

    class HealthResponse(BaseModel):
        """Health check response."""
        status: str
        version: str
        timestamp: str

    class StatusResponse(BaseModel):
        """System status response."""
        llm_initialized: bool
        llm_provider: Optional[str]
        configured_agents: List[str]
        active_agents: List[str]
        skills_loaded: int
        memory_topics: int

    class TaskInfo(BaseModel):
        """Scheduled task information."""
        task_id: str
        name: str
        description: Optional[str] = None
        schedule_type: str
        enabled: bool
        skill_id: Optional[str] = None  # Linked skill (None = auto-match)
        agent_id: Optional[str] = None
        next_run: Optional[str] = None
        last_run: Optional[str] = None
        run_count: int = 0

    class CreateAgentRequest(BaseModel):
        """Request to create an agent."""
        agent_id: str
        name: str
        description: str = ""
        role: str = ""
        model: str = "claude-sonnet-4-5-20250929"
        provider: str = "anthropic"
        temperature: float = 0.0
        max_tokens: int = 4096
        max_turns: int = 30
        timeout_seconds: int = 600
        system_prompt: str = "You are a helpful AI assistant."
        enabled_tools: List[str] = []
        enabled_skills: List[str] = []
        heartbeat_context_count: int = 3
        heartbeat_enabled: bool = True
        heartbeat_interval_minutes: int = 15

    class UpdateAgentRequest(BaseModel):
        """Request to update an agent."""
        name: Optional[str] = None
        description: Optional[str] = None
        role: Optional[str] = None
        model: Optional[str] = None
        provider: Optional[str] = None
        temperature: Optional[float] = None
        max_tokens: Optional[int] = None
        max_turns: Optional[int] = None
        timeout_seconds: Optional[int] = None
        system_prompt: Optional[str] = None
        enabled_tools: Optional[List[str]] = None
        enabled_skills: Optional[List[str]] = None
        heartbeat_context_count: Optional[int] = None
        heartbeat_enabled: Optional[bool] = None
        heartbeat_interval_minutes: Optional[int] = None

    class CreateTaskRequest(BaseModel):
        """Request to create or update a task."""
        task_id: str
        name: str
        schedule_type: str = "interval"
        interval_seconds: int = 3600
        cron_expression: Optional[str] = None
        run_at: Optional[str] = None  # For "once" schedule type
        events: Optional[List[str]] = None  # For "event_only" schedule type
        agent_id: str = "main"
        content: str = ""
        skill_id: Optional[str] = None  # Explicit skill link (None = auto-match at runtime)
        enabled: bool = True
        description: str = ""
        timeout_seconds: Optional[int] = None
        max_turns: Optional[int] = None
        context: Optional[dict] = None  # Task context with keywords (skills, task params, inject)
        created_by: str = "human"  # "human", "agent:{id}", "system"

    class TriggerTaskResponse(BaseModel):
        """Response from triggering a task."""
        task_id: str
        status: str
        response: Optional[str] = None
        error: Optional[str] = None

    # Skill Editor Models
    class SkillEditorIntentRequest(BaseModel):
        """Request to generate skill hypothesis from intent."""
        intent: str = Field(..., description="Natural language description of desired skill")

    class FormFieldModel(BaseModel):
        """A form field for skill editor."""
        id: str
        type: str
        question: str
        description: Optional[str] = None
        options: Optional[List[str]] = None
        default: Optional[Any] = None  # Can be str, bool, int, list, etc.
        required: bool = True
        placeholder: Optional[str] = None

    class SkillHypothesisModel(BaseModel):
        """LLM's hypothesis about the skill."""
        name: str
        description: str
        suggested_id: str
        suggested_triggers: List[str]
        suggested_tools: List[str]
        suggested_files: List[str]
        reasoning: str

    class EditorFormResponse(BaseModel):
        """Response with hypothesis and form fields."""
        hypothesis: SkillHypothesisModel
        fields: List[FormFieldModel]
        created_at: str

    class GenerateSkillRequest(BaseModel):
        """Request to generate skill from form answers."""
        form: EditorFormResponse
        answers: dict
        skill_id: Optional[str] = None

    class SkillFilesResponse(BaseModel):
        """Generated skill files."""
        skill_json: dict
        skill_md: str
        additional_files: dict = {}

    class SaveSkillRequest(BaseModel):
        """Request to save generated skill."""
        agent_id: str
        skill_id: str
        skill_files: SkillFilesResponse
        form: EditorFormResponse
        answers: dict

    class UpdateSkillRequest(BaseModel):
        """Request to update an existing skill."""
        answers: dict

    class ClientRegisterRequest(BaseModel):
        """Request to register a desktop client."""
        client_id: str
        client_version: str
        platform: str
        capabilities: List[str] = []

    class WakeWebhookRequest(BaseModel):
        """Request body for /hooks/wake/{agent_id}."""
        text: str = Field(..., description="Event text to inject")
        mode: str = Field("next", description="'now' or 'next'")

    class AgentWebhookRequest(BaseModel):
        """Request body for /hooks/agent/{agent_id}."""
        message: str = Field(..., description="Prompt for the agent")
        name: Optional[str] = Field(None, description="Label for logging")
        sessionKey: Optional[str] = Field(None, description="Session isolation key")
        deliver: bool = Field(True, description="Whether to send the reply")
        channel: Optional[str] = Field(None, description="Output channel plugin name")
        to: Optional[str] = Field(None, description="Destination within channel")


# ============================================================================
# APPLICATION FACTORY
# ============================================================================

def create_app() -> "FastAPI":
    """Create and configure the FastAPI application."""
    if not FASTAPI_AVAILABLE:
        raise ImportError("FastAPI is not installed. Install with: pip install fastapi uvicorn")

    app = FastAPI(
        title="Agentic Loop API",
        description="REST API for the Agentic Loop Framework",
        version=API_VERSION,
        docs_url="/docs",
        redoc_url="/redoc"
    )

    # Lazy initialization of manager
    _manager = None

    def get_manager():
        nonlocal _manager
        if _manager is None:
            from ..agent_manager import get_agent_manager
            _manager = get_agent_manager()
        return _manager

    # ========================================================================
    # AUTH DEPENDENCIES
    # ========================================================================

    # Import auth module
    try:
        from .auth import (
            get_current_user_dependency,
            require_auth_dependency,
            require_platform_admin_dependency,
            apply_tenant_filter,
            User
        )
        AUTH_AVAILABLE = True
    except ImportError:
        AUTH_AVAILABLE = False
        User = None

    # Create dependencies
    if AUTH_AVAILABLE:
        get_optional_user = get_current_user_dependency()
        require_user = require_auth_dependency()
        require_platform_admin = require_platform_admin_dependency()
    else:
        # Fallback: no auth required
        async def get_optional_user():
            return None
        async def require_user():
            return None
        async def require_platform_admin():
            return None

    def can_access_agent(user, agent_config) -> bool:
        """Check if user can access an agent."""
        if user is None:
            return True  # No auth enabled
        if user.is_platform_admin:
            return True
        return getattr(agent_config, 'company_id', 'default') == user.company_id

    async def verify_agent_access(agent_id: str, current_user) -> None:
        """
        Verify that the current user can access the given agent.
        Raises HTTPException if access denied.
        """
        from ..config.loader import get_config_manager

        config_mgr = get_config_manager()
        try:
            agent_config = config_mgr.load_agent(agent_id)
            if current_user and not can_access_agent(current_user, agent_config):
                raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    # ========================================================================
    # HEALTH & STATUS ENDPOINTS
    # ========================================================================

    @app.get("/version", tags=["System"])
    async def get_version():
        """Get API version information."""
        return {
            "name": "loopCore",
            "version": API_VERSION,
            "description": "Agentic Loop Framework API"
        }

    @app.get("/health", response_model=HealthResponse, tags=["System"])
    async def health_check():
        """Health check endpoint."""
        return HealthResponse(
            status="healthy",
            version=API_VERSION,
            timestamp=datetime.now().isoformat()
        )

    @app.post("/shutdown", tags=["System"])
    async def shutdown_server():
        """Shutdown the API server process."""
        import os
        import signal
        import threading

        def shutdown():
            # Give time for response to be sent
            import time
            time.sleep(0.5)
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=shutdown, daemon=True).start()
        return {"status": "shutting_down", "message": "Server will shutdown in 0.5 seconds"}

    @app.get("/status", response_model=StatusResponse, tags=["System"])
    async def get_status():
        """Get system status."""
        manager = get_manager()
        status = manager.get_status()
        return StatusResponse(**status)

    @app.get("/debug/truncations", tags=["System", "Debug"])
    async def get_truncation_log():
        """
        Get the in-memory log of JSON truncation repairs.

        Useful for debugging LLM response truncation issues.
        Returns details about each repair including:
        - caller: which component triggered the LLM call
        - repair_type: what fix was applied
        - missing braces/brackets count
        - response tail (last 300 chars)
        - token usage if available
        """
        from llm_client import get_truncation_log
        return {
            "truncations": get_truncation_log(),
            "count": len(get_truncation_log())
        }

    @app.delete("/debug/truncations", tags=["System", "Debug"])
    async def clear_truncation_log():
        """Clear the in-memory truncation log."""
        from llm_client import clear_truncation_log
        clear_truncation_log()
        return {"status": "cleared"}

    @app.post("/debug/llm-logging", tags=["System", "Debug"])
    async def toggle_llm_debug_logging(enabled: bool = Query(True, description="Enable or disable LLM debug logging")):
        """
        Enable or disable detailed LLM request/response logging to file.

        When enabled, logs are written to: data/LOGS/llm_debug_{date}.jsonl
        """
        from llm_client import setup_llm_debug_logging
        setup_llm_debug_logging(enabled=enabled)
        return {"status": "enabled" if enabled else "disabled"}

    @app.get("/debug/llm-usage", tags=["System", "Debug"])
    async def get_llm_usage():
        """Get LLM usage statistics for the current session."""
        from llm_client import get_default_client
        client = get_default_client()
        return client.get_usage_summary()

    @app.get("/debug/prompts", tags=["Debug"])
    async def get_prompt_debug_status():
        """Check if prompt logging is enabled."""
        from llm_client import get_default_client
        client = get_default_client()
        return {"debug_prompts": getattr(client, 'debug_prompts', False), "output_dir": "data/LOGS/prompts/"}

    @app.post("/debug/prompts", tags=["Debug"])
    async def toggle_prompt_debug(enable: bool = True):
        """Enable/disable full prompt dumping to data/LOGS/prompts/."""
        from llm_client import get_default_client
        client = get_default_client()
        client.debug_prompts = enable
        client._prompt_counter = 0
        return {"debug_prompts": enable, "output_dir": "data/LOGS/prompts/"}

    @app.get("/debug/agents/{agent_id}/memory", tags=["Debug"])
    async def get_agent_memory(agent_id: str, current_user = Depends(require_platform_admin)):
        """Get contents of an agent's memory directory (admin only)."""
        data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "loopCore"
        memory_dir = data_dir / "AGENTS" / agent_id / "memory"

        if not memory_dir.exists():
            return {"agent_id": agent_id, "memory": {}, "exists": False}

        memory = {}
        for path in memory_dir.glob("*.json"):
            try:
                with open(path) as f:
                    memory[path.name] = json.load(f)
            except Exception as e:
                memory[path.name] = {"error": str(e)}

        # Also include non-json files list
        other_files = [p.name for p in memory_dir.iterdir() if not p.name.endswith(".json")]

        return {"agent_id": agent_id, "memory": memory, "other_files": other_files, "exists": True}

    @app.post("/debug/agents/{agent_id}/memory/{filename}", tags=["Debug"])
    async def write_agent_memory(
        agent_id: str,
        filename: str,
        data: dict,
        current_user = Depends(require_platform_admin)
    ):
        """Write data to an agent's memory file (admin only). Use this to fix missing credentials."""
        if not filename.endswith(".json"):
            filename += ".json"

        data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "loopCore"
        memory_dir = data_dir / "AGENTS" / agent_id / "memory"

        # Create memory dir if needed
        memory_dir.mkdir(parents=True, exist_ok=True)

        file_path = memory_dir / filename

        with open(file_path, "w") as f:
            json.dump(data, f, indent=2)

        return {"success": True, "path": str(file_path), "data": data}

    @app.get("/debug/agents/{agent_id}/files", tags=["Debug"])
    async def list_agent_files(agent_id: str, current_user = Depends(require_platform_admin)):
        """List all files in an agent's directory (admin only)."""
        data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "loopCore"
        agent_dir = data_dir / "AGENTS" / agent_id

        if not agent_dir.exists():
            raise HTTPException(status_code=404, detail=f"Agent directory not found: {agent_id}")

        files = []
        for path in agent_dir.rglob("*"):
            if path.is_file():
                rel_path = path.relative_to(agent_dir)
                files.append({
                    "path": str(rel_path),
                    "size": path.stat().st_size
                })

        return {"agent_id": agent_id, "files": files, "count": len(files)}

    @app.get("/debug/agents/{agent_id}/tasks", tags=["Debug"])
    async def get_agent_tasks_debug(agent_id: str, current_user = Depends(require_platform_admin)):
        """Get all task definitions for an agent (admin only)."""
        data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "loopCore"
        tasks_dir = data_dir / "AGENTS" / agent_id / "tasks"

        if not tasks_dir.exists():
            return {"agent_id": agent_id, "tasks": [], "exists": False}

        tasks = []
        for task_dir in tasks_dir.iterdir():
            if task_dir.is_dir():
                task_json = task_dir / "task.json"
                task_md = task_dir / "task.md"

                task_info = {"task_id": task_dir.name}

                if task_json.exists():
                    try:
                        with open(task_json) as f:
                            task_info["config"] = json.load(f)
                    except Exception as e:
                        task_info["config_error"] = str(e)

                if task_md.exists():
                    try:
                        task_info["instructions"] = task_md.read_text()
                    except Exception as e:
                        task_info["instructions_error"] = str(e)

                tasks.append(task_info)

        return {"agent_id": agent_id, "tasks": tasks, "count": len(tasks)}

    # ========================================================================
    # AGENT ENDPOINTS
    # ========================================================================

    @app.get("/agents/{agent_id}/todo", tags=["Agents"])
    async def get_agent_todo(agent_id: str, status: str = Query("all", description="Filter: pending, completed, all")):
        """Get an agent's TO-DO list."""
        import json as _json
        data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "loopCore"
        todo_path = data_dir / "AGENTS" / agent_id / "todo.json"
        if not todo_path.exists():
            return {"agent_id": agent_id, "items": [], "pending": 0, "completed": 0}
        try:
            items = _json.loads(todo_path.read_text(encoding="utf-8"))
        except Exception:
            items = []
        pending = sum(1 for t in items if t.get("status") == "pending")
        completed = sum(1 for t in items if t.get("status") == "completed")
        if status != "all":
            items = [t for t in items if t.get("status") == status]
        return {"agent_id": agent_id, "items": items, "pending": pending, "completed": completed}

    @app.delete("/agents/{agent_id}/todo", tags=["Agents"])
    async def clear_agent_todo(agent_id: str):
        """Clear an agent's TO-DO list."""
        data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "loopCore"
        todo_path = data_dir / "AGENTS" / agent_id / "todo.json"
        if todo_path.exists():
            todo_path.unlink()
        return {"status": "ok", "agent_id": agent_id}

    # ========================================================================
    # AGENT ISSUES
    # ========================================================================

    @app.get("/agents/{agent_id}/issues", tags=["Agents"])
    async def get_agent_issues(
        agent_id: str,
        status: str = Query("open", description="Filter: open, dismissed, all"),
    ):
        """Get an agent's issues list."""
        import json as _json
        data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "loopCore"
        issues_path = data_dir / "AGENTS" / agent_id / "issues.json"
        if not issues_path.exists():
            return {"agent_id": agent_id, "items": [], "open": 0, "dismissed": 0}
        try:
            items = _json.loads(issues_path.read_text(encoding="utf-8"))
        except Exception:
            items = []
        open_count = sum(1 for i in items if i.get("status") == "open")
        dismissed_count = sum(1 for i in items if i.get("status") == "dismissed")
        if status != "all":
            items = [i for i in items if i.get("status") == status]
        return {
            "agent_id": agent_id,
            "items": items,
            "open": open_count,
            "dismissed": dismissed_count,
        }

    @app.post("/agents/{agent_id}/issues/{issue_id}/dismiss", tags=["Agents"])
    async def dismiss_agent_issue(
        agent_id: str,
        issue_id: str,
        body: dict = Body(default=None),
    ):
        """Dismiss an agent issue. Optionally create a TODO from todo_on_dismiss."""
        import json as _json
        from datetime import datetime, timezone

        data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "loopCore"
        issues_path = data_dir / "AGENTS" / agent_id / "issues.json"
        if not issues_path.exists():
            raise HTTPException(status_code=404, detail="No issues found for this agent")
        try:
            items = _json.loads(issues_path.read_text(encoding="utf-8"))
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to read issues file")

        issue = None
        for i in items:
            if i.get("id") == issue_id:
                issue = i
                break
        if not issue:
            raise HTTPException(status_code=404, detail=f"Issue {issue_id} not found")
        if issue.get("status") == "dismissed":
            return {"status": "already_dismissed", "issue_id": issue_id}

        now = datetime.now(timezone.utc).isoformat()
        issue["status"] = "dismissed"
        issue["dismissed_at"] = now
        issues_path.write_text(_json.dumps(items, indent=2), encoding="utf-8")

        # HiveLoop: resolve issue
        try:
            _agent_obj = get_manager().get_agent(agent_id)
            _hl_agent = getattr(_agent_obj, "_hiveloop", None) if _agent_obj else None
            if _hl_agent:
                _hl_agent.resolve_issue(
                    summary=issue.get("title", ""),
                    issue_id=issue_id,
                )
        except Exception:
            pass

        # Optionally create a TODO from todo_on_dismiss
        todo_created = False
        create_todo = (body or {}).get("create_todo", False)
        todo_text = issue.get("todo_on_dismiss")
        if create_todo and todo_text:
            todo_path = data_dir / "AGENTS" / agent_id / "todo.json"
            try:
                todos = _json.loads(todo_path.read_text(encoding="utf-8")) if todo_path.exists() else []
            except Exception:
                todos = []
            # Next todo ID
            max_n = 0
            for t in todos:
                tid = t.get("id", "")
                if tid.startswith("td_"):
                    try:
                        max_n = max(max_n, int(tid[3:]))
                    except ValueError:
                        pass
            new_todo = {
                "id": f"td_{max_n + 1:03d}",
                "task": todo_text,
                "status": "pending",
                "priority": "high",
                "context": f"Auto-created from dismissed issue {issue_id}",
                "created_at": now,
                "completed_at": None,
            }
            todos.append(new_todo)
            todo_path.write_text(_json.dumps(todos, indent=2), encoding="utf-8")
            todo_created = True

        return {
            "status": "dismissed",
            "issue_id": issue_id,
            "todo_created": todo_created,
        }

    @app.get("/agents/{agent_id}/heartbeat-history", tags=["Agents"])
    async def get_heartbeat_history(
        agent_id: str,
        limit: int = Query(20, description="Max entries to return (most recent first)"),
    ):
        """Get an agent's heartbeat history (most recent first)."""
        import json as _json
        data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "loopCore"
        hb_path = data_dir / "AGENTS" / agent_id / "heartbeat_history.json"
        if not hb_path.exists():
            return {"agent_id": agent_id, "entries": []}
        try:
            entries = _json.loads(hb_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []
        # Most recent first, capped to limit
        entries = list(reversed(entries))[:limit]
        return {"agent_id": agent_id, "entries": entries}

    @app.get("/agents", tags=["Agents"])
    async def list_agents(
        include_deleted: bool = Query(False, description="Include soft-deleted agents"),
        current_user = Depends(get_optional_user)
    ):
        """List all configured agents (filtered by tenant)."""
        from ..config.loader import get_config_manager

        manager = get_manager()
        config_mgr = get_config_manager()
        agents = []

        for agent_id in manager.list_agents():
            # Check if agent is soft-deleted
            try:
                agent_config = config_mgr.load_agent(agent_id)
                is_deleted = getattr(agent_config, 'is_deleted', False)

                # Skip deleted agents unless include_deleted is True
                if is_deleted and not include_deleted:
                    continue

                # Tenant filtering: skip if user doesn't have access
                if current_user and not can_access_agent(current_user, agent_config):
                    continue

                info = manager.get_agent_info(agent_id)
                if info:
                    info['is_deleted'] = is_deleted
                    info['company_id'] = getattr(agent_config, 'company_id', 'default')
                    agents.append(info)
                else:
                    agents.append({
                        "agent_id": agent_id,
                        "name": agent_id,
                        "is_deleted": is_deleted,
                        "company_id": getattr(agent_config, 'company_id', 'default')
                    })
            except Exception:
                # If we can't load the config, include it anyway (for platform admin)
                if current_user and not current_user.is_platform_admin:
                    continue
                info = manager.get_agent_info(agent_id)
                if info:
                    info['is_deleted'] = False
                    info['company_id'] = 'default'
                    agents.append(info)
                else:
                    agents.append({"agent_id": agent_id, "name": agent_id, "is_deleted": False, "company_id": "default"})

        return {"agents": agents}

    @app.get("/agents/{agent_id}", response_model=AgentInfo, tags=["Agents"])
    async def get_agent(agent_id: str, current_user = Depends(get_optional_user)):
        """Get agent information."""
        from ..config.loader import get_config_manager

        manager = get_manager()
        config_mgr = get_config_manager()

        # Check tenant access
        try:
            agent_config = config_mgr.load_agent(agent_id)
            if current_user and not can_access_agent(current_user, agent_config):
                raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        info = manager.get_agent_info(agent_id)
        if info is None:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        return AgentInfo(**info)

    @app.post("/agents", tags=["Agents"])
    async def create_agent(request: CreateAgentRequest, current_user = Depends(get_optional_user)):
        """Create a new agent configuration."""
        from ..config.loader import AgentConfig, LLMConfig, get_config_manager

        # Check if agent already exists
        config_mgr = get_config_manager()
        existing_agents = config_mgr.list_agents()
        if request.agent_id in existing_agents:
            raise HTTPException(status_code=400, detail=f"Agent already exists: {request.agent_id}")

        # Set company_id from current user
        company_id = "default"
        if current_user:
            company_id = current_user.company_id

        # Create agent config
        # Note: enabled_skills is not part of AgentConfig - skills are managed via SkillLoader
        agent_config = AgentConfig(
            agent_id=request.agent_id,
            name=request.name,
            description=request.description,
            role=request.role,
            company_id=company_id,
            llm=LLMConfig(
                provider=request.provider,
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens
            ),
            system_prompt=request.system_prompt,
            max_turns=request.max_turns,
            timeout_seconds=request.timeout_seconds,
            enabled_tools=request.enabled_tools or [],
            heartbeat_context_count=request.heartbeat_context_count,
            heartbeat_enabled=request.heartbeat_enabled,
            heartbeat_interval_minutes=request.heartbeat_interval_minutes,
        )

        # Save to file
        config_mgr.save_agent(agent_config)

        return {
            "status": "ok",
            "agent_id": request.agent_id,
            "message": f"Agent '{request.name}' created successfully"
        }

    @app.put("/agents/{agent_id}", tags=["Agents"])
    async def update_agent(agent_id: str, request: UpdateAgentRequest, current_user = Depends(get_optional_user)):
        """Update an existing agent configuration."""
        from ..config.loader import get_config_manager

        config_mgr = get_config_manager()

        # Check if agent exists
        existing_agents = config_mgr.list_agents()
        if agent_id not in existing_agents:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        # Load existing config
        agent_config = config_mgr.load_agent(agent_id)

        # Check tenant access
        if current_user and not can_access_agent(current_user, agent_config):
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        # Update fields that were provided
        if request.name is not None:
            agent_config.name = request.name
        if request.description is not None:
            agent_config.description = request.description
        if request.role is not None:
            agent_config.role = request.role
        if request.model is not None:
            agent_config.llm.model = request.model
        if request.provider is not None:
            agent_config.llm.provider = request.provider
        if request.temperature is not None:
            agent_config.llm.temperature = request.temperature
        if request.max_tokens is not None:
            agent_config.llm.max_tokens = request.max_tokens
        if request.max_turns is not None:
            agent_config.max_turns = request.max_turns
        if request.timeout_seconds is not None:
            agent_config.timeout_seconds = request.timeout_seconds
        if request.system_prompt is not None:
            agent_config.system_prompt = request.system_prompt
        if request.enabled_tools is not None:
            agent_config.enabled_tools = request.enabled_tools
        if request.heartbeat_context_count is not None:
            agent_config.heartbeat_context_count = request.heartbeat_context_count
        if request.heartbeat_enabled is not None:
            agent_config.heartbeat_enabled = request.heartbeat_enabled
        if request.heartbeat_interval_minutes is not None:
            agent_config.heartbeat_interval_minutes = request.heartbeat_interval_minutes
        # Note: enabled_skills is ignored - skills are managed via SkillLoader, not AgentConfig

        # Save updated config
        config_mgr.save_agent(agent_config)

        # Clear cached agent if it was loaded
        manager = get_manager()
        if agent_id in manager._agents:
            del manager._agents[agent_id]

        return {
            "status": "ok",
            "agent_id": agent_id,
            "message": f"Agent '{agent_config.name}' updated successfully"
        }

    @app.delete("/agents/{agent_id}", tags=["Agents"])
    async def delete_agent(agent_id: str, current_user = Depends(get_optional_user)):
        """Soft delete an agent configuration (sets is_deleted flag)."""
        from ..config.loader import get_config_manager

        config_mgr = get_config_manager()

        # Check if agent exists
        existing_agents = config_mgr.list_agents()
        if agent_id not in existing_agents:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        # Prevent deleting the main agent
        if agent_id == "main":
            raise HTTPException(status_code=400, detail="Cannot delete the main agent")

        # Load agent config and set is_deleted flag
        agent_config = config_mgr.load_agent(agent_id)

        # Check tenant access
        if current_user and not can_access_agent(current_user, agent_config):
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        agent_config.is_deleted = True
        config_mgr.save_agent(agent_config)

        # Remove from cache if loaded
        manager = get_manager()
        if agent_id in manager._agents:
            del manager._agents[agent_id]

        return {
            "status": "ok",
            "deleted": agent_id,
            "soft_delete": True
        }

    @app.post("/agents/{agent_id}/restore", tags=["Agents"])
    async def restore_agent(agent_id: str, current_user = Depends(get_optional_user)):
        """Restore a soft-deleted agent."""
        from ..config.loader import get_config_manager

        config_mgr = get_config_manager()

        # Check if agent exists
        existing_agents = config_mgr.list_agents()
        if agent_id not in existing_agents:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        # Load agent config and clear is_deleted flag
        agent_config = config_mgr.load_agent(agent_id)

        # Check tenant access
        if current_user and not can_access_agent(current_user, agent_config):
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        if not agent_config.is_deleted:
            raise HTTPException(status_code=400, detail=f"Agent is not deleted: {agent_id}")

        agent_config.is_deleted = False
        config_mgr.save_agent(agent_config)

        return {
            "status": "ok",
            "restored": agent_id
        }

    @app.post("/agents/{agent_id}/run", response_model=RunAgentResponse, tags=["Agents"])
    async def run_agent(agent_id: str, request: RunAgentRequest, current_user = Depends(get_optional_user)):
        """Run an agent with a message."""
        import asyncio
        from ..config.loader import get_config_manager

        # Check tenant access first
        config_mgr = get_config_manager()
        try:
            agent_config = config_mgr.load_agent(agent_id)
            if current_user and not can_access_agent(current_user, agent_config):
                raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        manager = get_manager()

        # Check if LLM is initialized
        status = manager.get_status()
        if not status.get("llm_initialized"):
            raise HTTPException(
                status_code=503,
                detail="LLM client not initialized. Check API key configuration."
            )

        try:
            # Run agent in a thread pool so the event loop stays free.
            # Without this, agents that use http_request to localhost deadlock —
            # the server can't serve the request because the event loop is
            # blocked running the agent that made the request.
            loop = asyncio.get_event_loop()

            # Generate session_id if not provided (enables conversation persistence)
            import uuid as _uuid
            session_id = request.session_id or f"chat_{_uuid.uuid4().hex[:8]}"

            # Build event_context for direct chat calls
            chat_event_context = {
                "source": "human",
                "priority": "HIGH",
                "session_key": session_id,
                "event_id": None,
                "triggered_skills": [],
                "agent_status": "started",
            }

            if request.skill_id:
                chat_event_context["skill_id"] = request.skill_id

            result = await loop.run_in_executor(
                None,
                lambda: manager.run_agent(
                    agent_id,
                    request.message,
                    session_id,
                    event_context=chat_event_context,
                    phase2_model=request.phase2_model,
                )
            )

            lr = result.loop_result
            return RunAgentResponse(
                agent_id=result.agent_id,
                session_id=result.session_id,
                status=result.status,
                response=result.final_response,
                turns=result.turns,
                tools_called=result.tools_called,
                total_tokens=result.total_tokens,
                duration_ms=result.total_duration_ms,
                error=result.error,
                pending_events=result.pending_events if result.pending_events else None,
                input_tokens=lr.total_tokens.input_tokens if lr else None,
                output_tokens=lr.total_tokens.output_tokens if lr else None,
                execution_trace=lr.execution_trace if lr and lr.execution_trace else None,
                plan=lr.plan if lr else None,
                step_stats=lr.get_step_stats() if lr and lr.execution_trace else None,
                reflections=[r.to_dict() for r in lr.reflections] if lr and lr.reflections else None,
                turn_details=_summarize_turns(lr.turns) if lr and lr.turns else None,
                journal=lr.journal if lr and lr.journal else None,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/agents/{agent_id}/sessions", tags=["Agents", "Sessions"])
    async def list_agent_sessions(agent_id: str, current_user = Depends(get_optional_user)):
        """List sessions for an agent."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        sessions = manager.list_sessions(agent_id)
        return {"agent_id": agent_id, "sessions": sessions}

    @app.get("/agents/{agent_id}/sessions/{session_id}", tags=["Agents", "Sessions"])
    async def get_agent_session(agent_id: str, session_id: str, current_user = Depends(get_optional_user)):
        """Get session details for a specific agent."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        session = manager.get_session(session_id, agent_id=agent_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        return session.to_dict()

    @app.delete("/agents/{agent_id}/sessions/{session_id}", tags=["Agents", "Sessions"])
    async def delete_agent_session(agent_id: str, session_id: str, current_user = Depends(get_optional_user)):
        """Delete a session for a specific agent."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        result = manager.delete_session(session_id, agent_id=agent_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        return {"deleted": session_id, "agent_id": agent_id}

    # ========================================================================
    # SESSION ENDPOINTS
    # ========================================================================

    @app.get("/sessions", tags=["Sessions"])
    async def list_sessions(agent_id: Optional[str] = None):
        """List all sessions."""
        manager = get_manager()
        sessions = manager.list_sessions(agent_id)
        return {"sessions": sessions}

    @app.get("/sessions/{session_id}", tags=["Sessions"])
    async def get_session(session_id: str):
        """Get session details."""
        manager = get_manager()
        session = manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        return session.to_dict()

    @app.delete("/sessions/{session_id}", tags=["Sessions"])
    async def delete_session(session_id: str):
        """Delete a session."""
        manager = get_manager()
        result = manager.delete_session(session_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        return {"deleted": session_id}

    # ========================================================================
    # TOOL ENDPOINTS
    # ========================================================================

    @app.get("/tools", tags=["Tools"])
    async def list_tools():
        """List all registered tools with metadata (built-in + extensions)."""
        manager = get_manager()
        # Tool registry is populated when the first agent is created.
        # Force-create the default agent if no registry exists yet.
        if manager.tool_registry is None:
            try:
                manager.create_agent("main")
            except Exception:
                pass
        registry = manager.tool_registry
        if registry is None:
            return {"tools": []}
        return {"tools": registry.get_tool_details()}

    # ========================================================================
    # SKILL ENDPOINTS
    # ========================================================================

    @app.get("/skills", tags=["Skills"])
    async def list_skills():
        """List all available skills (global only for backward compatibility)."""
        manager = get_manager()
        if manager.global_skill_loader is None:
            return {"skills": []}

        skills = []
        for skill_id in manager.global_skill_loader.list_skills():
            skill = manager.global_skill_loader.get_skill(skill_id)
            if skill:
                skills.append({
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description,
                    "triggers": skill.triggers,
                    "enabled": skill.enabled,
                    "source": skill.source
                })
        return {"skills": skills}

    @app.get("/skills/global", tags=["Skills"])
    async def list_global_skills():
        """List all global skills (inherited by all agents)."""
        manager = get_manager()
        if manager.global_skill_loader is None:
            return {"skills": []}

        skills = []
        for skill_id in manager.global_skill_loader.list_global_skills():
            skill = manager.global_skill_loader.get_skill(skill_id)
            if skill:
                skills.append({
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description,
                    "triggers": skill.triggers,
                    "enabled": skill.enabled,
                    "source": "global"
                })
        return {"skills": skills, "source": "global"}

    @app.get("/agents/{agent_id}/skills", tags=["Agents", "Skills"])
    async def list_agent_skills(agent_id: str, include_deleted: bool = False, current_user = Depends(get_optional_user)):
        """List skills available to an agent (global + private)."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()

        # Get agent's skill loader (includes both global and private)
        skill_loader = manager._get_agent_skill_loader(agent_id)

        global_skills = []
        private_skills = []

        for skill_id in skill_loader.list_global_skills():
            skill = skill_loader.get_skill(skill_id)
            if skill:
                global_skills.append({
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description,
                    "triggers": skill.triggers,
                    "enabled": skill.enabled,
                    "is_deleted": skill.is_deleted,
                    "source": "global"
                })

        for skill_id in skill_loader.list_private_skills(include_deleted=include_deleted):
            skill = skill_loader.get_skill(skill_id, include_deleted=include_deleted)
            if skill:
                private_skills.append({
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description,
                    "triggers": skill.triggers,
                    "enabled": skill.enabled,
                    "is_deleted": skill.is_deleted,
                    "source": "private"
                })

        return {
            "agent_id": agent_id,
            "global_skills": global_skills,
            "private_skills": private_skills,
            "total": len(global_skills) + len(private_skills)
        }

    @app.get("/agents/{agent_id}/skills/editable", tags=["Skill Editor"])
    async def list_editable_skills(agent_id: str):
        """List skills that can be edited (created with the editor)."""
        try:
            from ..skills.editor import SkillEditor

            manager = get_manager()
            editor = SkillEditor(manager.llm_client, str(manager.config_manager.global_config.paths.agents_dir))

            editable = editor.list_editable_skills(agent_id)

            return {
                "agent_id": agent_id,
                "editable_skills": editable
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # NOTE: This catch-all route must come AFTER /skills/editable and /skills/import
    @app.get("/agents/{agent_id}/skills/{skill_id}", tags=["Agents", "Skills"])
    async def get_agent_skill(agent_id: str, skill_id: str, current_user = Depends(get_optional_user)):
        """Get details of a specific skill available to an agent."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        skill_loader = manager._get_agent_skill_loader(agent_id)

        skill = skill_loader.get_skill(skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

        # Determine source
        source = "global" if skill_id in skill_loader.list_global_skills() else "private"

        return {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "triggers": skill.triggers,
            "enabled": skill.enabled,
            "source": source,
            "requires": {"tools": skill.requires.get("tools", [])} if skill.requires else {},
            "content": skill.content[:1000] if skill.content else None  # Preview only
        }

    def find_skill_dir(agent_id: str, skill_id: str) -> Optional[Path]:
        """
        Find a skill directory by skill_id.

        First tries direct folder name match, then searches by internal ID
        in skill.json files (for auto-generated folder names like sk_xxxxx).
        """
        manager = get_manager()
        config = manager.config_manager
        skills_base = Path(config.global_config.paths.agents_dir) / agent_id / "skills"

        # Try direct folder name match first
        direct_path = skills_base / skill_id
        if direct_path.exists() and (direct_path / "skill.json").exists():
            return direct_path

        # Search by internal ID in skill.json files
        if skills_base.exists():
            for skill_json_path in skills_base.glob("*/skill.json"):
                try:
                    data = json.loads(skill_json_path.read_text(encoding='utf-8'))
                    if data.get("id") == skill_id:
                        return skill_json_path.parent
                except (json.JSONDecodeError, IOError):
                    continue

        return None

    def _auto_restart_agent_if_active(agent_id: str) -> bool:
        """Lightweight skill refresh on a running agent.

        Called after skill add/remove/restore so the runtime picks up
        the new skill set and heartbeat list. No stop/start cycle —
        clears caches and refreshes state.heartbeat_skills in-place.
        The next LLM execution will build a fresh system prompt from
        the updated skill loader. Returns True if agent was active.
        """
        try:
            runtime = get_runtime()
            if runtime:
                result = runtime.refresh_heartbeat_skills(agent_id)
                if result.get("status") == "ok":
                    logger.info(f"Refreshed skills for active agent '{agent_id}'")
                    return True
        except Exception as e:
            logger.warning(f"Could not refresh agent '{agent_id}': {e}")
        return False

    def _sync_agent_registry(
        agent_id: str,
        skill_id: str,
        skill_data: dict,
        skill_dir: Path,
        action: str = "add",
    ) -> None:
        """Sync a single skill entry in the agent's per-agent registry.json.

        Args:
            agent_id: Agent ID
            skill_id: Skill folder name / ID
            skill_data: Parsed skill.json dict (ignored for action="remove")
            skill_dir: Absolute path to skill directory on disk
            action: "add" to upsert, "remove" to delete entry
        """
        from ..skills.registry import AgentSkillRegistry

        manager = get_manager()
        agents_dir = Path(manager.config_manager.global_config.paths.agents_dir)
        registry_path = agents_dir / agent_id / "skills" / "registry.json"

        if action == "remove":
            # For remove we need the name — try reading skill_data if provided
            name = (skill_data or {}).get("name") or skill_id
            AgentSkillRegistry.remove_entry(registry_path, name)
        else:
            name = skill_data.get("name") or skill_data.get("id") or skill_id
            description = skill_data.get("description", "")
            # Relative path from agent's skills dir to skill.md
            agent_skills_dir = agents_dir / agent_id / "skills"
            try:
                rel_path = str((skill_dir / "skill.md").relative_to(agent_skills_dir))
                rel_path = rel_path.replace("\\", "/")  # Normalize for Windows
            except ValueError:
                rel_path = f"{skill_id}/skill.md"

            # Auto-detect heartbeat from heartbeat.md presence
            heartbeat = None
            if (skill_dir / "heartbeat.md").exists():
                heartbeat = {
                    "interval_minutes": 15,
                    "prompt": f"Run {name} heartbeat routine.",
                }

            AgentSkillRegistry.upsert_entry(
                registry_path, name, description, rel_path, heartbeat
            )

        # Clear cached registry so it reloads from disk
        manager._agent_skill_registries.pop(agent_id, None)

    def _rebuild_agent_registry(agent_id: str) -> dict:
        """Scan all skill directories and rebuild the agent's registry.json.

        Returns the rebuilt registry dict.
        """
        from ..skills.registry import AgentSkillRegistry

        manager = get_manager()
        agents_dir = Path(manager.config_manager.global_config.paths.agents_dir)
        agent_skills_dir = agents_dir / agent_id / "skills"
        registry_path = agent_skills_dir / "registry.json"

        # Start fresh
        data = {"version": "1.0", "skills": []}

        if agent_skills_dir.exists():
            # Walk all subdirectories (including nested vendor dirs)
            for skill_json_path in agent_skills_dir.rglob("skill.json"):
                if skill_json_path.name != "skill.json":
                    continue
                try:
                    skill_data = json.loads(
                        skill_json_path.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError):
                    continue

                # Skip deleted skills
                if skill_data.get("is_deleted", False):
                    continue

                skill_dir = skill_json_path.parent
                name = skill_data.get("name") or skill_data.get("id") or skill_dir.name
                description = skill_data.get("description", "")

                try:
                    rel_path = str(
                        (skill_dir / "skill.md").relative_to(agent_skills_dir)
                    )
                    rel_path = rel_path.replace("\\", "/")
                except ValueError:
                    rel_path = f"{skill_dir.name}/skill.md"

                entry = {"name": name, "description": description, "path": rel_path}

                # Auto-detect heartbeat
                if (skill_dir / "heartbeat.md").exists():
                    entry["heartbeat"] = {
                        "interval_minutes": 15,
                        "prompt": f"Run {name} heartbeat routine.",
                    }

                data["skills"].append(entry)

        # Write registry
        agent_skills_dir.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # Clear cached registry
        manager._agent_skill_registries.pop(agent_id, None)

        logger.info(
            f"Rebuilt registry for agent '{agent_id}': {len(data['skills'])} skills"
        )
        return data

    @app.delete("/agents/{agent_id}/skills/{skill_id}", tags=["Agents", "Skills"])
    async def delete_agent_skill(agent_id: str, skill_id: str, hard_delete: bool = False, current_user = Depends(get_optional_user)):
        """
        Soft-delete a private skill from an agent.

        Sets is_deleted=true in skill.json. Use hard_delete=true to permanently remove.
        """
        await verify_agent_access(agent_id, current_user)
        import shutil
        manager = get_manager()
        config = manager.config_manager

        # Find skill directory (supports both folder name and internal ID lookup)
        skill_dir = find_skill_dir(agent_id, skill_id)
        if not skill_dir:
            raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

        skill_json_path = skill_dir / "skill.json"

        # Check if it's a private skill (in agent's directory)
        agent_skills_dir = Path(config.global_config.paths.agents_dir) / agent_id / "skills"
        if not str(skill_dir).startswith(str(agent_skills_dir)):
            raise HTTPException(status_code=400, detail="Cannot delete global skills")

        try:
            # Get the skill loader to update cache
            skill_loader = manager._get_agent_skill_loader(agent_id)
            # Get the internal skill ID from the JSON (may differ from skill_id parameter)
            skill_data = json.loads(skill_json_path.read_text(encoding='utf-8'))
            internal_skill_id = skill_data.get("id", skill_id)

            if hard_delete:
                # Permanently remove
                shutil.rmtree(skill_dir)
                # Remove from skill loader cache
                if internal_skill_id in skill_loader._private_skills:
                    del skill_loader._private_skills[internal_skill_id]
                if internal_skill_id in skill_loader._skills:
                    del skill_loader._skills[internal_skill_id]
                if internal_skill_id in skill_loader._deleted_skills:
                    del skill_loader._deleted_skills[internal_skill_id]
                logger.info(f"Hard-deleted skill {skill_id} for agent {agent_id}")
                _sync_agent_registry(agent_id, skill_id, skill_data, skill_dir, action="remove")
                _auto_restart_agent_if_active(agent_id)
                return {"status": "ok", "message": f"Skill '{skill_id}' permanently deleted"}
            else:
                # Soft delete - set is_deleted flag
                skill_data["is_deleted"] = True
                skill_data["deleted_at"] = datetime.now().isoformat()
                skill_json_path.write_text(json.dumps(skill_data, indent=2), encoding='utf-8')

                # Move skill from _private_skills to _deleted_skills in cache
                if internal_skill_id in skill_loader._private_skills:
                    skill = skill_loader._private_skills.pop(internal_skill_id)
                    skill.is_deleted = True
                    skill_loader._deleted_skills[internal_skill_id] = skill
                if internal_skill_id in skill_loader._skills:
                    del skill_loader._skills[internal_skill_id]

                logger.info(f"Soft-deleted skill {skill_id} for agent {agent_id}")
                _sync_agent_registry(agent_id, skill_id, skill_data, skill_dir, action="remove")
                _auto_restart_agent_if_active(agent_id)
                return {"status": "ok", "message": f"Skill '{skill_id}' deleted"}
        except Exception as e:
            logger.error(f"Failed to delete skill: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/agents/{agent_id}/skills/{skill_id}/restore", tags=["Agents", "Skills"])
    async def restore_agent_skill(agent_id: str, skill_id: str, current_user = Depends(get_optional_user)):
        """Restore a soft-deleted skill."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()

        # Find skill directory (supports both folder name and internal ID lookup)
        skill_dir = find_skill_dir(agent_id, skill_id)
        if not skill_dir:
            raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

        skill_json_path = skill_dir / "skill.json"

        try:
            skill_data = json.loads(skill_json_path.read_text(encoding='utf-8'))
            internal_skill_id = skill_data.get("id", skill_id)

            if not skill_data.get("is_deleted", False):
                return {"status": "ok", "message": f"Skill '{skill_id}' is not deleted"}

            skill_data["is_deleted"] = False
            skill_data.pop("deleted_at", None)
            skill_json_path.write_text(json.dumps(skill_data, indent=2), encoding='utf-8')

            # Move skill from _deleted_skills back to _private_skills in cache
            skill_loader = manager._get_agent_skill_loader(agent_id)
            if internal_skill_id in skill_loader._deleted_skills:
                skill = skill_loader._deleted_skills.pop(internal_skill_id)
                skill.is_deleted = False
                skill_loader._private_skills[internal_skill_id] = skill
                skill_loader._skills[internal_skill_id] = skill

            logger.info(f"Restored skill {skill_id} for agent {agent_id}")
            _sync_agent_registry(agent_id, skill_id, skill_data, skill_dir, action="add")
            _auto_restart_agent_if_active(agent_id)
            return {"status": "ok", "message": f"Skill '{skill_id}' restored"}
        except Exception as e:
            logger.error(f"Failed to restore skill: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/agents/{agent_id}/skills/rebuild-registry", tags=["Agents", "Skills"])
    async def rebuild_agent_skill_registry(agent_id: str, current_user = Depends(get_optional_user)):
        """Rebuild the per-agent skills/registry.json by scanning all skill directories.

        Useful as a recovery mechanism when the registry is missing or stale.
        """
        await verify_agent_access(agent_id, current_user)
        try:
            data = _rebuild_agent_registry(agent_id)
            restarted = _auto_restart_agent_if_active(agent_id)
            return {
                "status": "ok",
                "agent_id": agent_id,
                "skills_found": len(data.get("skills", [])),
                "registry": data,
                "agent_restarted": restarted,
            }
        except Exception as e:
            logger.error(f"Failed to rebuild registry for agent '{agent_id}': {e}")
            raise HTTPException(status_code=500, detail=str(e))

    class ImportSkillRequest(BaseModel):
        skill_md: str  # Required: the skill markdown content
        skill_id: Optional[str] = None  # Optional: auto-generated if not provided

    @app.post("/agents/{agent_id}/skills/import", tags=["Agents", "Skills"])
    async def import_agent_skill(agent_id: str, request: ImportSkillRequest):
        """
        Import a skill from markdown content.

        The skill.json metadata will be automatically generated by LLM
        based on the markdown content.
        """
        from ..skills.editor import SkillEditor

        manager = get_manager()

        if not request.skill_md or not request.skill_md.strip():
            raise HTTPException(status_code=400, detail="skill_md content is required")

        try:
            editor = SkillEditor(
                manager.llm_client,
                str(manager.config_manager.global_config.paths.agents_dir)
            )

            # Import skill - LLM generates skill.json from markdown
            skill_dir = editor.import_skill_from_md(
                agent_id=agent_id,
                skill_md=request.skill_md,
                skill_id=request.skill_id
            )

            # Read back the generated skill.json to get the ID
            skill_json_path = skill_dir / "skill.json"
            skill_json = json.loads(skill_json_path.read_text(encoding='utf-8'))
            skill_id = skill_json.get("id")

            # Refresh the skill loader cache so the new skill appears immediately
            if agent_id in manager._agent_skill_loaders:
                manager._agent_skill_loaders[agent_id].load_all()

            logger.info(f"Imported skill {skill_id} for agent {agent_id}")
            _sync_agent_registry(agent_id, skill_id, skill_json, skill_dir, action="add")
            _auto_restart_agent_if_active(agent_id)
            return {
                "status": "ok",
                "skill_id": skill_id,
                "skill_name": skill_json.get("name"),
                "message": f"Skill '{skill_json.get('name', skill_id)}' imported successfully"
            }
        except Exception as e:
            logger.error(f"Failed to import skill: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/skills/fetch", tags=["Skills"])
    async def fetch_skill(request: FetchSkillRequest):
        """Fetch a skill from URL."""
        manager = get_manager()
        if manager.skill_loader is None:
            raise HTTPException(status_code=503, detail="Skill loader not available")

        skill = manager.skill_loader.fetch_from_url(request.skill_id, request.url)
        if skill is None:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to fetch skill from {request.url}"
            )

        return {
            "skill_id": skill.id,
            "name": skill.name,
            "status": "fetched"
        }

    # ========================================================================
    # SKILL TEMPLATES ENDPOINTS
    # ========================================================================

    @app.get("/skills/templates", tags=["Skill Templates"])
    async def list_skill_templates(category: str = None):
        """List available skill templates."""
        manager = get_manager()
        templates_dir = Path(manager.config_manager.global_config.paths.config_dir).parent / "SKILLS_TEMPLATES"
        registry_path = templates_dir / "registry.json"

        if not registry_path.exists():
            return {"templates": [], "categories": []}

        try:
            registry = json.loads(registry_path.read_text(encoding='utf-8'))
            templates = registry.get("templates", [])
            categories = registry.get("categories", [])

            # Filter by category if specified
            if category:
                templates = [t for t in templates if t.get("category") == category]

            return {
                "templates": templates,
                "categories": categories,
                "total": len(templates)
            }
        except Exception as e:
            logger.error(f"Failed to load templates registry: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/skills/templates/{template_id}", tags=["Skill Templates"])
    async def get_skill_template(template_id: str):
        """Get a skill template with full content."""
        manager = get_manager()
        templates_dir = Path(manager.config_manager.global_config.paths.config_dir).parent / "SKILLS_TEMPLATES"
        template_dir = templates_dir / template_id
        skill_json_path = template_dir / "skill.json"
        skill_md_path = template_dir / "skill.md"

        if not skill_json_path.exists():
            raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")

        try:
            skill_data = json.loads(skill_json_path.read_text(encoding='utf-8'))
            skill_content = ""
            if skill_md_path.exists():
                skill_content = skill_md_path.read_text(encoding='utf-8')

            # Load template form if it exists
            form_path = template_dir / "_template_form.json"
            template_form = None
            if form_path.exists():
                template_form = json.loads(form_path.read_text(encoding='utf-8'))

            # Load additional .md files (heartbeat.md, etc.)
            additional_files = {}
            for md_file in template_dir.glob("*.md"):
                if md_file.name != "skill.md":
                    additional_files[md_file.name] = md_file.read_text(encoding='utf-8')

            return {
                "id": template_id,
                "skill_json": skill_data,
                "skill_md": skill_content,
                "template_form": template_form,
                "additional_files": additional_files
            }
        except Exception as e:
            logger.error(f"Failed to load template: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/agents/{agent_id}/skills/from-template", tags=["Skill Templates"])
    async def create_skill_from_template(
        agent_id: str,
        template_id: str,
        skill_id: str = None,
        form_answers: Optional[Dict[str, Any]] = Body(default=None),
        current_user = Depends(get_optional_user)
    ):
        """
        Create a skill from a template.

        Args:
            agent_id: Target agent
            template_id: Template to use
            skill_id: Custom skill ID (defaults to template_id)
            form_answers: Optional dict of placeholder key→value for template substitution
        """
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        templates_dir = Path(manager.config_manager.global_config.paths.config_dir).parent / "SKILLS_TEMPLATES"
        template_dir = templates_dir / template_id
        skill_json_path = template_dir / "skill.json"
        skill_md_path = template_dir / "skill.md"

        if not skill_json_path.exists():
            raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")

        try:
            skill_data = json.loads(skill_json_path.read_text(encoding='utf-8'))
            skill_content = skill_md_path.read_text(encoding='utf-8') if skill_md_path.exists() else ""

            # Use custom skill_id if provided
            final_skill_id = skill_id or template_id

            # Apply placeholder substitution if form answers provided
            def apply_substitution(text, answers):
                for key, value in answers.items():
                    placeholder = "{{" + key + "}}"
                    val_str = ", ".join(value) if isinstance(value, list) else str(value)
                    text = text.replace(placeholder, val_str)
                return text

            if form_answers:
                skill_content = apply_substitution(skill_content, form_answers)

            # Update skill data
            skill_data["id"] = final_skill_id
            skill_data["source"] = {
                "type": "template",
                "template_id": template_id,
                "created_at": datetime.now().isoformat()
            }

            # Save to agent's skills directory
            agents_dir = Path(manager.config_manager.global_config.paths.agents_dir)
            skill_dir = agents_dir / agent_id / "skills" / final_skill_id
            skill_dir.mkdir(parents=True, exist_ok=True)

            (skill_dir / "skill.json").write_text(json.dumps(skill_data, indent=2), encoding='utf-8')
            (skill_dir / "skill.md").write_text(skill_content, encoding='utf-8')

            # Copy ALL files from template dir (except template-only metadata)
            skip_files = {"skill.json", "skill.md", "_template_form.json"}
            for src_file in template_dir.iterdir():
                if src_file.is_file() and src_file.name not in skip_files:
                    content = src_file.read_text(encoding='utf-8')
                    if form_answers and src_file.suffix == ".md":
                        content = apply_substitution(content, form_answers)
                    (skill_dir / src_file.name).write_text(content, encoding='utf-8')

            logger.info(f"Created skill {final_skill_id} from template {template_id} for agent {agent_id}")

            # Clear cached skill loader and registry so agent picks up the new skill
            manager = get_manager()
            if agent_id in manager._agent_skill_loaders:
                del manager._agent_skill_loaders[agent_id]

            # Sync per-agent registry.json
            _sync_agent_registry(agent_id, final_skill_id, skill_data, skill_dir, action="add")

            # If agent is running in the runtime, restart it to pick up new skills
            restarted = _auto_restart_agent_if_active(agent_id)

            return {
                "status": "ok",
                "skill_id": final_skill_id,
                "agent_id": agent_id,
                "message": f"Skill '{final_skill_id}' created from template '{template_id}'",
                "agent_restarted": restarted,
            }
        except Exception as e:
            logger.error(f"Failed to create skill from template: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ========================================================================
    # SKILL VENDORS ENDPOINTS
    # ========================================================================

    @app.get("/skills/vendors", tags=["Skill Vendors"])
    async def list_skill_vendors():
        """List all registered skill vendors."""
        manager = get_manager()
        vendors_dir = Path(manager.config_manager.global_config.paths.config_dir).parent / "SKILLS_VENDORS"
        registry_path = vendors_dir / "registry.json"

        if not registry_path.exists():
            return {"vendors": []}

        try:
            registry = json.loads(registry_path.read_text(encoding='utf-8'))
            vendor_list = []

            for vendor_ref in registry.get("vendors", []):
                vendor_id = vendor_ref.get("id")
                vendor_file = vendors_dir / f"{vendor_id}.json"

                if vendor_file.exists():
                    vendor_data = json.loads(vendor_file.read_text(encoding='utf-8'))
                    vendor_list.append({
                        "id": vendor_data.get("id"),
                        "name": vendor_data.get("name"),
                        "description": vendor_data.get("description"),
                        "website": vendor_data.get("website"),
                        "logo_url": vendor_data.get("logo_url"),
                        "enabled": vendor_data.get("enabled", True),
                        "skill_count": len(vendor_data.get("skills", []))
                    })

            return {"vendors": vendor_list}
        except Exception as e:
            logger.error(f"Failed to load vendors: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/skills/vendors/{vendor_id}", tags=["Skill Vendors"])
    async def get_skill_vendor(vendor_id: str):
        """Get vendor details including their available skills."""
        manager = get_manager()
        vendors_dir = Path(manager.config_manager.global_config.paths.config_dir).parent / "SKILLS_VENDORS"
        vendor_file = vendors_dir / f"{vendor_id}.json"

        if not vendor_file.exists():
            raise HTTPException(status_code=404, detail=f"Vendor not found: {vendor_id}")

        try:
            vendor_data = json.loads(vendor_file.read_text(encoding='utf-8'))
            return vendor_data
        except Exception as e:
            logger.error(f"Failed to load vendor: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/skills/vendors", tags=["Skill Vendors"])
    async def create_skill_vendor(vendor: dict):
        """Create a new skill vendor."""
        manager = get_manager()
        vendors_dir = Path(manager.config_manager.global_config.paths.config_dir).parent / "SKILLS_VENDORS"
        vendors_dir.mkdir(parents=True, exist_ok=True)

        vendor_id = vendor.get("id")
        if not vendor_id:
            raise HTTPException(status_code=400, detail="Vendor ID is required")

        vendor_file = vendors_dir / f"{vendor_id}.json"
        if vendor_file.exists():
            raise HTTPException(status_code=409, detail=f"Vendor already exists: {vendor_id}")

        try:
            # Save vendor file
            vendor_file.write_text(json.dumps(vendor, indent=2), encoding='utf-8')

            # Update registry
            registry_path = vendors_dir / "registry.json"
            if registry_path.exists():
                registry = json.loads(registry_path.read_text(encoding='utf-8'))
            else:
                registry = {"version": "1.0.0", "vendors": []}

            registry["vendors"].append({"id": vendor_id, "name": vendor.get("name"), "enabled": True})
            registry["last_updated"] = datetime.now().isoformat()
            registry_path.write_text(json.dumps(registry, indent=2), encoding='utf-8')

            logger.info(f"Created vendor: {vendor_id}")
            return {"status": "ok", "vendor_id": vendor_id, "message": f"Vendor '{vendor_id}' created"}
        except Exception as e:
            logger.error(f"Failed to create vendor: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/skills/vendors/{vendor_id}", tags=["Skill Vendors"])
    async def update_skill_vendor(vendor_id: str, vendor: dict):
        """Update an existing skill vendor."""
        manager = get_manager()
        vendors_dir = Path(manager.config_manager.global_config.paths.config_dir).parent / "SKILLS_VENDORS"
        vendor_file = vendors_dir / f"{vendor_id}.json"

        if not vendor_file.exists():
            raise HTTPException(status_code=404, detail=f"Vendor not found: {vendor_id}")

        try:
            vendor["id"] = vendor_id  # Ensure ID matches
            vendor_file.write_text(json.dumps(vendor, indent=2), encoding='utf-8')
            logger.info(f"Updated vendor: {vendor_id}")
            return {"status": "ok", "vendor_id": vendor_id, "message": f"Vendor '{vendor_id}' updated"}
        except Exception as e:
            logger.error(f"Failed to update vendor: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/skills/vendors/{vendor_id}", tags=["Skill Vendors"])
    async def delete_skill_vendor(vendor_id: str):
        """Delete a skill vendor."""
        manager = get_manager()
        vendors_dir = Path(manager.config_manager.global_config.paths.config_dir).parent / "SKILLS_VENDORS"
        vendor_file = vendors_dir / f"{vendor_id}.json"

        if not vendor_file.exists():
            raise HTTPException(status_code=404, detail=f"Vendor not found: {vendor_id}")

        try:
            # Remove vendor file
            vendor_file.unlink()

            # Update registry
            registry_path = vendors_dir / "registry.json"
            if registry_path.exists():
                registry = json.loads(registry_path.read_text(encoding='utf-8'))
                registry["vendors"] = [v for v in registry.get("vendors", []) if v.get("id") != vendor_id]
                registry["last_updated"] = datetime.now().isoformat()
                registry_path.write_text(json.dumps(registry, indent=2), encoding='utf-8')

            logger.info(f"Deleted vendor: {vendor_id}")
            return {"status": "ok", "vendor_id": vendor_id, "message": f"Vendor '{vendor_id}' deleted"}
        except Exception as e:
            logger.error(f"Failed to delete vendor: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/agents/{agent_id}/skills/from-vendor", tags=["Skill Vendors"])
    async def create_skill_from_vendor(agent_id: str, vendor_id: str, skill_id: str, custom_skill_id: str = None, current_user = Depends(get_optional_user)):
        """
        Fetch and install a skill from an external vendor.

        Downloads the skill files from the vendor's URLs and saves to the agent's skills folder.
        """
        await verify_agent_access(agent_id, current_user)
        import requests

        manager = get_manager()
        vendors_dir = Path(manager.config_manager.global_config.paths.config_dir).parent / "SKILLS_VENDORS"
        vendor_file = vendors_dir / f"{vendor_id}.json"

        if not vendor_file.exists():
            raise HTTPException(status_code=404, detail=f"Vendor not found: {vendor_id}")

        try:
            vendor_data = json.loads(vendor_file.read_text(encoding='utf-8'))
            base_url = vendor_data.get("base_url", "")

            # Find the skill in vendor's skill list
            skill_info = None
            for s in vendor_data.get("skills", []):
                if s.get("id") == skill_id:
                    skill_info = s
                    break

            if not skill_info:
                raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found in vendor '{vendor_id}'")

            # Determine final skill ID
            final_skill_id = custom_skill_id or skill_id

            # Fetch skill files from vendor
            files = skill_info.get("files", {})
            skill_json_data = None
            skill_md_content = ""

            # Fetch skill.json if available
            if files.get("skill_json"):
                url = base_url + files["skill_json"]
                resp = requests.get(url, timeout=30)
                if resp.status_code == 200:
                    skill_json_data = resp.json()
                else:
                    logger.warning(f"Failed to fetch skill.json from {url}: {resp.status_code}")

            # Fetch skill.md
            if files.get("skill_md"):
                url = base_url + files["skill_md"]
                resp = requests.get(url, timeout=30)
                if resp.status_code == 200:
                    skill_md_content = resp.text
                else:
                    raise HTTPException(status_code=502, detail=f"Failed to fetch skill.md from vendor: {url}")

            # Build skill.json if not provided
            if not skill_json_data:
                skill_json_data = {
                    "id": final_skill_id,
                    "name": skill_info.get("name", skill_id),
                    "description": skill_info.get("description", ""),
                    "version": "1.0.0",
                    "triggers": [skill_id.replace("_", " ")],
                    "requires": {"tools": []},
                    "enabled": True
                }

            # Update skill metadata
            skill_json_data["id"] = final_skill_id
            skill_json_data["source"] = {
                "type": "vendor",
                "vendor_id": vendor_id,
                "vendor_name": vendor_data.get("name"),
                "original_skill_id": skill_id,
                "fetched_at": datetime.now().isoformat()
            }

            # Save to agent's skills directory under vendor subfolder
            # Path: data/AGENTS/{agent_id}/skills/{vendor_id}/{skill_id}/
            agents_dir = Path(manager.config_manager.global_config.paths.agents_dir)
            skill_dir = agents_dir / agent_id / "skills" / vendor_id / final_skill_id
            skill_dir.mkdir(parents=True, exist_ok=True)

            (skill_dir / "skill.json").write_text(json.dumps(skill_json_data, indent=2), encoding='utf-8')
            (skill_dir / "skill.md").write_text(skill_md_content, encoding='utf-8')

            # Copy ALL auxiliary files from the local global skills dir if it exists
            global_skill_dir = Path(manager.config_manager.global_config.paths.config_dir).parent / "SKILLS" / skill_id
            if global_skill_dir.is_dir():
                for src_file in global_skill_dir.iterdir():
                    if src_file.is_file() and src_file.name not in ("skill.json", "skill.md"):
                        import shutil
                        shutil.copy2(str(src_file), str(skill_dir / src_file.name))

            # Refresh the skill loader cache so the new skill appears immediately
            if agent_id in manager._agent_skill_loaders:
                manager._agent_skill_loaders[agent_id].load_all()

            logger.info(f"Installed skill {final_skill_id} from vendor {vendor_id} for agent {agent_id}")
            _sync_agent_registry(agent_id, final_skill_id, skill_json_data, skill_dir, action="add")
            _auto_restart_agent_if_active(agent_id)

            return {
                "status": "ok",
                "skill_id": final_skill_id,
                "agent_id": agent_id,
                "vendor_id": vendor_id,
                "skill_dir": str(skill_dir),
                "message": f"Skill '{final_skill_id}' installed from '{vendor_data.get('name')}'"
            }
        except requests.RequestException as e:
            logger.error(f"Failed to fetch from vendor: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to fetch from vendor: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to install skill from vendor: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ========================================================================
    # SKILL EDITOR ENDPOINTS
    # ========================================================================

    @app.post("/skills/editor/hypothesis", tags=["Skill Editor"])
    async def generate_skill_hypothesis(request: SkillEditorIntentRequest):
        """
        Generate a skill hypothesis and dynamic form from user intent.

        Step 1 of skill creation: User describes what they want,
        LLM generates hypothesis and form questions.
        """
        try:
            from ..skills.editor import SkillEditor

            manager = get_manager()
            editor = SkillEditor(manager.llm_client, str(manager.config_manager.global_config.paths.agents_dir))

            form = editor.generate_hypothesis(request.intent)

            return {
                "hypothesis": form.hypothesis.to_dict(),
                "fields": [f.to_dict() for f in form.fields],
                "created_at": form.created_at
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/skills/editor/generate", tags=["Skill Editor"])
    async def generate_skill_files(request: GenerateSkillRequest):
        """
        Generate skill files from form and answers.

        Step 2 of skill creation: After user fills out the form,
        generate the actual skill files.
        """
        try:
            from ..skills.editor import SkillEditor, EditorForm, SkillHypothesis, FormField, FormFieldType

            manager = get_manager()
            editor = SkillEditor(manager.llm_client, str(manager.config_manager.global_config.paths.agents_dir))

            # Reconstruct form from request
            hypothesis = SkillHypothesis(
                name=request.form.hypothesis.name,
                description=request.form.hypothesis.description,
                suggested_id=request.form.hypothesis.suggested_id,
                suggested_triggers=request.form.hypothesis.suggested_triggers,
                suggested_tools=request.form.hypothesis.suggested_tools,
                suggested_files=request.form.hypothesis.suggested_files,
                reasoning=request.form.hypothesis.reasoning
            )

            fields = []
            for f in request.form.fields:
                fields.append(FormField(
                    id=f.id,
                    type=FormFieldType(f.type),
                    question=f.question,
                    description=f.description,
                    options=f.options,
                    default=f.default,
                    required=f.required,
                    placeholder=f.placeholder
                ))

            form = EditorForm(
                hypothesis=hypothesis,
                fields=fields,
                created_at=request.form.created_at
            )

            skill_files = editor.generate_skill(form, request.answers, request.skill_id)

            return {
                "skill_json": skill_files.skill_json,
                "skill_md": skill_files.skill_md,
                "additional_files": skill_files.additional_files
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/skills/editor/save", tags=["Skill Editor"])
    async def save_generated_skill(request: SaveSkillRequest):
        """
        Save generated skill files to disk.

        Step 3 of skill creation: Persist the skill and editor form
        for future editing.
        """
        try:
            from ..skills.editor import SkillEditor, EditorForm, SkillHypothesis, SkillFiles, FormField, FormFieldType

            manager = get_manager()
            editor = SkillEditor(manager.llm_client, str(manager.config_manager.global_config.paths.agents_dir))

            # Reconstruct form
            hypothesis = SkillHypothesis(
                name=request.form.hypothesis.name,
                description=request.form.hypothesis.description,
                suggested_id=request.form.hypothesis.suggested_id,
                suggested_triggers=request.form.hypothesis.suggested_triggers,
                suggested_tools=request.form.hypothesis.suggested_tools,
                suggested_files=request.form.hypothesis.suggested_files,
                reasoning=request.form.hypothesis.reasoning
            )

            fields = []
            for f in request.form.fields:
                fields.append(FormField(
                    id=f.id,
                    type=FormFieldType(f.type),
                    question=f.question,
                    description=f.description,
                    options=f.options,
                    default=f.default,
                    required=f.required,
                    placeholder=f.placeholder
                ))

            # Prepare answers (make a mutable copy)
            answers = dict(request.answers)

            # Check if "Other" field has content - expand into proper fields
            other_content = answers.get('_other', '').strip() if isinstance(answers.get('_other'), str) else ''
            if other_content:
                try:
                    new_fields, new_answers = editor.expand_other_field(
                        EditorForm(hypothesis=hypothesis, fields=fields, created_at=request.form.created_at),
                        other_content
                    )
                    # Merge new fields
                    fields.extend(new_fields)
                    # Merge new answers
                    answers.update(new_answers)
                    # Clear _other since it's now captured in proper fields
                    answers.pop('_other', None)
                    logger.info(f"Expanded 'Other' into {len(new_fields)} new fields")
                except Exception as e:
                    logger.warning(f"Failed to expand 'Other' field: {e}")
                    # Continue without expansion - the skill will still be saved

            form = EditorForm(
                hypothesis=hypothesis,
                fields=fields,
                created_at=request.form.created_at
            )

            skill_files = SkillFiles(
                skill_json=request.skill_files.skill_json,
                skill_md=request.skill_files.skill_md,
                additional_files=request.skill_files.additional_files
            )

            skill_dir = editor.save_skill(
                request.agent_id,
                request.skill_id,
                skill_files,
                form,
                answers
            )

            # Refresh the skill loader cache so the new skill appears immediately
            if request.agent_id in manager._agent_skill_loaders:
                manager._agent_skill_loaders[request.agent_id].load_all()

            # Sync per-agent registry.json
            skill_json_data = skill_files.skill_json if isinstance(skill_files.skill_json, dict) else {}
            _sync_agent_registry(request.agent_id, request.skill_id, skill_json_data, skill_dir, action="add")
            _auto_restart_agent_if_active(request.agent_id)

            return {
                "status": "ok",
                "skill_id": request.skill_id,
                "agent_id": request.agent_id,
                "skill_dir": str(skill_dir),
                "expanded_other": bool(other_content)
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/agents/{agent_id}/skills/editable", tags=["Skill Editor"])
    async def list_editable_skills(agent_id: str, current_user = Depends(get_optional_user)):
        """List skills that can be edited (created with the editor)."""
        await verify_agent_access(agent_id, current_user)
        try:
            from ..skills.editor import SkillEditor

            manager = get_manager()
            editor = SkillEditor(manager.llm_client, str(manager.config_manager.global_config.paths.agents_dir))

            editable = editor.list_editable_skills(agent_id)

            return {
                "agent_id": agent_id,
                "editable_skills": editable
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # NOTE: This catch-all route must come AFTER all specific /skills/* routes
    @app.get("/skills/{skill_id}", tags=["Skills"])
    async def get_skill(skill_id: str):
        """Get skill details."""
        manager = get_manager()
        if manager.skill_loader is None:
            raise HTTPException(status_code=404, detail="Skill loader not available")

        skill = manager.skill_loader.get_skill(skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

        return {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "triggers": skill.triggers,
            "enabled": skill.enabled,
            "content": skill.content[:1000] if skill.content else None,
            "files": list(skill.files.keys())
        }

    @app.get("/agents/{agent_id}/skills/{skill_id}/editor", tags=["Skill Editor"])
    async def get_skill_editor_form(agent_id: str, skill_id: str, current_user = Depends(get_optional_user)):
        """Get the editor form for an existing skill (for editing)."""
        await verify_agent_access(agent_id, current_user)
        try:
            from ..skills.editor import SkillEditor

            manager = get_manager()
            editor = SkillEditor(manager.llm_client, str(manager.config_manager.global_config.paths.agents_dir))

            editor_data = editor.load_skill_for_editing(agent_id, skill_id)

            if editor_data is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Skill {skill_id} was not created with the editor or does not exist"
                )

            return editor_data
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/agents/{agent_id}/skills/{skill_id}/editor", tags=["Skill Editor"])
    async def update_skill_via_editor(agent_id: str, skill_id: str, request: UpdateSkillRequest, current_user = Depends(get_optional_user)):
        """Update an existing skill with new answers."""
        await verify_agent_access(agent_id, current_user)
        try:
            from ..skills.editor import SkillEditor

            manager = get_manager()
            editor = SkillEditor(manager.llm_client, str(manager.config_manager.global_config.paths.agents_dir))

            skill_files = editor.update_skill(agent_id, skill_id, request.answers)

            # Refresh the skill loader cache
            if agent_id in manager._agent_skill_loaders:
                manager._agent_skill_loaders[agent_id].load_all()

            # Re-sync per-agent registry.json (skill content may have changed)
            agents_dir = Path(manager.config_manager.global_config.paths.agents_dir)
            skill_dir = agents_dir / agent_id / "skills" / skill_id
            skill_json_data = skill_files.skill_json if isinstance(skill_files.skill_json, dict) else {}
            _sync_agent_registry(agent_id, skill_id, skill_json_data, skill_dir, action="add")
            _auto_restart_agent_if_active(agent_id)

            return {
                "status": "updated",
                "skill_id": skill_id,
                "agent_id": agent_id,
                "skill_json": skill_files.skill_json,
                "skill_md": skill_files.skill_md
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ========================================================================
    # MEMORY ENDPOINTS
    # ========================================================================

    @app.get("/memory/topics", tags=["Memory"])
    async def list_topics():
        """List memory topics (legacy, returns empty - use /agents/{agent_id}/memory/topics)."""
        return {"topics": [], "note": "Use /agents/{agent_id}/memory/topics for per-agent memory"}

    @app.get("/memory/search", tags=["Memory"])
    async def search_memory(
        query: str = Query(..., description="Search query"),
        topic: Optional[str] = Query(None, description="Topic to search in"),
        limit: int = Query(10, description="Max results")
    ):
        """Search memory (legacy, returns empty - use /agents/{agent_id}/memory/search)."""
        return {"results": [], "query": query, "note": "Use /agents/{agent_id}/memory/search for per-agent memory"}

    @app.get("/agents/{agent_id}/memory/topics", tags=["Agents", "Memory"])
    async def list_agent_memory_topics(agent_id: str, current_user = Depends(get_optional_user)):
        """List memory topics for an agent."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        memory_manager = manager._get_agent_memory_manager(agent_id)
        topics = memory_manager.list_topics()
        return {"agent_id": agent_id, "topics": topics}

    @app.get("/agents/{agent_id}/memory/search", tags=["Agents", "Memory"])
    async def search_agent_memory(
        agent_id: str,
        query: str = Query(..., description="Search query"),
        topic: Optional[str] = Query(None, description="Topic to search in"),
        limit: int = Query(10, description="Max results"),
        current_user = Depends(get_optional_user)
    ):
        """Search an agent's memory."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        memory_manager = manager._get_agent_memory_manager(agent_id)
        results = memory_manager.search_memory(query, topic, limit)
        return {"agent_id": agent_id, "results": results, "query": query}

    @app.get("/agents/{agent_id}/memory/stats", tags=["Agents", "Memory"])
    async def get_agent_memory_stats(agent_id: str, current_user = Depends(get_optional_user)):
        """Get memory statistics for an agent."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        memory_manager = manager._get_agent_memory_manager(agent_id)
        stats = memory_manager.get_memory_stats()
        return {"agent_id": agent_id, **stats}

    # ========================================================================
    # RUN ENDPOINTS
    # ========================================================================

    @app.get("/runs", tags=["Runs"])
    async def list_runs(
        agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
        date: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD)"),
        limit: int = Query(50, description="Max results")
    ):
        """List recent runs (aggregated from all agents if agent_id not specified)."""
        manager = get_manager()
        runs = manager.list_runs(agent_id, date, limit)
        return {"runs": runs}

    @app.get("/agents/{agent_id}/runs", tags=["Agents", "Runs"])
    async def list_agent_runs(
        agent_id: str,
        date: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD)"),
        limit: int = Query(50, description="Max results"),
        current_user = Depends(get_optional_user)
    ):
        """List runs for a specific agent."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        runs = manager.list_runs(agent_id=agent_id, date=date, limit=limit)
        return {"agent_id": agent_id, "runs": runs}

    @app.get("/runs/{agent_id}/{date}/{run_id}", tags=["Runs"])
    async def get_run(agent_id: str, date: str, run_id: str, current_user = Depends(get_optional_user)):
        """Get run details."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        run = manager.get_run(agent_id, date, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run.to_dict()

    @app.get("/agents/{agent_id}/runs/{date}/{run_id}", tags=["Agents", "Runs"])
    async def get_agent_run(agent_id: str, date: str, run_id: str, current_user = Depends(get_optional_user)):
        """Get run details for a specific agent."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        run = manager.get_run(agent_id, date, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run.to_dict()

    @app.get("/runs/{agent_id}/{date}/{run_id}/transcript", tags=["Runs"])
    async def get_run_transcript(agent_id: str, date: str, run_id: str, current_user = Depends(get_optional_user)):
        """Get run transcript."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        transcript = manager.get_transcript(agent_id, date, run_id)
        if transcript is None:
            raise HTTPException(status_code=404, detail="Transcript not found")
        return {"transcript": transcript}

    @app.get("/agents/{agent_id}/runs/{date}/{run_id}/transcript", tags=["Agents", "Runs"])
    async def get_agent_run_transcript(agent_id: str, date: str, run_id: str, current_user = Depends(get_optional_user)):
        """Get run transcript for a specific agent."""
        await verify_agent_access(agent_id, current_user)
        manager = get_manager()
        transcript = manager.get_transcript(agent_id, date, run_id)
        if transcript is None:
            raise HTTPException(status_code=404, detail="Transcript not found")
        return {"transcript": transcript}

    # ========================================================================
    # SCHEDULER/TASK ENDPOINTS
    # ========================================================================

    _scheduler = None

    def get_scheduler():
        """
        Get or create the TaskScheduler instance.

        Creates the scheduler on first call and starts its background loop
        so that tasks execute in-process alongside the API server.
        """
        nonlocal _scheduler
        if _scheduler is None:
            manager = get_manager()
            if manager is None:
                return None

            # Check if manager already has a scheduler (e.g. from CLI --scheduler)
            existing = manager.get_scheduler()
            if existing is not None:
                _scheduler = existing
                return _scheduler

            try:
                from ..scheduler import TaskScheduler, create_task_executor
                paths = manager.global_config.paths
                executor = create_task_executor(manager)

                # Use per-agent structure (agents_dir) if available
                agents_dir = getattr(paths, 'agents_dir', None)
                tasks_dir = getattr(paths, 'tasks_dir', None)

                _scheduler = TaskScheduler(
                    agents_dir=agents_dir,
                    tasks_dir=tasks_dir,  # Fallback to legacy
                    executor=executor
                )
                _scheduler._load_all_tasks()
                # Wire scheduler into manager so agents get task tools registered
                manager.set_scheduler(_scheduler)
                # Start the background loop so tasks execute in-process
                _scheduler.start()
            except Exception as e:
                print(f"Scheduler init error: {e}")
                return None
        return _scheduler

    @app.get("/api/scheduler/status", tags=["Scheduler"])
    async def get_scheduler_status():
        """Get scheduler status and health information."""
        scheduler = get_scheduler()
        if scheduler is None:
            return {
                "status": "unavailable",
                "running": False,
                "started_at": None,
                "uptime_seconds": None,
                "last_heartbeat": None,
                "heartbeat_age_seconds": None,
                "heartbeat_ok": False,
                "total_tasks": 0,
                "enabled_tasks": 0,
                "error": "Scheduler not initialized"
            }
        return scheduler.get_status()

    @app.post("/api/scheduler/rescan", tags=["Scheduler"])
    async def rescan_tasks():
        """Rescan and reload all tasks from disk."""
        scheduler = get_scheduler()
        if scheduler is None:
            return {"status": "error", "message": "Scheduler not initialized"}

        try:
            scheduler._load_all_tasks()
            task_count = len(scheduler._tasks)
            return {"status": "ok", "message": f"Rescanned tasks", "task_count": task_count}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.post("/api/scheduler/stop", tags=["Scheduler"])
    async def stop_scheduler():
        """Stop the scheduler process (local or external)."""
        scheduler = get_scheduler()
        if scheduler is None:
            return {"status": "error", "message": "Scheduler not initialized"}

        try:
            # Check if scheduler is running
            status = scheduler.get_status()
            if not status.get("running"):
                return {"status": "ok", "message": "Scheduler is not running"}

            # Request stop (works for both local and external schedulers)
            success = scheduler.request_stop()
            if success:
                if status.get("external"):
                    return {"status": "ok", "message": "Stop signal sent to external scheduler"}
                else:
                    return {"status": "ok", "message": "Scheduler stopped"}
            else:
                return {"status": "error", "message": "Failed to send stop signal"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.post("/api/scheduler/start", tags=["Scheduler"])
    async def start_scheduler():
        """Start the scheduler as an independent process (Windows only)."""
        import os
        import time
        from pathlib import Path

        scheduler = get_scheduler()
        if scheduler is None:
            return {"status": "error", "message": "Scheduler not initialized"}

        try:
            # Check if scheduler is already running
            status = scheduler.get_status()
            if status.get("running"):
                return {"status": "ok", "message": "Scheduler is already running"}

            # Calculate src directory from this file's location
            # app.py is at: src/loop_core/api/app.py
            # src is 3 levels up
            src_dir = Path(__file__).resolve().parent.parent.parent

            # Build the command to run in a new window
            # Using cmd /k to keep window open, with title
            cmd = f'start "loopCore Scheduler" cmd /k "cd /d {src_dir} && python -m loop_core.cli --scheduler"'

            os.system(cmd)

            # Wait briefly and verify it started
            time.sleep(2)
            new_status = scheduler.get_status()
            if new_status.get("running"):
                return {"status": "ok", "message": "Scheduler started successfully"}
            else:
                return {"status": "warning", "message": "Scheduler launch initiated, but not yet detected as running"}

        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.get("/api/tasks", tags=["Tasks"])
    async def list_tasks(agent_id: Optional[str] = Query(None, description="Filter by agent ID")):
        """List all scheduled tasks, optionally filtered by agent."""
        scheduler = get_scheduler()
        if scheduler is None:
            return {"tasks": [], "error": "Scheduler not available"}
        tasks = scheduler.list_tasks(agent_id=agent_id)
        return {"tasks": tasks}

    @app.get("/agents/{agent_id}/tasks", tags=["Agents", "Tasks"])
    async def list_agent_tasks(agent_id: str, current_user = Depends(get_optional_user)):
        """List scheduled tasks for a specific agent."""
        await verify_agent_access(agent_id, current_user)
        scheduler = get_scheduler()
        if scheduler is None:
            return {"agent_id": agent_id, "tasks": [], "error": "Scheduler not available"}
        tasks = scheduler.list_tasks(agent_id=agent_id)
        return {"agent_id": agent_id, "tasks": tasks}

    @app.post("/agents/{agent_id}/tasks", tags=["Agents", "Tasks"])
    async def create_agent_task(agent_id: str, request: CreateTaskRequest, current_user = Depends(get_optional_user)):
        """Create a new scheduled task for a specific agent."""
        await verify_agent_access(agent_id, current_user)
        scheduler = get_scheduler()
        if scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")

        # Build schedule config based on type
        if request.schedule_type == "interval":
            schedule = {"type": "interval", "interval_seconds": request.interval_seconds}
        elif request.schedule_type == "cron":
            if not request.cron_expression:
                raise HTTPException(status_code=400, detail="Cron expression required")
            schedule = {"type": "cron", "expression": request.cron_expression}
        elif request.schedule_type == "once":
            schedule = {"type": "once"}
            if request.run_at:
                schedule["run_at"] = request.run_at  # Pass the scheduled run time
        else:
            schedule = {"type": "event_only"}
            if request.events:
                schedule["events"] = request.events

        try:
            task = scheduler.create_task(
                task_id=request.task_id,
                name=request.name,
                task_md_content=request.content or f"# {request.name}\n\nTask instructions.",
                schedule=schedule,
                agent_id=agent_id,  # Use path parameter
                skill_id=request.skill_id,
                enabled=request.enabled,
                description=request.description,
                created_by=request.created_by
            )
            return {
                "status": "ok",
                "agent_id": agent_id,
                "task_id": task.task_id,
                "skill_id": task.skill_id,
                "next_run": task.next_run.isoformat() if task.next_run else None
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/tasks/{task_id}", tags=["Tasks"])
    async def get_task(task_id: str):
        """Get task details."""
        scheduler = get_scheduler()
        if scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        task = scheduler.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        return task

    @app.post("/api/tasks", tags=["Tasks"])
    async def create_task(request: CreateTaskRequest):
        """Create a new scheduled task."""
        scheduler = get_scheduler()
        if scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")

        # Build schedule config
        if request.schedule_type == "interval":
            schedule = {"type": "interval", "interval_seconds": request.interval_seconds}
        elif request.schedule_type == "cron":
            if not request.cron_expression:
                raise HTTPException(status_code=400, detail="Cron expression required")
            schedule = {"type": "cron", "expression": request.cron_expression}
        elif request.schedule_type == "once":
            schedule = {"type": "once"}
            if request.run_at:
                schedule["run_at"] = request.run_at  # Pass the scheduled run time
        else:
            schedule = {"type": "event_only"}
            if request.events:
                schedule["events"] = request.events

        try:
            task = scheduler.create_task(
                task_id=request.task_id,
                name=request.name,
                task_md_content=request.content or f"# {request.name}\n\nTask instructions.",
                schedule=schedule,
                agent_id=request.agent_id,
                skill_id=request.skill_id,  # Optional skill link
                enabled=request.enabled,
                description=request.description,
                created_by=request.created_by,
                context=request.context or {}  # Task context with keywords
            )
            return {
                "status": "ok",
                "task_id": task.task_id,
                "skill_id": task.skill_id,
                "context_keys": list((request.context or {}).keys()),
                "next_run": task.next_run.isoformat() if task.next_run else None
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/tasks/{task_id}/trigger", tags=["Tasks"])
    async def trigger_task(task_id: str):
        """Manually trigger a task."""
        import asyncio
        scheduler = get_scheduler()
        if scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        # Run in executor so the event loop stays free — agents that use
        # http_request to read the feed from localhost would deadlock otherwise.
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: scheduler.trigger_task(task_id))
        return result

    @app.put("/api/tasks/{task_id}/enable", tags=["Tasks"])
    async def enable_task(task_id: str):
        """Enable a task."""
        scheduler = get_scheduler()
        if scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        success = scheduler.enable_task(task_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        return {"status": "ok", "task_id": task_id, "enabled": True}

    @app.put("/api/tasks/{task_id}/disable", tags=["Tasks"])
    async def disable_task(task_id: str):
        """Disable a task."""
        scheduler = get_scheduler()
        if scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        success = scheduler.disable_task(task_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        return {"status": "ok", "task_id": task_id, "enabled": False}

    @app.put("/api/tasks/{task_id}", tags=["Tasks"])
    async def update_task(task_id: str, request: CreateTaskRequest):
        """Update an existing task."""
        scheduler = get_scheduler()
        if scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")

        task = scheduler.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

        # Build schedule config
        if request.schedule_type == "interval":
            schedule = {"type": "interval", "interval_seconds": request.interval_seconds or 3600}
        elif request.schedule_type == "cron":
            if not request.cron_expression:
                raise HTTPException(status_code=400, detail="Cron expression required")
            schedule = {"type": "cron", "expression": request.cron_expression}
        elif request.schedule_type == "once":
            schedule = {"type": "once", "run_at": request.run_at} if request.run_at else {"type": "once"}
        else:
            schedule = {"type": "event_only", "events": request.events or []}

        try:
            # Update task files directly
            task_folder = task.get("folder_path") or task.get("_folder_path")
            if task_folder:
                task_path = Path(task_folder)
            else:
                # Find the task folder
                manager = get_manager()
                agents_dir = Path(manager.config_manager.global_config.paths.agents_dir)
                agent_id = task.get("agent_id") or task.get("execution", {}).get("agent_id") or "main"
                task_path = agents_dir / agent_id / "tasks" / task_id

            if not task_path.exists():
                raise HTTPException(status_code=404, detail=f"Task folder not found: {task_id}")

            # Read and update task.json
            task_json_path = task_path / "task.json"
            if task_json_path.exists():
                task_data = json.loads(task_json_path.read_text(encoding='utf-8'))
            else:
                task_data = {}

            # Update fields
            task_data["name"] = request.name or task_data.get("name", task_id)
            task_data["description"] = request.description or task_data.get("description", "")
            task_data["schedule"] = schedule
            task_data["updated_at"] = datetime.now().isoformat()

            # Initialize execution section if needed
            if "execution" not in task_data:
                task_data["execution"] = {}

            # Update skill_id (can be set to None to enable auto-matching)
            task_data["execution"]["skill_id"] = request.skill_id

            if request.timeout_seconds:
                task_data["execution"]["timeout_seconds"] = request.timeout_seconds
            if request.max_turns:
                task_data["execution"]["max_turns"] = request.max_turns

            # Update enabled status
            if "status" not in task_data:
                task_data["status"] = {}
            task_data["status"]["enabled"] = request.enabled

            task_json_path.write_text(json.dumps(task_data, indent=2), encoding='utf-8')

            # Update task.md if content provided
            if request.content:
                task_md_path = task_path / "task.md"
                task_md_path.write_text(request.content, encoding='utf-8')

            # Reload task in scheduler
            scheduler.reload_task(task_id)

            logger.info(f"Updated task: {task_id}")
            return {"status": "ok", "task_id": task_id, "message": f"Task '{task_id}' updated"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to update task: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/tasks/{task_id}", tags=["Tasks"])
    async def delete_task(task_id: str):
        """Delete a task."""
        scheduler = get_scheduler()
        if scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        success = scheduler.delete_task(task_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        return {"status": "ok", "deleted": task_id}

    @app.get("/api/tasks/{task_id}/runs", tags=["Tasks"])
    async def get_task_runs(task_id: str, limit: int = Query(10, description="Max results")):
        """Get task run history."""
        scheduler = get_scheduler()
        if scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        runs = scheduler.get_task_runs(task_id, limit=limit)
        return {"task_id": task_id, "runs": runs}

    # ========================================================================
    # DESKTOP CLIENT ENDPOINTS
    # ========================================================================

    # Lazy initialization for desktop client components
    _client_registry = None
    _request_queue = None

    def get_client_registry():
        nonlocal _client_registry
        if _client_registry is None:
            from ..desktop_client import get_client_registry as _get_registry
            _client_registry = _get_registry()
        return _client_registry

    def get_request_queue():
        nonlocal _request_queue
        if _request_queue is None:
            from ..desktop_client import get_request_queue as _get_queue
            _request_queue = _get_queue()
        return _request_queue

    def validate_client_token(authorization: str = Header(...)) -> str:
        """Validate client token and return client_id."""
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid authorization header")
        token = authorization[7:]  # Remove "Bearer " prefix
        registry = get_client_registry()
        client_id = registry.validate_token(token)
        if client_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return client_id

    @app.post("/api/client/auth", tags=["Desktop Client"])
    async def authenticate_client(
        client_id: str,
        client_version: str,
        platform: str,
        device_fingerprint: str = None
    ):
        """
        Authenticate a desktop client.
        Returns bearer token for subsequent requests.
        """
        registry = get_client_registry()
        auth_token = registry.authenticate_client(
            client_id=client_id,
            platform=platform,
            client_version=client_version,
            device_fingerprint=device_fingerprint
        )
        return {
            "token": auth_token.token,
            "token_expires_at": auth_token.expires_at.isoformat(),
            "refresh_token": auth_token.refresh_token,
            "server_version": "1.0.0",
            "poll_interval_ms": 10000
        }

    @app.post("/api/client/auth/refresh", tags=["Desktop Client"])
    async def refresh_client_token(refresh_token: str):
        """Refresh an expiring client token."""
        registry = get_client_registry()
        auth_token = registry.refresh_token(refresh_token)
        if auth_token is None:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        return {
            "token": auth_token.token,
            "token_expires_at": auth_token.expires_at.isoformat(),
            "refresh_token": auth_token.refresh_token
        }

    @app.get("/api/client/pending-requests", tags=["Desktop Client"])
    async def get_pending_requests(
        current_user = Depends(get_optional_user),
        authorization: str = Header(None),
        since: str = None
    ):
        """
        Poll for pending capability requests and operations.
        Client calls this at regular intervals.
        Accepts either user auth token or client token.
        """
        client_id = None

        # Try user authentication first (from desktop client with user login)
        if current_user:
            # Use user_id as the client identifier
            client_id = f"user_{current_user.user_id}"
        elif authorization:
            # Fall back to client token validation
            client_id = validate_client_token(authorization)

        if not client_id:
            raise HTTPException(status_code=401, detail="Authentication required")

        queue = get_request_queue()
        pending = queue.get_pending_requests(client_id)

        # Convert to response format
        requests = []
        for req in pending:
            requests.append({
                "request_id": req.request_id,
                "type": req.type.value,
                "payload": req.payload,
                "created_at": req.created_at.isoformat(),
                "expires_at": req.expires_at.isoformat(),
                "priority": req.priority.value
            })

        return {
            "requests": requests,
            "next_poll_ms": 10000
        }

    @app.post("/api/client/respond", tags=["Desktop Client"])
    async def respond_to_request(
        request_id: str,
        status: str,
        payload: dict = None,
        error: dict = None,
        current_user = Depends(get_optional_user),
        authorization: str = Header(None)
    ):
        """
        Client responds to a pending request.
        Used for capability grants/denials and operation results.
        Accepts either user auth token or client token.
        """
        client_id = None
        if current_user:
            client_id = f"user_{current_user.user_id}"
        elif authorization:
            client_id = validate_client_token(authorization)

        if not client_id:
            raise HTTPException(status_code=401, detail="Authentication required")
        queue = get_request_queue()

        from ..desktop_client import ResponseStatus
        try:
            response_status = ResponseStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

        success = queue.respond_to_request(
            request_id=request_id,
            status=response_status,
            payload=payload or error
        )

        if not success:
            raise HTTPException(status_code=404, detail=f"Request not found: {request_id}")

        return {"status": "ok", "request_id": request_id}

    @app.post("/api/client/operation/{operation_id}/progress", tags=["Desktop Client"])
    async def report_operation_progress(
        operation_id: str,
        progress: int,
        current_step: str = None,
        bytes_processed: int = None,
        authorization: str = Header(...)
    ):
        """Report progress on a long-running operation."""
        client_id = validate_client_token(authorization)
        # For now, just acknowledge - full implementation would update operation state
        return {
            "status": "ok",
            "operation_id": operation_id,
            "progress": progress
        }

    @app.post("/api/client/operation/{operation_id}/result", tags=["Desktop Client"])
    async def report_operation_result(
        operation_id: str,
        result_type: str,
        success: bool,
        data: dict = None,
        error: dict = None,
        duration_ms: int = 0,
        authorization: str = Header(...)
    ):
        """Report final result of an operation."""
        client_id = validate_client_token(authorization)
        # For now, just acknowledge - full implementation would store result
        return {
            "status": "ok",
            "operation_id": operation_id,
            "success": success
        }

    @app.post("/api/client/heartbeat", tags=["Desktop Client"])
    async def client_heartbeat(
        active_capabilities: list = [],
        memory_usage_mb: float = None,
        disk_free_gb: float = None,
        cpu_usage_percent: float = None,
        authorization: str = Header(...)
    ):
        """Keep-alive and status update."""
        client_id = validate_client_token(authorization)
        registry = get_client_registry()

        system_status = None
        if memory_usage_mb is not None:
            from ..desktop_client import SystemStatus
            system_status = SystemStatus(
                memory_usage_mb=memory_usage_mb,
                disk_free_gb=disk_free_gb or 0,
                cpu_usage_percent=cpu_usage_percent or 0
            )

        capabilities_to_revoke = registry.update_heartbeat(
            client_id=client_id,
            active_capabilities=active_capabilities,
            system_status=system_status
        )

        return {
            "server_time": datetime.now().isoformat(),
            "capabilities_to_revoke": capabilities_to_revoke,
            "config_updates": None
        }

    @app.post("/api/client/disconnect", tags=["Desktop Client"])
    async def client_disconnect(
        reason: str,
        revoke_all_capabilities: bool = False,
        authorization: str = Header(...)
    ):
        """Graceful client disconnect."""
        client_id = validate_client_token(authorization)
        registry = get_client_registry()

        registry.disconnect_client(
            client_id=client_id,
            revoke_capabilities=revoke_all_capabilities
        )

        return {"status": "ok", "message": "Disconnected"}

    @app.post("/api/client/register", tags=["Desktop Client"])
    async def register_client(
        request: ClientRegisterRequest,
        current_user = Depends(get_optional_user)
    ):
        """
        Register a desktop client with user authentication.
        This links the client to the authenticated user's company.
        """
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        registry = get_client_registry()

        # Register client with user/company context
        try:
            registry.register_client_for_user(
                client_id=request.client_id,
                user_id=current_user.user_id,
                company_id=current_user.company_id,
                platform=request.platform,
                client_version=request.client_version,
                capabilities=request.capabilities
            )
        except AttributeError:
            # Fallback if registry doesn't have the method yet
            pass

        return {
            "status": "ok",
            "client_id": request.client_id,
            "company_id": current_user.company_id,
            "user_id": current_user.user_id,
            "poll_interval_ms": 10000
        }

    # ========== ADMIN ENDPOINTS FOR DESKTOP CLIENTS ==========

    @app.get("/api/clients", tags=["Desktop Client Admin"])
    async def list_clients(online_only: bool = False):
        """List all registered desktop clients."""
        registry = get_client_registry()
        clients = registry.list_clients(online_only=online_only)
        return {
            "clients": [c.dict() for c in clients],
            "total": len(clients)
        }

    @app.get("/api/clients/{client_id}", tags=["Desktop Client Admin"])
    async def get_client_details(client_id: str):
        """Get detailed info about a client."""
        registry = get_client_registry()
        queue = get_request_queue()

        client = registry.get_client(client_id)
        if client is None:
            raise HTTPException(status_code=404, detail=f"Client not found: {client_id}")

        pending_count = queue.get_queue_size(client_id)
        return client.to_detail_info(pending_requests_count=pending_count).dict()

    @app.get("/api/clients/{client_id}/capabilities", tags=["Desktop Client Admin"])
    async def get_client_capabilities(client_id: str):
        """Get capabilities granted to a specific client."""
        registry = get_client_registry()
        client = registry.get_client(client_id)
        if client is None:
            raise HTTPException(status_code=404, detail=f"Client not found: {client_id}")

        return {
            "client_id": client_id,
            "capabilities": [c.dict() for c in client.capabilities.values()]
        }

    @app.delete("/api/clients/{client_id}/capabilities/{capability_id}", tags=["Desktop Client Admin"])
    async def revoke_client_capability(client_id: str, capability_id: str):
        """Revoke a capability from a client."""
        registry = get_client_registry()
        success = registry.revoke_capability(client_id, capability_id)
        if not success:
            raise HTTPException(status_code=404, detail="Client or capability not found")
        return {"status": "ok", "revoked": capability_id}

    @app.post("/api/clients/{client_id}/request-capability", tags=["Desktop Client Admin"])
    async def request_capability_from_client(
        client_id: str,
        agent_id: str,
        capability_type: str,
        paths: list = None,
        reason: str = "Requested by backend"
    ):
        """
        Request a capability from a client (queues a capability request).
        The client will see this when polling and prompt the user.
        """
        registry = get_client_registry()
        queue = get_request_queue()

        client = registry.get_client(client_id)
        if client is None:
            raise HTTPException(status_code=404, detail=f"Client not found: {client_id}")

        from ..desktop_client import RequestType, RequestPriority

        # Build capability request payload
        capabilities = [{
            "type": capability_type,
            "scope": {"paths": [{"path": p, "recursive": True} for p in (paths or [])]}
        }]

        payload = {
            "agent_id": agent_id,
            "capabilities": capabilities,
            "reason": reason
        }

        request_id = queue.queue_request(
            client_id=client_id,
            request_type=RequestType.CAPABILITY_REQUEST,
            payload=payload,
            priority=RequestPriority.NORMAL
        )

        return {
            "status": "queued",
            "request_id": request_id,
            "client_id": client_id
        }

    # ========================================================================
    # LLM USAGE ENDPOINTS
    # ========================================================================

    @app.get("/usage/dates", tags=["Usage"])
    async def list_usage_dates():
        """List available usage log dates."""
        log_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "LOGS"
        dates = []
        if log_dir.exists():
            for f in sorted(log_dir.glob("llm_usage_*.jsonl"), reverse=True):
                date_str = f.stem.replace("llm_usage_", "")
                dates.append(date_str)
        return {"dates": dates}

    @app.get("/usage", tags=["Usage"])
    async def get_usage(
        date: Optional[str] = None,
        agent_id: Optional[str] = None,
        system: Optional[str] = None,
    ):
        """
        Get LLM usage data from centralized JSONL logs.

        Query params:
          - date: YYYYMMDD (default: today)
          - agent_id: filter by agent
          - system: filter by "loopCore" or "loopColony"

        Returns:
          - calls: list of individual call records
          - by_agent: {agent_id: {calls, input_tokens, output_tokens, total_cost}}
          - by_model: {model: {calls, input_tokens, output_tokens, total_cost}}
          - totals: {calls, input_tokens, output_tokens, total_cost}
        """
        if date is None:
            date = datetime.now().strftime("%Y%m%d")

        log_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "LOGS"
        log_file = log_dir / f"llm_usage_{date}.jsonl"

        if not log_file.exists():
            return {
                "date": date,
                "calls": [],
                "by_agent": {},
                "by_model": {},
                "totals": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_cost": 0.0},
            }

        calls = []
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Apply filters
                    if agent_id and entry.get("agent_id") != agent_id:
                        continue
                    if system and entry.get("system") != system:
                        continue

                    calls.append(entry)
        except Exception as e:
            logger.error(f"Failed to read usage log: {e}")
            raise HTTPException(status_code=500, detail=str(e))

        # Aggregate by agent
        by_agent = {}
        for c in calls:
            aid = c.get("agent_id") or "(no agent)"
            if aid not in by_agent:
                by_agent[aid] = {
                    "agent_name": c.get("agent_name"),
                    "system": c.get("system"),
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "input_cost": 0.0,
                    "output_cost": 0.0,
                    "total_cost": 0.0,
                }
            by_agent[aid]["calls"] += 1
            by_agent[aid]["input_tokens"] += c.get("input_tokens", 0)
            by_agent[aid]["output_tokens"] += c.get("output_tokens", 0)
            by_agent[aid]["input_cost"] += c.get("input_cost", 0.0)
            by_agent[aid]["output_cost"] += c.get("output_cost", 0.0)
            by_agent[aid]["total_cost"] += c.get("total_cost", 0.0)

        # Aggregate by model
        by_model = {}
        for c in calls:
            model = c.get("model", "unknown")
            if model not in by_model:
                by_model[model] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_cost": 0.0,
                }
            by_model[model]["calls"] += 1
            by_model[model]["input_tokens"] += c.get("input_tokens", 0)
            by_model[model]["output_tokens"] += c.get("output_tokens", 0)
            by_model[model]["total_cost"] += c.get("total_cost", 0.0)

        # Totals
        totals = {
            "calls": len(calls),
            "input_tokens": sum(c.get("input_tokens", 0) for c in calls),
            "output_tokens": sum(c.get("output_tokens", 0) for c in calls),
            "total_cost": round(sum(c.get("total_cost", 0.0) for c in calls), 6),
        }

        return {
            "date": date,
            "calls": calls,
            "by_agent": by_agent,
            "by_model": by_model,
            "totals": totals,
        }

    # ========================================================================
    # STATIC FILES & ADMIN PANEL
    # ========================================================================

    # Setup authentication routes
    try:
        from .auth import setup_auth_routes, bootstrap_default_users
        setup_auth_routes(app)
        bootstrap_default_users()
    except ImportError as e:
        logger.warning(f"Auth module not available: {e}")

    # Setup WebSocket routes (for AWS API Gateway integration)
    try:
        from .ws import router as ws_router
        app.include_router(ws_router)
        logger.info("WebSocket routes registered")
    except ImportError as e:
        logger.warning(f"WebSocket module not available: {e}")

    # Redirect to /static/*.html so the browser's URL is inside /static/,
    # which makes relative paths in HTML (css/login.css, js/login.js) resolve
    # to /static/css/login.css, /static/js/login.js — matching the mount point.
    # This also keeps the same file structure working in production (S3/CloudFront)
    # without needing a /static/ prefix in the bucket.

    @app.get("/login", tags=["Admin"])
    async def login_page():
        """Redirect to login page inside static mount."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/static/login.html")

    @app.get("/dashboard", tags=["Admin"])
    async def customer_dashboard():
        """Redirect to customer dashboard inside static mount."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/static/customer.html")

    @app.get("/admin", tags=["Admin"])
    async def platform_admin_panel():
        """Redirect to admin panel inside static mount."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/static/index.html")

    @app.get("/", tags=["Admin"])
    async def root_redirect():
        """Redirect root to login page."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/static/login.html")

    # ========================================================================
    # AGENT RUNTIME ENDPOINTS
    # ========================================================================

    _runtime = None

    def get_runtime():
        """
        Get or create the API-side AgentRuntime instance.

        On first call, creates the runtime, registers it with the manager,
        and starts the main loop.
        """
        nonlocal _runtime
        if _runtime is not None:
            return _runtime

        manager = get_manager()
        if manager is None:
            return None

        # Check if manager already has a runtime (e.g. from CLI startup)
        existing = manager.get_runtime()
        if existing is not None:
            _runtime = existing
            return _runtime

        try:
            from ..runtime import AgentRuntime
            from ..scheduler import create_task_executor

            paths = manager.global_config.paths
            agents_dir = getattr(paths, 'agents_dir', None)
            if not agents_dir:
                return None

            _runtime = AgentRuntime(
                agent_manager=manager,
                agents_dir=agents_dir,
                executor_factory=create_task_executor,
            )

            # Set up output router with plugins
            try:
                from ..routing import OutputRouter
                from ..routing.plugins import LoopColonyPlugin

                router = OutputRouter(agents_dir=agents_dir)
                router.register(LoopColonyPlugin())
                _runtime.set_router(router)
            except Exception as e:
                logger.warning(f"Output router init error (non-fatal): {e}")

            manager.set_runtime(_runtime)
            _runtime.start()

            # Auto-restore agents that were active before server crash/restart
            try:
                restored = _runtime.restore_previously_active(max_age_minutes=10)
                if restored:
                    logger.info("Auto-restored %d agent(s): %s", len(restored), restored)
                else:
                    logger.info("No agents to auto-restore")
            except Exception as e:
                logger.warning("Agent auto-restore error (non-fatal): %s", e)
        except Exception as e:
            logger.error(f"Runtime init error: {e}")
            return None

        return _runtime

    @app.post("/agents/{agent_id}/start", tags=["Agents", "Runtime"])
    async def start_agent(agent_id: str, current_user=Depends(get_optional_user)):
        """Start an agent (activate heartbeats, task scheduling, event queue)."""
        await verify_agent_access(agent_id, current_user)
        runtime = get_runtime()
        if runtime is None:
            raise HTTPException(status_code=503, detail="Runtime not available")
        return runtime.start_agent(agent_id)

    @app.post("/agents/{agent_id}/stop", tags=["Agents", "Runtime"])
    async def stop_agent(agent_id: str, current_user=Depends(get_optional_user)):
        """Stop an agent (deactivate heartbeats, clear queue)."""
        await verify_agent_access(agent_id, current_user)
        runtime = get_runtime()
        if runtime is None:
            raise HTTPException(status_code=503, detail="Runtime not available")
        return runtime.stop_agent(agent_id)

    class HeartbeatIntervalRequest(BaseModel):
        interval_minutes: int = Field(ge=1, le=1440)

    @app.post("/agents/{agent_id}/heartbeat-interval", tags=["Agents", "Runtime"])
    async def update_heartbeat_interval(
        agent_id: str,
        request: HeartbeatIntervalRequest,
        current_user=Depends(get_optional_user),
    ):
        """Update the agent's heartbeat interval (persists to config)."""
        await verify_agent_access(agent_id, current_user)
        runtime = get_runtime()
        if runtime is None:
            raise HTTPException(status_code=503, detail="Runtime not available")
        result = runtime.update_heartbeat_interval(agent_id, request.interval_minutes)
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result

    @app.post("/agents/{agent_id}/trigger-heartbeat", tags=["Agents", "Runtime"])
    async def trigger_heartbeat(agent_id: str, current_user=Depends(get_optional_user)):
        """Manually trigger an immediate heartbeat for an agent."""
        await verify_agent_access(agent_id, current_user)
        runtime = get_runtime()
        if runtime is None:
            raise HTTPException(status_code=503, detail="Runtime not available")
        return runtime.trigger_heartbeat(agent_id)

    @app.post("/agents/{agent_id}/reset", tags=["Agents", "Runtime"])
    async def reset_agent(agent_id: str, current_user=Depends(get_optional_user)):
        """Reset an agent: stop it, clear queue, TODO list, saved queue, heartbeat history, and event history."""
        await verify_agent_access(agent_id, current_user)

        # Stop agent if active and clear in-memory state
        was_active = False
        try:
            runtime = get_runtime()
            if runtime is not None:
                status = runtime.get_agent_status(agent_id)
                if status.get("active"):
                    runtime.stop_agent(agent_id)
                    was_active = True
                # Clear in-memory event history and metrics
                with runtime._lock:
                    state = runtime._agents.get(agent_id)
                    if state:
                        state.event_history.clear()
                        state.pending_events.clear()
                        state.heartbeats_fired = 0
                        state.heartbeats_skipped = 0
                        state.events_processed = 0
                        state.events_failed = 0
                        state.webhooks_received = 0
                        state.total_run_duration_ms = 0
        except Exception as e:
            logger.warning(f"Error clearing runtime state for {agent_id}: {e}")

        # Clear persistent files
        data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "loopCore"
        agent_dir = data_dir / "AGENTS" / agent_id
        cleared = []
        for filename in ("todo.json", "issues.json", ".saved_queue.json", "heartbeat_history.json"):
            fpath = agent_dir / filename
            if fpath.exists():
                try:
                    fpath.unlink()
                    cleared.append(filename)
                except Exception as e:
                    logger.warning(f"Could not delete {fpath}: {e}")

        return {
            "status": "ok",
            "agent_id": agent_id,
            "was_active": was_active,
            "cleared_files": cleared,
            "message": f"Agent reset. Cleared: {', '.join(cleared) if cleared else 'nothing on disk'}. "
                       + ("Agent was stopped." if was_active else "Agent was already stopped.")
        }

    @app.get("/agents/{agent_id}/runtime-status", tags=["Agents", "Runtime"])
    async def get_agent_runtime_status(agent_id: str):
        """Get runtime status for an agent (active, queue depth, timers)."""
        runtime = get_runtime()
        if runtime is None:
            return {"active": False, "queue_depth": 0}
        return runtime.get_agent_status(agent_id)

    @app.get("/agents/{agent_id}/queue", tags=["Agents", "Runtime"])
    async def get_agent_queue(agent_id: str):
        """Get the full queue contents for an agent (pending events with previews)."""
        runtime = get_runtime()
        if runtime is None:
            return {"agent_id": agent_id, "queue": [], "current_event": None}
        queue = runtime.get_agent_queue(agent_id)
        status = runtime.get_agent_status(agent_id)
        return {
            "agent_id": agent_id,
            "queue": queue,
            "current_event": status.get("current_event"),
        }

    @app.get("/agents/{agent_id}/events/history", tags=["Agents", "Runtime"])
    async def get_event_history(agent_id: str, limit: int = 20):
        """Get the last N completed events for an agent (most recent first)."""
        runtime = get_runtime()
        if runtime is None:
            return {"agent_id": agent_id, "events": []}
        events = runtime.get_agent_event_history(agent_id, limit=limit)
        return {"agent_id": agent_id, "events": events}

    @app.get("/agents/{agent_id}/events/pending", tags=["Agents", "Runtime"])
    async def get_pending_events(agent_id: str):
        """Get events awaiting human approval for an agent."""
        runtime = get_runtime()
        if runtime is None:
            return {"agent_id": agent_id, "pending_events": []}
        events = runtime.get_pending_events(agent_id)
        return {"agent_id": agent_id, "pending_events": events}

    @app.get("/agents/{agent_id}/events/{event_id}", tags=["Agents", "Runtime"])
    async def get_event_detail(agent_id: str, event_id: str):
        """Get full detail for a single event (searches all states)."""
        runtime = get_runtime()
        if runtime is None:
            raise HTTPException(status_code=503, detail="Runtime not initialized")
        detail = runtime.get_event_detail(agent_id, event_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")
        return detail

    @app.post("/agents/{agent_id}/events/{event_id}/approve", tags=["Agents", "Runtime"])
    async def approve_event(agent_id: str, event_id: str):
        """Move a pending event to the active queue."""
        runtime = get_runtime()
        if runtime is None:
            raise HTTPException(status_code=503, detail="Runtime not initialized")
        result = runtime.approve_event(agent_id, event_id)
        if result.get("status") == "error":
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result

    @app.post("/agents/{agent_id}/events/{event_id}/drop", tags=["Agents", "Runtime"])
    async def drop_event(agent_id: str, event_id: str):
        """Drop a pending event."""
        runtime = get_runtime()
        if runtime is None:
            raise HTTPException(status_code=503, detail="Runtime not initialized")
        result = runtime.drop_event(agent_id, event_id)
        if result.get("status") == "error":
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result

    @app.get("/api/runtime/status", tags=["Runtime"])
    async def get_runtime_status():
        """Get overall runtime status (running, active agents, queue totals)."""
        runtime = get_runtime()
        if runtime is None:
            return {
                "running": False,
                "active_agents": [],
                "total_queued": 0,
                "running_llm_calls": 0,
            }
        return runtime.get_status()

    # ========================================================================
    # WEBHOOKS
    # ========================================================================

    def _fetch_dm_context(text: str, agent_id: str) -> str:
        """Extract msg_id from webhook text, fetch message via loopColony REST API.

        Reads the agent's stored loopColony credentials from its memory file,
        then calls GET /conversations/messages/{msg_id} through the HTTP API.
        Never imports loopColony internals directly — keeps the system boundary clean.

        Returns a formatted string with the DM content, or empty string on failure.
        This saves the agent 4-5 navigation turns (notification list -> conversation
        list -> conversation detail -> message read).
        """
        import re
        match = re.search(r"source: (msg_[a-f0-9]+)", text)
        if not match:
            return ""
        msg_id = match.group(1)
        try:
            import json as _json
            import requests as req_lib
            from pathlib import Path

            # Read agent's loopColony credentials from its own memory file
            data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "loopCore"
            creds_path = data_dir / "AGENTS" / agent_id / "memory" / "loopcolony.json"
            if not creds_path.exists():
                return ""
            creds = _json.loads(creds_path.read_text(encoding="utf-8"))
            base_url = creds.get("base_url", "")
            auth_token = creds.get("auth_token", "")
            if not base_url or not auth_token:
                return ""

            # Fetch the message via loopColony REST API
            url = f"{base_url.rstrip('/')}/conversations/messages/{msg_id}"
            resp = req_lib.get(
                url,
                headers={"Authorization": f"Bearer {auth_token}"},
                timeout=5,
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
            message = data.get("message", {})
            if not message:
                return ""

            author = message.get("author", {})
            sender_name = author.get("name", "Unknown") if isinstance(author, dict) else "Unknown"
            # Fallback: raw author_id if no nested author object
            if sender_name == "Unknown" and message.get("author_id"):
                sender_name = message["author_id"]

            conv_id = message.get("conversation_id", "")
            body = message.get("body", "")

            return (
                f"--- DM Content (auto-fetched) ---\n"
                f"From: {sender_name}\n"
                f"Conversation: {conv_id}\n"
                f"Message ID: {msg_id}\n"
                f"Body: {body}\n"
                f"---"
            )
        except Exception:
            return ""

    def verify_webhook_secret(agent_id: str, x_hook_secret: Optional[str]) -> None:
        """
        Verify X-Hook-Secret header against the agent's configured webhook_secret.
        Raises HTTPException(403) if no secret configured or mismatch.
        """
        from ..config.loader import get_config_manager

        config_mgr = get_config_manager()
        try:
            agent_config = config_mgr.load_agent(agent_id)
        except Exception:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        configured_secret = getattr(agent_config, "webhook_secret", None)
        if not configured_secret:
            raise HTTPException(
                status_code=403,
                detail="Webhooks not enabled for this agent (no webhook_secret configured)",
            )

        if not x_hook_secret or x_hook_secret != configured_secret:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")

    @app.post("/hooks/wake/{agent_id}", tags=["Webhooks"])
    async def webhook_wake(
        agent_id: str,
        request: WakeWebhookRequest,
        x_hook_secret: Optional[str] = Header(None, alias="X-Hook-Secret"),
    ):
        """
        Wake an agent with an external event (always async).

        Wraps the text as a system event and pushes it into the agent's queue.
        Returns immediately with {status: "queued"}.
        """
        verify_webhook_secret(agent_id, x_hook_secret)

        runtime = get_runtime()
        if runtime is None:
            raise HTTPException(status_code=503, detail="Runtime not available")

        from ..runtime import AgentEvent, Priority
        from datetime import datetime, timezone

        # DM fast-path: if event mentions a source message, fetch and inject its content
        dm_context = ""
        if "source: msg_" in request.text:
            dm_context = _fetch_dm_context(request.text, agent_id)

        if dm_context:
            prompt = f"System event: {request.text}.\n\n{dm_context}\n\nRead your HEARTBEAT.md and follow it."
        else:
            prompt = f"System event: {request.text}. Read your HEARTBEAT.md and follow it."

        event = AgentEvent(
            priority=Priority.NORMAL,
            timestamp=datetime.now(timezone.utc),
            message=prompt,
            session_key=None,
            source="webhook:wake",
        )

        result = runtime.push_event(agent_id, event)
        if result.get("status") == "agent_not_active":
            raise HTTPException(
                status_code=409,
                detail=f"Agent '{agent_id}' is not active. Start it first.",
            )
        return result

    @app.post("/hooks/agent/{agent_id}", tags=["Webhooks"])
    async def webhook_agent(
        agent_id: str,
        request: AgentWebhookRequest,
        x_hook_secret: Optional[str] = Header(None, alias="X-Hook-Secret"),
    ):
        """
        Run an agent via webhook.

        - No channel → synchronous (returns the LLM response directly).
        - With channel → async (queues event with routing, returns {status: "queued"}).
        """
        verify_webhook_secret(agent_id, x_hook_secret)

        # Async path: channel specified → queue with routing
        if request.channel:
            runtime = get_runtime()
            if runtime is None:
                raise HTTPException(status_code=503, detail="Runtime not available")

            from ..runtime import AgentEvent, Priority
            from ..routing.base import OutputRouteConfig
            from datetime import datetime, timezone

            routing = OutputRouteConfig(
                channel=request.channel,
                to=request.to,
                deliver=request.deliver,
                name=request.name,
            )
            event = AgentEvent(
                priority=Priority.NORMAL,
                timestamp=datetime.now(timezone.utc),
                message=request.message,
                session_key=request.sessionKey,
                source=f"webhook:agent:{request.name or 'unnamed'}",
                routing=routing,
            )

            result = runtime.push_event(agent_id, event)
            if result.get("status") == "agent_not_active":
                raise HTTPException(
                    status_code=409,
                    detail=f"Agent '{agent_id}' is not active. Start it first.",
                )
            return result

        # Sync path: no channel → run directly and return response
        import asyncio

        manager = get_manager()
        status = manager.get_status()
        if not status.get("llm_initialized"):
            raise HTTPException(
                status_code=503,
                detail="LLM client not initialized. Check API key configuration.",
            )

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: manager.run_agent(
                    agent_id,
                    request.message,
                    request.sessionKey,
                ),
            )
            return {
                "agent_id": result.agent_id,
                "session_id": result.session_id,
                "status": result.status,
                "response": result.final_response,
                "turns": result.turns,
                "duration_ms": result.total_duration_ms,
            }
        except Exception as e:
            logger.error(f"Webhook sync run error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ========================================================================
    # FEED ENDPOINTS
    # ========================================================================

    @app.get("/api/feed", tags=["Feed"])
    async def get_feed(
        limit: int = Query(50, ge=1, le=100, description="Max messages to return"),
        offset: int = Query(0, ge=0, description="Number of messages to skip"),
        agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
        unread_only: bool = Query(False, description="Only return unread messages"),
        message_type: Optional[str] = Query(None, description="Filter by type: info, success, warning, error, request")
    ):
        """
        Get messages from the operator feed.

        Messages are posted by agents to communicate with their human operators.
        Returns messages sorted by newest first.
        """
        from loop_core.tools.feed_tools import get_feed_messages
        return get_feed_messages(
            limit=limit,
            offset=offset,
            agent_id=agent_id,
            unread_only=unread_only,
            message_type=message_type
        )

    @app.get("/api/feed/unread-count", tags=["Feed"])
    async def get_feed_unread_count(
        agent_id: Optional[str] = Query(None, description="Filter by agent ID")
    ):
        """Get count of unread feed messages."""
        from loop_core.tools.feed_tools import get_unread_count
        return {"unread_count": get_unread_count(agent_id=agent_id)}

    @app.put("/api/feed/{message_id}/read", tags=["Feed"])
    async def mark_feed_message_read(message_id: str):
        """Mark a feed message as read."""
        from loop_core.tools.feed_tools import mark_message_read
        return mark_message_read(message_id)

    @app.post("/api/feed/mark-all-read", tags=["Feed"])
    async def mark_all_feed_messages_read(
        agent_id: Optional[str] = Query(None, description="Only mark messages from this agent")
    ):
        """Mark all feed messages as read."""
        from loop_core.tools.feed_tools import mark_all_read
        return mark_all_read(agent_id=agent_id)

    @app.delete("/api/feed/{message_id}", tags=["Feed"])
    async def delete_feed_message(message_id: str):
        """Delete a feed message."""
        from loop_core.tools.feed_tools import delete_message
        return delete_message(message_id)

    # ====================================================================
    # HUMAN DELEGATION
    # ====================================================================

    @app.get("/delegation/{delegation_id}/form", tags=["Delegation"])
    async def get_delegation_form(
        delegation_id: str,
        format: str = Query("html", description="Response format: 'html' (rendered form) or 'json' (raw schema)"),
    ):
        """Get the delegation form for a human to fill out.

        Returns either a self-contained HTML page (default) or raw JSON schema.
        """
        from ..delegation import load_suspended_delegation
        from ..forms import render_form_html

        manager = get_manager()
        agents_dir = Path(manager.config_manager.global_config.paths.agents_dir)

        # Scan all agents for matching delegation
        for agent_dir in agents_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            data = load_suspended_delegation(str(agent_dir))
            if not data:
                continue
            if data.get("delegation_id") != delegation_id:
                continue
            if data.get("type") != "human":
                raise HTTPException(404, "Not a human delegation")

            form_schema = data.get("form_schema", {})

            if format == "json":
                return {"delegation_id": delegation_id, "form_schema": form_schema}

            # Render HTML
            html = render_form_html(
                form_schema,
                form_id=delegation_id,
                footer_text=f"Delegation ID: {delegation_id}",
                success_message="Your response has been recorded. The agent will resume its work.",
            )
            from fastapi.responses import HTMLResponse
            return HTMLResponse(content=html)

        raise HTTPException(404, "Delegation not found or already completed")

    @app.post("/delegation/{delegation_id}/complete", tags=["Delegation"])
    async def complete_delegation(delegation_id: str, request: Request):
        """Submit a human's response to a delegation.

        Accepts JSON body with field values from the form. Triggers the
        agent's resume pipeline (same as agent delegation completion).
        """
        from ..delegation import load_suspended_delegation, delete_suspended_delegation
        from ..forms import validate_form_response

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON body")

        manager = get_manager()
        agents_dir = Path(manager.config_manager.global_config.paths.agents_dir)

        # Find the delegation
        for agent_dir in agents_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            data = load_suspended_delegation(str(agent_dir))
            if not data:
                continue
            if data.get("delegation_id") != delegation_id:
                continue
            if data.get("type") != "human":
                raise HTTPException(400, "Not a human delegation")

            agent_id = agent_dir.name
            human_id = data.get("waiting_on", "unknown")

            # Validate and coerce the response against the form schema
            form_schema = data.get("form_schema", {})
            validated = validate_form_response(form_schema, body)
            if not validated.valid:
                raise HTTPException(400, f"Validation errors: {'; '.join(validated.errors)}")
            result_data = validated.data

            # Fire callback via runtime if available
            runtime = get_runtime()
            if runtime:
                success = runtime.complete_human_delegation(delegation_id, result_data)
                if success:
                    delete_suspended_delegation(str(agent_dir))
                    return {
                        "success": True,
                        "message": f"Response recorded. Agent '{agent_id}' will resume.",
                        "delegation_id": delegation_id,
                    }

            # Fallback: no runtime -- just save the result and delete suspension
            # The agent will pick it up on next resume
            result_path = agent_dir / "delegation_results" / f"{delegation_id}.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps({
                "delegation_id": delegation_id,
                "from_human": human_id,
                "result": result_data,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }, indent=2), encoding="utf-8")
            delete_suspended_delegation(str(agent_dir))

            return {
                "success": True,
                "message": f"Response recorded for agent '{agent_id}'.",
                "delegation_id": delegation_id,
            }

        raise HTTPException(404, "Delegation not found or already completed")

    # Mount static files (must be after all routes to avoid conflicts)
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Initialize scheduler eagerly so agents always have task tools
    get_scheduler()

    # Initialize runtime eagerly so Start/Stop is available immediately
    get_runtime()

    # Initialize HiveLoop observability
    hb = hiveloop.init(
        api_key="hb_live_dev000000000000000000000000000000",
        endpoint="http://localhost:8451",
        environment="production",
    )
    app.state.hiveloop = hb
    get_manager().set_hiveloop(hb)

    return app


# ============================================================================
# APPLICATION INSTANCE
# ============================================================================

if FASTAPI_AVAILABLE:
    app = create_app()
else:
    app = None


# ============================================================================
# MAIN
# ============================================================================

def main(port: int = 8431):
    """Run the API server."""
    if not FASTAPI_AVAILABLE:
        print("FastAPI is not installed. Install with:")
        print("  pip install fastapi uvicorn")
        return

    try:
        import uvicorn
    except ImportError:
        print("Uvicorn is not installed. Install with:")
        print("  pip install uvicorn")
        return

    print("Starting Agentic Loop API server...")
    print(f"Admin Panel: http://localhost:{port}")
    print(f"API docs: http://localhost:{port}/docs")
    uvicorn.run(app, host="localhost", port=port)


if __name__ == "__main__":
    main()

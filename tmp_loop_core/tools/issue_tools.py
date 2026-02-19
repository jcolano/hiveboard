"""
ISSUE_TOOLS
============

Persistent issue reporting for agents.

Agents encounter problems they cannot solve autonomously -- expired credentials,
missing permissions, API errors, configuration issues.  This tool lets them
report those problems to a persistent, per-agent issues list that humans can
review in the Runtime admin panel and dismiss once resolved.

Storage
-------
``data/AGENTS/{agent_id}/issues.json`` -- list of issue objects:

  [{"id": "iss_001", "title": "...", "severity": "high", ...}, ...]

Deduplication: if an open issue with the same title already exists, we
increment ``occurrence_count`` and update ``last_occurrence_at`` instead of
creating a duplicate.

Tools
-----
- ``report_issue`` -- Report a problem the agent cannot resolve on its own
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult
from ..observability import get_hiveloop_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _issues_path(agent_dir: str) -> Path:
    return Path(agent_dir) / "issues.json"


def _load_issues(agent_dir: str) -> list:
    path = _issues_path(agent_dir)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_issues(agent_dir: str, issues: list) -> None:
    path = _issues_path(agent_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(issues, indent=2), encoding="utf-8")


def _next_id(issues: list) -> str:
    max_n = 0
    for iss in issues:
        iid = iss.get("id", "")
        if iid.startswith("iss_"):
            try:
                max_n = max(max_n, int(iid[4:]))
            except ValueError:
                pass
    return f"iss_{max_n + 1:03d}"


def get_open_issues_prompt(agent_dir: str) -> str:
    """Build a prompt section listing open issues for context injection."""
    issues = _load_issues(agent_dir)
    open_issues = [i for i in issues if i.get("status") == "open"]
    if not open_issues:
        return ""

    lines = ["[OPEN ISSUES -- problems you previously reported that are still unresolved]"]
    for iss in open_issues:
        sev = iss.get("severity", "medium").upper()
        count = iss.get("occurrence_count", 1)
        count_str = f" (x{count})" if count > 1 else ""
        lines.append(f"  - [{iss['id']}] [{sev}] {iss['title']}{count_str}")
        if iss.get("description"):
            lines.append(f"    {iss['description'][:120]}")
    lines.append(f"Total: {len(open_issues)} open issue(s).")
    lines.append(
        "These issues are awaiting human resolution. Do NOT retry the failed "
        "operations unless you have reason to believe the root cause was fixed."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

VALID_SEVERITIES = ("critical", "high", "medium", "low")
VALID_CATEGORIES = ("error", "functional", "technical", "permissions", "config", "other")


class IssueReportTool(BaseTool):
    """Report a problem the agent cannot resolve on its own."""

    def __init__(self, agent_dir: str):
        self._agent_dir = agent_dir

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="report_issue",
            description=(
                "Report a problem you cannot resolve on your own. Use this for "
                "errors, permission failures, missing configuration, or any blocker "
                "that requires human intervention. The issue will appear in the "
                "admin panel for the operator to review and dismiss."
            ),
            parameters=[
                ToolParameter(
                    name="title",
                    type="string",
                    description="Short summary of the issue (max 200 chars)",
                ),
                ToolParameter(
                    name="description",
                    type="string",
                    description="Full details: what happened, what you tried, what is needed",
                ),
                ToolParameter(
                    name="severity",
                    type="string",
                    description="How urgent is this issue",
                    required=False,
                    enum=list(VALID_SEVERITIES),
                    default="medium",
                ),
                ToolParameter(
                    name="category",
                    type="string",
                    description="Type of issue",
                    required=False,
                    enum=list(VALID_CATEGORIES),
                    default="error",
                ),
                ToolParameter(
                    name="context",
                    type="object",
                    description="Structured context data (e.g. tool name, error code, entity ID)",
                    required=False,
                ),
                ToolParameter(
                    name="todo_on_dismiss",
                    type="string",
                    description=(
                        "Optional: text for a TODO item that will be auto-created "
                        "when the human dismisses this issue (e.g. 'Retry creating "
                        "the contact after credentials are refreshed')"
                    ),
                    required=False,
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        title = kwargs.get("title", "").strip()
        if not title:
            return ToolResult(success=False, output="", error="title is required")
        if len(title) > 200:
            title = title[:200]

        description = kwargs.get("description", "").strip()
        if not description:
            return ToolResult(success=False, output="", error="description is required")

        severity = kwargs.get("severity", "medium")
        if severity not in VALID_SEVERITIES:
            severity = "medium"

        category = kwargs.get("category", "error")
        if category not in VALID_CATEGORIES:
            category = "error"

        context = kwargs.get("context") or {}
        todo_on_dismiss = kwargs.get("todo_on_dismiss", "")
        now = datetime.now(timezone.utc).isoformat()

        issues = _load_issues(self._agent_dir)

        # Deduplication: same title + open status -> increment count
        for iss in issues:
            if iss.get("status") == "open" and iss.get("title") == title:
                iss["occurrence_count"] = iss.get("occurrence_count", 1) + 1
                iss["last_occurrence_at"] = now
                # Update description/severity if provided again
                if description:
                    iss["description"] = description
                if context:
                    iss["context"] = context
                _save_issues(self._agent_dir, issues)

                # HiveLoop: report issue (dedup occurrence)
                _hl_agent = get_hiveloop_agent()
                if _hl_agent:
                    try:
                        _hl_agent.report_issue(
                            summary=title,
                            severity=severity,
                            category=category,
                            issue_id=iss["id"],
                            context=context or {},
                            occurrence_count=iss["occurrence_count"],
                        )
                    except Exception:
                        pass

                open_count = sum(1 for i in issues if i.get("status") == "open")
                return ToolResult(
                    success=True,
                    output=(
                        f"Duplicate issue [{iss['id']}] updated "
                        f"(occurrence #{iss['occurrence_count']}). "
                        f"{open_count} open issue(s) total."
                    ),
                )

        # New issue
        issue = {
            "id": _next_id(issues),
            "title": title,
            "description": description,
            "severity": severity,
            "category": category,
            "context": context,
            "created_at": now,
            "status": "open",
            "occurrence_count": 1,
            "last_occurrence_at": now,
            "dismissed_at": None,
            "todo_on_dismiss": todo_on_dismiss if todo_on_dismiss else None,
        }
        issues.append(issue)
        _save_issues(self._agent_dir, issues)

        # HiveLoop: report new issue
        _hl_agent = get_hiveloop_agent()
        if _hl_agent:
            try:
                _hl_agent.report_issue(
                    summary=title,
                    severity=severity,
                    category=category,
                    issue_id=issue["id"],
                    context=context or {},
                    occurrence_count=1,
                )
            except Exception:
                pass

        open_count = sum(1 for i in issues if i.get("status") == "open")
        return ToolResult(
            success=True,
            output=(
                f"Issue [{issue['id']}] reported ({severity}/{category}). "
                f"{open_count} open issue(s) total."
            ),
        )

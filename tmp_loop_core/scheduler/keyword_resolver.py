"""
KEYWORD_RESOLVER
================

Resolves ``$KEYWORD$`` placeholders in task context to actual values at execution time.

This is NOT related to skill triggers. Keywords and triggers are separate systems:
- **Keywords** (this file): Dynamic variable substitution in task context.
  Example: ``$CREDENTIALS:loopcolony$`` → ``{"base_url": "...", "auth_token": "..."}``
- **Skill triggers** (matcher.py): Text hints for selecting which skill to use.
  Example: "join loopColony" → selects the loopcolony skill.

Syntax
------
::

    $KEYWORD_NAME$                    — Required (fails if unresolved)
    $KEYWORD_NAME?$                   — Optional (returns None if fails)
    $KEYWORD_NAME|default_value$      — Default value if fails
    $KEYWORD_NAME:colon_param$        — With colon parameter
    $KEYWORD_NAME[bracket,params]$    — With bracket parameters
    $KEYWORD_NAME:param[extra]?|def$  — All modifiers combined

Available Keywords
------------------
- ``$CREDENTIALS:name$`` — Load from agent's ``credentials.json`` (e.g. loopcolony, openweather)
- ``$STATE:key$`` — Read from task's ``state.json`` (persisted between runs)
- ``$POSTS_RECENT[count]$`` — Fetch recent posts from loopColony API (default: 10)
- ``$POSTS_RANGE[from,to,limit]$`` — Fetch posts by date range (e.g. "7d,now,20")
- ``$MY_COMMENTS[count]$`` — Fetch agent's recent comments from loopColony
- ``$FILE:path$`` — Read file relative to agent's directory

Resolution Flow
---------------
1. Task scheduler calls ``resolve_context(context_dict, agent_id, task_id)``
2. Resolver recursively walks dicts, lists, and strings looking for ``$...$``
3. Each match is resolved by the appropriate handler (credentials, state, API, etc.)
4. Resolved values are formatted by ContextFormatter into readable prompt sections
5. Result is injected into the agent's prompt alongside skill instructions

Storage Paths
-------------
- Credentials: ``data/AGENTS/{agent_id}/credentials.json``
- State: ``data/AGENTS/{agent_id}/tasks/{task_id}/state.json``
- Registry (informational): ``data/CONFIG/keyword_registry.json``
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ResolverContext:
    """Context available during keyword resolution."""
    agent_id: str
    task_id: str
    agents_dir: Path
    config_dir: Path

    @property
    def agent_dir(self) -> Path:
        return self.agents_dir / self.agent_id

    @property
    def task_dir(self) -> Path:
        return self.agent_dir / "tasks" / self.task_id

    @property
    def credentials_path(self) -> Path:
        return self.agent_dir / "credentials.json"

    @property
    def state_path(self) -> Path:
        return self.task_dir / "state.json"


class KeywordResolutionError(Exception):
    """Raised when a required keyword cannot be resolved."""
    pass


class KeywordResolver:
    """
    Resolves ``$KEYWORD$`` placeholders in task context dicts to actual values.

    Resolution is recursive — walks dicts, lists, and strings. Only full-string
    matches are resolved (no inline interpolation within a larger string).

    Handlers are dispatched by keyword name:
    - CREDENTIALS → ``_resolve_credentials()`` → reads agent's credentials.json
    - STATE → ``_resolve_state()`` → reads task's state.json
    - POSTS_RECENT → ``_resolve_posts_recent()`` → HTTP call to loopColony API
    - POSTS_RANGE → ``_resolve_posts_range()`` → HTTP call with date filters
    - MY_COMMENTS → ``_resolve_my_comments()`` → HTTP call for agent's comments
    - FILE → ``_resolve_file()`` → reads file from agent's directory

    After resolution, the ContextFormatter structures results into readable
    prompt sections (Credentials, Task Parameters, Injected Data, State).

    Usage::

        resolver = KeywordResolver(agents_dir, config_dir)
        resolved = resolver.resolve_context(context_dict, agent_id, task_id)
    """

    # Pattern to match keywords: $KEYWORD$ or $KEYWORD:param$ or $KEYWORD[params]$
    # With optional modifiers: ? or |default
    KEYWORD_PATTERN = re.compile(
        r'\$([A-Z_]+)(?::([^$\[\]|?]+))?(?:\[([^\]]*)\])?([\?])?(?:\|([^$]+))?\$'
    )

    def __init__(self, agents_dir: Path, config_dir: Path):
        self.agents_dir = Path(agents_dir)
        self.config_dir = Path(config_dir)
        self._registry = self._load_registry()

    def _load_registry(self) -> dict:
        """Load keyword registry from config."""
        registry_path = self.config_dir / "keyword_registry.json"
        if registry_path.exists():
            return json.loads(registry_path.read_text(encoding='utf-8'))
        return {"keywords": {}}

    def resolve_context(self, context: dict, agent_id: str, task_id: str) -> dict:
        """
        Resolve all keywords in a context dictionary.

        Args:
            context: The context dict with potential keywords
            agent_id: The agent executing the task
            task_id: The task being executed

        Returns:
            Resolved context with keywords replaced by actual values
        """
        resolver_ctx = ResolverContext(
            agent_id=agent_id,
            task_id=task_id,
            agents_dir=self.agents_dir,
            config_dir=self.config_dir
        )

        return self._resolve_value(context, resolver_ctx)

    def _resolve_value(self, value: Any, ctx: ResolverContext) -> Any:
        """Recursively resolve keywords in a value."""
        if isinstance(value, str):
            return self._resolve_string(value, ctx)
        elif isinstance(value, dict):
            return {k: self._resolve_value(v, ctx) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._resolve_value(item, ctx) for item in value]
        else:
            return value

    def _resolve_string(self, value: str, ctx: ResolverContext) -> Any:
        """Resolve keywords in a string value."""
        # Check if the entire string is a single keyword
        match = self.KEYWORD_PATTERN.fullmatch(value)
        if match:
            return self._resolve_keyword(match, ctx)

        # Check for embedded keywords (not supported for now - return as-is)
        # Could implement string interpolation here if needed
        return value

    def _resolve_keyword(self, match: re.Match, ctx: ResolverContext) -> Any:
        """
        Resolve a single keyword match.

        Groups:
            1: keyword name (e.g., CREDENTIALS, STATE)
            2: colon parameter (e.g., loopcolony in $CREDENTIALS:loopcolony$)
            3: bracket parameters (e.g., 10 in $POSTS_RECENT[10]$)
            4: optional modifier (?)
            5: default value (after |)
        """
        keyword = match.group(1)
        colon_param = match.group(2)
        bracket_params = match.group(3)
        is_optional = match.group(4) == '?'
        default_value = match.group(5)

        # Parse bracket parameters
        params = []
        if bracket_params:
            params = [p.strip() for p in bracket_params.split(',')]

        try:
            # Route to appropriate resolver
            if keyword == "CREDENTIALS":
                return self._resolve_credentials(colon_param, ctx)
            elif keyword == "STATE":
                return self._resolve_state(colon_param, ctx)
            elif keyword == "POSTS_RECENT":
                count = int(params[0]) if params else 10
                return self._resolve_posts_recent(count, ctx)
            elif keyword == "POSTS_RANGE":
                from_date = params[0] if len(params) > 0 else "7d"
                to_date = params[1] if len(params) > 1 else "now"
                limit = int(params[2]) if len(params) > 2 else 20
                return self._resolve_posts_range(from_date, to_date, limit, ctx)
            elif keyword == "MY_COMMENTS":
                count = int(params[0]) if params else 20
                return self._resolve_my_comments(count, ctx)
            elif keyword == "FILE":
                return self._resolve_file(colon_param, ctx)
            else:
                raise KeywordResolutionError(f"Unknown keyword: {keyword}")

        except Exception as e:
            if is_optional:
                return None
            elif default_value is not None:
                # Try to parse default as JSON, fall back to string
                try:
                    return json.loads(default_value)
                except:
                    return default_value
            else:
                raise KeywordResolutionError(f"Failed to resolve ${keyword}$: {e}")

    def _resolve_credentials(self, name: str, ctx: ResolverContext) -> dict:
        """Resolve $CREDENTIALS:name$ - load from agent's credentials.json."""
        if not ctx.credentials_path.exists():
            raise KeywordResolutionError(
                f"Credentials file not found: {ctx.credentials_path}"
            )

        credentials = json.loads(ctx.credentials_path.read_text(encoding='utf-8'))

        if name not in credentials:
            raise KeywordResolutionError(
                f"Credential '{name}' not found in {ctx.credentials_path}"
            )

        return credentials[name]

    def _resolve_state(self, key: str, ctx: ResolverContext) -> Any:
        """Resolve $STATE:key$ - read from task's state.json."""
        if not ctx.state_path.exists():
            return None  # No state yet is OK

        state = json.loads(ctx.state_path.read_text(encoding='utf-8'))
        return state.get(key)

    def _resolve_posts_recent(self, count: int, ctx: ResolverContext) -> list:
        """Resolve $POSTS_RECENT[count]$ - fetch recent posts from loopColony."""
        credentials = self._resolve_credentials("loopcolony", ctx)

        import requests
        response = requests.get(
            f"{credentials['base_url']}/posts",
            params={
                "workspace_id": credentials["workspace_id"],
                "sort": "new",
                "page_size": count
            },
            headers={"Authorization": f"Bearer {credentials['auth_token']}"},
            timeout=30
        )
        response.raise_for_status()

        data = response.json()
        return data.get("posts", [])

    def _resolve_posts_range(
        self,
        from_date: str,
        to_date: str,
        limit: int,
        ctx: ResolverContext
    ) -> list:
        """Resolve $POSTS_RANGE[from, to, limit]$ - fetch posts in date range."""
        # For now, just use POSTS_RECENT with limit
        # TODO: Implement actual date filtering when loopColony API supports it
        return self._resolve_posts_recent(limit, ctx)

    def _resolve_my_comments(self, count: int, ctx: ResolverContext) -> list:
        """Resolve $MY_COMMENTS[count]$ - fetch agent's recent comments."""
        credentials = self._resolve_credentials("loopcolony", ctx)

        # TODO: Implement when loopColony has a comments endpoint for a member
        # For now, return empty list
        return []

    def _resolve_file(self, path: str, ctx: ResolverContext) -> str:
        """Resolve $FILE:path$ - read file from agent's directory."""
        file_path = ctx.agent_dir / path

        if not file_path.exists():
            raise KeywordResolutionError(f"File not found: {file_path}")

        # Security: ensure path is within agent directory
        try:
            file_path.resolve().relative_to(ctx.agent_dir.resolve())
        except ValueError:
            raise KeywordResolutionError(
                f"Access denied: {path} is outside agent directory"
            )

        return file_path.read_text(encoding='utf-8')


def format_resolved_context(context: dict) -> str:
    """
    Format resolved context for injection into prompt.

    Args:
        context: Resolved context dictionary

    Returns:
        Formatted string for prompt injection
    """
    lines = ["TASK CONTEXT:", ""]

    # Skills section
    if "skills" in context:
        for skill_name, skill_data in context["skills"].items():
            lines.append(f"## Credentials ({skill_name})")
            if isinstance(skill_data, dict):
                for key, value in skill_data.items():
                    # Mask auth tokens for security in logs (but show in prompt)
                    lines.append(f"- {key}: {value}")
            lines.append("")

    # Task parameters section
    if "task" in context:
        lines.append("## Task Parameters")
        for key, value in context["task"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")

    # Injected data section
    if "inject" in context:
        lines.append("## Injected Data")
        for key, value in context["inject"].items():
            lines.append(f"### {key}")
            if isinstance(value, list):
                lines.append(f"({len(value)} items)")
                for i, item in enumerate(value[:10], 1):  # Show first 10
                    if isinstance(item, dict):
                        # Format post/comment summary
                        title = item.get("title", item.get("body", "")[:50])
                        author = item.get("author", {}).get("name", "unknown")
                        lines.append(f"{i}. [{item.get('id', '?')}] \"{title}\" by {author}")
                    else:
                        lines.append(f"{i}. {item}")
                if len(value) > 10:
                    lines.append(f"... and {len(value) - 10} more")
            elif isinstance(value, dict):
                lines.append(json.dumps(value, indent=2))
            else:
                lines.append(str(value))
            lines.append("")

    # State section
    if "state" in context:
        lines.append("## State (from previous run)")
        for key, value in context["state"].items():
            if isinstance(value, list):
                lines.append(f"- {key}: [{len(value)} items]")
            else:
                lines.append(f"- {key}: {value}")
        lines.append("")

    return "\n".join(lines)

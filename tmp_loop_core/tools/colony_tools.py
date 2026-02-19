"""
COLONY_TOOLS
=============

Dedicated tools for interacting with the loopColony workspace API.

Two tools that provide read and write access to **all** workspace entities
(posts, tasks, notifications, conversations, members, etc.) in loopColony.
These replace verbose ``http_request`` invocations with compact, domain-specific
calls -- the agent says ``workspace_read(action="notifications")`` instead of
constructing a raw HTTP GET with headers, URL, and query params.

``workspace_read``
    Read/list/search any workspace entity (20 actions).

``workspace_write``
    Create, update, delete, or perform actions on workspace entities (18 actions).

Design follows the same pattern as ``crm_search``/``crm_write``: the agent
provides credentials (base_url, auth_token, workspace_id) and the tool handles
URL construction, headers, and serialization.

Supported Actions
-----------------

**workspace_read** (20 read actions)::

    unread_count     -> GET /notifications/unread
    notifications    -> GET /notifications
    conversations    -> GET /conversations
    conversation     -> GET /conversations/{id}
    feed             -> GET /feed
    feed_following   -> GET /feed/following
    posts            -> GET /posts
    post             -> GET /posts/{id}
    post_comments    -> GET /posts/{id}/comments
    tasks            -> GET /tasks
    task             -> GET /tasks/{id}
    topics           -> GET /topics
    topic            -> GET /topics/{id}
    members          -> GET /members/workspace/{ws_id}
    member           -> GET /members/{id}
    profile          -> GET /members/me
    search           -> GET /search
    handoffs         -> GET /posts (with metadata_handoff_for filter)
    message          -> GET /conversations/messages/{id}
    sync             -> GET /sync?since=&workspace_id= (incremental sync)

**workspace_write** (18 write actions)::

    update_presence    -> POST /members/heartbeat
    mark_read          -> POST /notifications/read
    create_post        -> POST /posts
    delete_post        -> DELETE /posts/{id}
    comment_on_post    -> POST /posts/{id}/comments
    reply_to_comment   -> POST /comments/{id}/comments
    delete_comment     -> DELETE /comments/{id}
    vote               -> POST /{target_type}/{id}/vote
    remove_vote        -> DELETE /{target_type}/{id}/vote
    start_conversation -> POST /conversations
    send_message       -> POST /conversations/{id}/messages
    create_task        -> POST /tasks
    update_task        -> POST /tasks/{id}/update
    task_comment       -> POST /tasks/{id}/comments
    delete_task        -> POST /tasks/{id}/delete
    create_topic       -> POST /topics
    follow_member      -> POST /members/{id}/follow
    unfollow_member    -> DELETE /members/{id}/follow
"""

import json
from typing import Dict, List, Optional

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


# ---------------------------------------------------------------------------
# Action routing tables
# ---------------------------------------------------------------------------

# Each value: (HTTP_METHOD, path_template)
# {id} is replaced with record_id, {ws_id} with workspace_id

COLONY_READ_ACTIONS: Dict[str, tuple] = {
    "unread_count":    ("GET", "/notifications/unread"),
    "notifications":   ("GET", "/notifications"),
    "conversations":   ("GET", "/conversations"),
    "conversation":    ("GET", "/conversations/{id}"),
    "feed":            ("GET", "/feed"),
    "feed_following":  ("GET", "/feed/following"),
    "posts":           ("GET", "/posts"),
    "post":            ("GET", "/posts/{id}"),
    "post_comments":   ("GET", "/posts/{id}/comments"),
    "tasks":           ("GET", "/tasks"),
    "task":            ("GET", "/tasks/{id}"),
    "topics":          ("GET", "/topics"),
    "topic":           ("GET", "/topics/{id}"),
    "members":         ("GET", "/members/workspace/{ws_id}"),
    "member":          ("GET", "/members/{id}"),
    "profile":         ("GET", "/members/me"),
    "search":          ("GET", "/search"),
    "handoffs":        ("GET", "/posts"),
    "message":         ("GET", "/conversations/messages/{id}"),
    "sync":            ("GET", "/sync"),
}

COLONY_WRITE_ACTIONS: Dict[str, tuple] = {
    "update_presence":    ("POST",   "/members/heartbeat"),
    "mark_read":          ("POST",   "/notifications/read"),
    "create_post":        ("POST",   "/posts"),
    "delete_post":        ("DELETE", "/posts/{id}"),
    "comment_on_post":    ("POST",   "/posts/{id}/comments"),
    "reply_to_comment":   ("POST",   "/comments/{id}/comments"),
    "delete_comment":     ("DELETE", "/comments/{id}"),
    "vote":               ("POST",   "/{target_type}/{id}/vote"),
    "remove_vote":        ("DELETE", "/{target_type}/{id}/vote"),
    "start_conversation": ("POST",   "/conversations"),
    "send_message":       ("POST",   "/conversations/{id}/messages"),
    "create_task":        ("POST",   "/tasks"),
    "update_task":        ("POST",   "/tasks/{id}/update"),
    "task_comment":       ("POST",   "/tasks/{id}/comments"),
    "delete_task":        ("POST",   "/tasks/{id}/delete"),
    "create_topic":       ("POST",   "/topics"),
    "follow_member":      ("POST",   "/members/{id}/follow"),
    "unfollow_member":    ("DELETE", "/members/{id}/follow"),
}

READ_ACTIONS_STR = ", ".join(sorted(COLONY_READ_ACTIONS.keys()))
WRITE_ACTIONS_STR = ", ".join(sorted(COLONY_WRITE_ACTIONS.keys()))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_url(base_url: str, path_template: str,
               record_id: str = "", workspace_id: str = "",
               data: dict = None) -> str:
    """Build the full URL from template, substituting placeholders."""
    path = path_template
    path = path.replace("{id}", record_id or "")
    path = path.replace("{ws_id}", workspace_id or "")
    # For vote/remove_vote: {target_type} comes from data
    if "{target_type}" in path and data:
        target = data.get("target_type", "posts")
        path = path.replace("{target_type}", target)
    return base_url.rstrip("/") + path


def _format_response(data) -> str:
    """Format API response for the LLM."""
    if isinstance(data, list):
        count = len(data)
        if count <= 10:
            return json.dumps(data, indent=2, default=str)
        truncated = data[:10]
        text = json.dumps(truncated, indent=2, default=str)
        return f"{text}\n\n... and {count - 10} more (total: {count})"
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# ColonyReadTool
# ---------------------------------------------------------------------------

class ColonyReadTool(BaseTool):
    """Read, list, or search any loopColony workspace entity."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="workspace_read",
            description=(
                "Read data from the loopColony workspace. Supports 20 actions: "
                "unread_count, notifications, conversations, conversation, feed, "
                "feed_following, posts, post, post_comments, tasks, task, topics, "
                "topic, members, member, profile, search, handoffs, message, sync. "
                "Provide action name and optional filters. "
                "sync: pass filters.since (ISO 8601 timestamp) to get all changes since last check."
            ),
            parameters=[
                ToolParameter(
                    name="base_url",
                    type="string",
                    description="loopColony API base URL",
                ),
                ToolParameter(
                    name="auth_token",
                    type="string",
                    description="Bearer token for loopColony",
                ),
                ToolParameter(
                    name="workspace_id",
                    type="string",
                    description="Workspace ID scope",
                ),
                ToolParameter(
                    name="action",
                    type="string",
                    description=(
                        f"Read action: {READ_ACTIONS_STR}"
                    ),
                ),
                ToolParameter(
                    name="record_id",
                    type="string",
                    description=(
                        "Entity ID for single-record fetches "
                        "(conversation, post, task, member, topic)"
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="filters",
                    type="object",
                    description=(
                        "Query params: sort, status, type, q, unread_only, "
                        "filter, page_size, topic, metadata_type, "
                        "metadata_handoff_for, agent_id, etc."
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Max results to return",
                    required=False,
                ),
            ],
        )

    def execute(
        self,
        base_url: str = "",
        auth_token: str = "",
        workspace_id: str = "",
        action: str = "",
        record_id: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: Optional[int] = None,
        **kwargs,
    ) -> ToolResult:
        # Auto-fill credentials — LLM often hallucinates placeholders
        _creds = self._apply_credentials(locals())
        base_url, auth_token, workspace_id = _creds["base_url"], _creds["auth_token"], _creds["workspace_id"]

        route = COLONY_READ_ACTIONS.get(action)
        if route is None:
            return ToolResult(
                success=False, output="",
                error=f"Unknown read action '{action}'. Available: {READ_ACTIONS_STR}",
            )

        method, path_template = route

        # Validate record_id for actions that need it
        needs_id = "{id}" in path_template
        if needs_id and not record_id:
            return ToolResult(
                success=False, output="",
                error=f"Action '{action}' requires record_id",
            )

        try:
            import requests as req_lib
        except ImportError:
            return ToolResult(
                success=False, output="",
                error="requests package not installed",
            )

        url = _build_url(base_url, path_template, record_id, workspace_id)
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }

        # Build query parameters
        params = {}
        # Most list endpoints need workspace_id
        if action in ("feed", "posts", "tasks", "topics", "handoffs"):
            params["workspace_id"] = workspace_id
        if filters:
            params.update(filters)
        if limit is not None:
            params["limit"] = limit

        # Special case: handoffs adds metadata_handoff_for filter
        if action == "handoffs":
            # agent_id for handoff can come from filters or be auto-detected
            if "metadata_handoff_for" not in params and "agent_id" not in params:
                pass  # Agent should provide this in filters

        # Special case: sync needs since + workspace_id as query params
        if action == "sync":
            since = (filters or {}).get("since", "") if filters else ""
            if not since:
                return ToolResult(
                    success=False, output="",
                    error="Action 'sync' requires filters.since (ISO 8601 timestamp)",
                )
            params["since"] = since
            params["workspace_id"] = workspace_id

        try:
            resp = req_lib.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return ToolResult(
                success=True,
                output=_format_response(data),
                metadata={"action": action, "record_id": record_id},
            )
        except req_lib.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            body = ""
            try:
                body = e.response.text[:500] if e.response is not None else ""
            except Exception:
                pass
            return ToolResult(
                success=False, output="",
                error=f"HTTP {status}: {body}",
            )
        except Exception as e:
            return ToolResult(
                success=False, output="",
                error=f"workspace_read failed: {str(e)}",
            )


# ---------------------------------------------------------------------------
# ColonyWriteTool
# ---------------------------------------------------------------------------

class ColonyWriteTool(BaseTool):
    """Create, update, delete, or perform actions on loopColony workspace entities."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="workspace_write",
            description=(
                "Write data to the loopColony workspace. Supports 18 actions: "
                "update_presence, mark_read, create_post, delete_post, "
                "comment_on_post, reply_to_comment, delete_comment, vote, "
                "remove_vote, start_conversation, send_message, create_task, "
                "update_task, task_comment, delete_task, create_topic, "
                "follow_member, unfollow_member. "
                "Provide action name, optional record_id, and data."
            ),
            parameters=[
                ToolParameter(
                    name="base_url",
                    type="string",
                    description="loopColony API base URL",
                ),
                ToolParameter(
                    name="auth_token",
                    type="string",
                    description="Bearer token for loopColony",
                ),
                ToolParameter(
                    name="workspace_id",
                    type="string",
                    description="Workspace ID scope",
                ),
                ToolParameter(
                    name="action",
                    type="string",
                    description=(
                        f"Write action: {WRITE_ACTIONS_STR}"
                    ),
                ),
                ToolParameter(
                    name="record_id",
                    type="string",
                    description=(
                        "Target entity ID (for comment_on_post, send_message, "
                        "update_task, delete_post, etc.)"
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="data",
                    type="object",
                    description=(
                        "Request body fields. For create_post: title, body, topic "
                        "(topic ID like 'tp_xxx' from the topics list), "
                        "metadata, visibility. For create_topic: name, description. "
                        "For send_message: body. For vote: "
                        "target_type (posts/comments), value (1/-1). Etc."
                    ),
                    required=False,
                ),
            ],
        )

    def execute(
        self,
        base_url: str = "",
        auth_token: str = "",
        workspace_id: str = "",
        action: str = "",
        record_id: Optional[str] = None,
        data: Optional[dict] = None,
        **kwargs,
    ) -> ToolResult:
        # Auto-fill credentials — LLM often hallucinates placeholders
        _creds = self._apply_credentials(locals())
        base_url, auth_token, workspace_id = _creds["base_url"], _creds["auth_token"], _creds["workspace_id"]

        route = COLONY_WRITE_ACTIONS.get(action)
        if route is None:
            return ToolResult(
                success=False, output="",
                error=f"Unknown write action '{action}'. Available: {WRITE_ACTIONS_STR}",
            )

        method, path_template = route

        # Validate record_id for actions that need it
        needs_id = "{id}" in path_template
        if needs_id and not record_id:
            return ToolResult(
                success=False, output="",
                error=f"Action '{action}' requires record_id",
            )

        # Validate data for actions that require a body
        _ACTIONS_REQUIRED_FIELDS = {
            "create_post": "title, body",
            "create_task": "title",
            "create_topic": "name, description (min 10 chars)",
            "comment_on_post": "body",
            "reply_to_comment": "body",
            "send_message": "body",
            "update_task": "fields to update (e.g. status, title)",
            "task_comment": "body",
            "vote": "target_type (posts/comments), value (1 or -1)",
        }
        required_fields = _ACTIONS_REQUIRED_FIELDS.get(action)
        if required_fields and not data:
            return ToolResult(
                success=False, output="",
                error=(
                    f"Action '{action}' requires the 'data' parameter with fields: "
                    f"{required_fields}. Pass these as a JSON object in the 'data' parameter."
                ),
            )

        try:
            import requests as req_lib
        except ImportError:
            return ToolResult(
                success=False, output="",
                error="requests package not installed",
            )

        url = _build_url(base_url, path_template, record_id, workspace_id, data)
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }

        # Build request body
        payload = dict(data or {})

        # Auto-inject workspace_id for create actions that need it
        if action in ("create_post", "create_task", "create_topic"):
            payload.setdefault("workspace_id", workspace_id)

        # Auto-fill description for create_topic if missing (required, min 10 chars)
        if action == "create_topic" and not payload.get("description"):
            topic_name = payload.get("name", "General")
            payload["description"] = f"{topic_name} discussions and related topics"

        # For mark_read: notification_ids go in body (optional)
        # For vote: value goes in body, target_type was used for URL
        # Remove target_type from payload since it's in the URL
        if action in ("vote", "remove_vote"):
            payload.pop("target_type", None)

        try:
            if method == "POST":
                resp = req_lib.post(
                    url, headers=headers, json=payload if payload else None,
                    timeout=10,
                )
            elif method == "DELETE":
                resp = req_lib.delete(url, headers=headers, timeout=10)
            else:
                return ToolResult(
                    success=False, output="",
                    error=f"Unsupported method: {method}",
                )

            resp.raise_for_status()

            # Parse response
            try:
                resp_data = resp.json()
            except Exception:
                resp_data = {"status": "ok"}

            return ToolResult(
                success=True,
                output=_format_response(resp_data),
                metadata={"action": action, "record_id": record_id},
            )

        except req_lib.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            body = ""
            try:
                body = e.response.text[:500] if e.response is not None else ""
            except Exception:
                pass
            return ToolResult(
                success=False, output="",
                error=f"HTTP {status}: {body}",
            )
        except Exception as e:
            return ToolResult(
                success=False, output="",
                error=f"workspace_write failed: {str(e)}",
            )

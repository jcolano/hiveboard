"""
TICKET_CRM_TOOLS
================

CRM ticketing tools for the Agentic Loop Framework.

These tools operate on loopColony's CRM ticketing system (NOT loopCore's
task scheduler).  The ``_crm`` suffix distinguishes them from task_tools.py
which manages loopCore's internal scheduled tasks.

``support_ticket_create``
    Create a support ticket in loopColony's ticketing system.

``support_ticket_update``
    Update a ticket — add comment, resolve, close, reopen, assign, or escalate.

The agent provides its own credentials (base_url, auth_token) which it reads
from its memory at runtime.

Requires the ``requests`` library (already installed).

support_ticket_create — How It Works
---------------------------------
1. Sends ``POST /tickets`` with the subject, description, priority, and any
   optional fields (category, assignee_id, CRM links, queue, tags).
2. The ``source`` field is set to ``"api"`` to indicate agent-created tickets.
3. Returns the ticket ID, ticket number, and initial status.

Parameters:

- ``base_url``, ``auth_token``, ``workspace_id`` — standard auth (from memory)
- ``subject`` (required) — ticket subject line
- ``description`` — detailed description of the issue
- ``priority`` — ``low``, ``medium`` (default), ``high``, or ``urgent``
- ``category`` — category label for routing
- ``assignee_id`` — assign to a specific team member
- ``contact_id`` — link to CRM contact (the customer who reported it)
- ``company_id`` — link to CRM company
- ``deal_id`` — link to CRM deal
- ``queue_id`` — assign to a ticket queue for team-based routing
- ``tags`` — list of labels/tags

Returns: ``ToolResult`` with output like ``"Ticket TKT-001 created (status: new, priority: high)"``

support_ticket_update — How It Works
---------------------------------
Dispatches to the appropriate ticket action endpoint based on ``action``:

+-------------+-----------------------------------+----------------------------+
| Action      | API Endpoint                      | Required Params            |
+=============+===================================+============================+
| ``comment`` | ``POST /tickets/{id}/comments``   | ``comment``                |
+-------------+-----------------------------------+----------------------------+
| ``resolve`` | ``POST /tickets/{id}/resolve``    | ``comment`` (optional)     |
+-------------+-----------------------------------+----------------------------+
| ``close``   | ``POST /tickets/{id}/close``      | —                          |
+-------------+-----------------------------------+----------------------------+
| ``reopen``  | ``POST /tickets/{id}/reopen``     | —                          |
+-------------+-----------------------------------+----------------------------+
| ``assign``  | ``POST /tickets/{id}/assign``     | ``assignee_id``            |
+-------------+-----------------------------------+----------------------------+
| ``escalate``| ``POST /tickets/{id}/escalate``   | —                          |
+-------------+-----------------------------------+----------------------------+

Parameters:

- ``base_url``, ``auth_token`` — standard auth
- ``ticket_id`` (required) — the ticket to update
- ``action`` (required) — one of the six actions above
- ``comment`` — comment text; required for ``comment`` action, optional for ``resolve``
- ``internal`` — if True, marks the comment as an internal note (not visible to customer)
- ``assignee_id`` — target member for the ``assign`` action

Returns: ``ToolResult`` with output like ``"Ticket tkt_456: resolved"``

When to Use These vs crm_search/crm_write
------------------------------------------
- Use ``support_ticket_create`` to create tickets (sets ``source: "api"`` automatically).
- Use ``support_ticket_update`` for lifecycle actions (resolve, close, escalate, etc.)
  that go beyond simple field updates.
- Use ``crm_search(entity="tickets", ...)`` to list/search/get tickets.
- Use ``crm_write(entity="tickets", action="update", ...)`` for simple field
  updates (e.g. changing priority or category without a lifecycle action).

Usage Examples
--------------
**Create a ticket from a customer email**::

    support_ticket_create(subject="Cannot access dashboard",
                      description="Customer reports 403 error on /dashboard",
                      priority="high", contact_id="contact_jane",
                      category="access-issue")

**Add a diagnostic comment**::

    support_ticket_update(ticket_id="tkt_456", action="comment",
                      comment="Checked logs: auth token expired at 14:32 UTC",
                      internal=True)

**Resolve with resolution note**::

    support_ticket_update(ticket_id="tkt_456", action="resolve",
                      comment="Reset auth token. Customer confirmed access restored.")

**Escalate to senior support**::

    support_ticket_update(ticket_id="tkt_789", action="escalate")

**Reassign to another rep**::

    support_ticket_update(ticket_id="tkt_789", action="assign",
                      assignee_id="member_senior_rep")
"""

from typing import List, Optional

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class TicketCreateCrmTool(BaseTool):
    """Create a support ticket in loopColony's CRM ticketing system."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="support_ticket_create",
            description=(
                "Create a support ticket in loopColony's CRM ticketing system. "
                "Set subject, priority, category, assignee, and link to CRM "
                "contacts/companies/deals. Returns the ticket ID and number."
            ),
            parameters=[
                ToolParameter(
                    name="base_url",
                    type="string",
                    description="loopColony API base URL (from memory/loopcolony.json)",
                ),
                ToolParameter(
                    name="auth_token",
                    type="string",
                    description="Agent's bearer token for loopColony",
                ),
                ToolParameter(
                    name="workspace_id",
                    type="string",
                    description="Workspace ID scope",
                ),
                ToolParameter(
                    name="subject",
                    type="string",
                    description="Ticket subject",
                ),
                ToolParameter(
                    name="description",
                    type="string",
                    description="Ticket description",
                    required=False,
                ),
                ToolParameter(
                    name="priority",
                    type="string",
                    description="Priority: low, medium, high, or urgent (default: medium)",
                    required=False,
                ),
                ToolParameter(
                    name="category",
                    type="string",
                    description="Category label",
                    required=False,
                ),
                ToolParameter(
                    name="assignee_id",
                    type="string",
                    description="Assign to a team member",
                    required=False,
                ),
                ToolParameter(
                    name="contact_id",
                    type="string",
                    description="Link to CRM contact",
                    required=False,
                ),
                ToolParameter(
                    name="company_id",
                    type="string",
                    description="Link to CRM company",
                    required=False,
                ),
                ToolParameter(
                    name="deal_id",
                    type="string",
                    description="Link to CRM deal",
                    required=False,
                ),
                ToolParameter(
                    name="queue_id",
                    type="string",
                    description="Assign to a ticket queue",
                    required=False,
                ),
                ToolParameter(
                    name="tags",
                    type="array",
                    description="Labels/tags for the ticket",
                    required=False,
                ),
            ],
        )

    def execute(
        self,
        base_url: str,
        auth_token: str,
        workspace_id: str,
        subject: str,
        description: Optional[str] = None,
        priority: Optional[str] = None,
        category: Optional[str] = None,
        assignee_id: Optional[str] = None,
        contact_id: Optional[str] = None,
        company_id: Optional[str] = None,
        deal_id: Optional[str] = None,
        queue_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        **kwargs,
    ) -> ToolResult:
        try:
            import requests
        except ImportError:
            return ToolResult(
                success=False,
                output="",
                error="requests package not installed. Run: pip install requests",
            )

        base_url = base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }

        try:
            payload = {
                "workspace_id": workspace_id,
                "subject": subject,
                "source": "api",
            }
            if description:
                payload["description"] = description
            if priority:
                payload["priority"] = priority
            if category:
                payload["category"] = category
            if assignee_id:
                payload["assignee_id"] = assignee_id
            if contact_id:
                payload["contact_id"] = contact_id
            if company_id:
                payload["company_id"] = company_id
            if deal_id:
                payload["deal_id"] = deal_id
            if queue_id:
                payload["queue_id"] = queue_id
            if tags:
                payload["tags"] = tags

            resp = requests.post(
                f"{base_url}/tickets",
                headers=headers,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            ticket = resp.json()

            ticket_id = ticket.get("id", "")
            ticket_number = ticket.get("ticket_number", "")
            status = ticket.get("status", "new")
            prio = ticket.get("priority", priority or "medium")

            return ToolResult(
                success=True,
                output=f"Ticket {ticket_number} created (status: {status}, priority: {prio})",
                metadata={
                    "ticket_id": ticket_id,
                    "ticket_number": ticket_number,
                    "status": status,
                },
            )

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            body = ""
            try:
                body = e.response.text[:500] if e.response is not None else ""
            except Exception:
                pass
            return ToolResult(
                success=False,
                output="",
                error=f"HTTP {status_code}: {body}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Ticket creation failed: {str(e)}",
            )


class TicketUpdateCrmTool(BaseTool):
    """Update a CRM ticket — comment, resolve, close, reopen, assign, or escalate."""

    VALID_ACTIONS = {"comment", "resolve", "close", "reopen", "assign", "escalate"}

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="support_ticket_update",
            description=(
                "Update a ticket in loopColony's CRM ticketing system. "
                "Actions: comment (add a note), resolve (with resolution note), "
                "close, reopen, assign (to a team member), or escalate."
            ),
            parameters=[
                ToolParameter(
                    name="base_url",
                    type="string",
                    description="loopColony API base URL (from memory/loopcolony.json)",
                ),
                ToolParameter(
                    name="auth_token",
                    type="string",
                    description="Agent's bearer token for loopColony",
                ),
                ToolParameter(
                    name="ticket_id",
                    type="string",
                    description="Ticket ID to update",
                ),
                ToolParameter(
                    name="action",
                    type="string",
                    description="Action: comment, resolve, close, reopen, assign, or escalate",
                ),
                ToolParameter(
                    name="comment",
                    type="string",
                    description="Comment text (required for comment and resolve actions)",
                    required=False,
                ),
                ToolParameter(
                    name="internal",
                    type="boolean",
                    description="Mark comment as internal note (default: false)",
                    required=False,
                ),
                ToolParameter(
                    name="assignee_id",
                    type="string",
                    description="Target member ID for assign action",
                    required=False,
                ),
            ],
        )

    def execute(
        self,
        base_url: str,
        auth_token: str,
        ticket_id: str,
        action: str,
        comment: Optional[str] = None,
        internal: bool = False,
        assignee_id: Optional[str] = None,
        **kwargs,
    ) -> ToolResult:
        if action not in self.VALID_ACTIONS:
            return ToolResult(
                success=False,
                output="",
                error=f"Invalid action '{action}'. Must be one of: {', '.join(sorted(self.VALID_ACTIONS))}",
            )

        try:
            import requests
        except ImportError:
            return ToolResult(
                success=False,
                output="",
                error="requests package not installed. Run: pip install requests",
            )

        base_url = base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }

        try:
            ticket_url = f"{base_url}/tickets/{ticket_id}"

            if action == "comment":
                if not comment:
                    return ToolResult(
                        success=False,
                        output="",
                        error="comment parameter is required for the comment action",
                    )
                resp = requests.post(
                    f"{ticket_url}/comments",
                    headers=headers,
                    json={"body": comment, "is_internal": internal},
                    timeout=10,
                )
                resp.raise_for_status()
                desc = "internal note added" if internal else "comment added"

            elif action == "resolve":
                resp = requests.post(
                    f"{ticket_url}/resolve",
                    headers=headers,
                    json={"resolution_note": comment or ""},
                    timeout=10,
                )
                resp.raise_for_status()
                desc = "resolved"

            elif action == "close":
                resp = requests.post(
                    f"{ticket_url}/close",
                    headers=headers,
                    timeout=10,
                )
                resp.raise_for_status()
                desc = "closed"

            elif action == "reopen":
                resp = requests.post(
                    f"{ticket_url}/reopen",
                    headers=headers,
                    timeout=10,
                )
                resp.raise_for_status()
                desc = "reopened"

            elif action == "assign":
                if not assignee_id:
                    return ToolResult(
                        success=False,
                        output="",
                        error="assignee_id parameter is required for the assign action",
                    )
                resp = requests.post(
                    f"{ticket_url}/assign",
                    headers=headers,
                    json={"assignee_id": assignee_id},
                    timeout=10,
                )
                resp.raise_for_status()
                desc = f"assigned to {assignee_id}"

            elif action == "escalate":
                resp = requests.post(
                    f"{ticket_url}/escalate",
                    headers=headers,
                    timeout=10,
                )
                resp.raise_for_status()
                desc = "escalated"

            # Try to get updated status from response
            new_status = None
            try:
                resp_data = resp.json()
                new_status = resp_data.get("status")
            except Exception:
                pass

            return ToolResult(
                success=True,
                output=f"Ticket {ticket_id}: {desc}",
                metadata={
                    "ticket_id": ticket_id,
                    "action": action,
                    "new_status": new_status,
                },
            )

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            body = ""
            try:
                body = e.response.text[:500] if e.response is not None else ""
            except Exception:
                pass
            return ToolResult(
                success=False,
                output="",
                error=f"HTTP {status_code}: {body}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Ticket update failed: {str(e)}",
            )

"""
CRM_TOOLS
=========

Generic CRM tools for the Agentic Loop Framework.

Two tools that provide read and write access to **all** CRM entities in
loopColony.  These replace verbose ``http_request`` invocations with compact,
domain-specific calls.  The agent specifies an ``entity`` name (e.g.
``"contacts"``, ``"deals"``); the tool maps it to the correct API path
internally, adds auth headers, and handles serialization.

``crm_search``
    List, search, or get a single record from any CRM entity.

``crm_write``
    Create, update, or delete a record on any CRM entity.

The agent provides its own credentials (base_url, auth_token) which it
reads from its memory at runtime.  ``workspace_id`` scopes every request
to the correct tenant.

Requires the ``requests`` library (already installed).

Supported Entities
------------------
The ``entity`` parameter accepts any of the following names.  The tool
resolves each to the corresponding loopColony REST path.

**Core CRM**::

    contacts      → /contacts          (people / leads)
    companies     → /companies         (organizations)
    deals         → /deals             (sales opportunities)
    activities    → /activities        (calls, emails, meetings, notes)
    crm_fields    → /crm/fields        (custom field definitions)

**Sales Enablement**::

    products            → /products
    product_categories  → /product-categories
    price_books         → /price-books
    quotes              → /quotes
    bundles             → /bundles
    discount_rules      → /discount-rules
    approvals           → /approvals
    contracts           → /contracts
    guided_selling      → /guided-selling

**Ticketing & Knowledge Base**::

    tickets            → /tickets
    sla_policies       → /sla-policies
    ticket_templates   → /ticket-templates
    ticket_queues      → /ticket-queues
    escalation_rules   → /escalation-rules
    kb_categories      → /kb/categories
    kb_articles        → /kb/articles

**Email**::

    emails           → /emails
    email_templates  → /email-templates
    email_threads    → /email-threads
    mail_accounts    → /mail-accounts

**Calendar & Booking**::

    calendars             → /calendars
    calendar_events       → /calendar-events
    booking_slots         → /booking-slots
    booking_reservations  → /booking-reservations

**Analytics (read-only)**::

    analytics  → /crm/analytics
    dashboard  → /crm/dashboard
    timeline   → /crm/timeline
    pipeline   → /deals/pipeline      (deals grouped by stage)

How It Works
------------
1. The agent calls ``crm_search`` or ``crm_write`` with an ``entity`` name.
2. The tool looks up the entity in ``ENTITY_PATHS`` to get the URL path.
3. It adds ``Authorization: Bearer {auth_token}`` and ``workspace_id`` to
   every request automatically.
4. For reads, filters are passed as query parameters.
5. For writes, data is sent as the JSON body.
6. The raw JSON response is returned so the agent can reason over it.

Error handling follows the standard pattern: HTTP errors return the status
code and response body snippet; connection errors return the exception
message.

Usage Examples
--------------
**Search / list records**::

    # List all leads
    crm_search(entity="contacts", filters={"status": "lead"}, limit=20)

    # Get a single deal by ID
    crm_search(entity="deals", record_id="deal_456")

    # Get pipeline view (deals grouped by stage)
    crm_search(entity="pipeline")

    # Search KB articles
    crm_search(entity="kb_articles", filters={"q": "password reset"})

    # List products in a category
    crm_search(entity="products", filters={"category_id": "cat_123"})

**Create records**::

    # Create a new contact
    crm_write(entity="contacts", action="create",
              data={"name": "Jane Smith", "email": "jane@acme.com",
                    "status": "lead", "owner_id": "member_sarah"})

    # Log an activity
    crm_write(entity="activities", action="create",
              data={"type": "call", "title": "Discovery call with Acme",
                    "contact_id": "contact_123", "deal_id": "deal_456",
                    "description": "Discussed pricing. Follow up next week."})

**Update records**::

    # Move a deal to next stage
    crm_write(entity="deals", action="update", record_id="deal_456",
              data={"stage": "negotiation", "probability": 0.75})

    # Update contact score
    crm_write(entity="contacts", action="update", record_id="contact_123",
              data={"custom_fields": {"lead_score": 85}})

**Delete records**::

    # Remove a duplicate contact
    crm_write(entity="contacts", action="delete", record_id="contact_dup")

When to Use crm_search/crm_write vs Specialized Tools
------------------------------------------------------
- Use ``email_send`` (not crm_write on "emails") to compose and send email
  — it handles the multi-step draft→send workflow.
- Use ``support_ticket_create`` / ``support_ticket_update`` for ticket lifecycle
  actions (resolve, close, escalate) that go beyond simple CRUD.
- Use ``crm_search`` / ``crm_write`` for all other CRM data access:
  contacts, deals, companies, activities, products, quotes, KB articles, etc.
"""

from typing import Dict, List, Optional

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


# ---------------------------------------------------------------------------
# Entity name → URL path segment mapping
# ---------------------------------------------------------------------------
# Keys are the names agents use; values are the URL path after the base_url.
# Most entities follow a simple /{path} pattern for list/create and
# /{path}/{id} for get/update/delete.

ENTITY_PATHS: Dict[str, str] = {
    # Core CRM
    "contacts": "contacts",
    "companies": "companies",
    "deals": "deals",
    "activities": "activities",
    "crm_fields": "crm/fields",
    # Sales enablement
    "products": "products",
    "product_categories": "product-categories",
    "price_books": "price-books",
    "quotes": "quotes",
    "bundles": "bundles",
    "discount_rules": "discount-rules",
    "approvals": "approvals",
    "contracts": "contracts",
    "guided_selling": "guided-selling",
    # Ticketing
    "tickets": "tickets",
    "sla_policies": "sla-policies",
    "ticket_templates": "ticket-templates",
    "ticket_queues": "ticket-queues",
    "escalation_rules": "escalation-rules",
    # Knowledge base
    "kb_categories": "kb/categories",
    "kb_articles": "kb/articles",
    # Email
    "emails": "emails",
    "email_templates": "email-templates",
    "email_threads": "email-threads",
    "mail_accounts": "mail-accounts",
    # Calendar & booking
    "calendars": "calendars",
    "calendar_events": "calendar-events",
    "booking_slots": "booking-slots",
    "booking_reservations": "booking-reservations",
    # Analytics (read-only)
    "analytics": "crm/analytics",
    "dashboard": "crm/dashboard",
    "timeline": "crm/timeline",
    # Special views (read-only)
    "pipeline": "deals/pipeline",
    # Inbound module
    "inbound_forms": "inbound/forms",
    "inbound_submissions": "inbound/submissions",
    "inbound_experiments": "inbound/experiments",
    "inbound_analytics": "inbound/analytics",
}

ENTITY_NAMES_STR = ", ".join(sorted(ENTITY_PATHS.keys()))


class CrmSearchTool(BaseTool):
    """Search, list, or get a single record from any loopColony CRM entity."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="crm_search",
            description=(
                "Search, list, or get a single record from any CRM entity in "
                "loopColony. Provide an entity name and optional filters. "
                "If record_id is given, fetches that single record. "
                f"Entities: {ENTITY_NAMES_STR}."
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
                    name="entity",
                    type="string",
                    description=(
                        "CRM entity name, e.g. contacts, deals, products, "
                        "tickets, kb_articles, pipeline"
                    ),
                ),
                ToolParameter(
                    name="record_id",
                    type="string",
                    description="Fetch a single record by ID (omit to list/search)",
                    required=False,
                ),
                ToolParameter(
                    name="filters",
                    type="object",
                    description=(
                        "Query filters as key-value pairs, e.g. "
                        '{\"status\": \"lead\", \"owner_id\": \"member_123\"}'
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Max records to return (default 50)",
                    required=False,
                ),
                ToolParameter(
                    name="offset",
                    type="integer",
                    description="Number of records to skip for pagination",
                    required=False,
                ),
            ],
        )

    def execute(
        self,
        base_url: str = "",
        auth_token: str = "",
        workspace_id: str = "",
        entity: str = "",
        record_id: Optional[str] = None,
        filters: Optional[dict] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        **kwargs,
    ) -> ToolResult:
        # Auto-fill credentials — LLM often hallucinates placeholders
        _creds = self._apply_credentials(locals())
        base_url, auth_token, workspace_id = _creds["base_url"], _creds["auth_token"], _creds["workspace_id"]

        path = ENTITY_PATHS.get(entity)
        if path is None:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Unknown entity '{entity}'. "
                    f"Available: {ENTITY_NAMES_STR}"
                ),
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
            if record_id:
                # GET single record
                url = f"{base_url}/{path}/{record_id}"
                resp = requests.get(url, headers=headers, timeout=10)
            else:
                # GET list with filters
                url = f"{base_url}/{path}"
                params = {"workspace_id": workspace_id}
                if filters:
                    params.update(filters)
                if limit is not None:
                    params["limit"] = limit
                if offset is not None:
                    params["offset"] = offset
                resp = requests.get(
                    url, params=params, headers=headers, timeout=15,
                )

            resp.raise_for_status()
            data = resp.json()

            # Format output depending on response shape
            if record_id:
                return ToolResult(
                    success=True,
                    output=_format_record(entity, data),
                    metadata={"entity": entity, "record_id": record_id},
                )
            else:
                count = len(data) if isinstance(data, list) else "?"
                return ToolResult(
                    success=True,
                    output=_format_list(entity, data),
                    metadata={
                        "entity": entity,
                        "count": count,
                        "filters": filters or {},
                    },
                )

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            body = ""
            try:
                body = e.response.text[:500] if e.response is not None else ""
            except Exception:
                pass
            return ToolResult(
                success=False,
                output="",
                error=f"HTTP {status}: {body}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"CRM search failed: {str(e)}",
            )


class CrmWriteTool(BaseTool):
    """Create, update, or delete a record on any loopColony CRM entity."""

    VALID_ACTIONS = {"create", "update", "delete"}

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="crm_write",
            description=(
                "Create, update, or delete a record on any CRM entity in "
                "loopColony. Specify the entity, action, and data. "
                f"Entities: {ENTITY_NAMES_STR}."
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
                    name="entity",
                    type="string",
                    description=(
                        "CRM entity name, e.g. contacts, deals, products, "
                        "activities, kb_articles"
                    ),
                ),
                ToolParameter(
                    name="action",
                    type="string",
                    description="Action: create, update, or delete",
                ),
                ToolParameter(
                    name="record_id",
                    type="string",
                    description="Record ID (required for update and delete)",
                    required=False,
                ),
                ToolParameter(
                    name="data",
                    type="object",
                    description=(
                        "Record fields for create or update, e.g. "
                        '{\"name\": \"Jane\", \"status\": \"lead\"}'
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
        entity: str = "",
        action: str = "",
        record_id: Optional[str] = None,
        data: Optional[dict] = None,
        **kwargs,
    ) -> ToolResult:
        # Auto-fill credentials — LLM often hallucinates placeholders
        _creds = self._apply_credentials(locals())
        base_url, auth_token, workspace_id = _creds["base_url"], _creds["auth_token"], _creds["workspace_id"]

        if action not in self.VALID_ACTIONS:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Invalid action '{action}'. "
                    f"Must be one of: {', '.join(sorted(self.VALID_ACTIONS))}"
                ),
            )

        path = ENTITY_PATHS.get(entity)
        if path is None:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Unknown entity '{entity}'. "
                    f"Available: {ENTITY_NAMES_STR}"
                ),
            )

        if action in ("update", "delete") and not record_id:
            return ToolResult(
                success=False,
                output="",
                error=f"record_id is required for '{action}' action",
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
            if action == "create":
                payload = dict(data or {})
                payload["workspace_id"] = workspace_id
                resp = requests.post(
                    f"{base_url}/{path}",
                    headers=headers,
                    json=payload,
                    timeout=10,
                )
                resp.raise_for_status()
                result_data = resp.json()
                new_id = result_data.get("id", "")
                return ToolResult(
                    success=True,
                    output=f"{entity} record created: {new_id}",
                    metadata={
                        "entity": entity,
                        "action": "create",
                        "record_id": new_id,
                    },
                )

            elif action == "update":
                resp = requests.put(
                    f"{base_url}/{path}/{record_id}",
                    headers=headers,
                    json=data or {},
                    timeout=10,
                )
                resp.raise_for_status()
                return ToolResult(
                    success=True,
                    output=f"{entity} record updated: {record_id}",
                    metadata={
                        "entity": entity,
                        "action": "update",
                        "record_id": record_id,
                    },
                )

            elif action == "delete":
                resp = requests.delete(
                    f"{base_url}/{path}/{record_id}",
                    headers=headers,
                    timeout=10,
                )
                resp.raise_for_status()
                return ToolResult(
                    success=True,
                    output=f"{entity} record deleted: {record_id}",
                    metadata={
                        "entity": entity,
                        "action": "delete",
                        "record_id": record_id,
                    },
                )

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            body = ""
            try:
                body = e.response.text[:500] if e.response is not None else ""
            except Exception:
                pass
            return ToolResult(
                success=False,
                output="",
                error=f"HTTP {status}: {body}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"CRM write failed: {str(e)}",
            )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_record(entity: str, data: dict) -> str:
    """Format a single CRM record for the LLM."""
    import json
    return json.dumps(data, indent=2, default=str)


def _format_list(entity: str, data) -> str:
    """Format a list of CRM records for the LLM."""
    import json

    if isinstance(data, list):
        count = len(data)
        # For short lists, return full JSON; for longer ones, summarize
        if count <= 10:
            return json.dumps(data, indent=2, default=str)
        else:
            # Show first 10 and indicate there are more
            truncated = data[:10]
            text = json.dumps(truncated, indent=2, default=str)
            return f"{text}\n\n... and {count - 10} more records (total: {count})"
    else:
        # Dict response (e.g. pipeline view, paginated response)
        return json.dumps(data, indent=2, default=str)

"""
EMAIL_TOOLS
===========

Email tool for the Agentic Loop Framework.

``email_send``
    Compose and send an email (or save as draft) via loopColony's email API.
    Optionally renders from an email template.  Handles the multi-step
    workflow (resolve account → render template → create draft → send) in
    a single tool call.

    The agent provides its own credentials (base_url, auth_token) which it
    reads from its memory at runtime.

    Requires the ``requests`` library (already installed).

How It Works
------------
1. **Resolve mail account** — If ``mail_account_id`` is not provided, the
   tool fetches ``GET /mail-accounts?workspace_id=...`` and picks the one
   marked ``is_default=true`` (or the first available).
2. **Render template** (optional) — If ``template_id`` is given, the tool
   calls ``POST /email-templates/{id}/render`` with ``template_context``
   and uses the rendered subject/body instead of the raw parameters.
3. **Build recipients** — Constructs a recipients array from ``to`` (type
   ``"to"``) and ``cc`` (type ``"cc"``).
4. **Create draft** — ``POST /emails`` with subject, body_text, body_html,
   recipients, and any CRM link fields (contact_id, deal_id, company_id,
   ticket_id).
5. **Send** — Unless ``draft_only=True``, calls ``POST /emails/{id}/send``
   to deliver the email.

Parameters
----------
base_url : str
    loopColony API base URL (from agent's memory/loopcolony.json).
auth_token : str
    Agent's bearer token for loopColony.
workspace_id : str
    Workspace scope.
to : list[str]
    Recipient email addresses.
subject : str
    Email subject line.
body : str, optional
    Email body text.  Not required if ``template_id`` is provided.
cc : list[str], optional
    CC email addresses.
mail_account_id : str, optional
    Sender account ID.  Uses workspace default if omitted.
contact_id : str, optional
    Link email to a CRM contact.
deal_id : str, optional
    Link email to a CRM deal.
company_id : str, optional
    Link email to a CRM company.
ticket_id : str, optional
    Link email to a CRM ticket.
template_id : str, optional
    Render email from this template instead of using raw body.
template_context : dict, optional
    Merge fields for template rendering (e.g. ``{"first_name": "Alice"}``).
draft_only : bool, optional
    If True, save as draft without sending.  Default False.

Returns
-------
ToolResult
    On success: ``output`` = "Email sent to alice@acme.com: \"Subject\""
    ``metadata`` = ``{"message_id": ..., "thread_id": ..., "status": "sent"|"draft"}``

Error Handling
--------------
- Missing mail accounts → clear error "No mail accounts configured"
- HTTP errors → status code + response body snippet
- Connection errors → exception message

Usage Examples
--------------
**Simple send**::

    email_send(to=["alice@acme.com"], subject="Quick follow-up",
               body="Hi Alice, great chatting today...")

**Template-based send with CRM link**::

    email_send(to=["bob@corp.com"], subject="Welcome!",
               template_id="tmpl_onboarding", template_context={"name": "Bob"},
               contact_id="contact_bob123")

**Save as draft**::

    email_send(to=["team@acme.com"], subject="Q1 Report",
               body="Attached is the quarterly report...", draft_only=True)

**With CC and deal link**::

    email_send(to=["alice@acme.com"], cc=["manager@acme.com"],
               subject="Proposal", body="Please find attached...",
               deal_id="deal_456")
"""

from typing import List, Optional

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class EmailSendTool(BaseTool):
    """Compose and send an email via loopColony's email system."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="email_send",
            description=(
                "Compose and send an email through loopColony's email system. "
                "Can also save as draft without sending. Supports templates, "
                "CC recipients, and linking to CRM contacts/deals/companies/tickets. "
                "Handles mail account resolution, template rendering, and sending "
                "in one step."
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
                    name="to",
                    type="array",
                    description="Recipient email addresses",
                ),
                ToolParameter(
                    name="subject",
                    type="string",
                    description="Email subject line",
                ),
                ToolParameter(
                    name="body",
                    type="string",
                    description="Email body text (not required if template_id provided)",
                    required=False,
                ),
                ToolParameter(
                    name="cc",
                    type="array",
                    description="CC email addresses",
                    required=False,
                ),
                ToolParameter(
                    name="mail_account_id",
                    type="string",
                    description="Sender account ID (uses workspace default if omitted)",
                    required=False,
                ),
                ToolParameter(
                    name="contact_id",
                    type="string",
                    description="Link email to a CRM contact",
                    required=False,
                ),
                ToolParameter(
                    name="deal_id",
                    type="string",
                    description="Link email to a CRM deal",
                    required=False,
                ),
                ToolParameter(
                    name="company_id",
                    type="string",
                    description="Link email to a CRM company",
                    required=False,
                ),
                ToolParameter(
                    name="ticket_id",
                    type="string",
                    description="Link email to a CRM ticket",
                    required=False,
                ),
                ToolParameter(
                    name="template_id",
                    type="string",
                    description="Render email from a template instead of raw body",
                    required=False,
                ),
                ToolParameter(
                    name="template_context",
                    type="object",
                    description="Merge fields for template rendering",
                    required=False,
                ),
                ToolParameter(
                    name="draft_only",
                    type="boolean",
                    description="Save as draft without sending (default: false)",
                    required=False,
                ),
            ],
        )

    def execute(
        self,
        base_url: str = "",
        auth_token: str = "",
        workspace_id: str = "",
        to: List[str] = None,
        subject: str = "",
        body: Optional[str] = None,
        cc: Optional[List[str]] = None,
        mail_account_id: Optional[str] = None,
        contact_id: Optional[str] = None,
        deal_id: Optional[str] = None,
        company_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
        template_id: Optional[str] = None,
        template_context: Optional[dict] = None,
        draft_only: bool = False,
        **kwargs,
    ) -> ToolResult:
        # Apply stored credentials (overrides LLM-provided values)
        _creds = self._apply_credentials(locals())
        base_url = _creds.get("base_url", base_url)
        auth_token = _creds.get("auth_token", auth_token)
        workspace_id = _creds.get("workspace_id", workspace_id)
        mail_account_id = _creds.get("mail_account_id", mail_account_id) or mail_account_id

        to = to or []

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
            # Step 1: Resolve mail account if not provided
            if not mail_account_id:
                resp = requests.get(
                    f"{base_url}/mail-accounts",
                    params={"workspace_id": workspace_id},
                    headers=headers,
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                # Handle nested response: {"mail_accounts": [...], "total": N}
                accounts = data.get("mail_accounts", data) if isinstance(data, dict) else data
                if not accounts or not isinstance(accounts, list):
                    return ToolResult(
                        success=False,
                        output="",
                        error="No mail accounts configured in this workspace",
                    )
                # Pick default or first available
                mail_account_id = next(
                    (a["id"] for a in accounts if a.get("is_default")),
                    accounts[0]["id"],
                )

            # Step 2: Render template if provided
            email_subject = subject
            email_body = body or ""
            if template_id:
                resp = requests.post(
                    f"{base_url}/email-templates/{template_id}/render",
                    headers=headers,
                    json={"context": template_context or {}},
                    timeout=10,
                )
                resp.raise_for_status()
                rendered = resp.json()
                email_subject = rendered.get("subject", email_subject)
                email_body = rendered.get("body", email_body)

            # Step 3: Build recipients
            recipients = [{"type": "to", "email": addr} for addr in to]
            if cc:
                recipients += [{"type": "cc", "email": addr} for addr in cc]

            # Step 4: Create draft
            payload = {
                "workspace_id": workspace_id,
                "mail_account_id": mail_account_id,
                "subject": email_subject,
                "body_text": email_body,
                "body_html": email_body,
                "recipients": recipients,
            }
            if contact_id:
                payload["contact_id"] = contact_id
            if deal_id:
                payload["deal_id"] = deal_id
            if company_id:
                payload["company_id"] = company_id
            if ticket_id:
                payload["ticket_id"] = ticket_id

            resp = requests.post(
                f"{base_url}/emails",
                headers=headers,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            email_data = resp.json()
            message_id = email_data.get("id", "")

            # Step 5: Send unless draft_only
            if not draft_only:
                resp = requests.post(
                    f"{base_url}/emails/{message_id}/send",
                    headers=headers,
                    timeout=15,
                )
                resp.raise_for_status()
                status = "sent"
            else:
                status = "draft"

            to_str = ", ".join(to)
            return ToolResult(
                success=True,
                output=f"Email {status} to {to_str}: \"{email_subject}\"",
                metadata={
                    "message_id": message_id,
                    "thread_id": email_data.get("thread_id"),
                    "status": status,
                },
            )

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            body_text = ""
            try:
                body_text = e.response.text[:500] if e.response is not None else ""
            except Exception:
                pass
            return ToolResult(
                success=False,
                output="",
                error=f"HTTP {status}: {body_text}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Email send failed: {str(e)}",
            )

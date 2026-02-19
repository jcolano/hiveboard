"""
Tool system for the Agentic Loop Framework.

Tools are code-based capabilities that the LLM can invoke during agentic execution.
Each tool defines a JSON schema (for the LLM to know how to call it) and an
execute method (for runtime).

Available Tools (27 total)
--------------------------
**File I/O** (file_tools.py):
  - ``file_read``  — Read files from allowed directories (sandboxed per-agent)
  - ``file_write`` — Write files to allowed directories

**HTTP & Web** (http_tools.py):
  - ``http_request``  — Make HTTP requests (GET, POST, PUT, PATCH, DELETE)
  - ``webpage_fetch``  — Fetch web pages and extract text/markdown/html

**Task Management** (task_tools.py) — requires scheduler:
  - ``schedule_create``  — Create scheduled tasks (interval, cron, once, event_only)
  - ``schedule_list``    — List agent's own tasks
  - ``schedule_get``     — Get task details
  - ``schedule_update``  — Update task config or instructions
  - ``schedule_delete``  — Delete a task
  - ``schedule_trigger`` — Manually trigger a task immediately
  - ``schedule_run_list``    — Get run history for a task

**State Persistence** (state_tools.py):
  - ``schedule_state_set`` — Persist state between task runs (state.json)
  - ``schedule_state_get``  — Read persisted task state

**Feed / Agent-to-Human Communication** (feed_tools.py):
  - ``feed_post`` — Post messages to the operator feed (visible in admin UI)

**Search** (search_tools.py):
  - ``web_search`` — Search the web via DuckDuckGo (general + news)

**Spreadsheets** (spreadsheet_tools.py):
  - ``csv_export``         — Export tabular data to CSV
  - ``excel_workbook_create`` — Create formatted Excel (.xlsx) workbooks

**Notifications** (notification_tools.py):
  - ``send_dm_notification`` — Send a DM to a loopColony team member

**Image Generation** (image_tools.py):
  - ``image_generate`` — Generate images via Google Gemini (flash/pro)

**Document Extraction** (extract_tools.py):
  - ``document_extract`` — Extract structured data from text via few-shot LLM

**Email** (email_tools.py):
  - ``email_send`` — Compose and send email (or save as draft) via loopColony

**CRM Ticketing** (ticket_crm_tools.py):
  - ``support_ticket_create`` — Create a support ticket in loopColony's CRM
  - ``support_ticket_update`` — Update a ticket (comment, resolve, close, assign, etc.)

**CRM Data** (crm_tools.py):
  - ``crm_search`` — List, search, or get a single record from any CRM entity
  - ``crm_write``  — Create, update, or delete a record on any CRM entity

**Issue Reporting** (issue_tools.py):
  - ``report_issue`` — Report a problem the agent cannot resolve (visible in admin)

Tool Enablement
---------------
All tools are registered unconditionally for every agent. Tools are sandboxed
per-agent to their own directories.

Skills vs Tools
---------------
- **Skills** are markdown behavior definitions (what to do).
- **Tools** are Python code capabilities (how to do it).
Skills can declare ``requires.tools`` in skill.json, but this is purely
declarative — tools must be independently enabled in the agent's config.
"""

from .base import (
    BaseTool,
    ToolParameter,
    ToolDefinition,
    ToolResult,
    ToolRegistry,
    get_tool_registry,
)

from .task_tools import (
    TaskCreateTool,
    TaskListTool,
    TaskGetTool,
    TaskUpdateTool,
    TaskDeleteTool,
    TaskTriggerTool,
    TaskRunsTool,
)

from .state_tools import (
    SaveTaskStateTool,
    GetTaskStateTool,
)

from .feed_tools import (
    FeedPostTool,
    get_feed_messages,
    mark_message_read,
    mark_all_read,
    delete_message,
    get_unread_count,
)

from .search_tools import WebSearchTool
from .spreadsheet_tools import CsvExportTool, SpreadsheetCreateTool
from .notification_tools import SendNotificationTool
from .image_tools import ImageGenerateTool
from .extract_tools import DocumentExtractTool
from .email_tools import EmailSendTool
from .ticket_crm_tools import TicketCreateCrmTool, TicketUpdateCrmTool
from .crm_tools import CrmSearchTool, CrmWriteTool
from .issue_tools import IssueReportTool

__all__ = [
    # Base classes
    "BaseTool",
    "ToolParameter",
    "ToolDefinition",
    "ToolResult",
    "ToolRegistry",
    "get_tool_registry",
    # Task tools
    "TaskCreateTool",
    "TaskListTool",
    "TaskGetTool",
    "TaskUpdateTool",
    "TaskDeleteTool",
    "TaskTriggerTool",
    "TaskRunsTool",
    # State tools
    "SaveTaskStateTool",
    "GetTaskStateTool",
    # Feed tools
    "FeedPostTool",
    "get_feed_messages",
    "mark_message_read",
    "mark_all_read",
    "delete_message",
    "get_unread_count",
    # Search tools
    "WebSearchTool",
    # Spreadsheet tools
    "CsvExportTool",
    "SpreadsheetCreateTool",
    # Notification tools
    "SendNotificationTool",
    # Image tools
    "ImageGenerateTool",
    # Extract tools
    "DocumentExtractTool",
    # Email tools
    "EmailSendTool",
    # CRM Ticket tools
    "TicketCreateCrmTool",
    "TicketUpdateCrmTool",
    # CRM Data tools
    "CrmSearchTool",
    "CrmWriteTool",
    # Issue reporting tools
    "IssueReportTool",
]

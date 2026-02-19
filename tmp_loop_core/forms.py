"""
FORMS
=====

Form schema generation, HTML rendering, and response validation.

This is the single source of truth for form creation across loopCore.
Any feature that needs forms -- human delegation, surveys, landing pages,
skill template configuration, etc. -- imports from here.

Three ways to request a form (unstructured -> structured):

    Level 1 -- Natural language description (LLM-powered):
        schema = generate_form_from_description("Build a contact form with ...")

    Level 2 -- List of question strings (LLM-powered):
        schema = generate_form_from_questions(["What is your name?", ...])

    Level 3/4 -- Structured field definitions (pure Python):
        schema = generate_form_schema(fields_def=[{...}, ...], title="...")

All three return the same FormSchema dict. The HTML renderer and response
validator work with any schema regardless of how it was created.

Public API:
    generate_form_from_description(description, ...) -> dict   [LLM]
    generate_form_from_questions(questions, ...) -> dict        [LLM]
    generate_form_schema(fields_def, ...) -> dict               [pure]
    validate_form_response(schema, raw_response) -> FormResponse
    render_form_html(form_schema, ...) -> str
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Valid field types -- used by validation and LLM prompt
FIELD_TYPES = ("text", "number", "date", "email", "url", "textarea", "select", "checkbox")


# ---------------------------------------------------------------------------
# FormResponse -- returned by validate_form_response
# ---------------------------------------------------------------------------

@dataclass
class FormResponse:
    """Result of validating a raw form submission against a schema."""
    valid: bool
    data: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Level 3/4 -- Structured field definitions (pure Python)
# ---------------------------------------------------------------------------

def generate_form_schema(
    fields_def: Optional[list] = None,
    title: str = "Form",
    description: str = "",
    notes_field: bool = True,
) -> dict:
    """Build a form schema from structured field definitions.

    This is the pure-Python path -- no LLM call. Use this when the caller
    already knows exactly what fields the form should have.

    Args:
        fields_def: List of field dicts. Each may contain:
            - field/name (str): field key used in the submission payload
            - type (str): one of FIELD_TYPES
            - description (str): human-readable label
            - required (bool): whether the field must be filled
            - options (list[str]): choices for select fields
            If None or empty, a single free-text "result" textarea is used.
        title: Form title.
        description: Longer explanation shown below the title.
        notes_field: Include an optional "Additional Notes" textarea.

    Returns:
        Form schema dict: {title, description, fields, notes_field}
    """
    fields = _normalize_fields(fields_def) if fields_def else [_default_field()]
    if not fields:
        fields = [_default_field()]

    return {
        "title": title[:100],
        "description": description,
        "fields": fields,
        "notes_field": notes_field,
    }


# ---------------------------------------------------------------------------
# Level 1 -- Natural language description (LLM-powered)
# ---------------------------------------------------------------------------

def generate_form_from_description(
    description: str,
    llm_client=None,
    notes_field: bool = True,
) -> dict:
    """Generate a form schema from a natural language description.

    Uses the LLM to interpret the description and produce appropriate
    form fields with types, labels, and options.

    Args:
        description: Free-text description of the desired form.
            Examples:
            - "Build a contact form with name, email, and message"
            - "Create a customer satisfaction survey with rating 1-5"
            - "I need a form to collect shipping addresses"
        llm_client: LLM client instance. If None, creates a default one.
        notes_field: Include an optional "Additional Notes" textarea.

    Returns:
        Form schema dict: {title, description, fields, notes_field}
    """
    if not description or not description.strip():
        return generate_form_schema(title="Form", description="", notes_field=notes_field)

    client = llm_client or _get_llm_client()
    if not client:
        logger.warning("No LLM client available, falling back to default form")
        return generate_form_schema(title="Form", description=description, notes_field=notes_field)

    prompt = f"""You are a form designer. Given the description below, generate a JSON form schema.

Description: {description}

Return a JSON object with exactly these keys:
- "title": short form title (max 100 chars)
- "description": brief description of the form's purpose (1-2 sentences)
- "fields": array of field objects, each with:
    - "name": field key in snake_case (e.g. "full_name", "email_address")
    - "label": human-readable label (e.g. "Full Name", "Email Address")
    - "type": one of: text, number, date, email, url, textarea, select, checkbox
    - "required": true or false
    - "options": array of strings ONLY for select type, null for all other types

Rules:
- Use the most specific type: email for emails, url for URLs, number for quantities, textarea for long text, select when there are fixed choices
- Mark fields required when they are clearly essential
- Order fields logically (identification first, then details, then optional)
- Keep field names short and descriptive in snake_case

Return ONLY the JSON object. No markdown, no explanation."""

    try:
        result = client.complete_json(
            prompt=prompt,
            system="You are a precise form designer. Return only valid JSON.",
            caller="forms.generate_from_description",
            max_tokens=2048,
        )
        if result and isinstance(result, dict):
            return _schema_from_llm_result(result, notes_field)
    except Exception as e:
        logger.warning("LLM form generation failed: %s", e)

    # Fallback
    return generate_form_schema(title="Form", description=description, notes_field=notes_field)


# ---------------------------------------------------------------------------
# Level 2 -- List of question strings (LLM-powered)
# ---------------------------------------------------------------------------

def generate_form_from_questions(
    questions: list,
    title: str = "",
    description: str = "",
    llm_client=None,
    notes_field: bool = True,
) -> dict:
    """Generate a form schema from a list of question strings.

    Uses the LLM to infer the best field type for each question.

    Args:
        questions: List of question strings.
            Examples:
            - ["What is your name?", "Rate your experience 1-5", "Comments?"]
            - ["Company name", "Number of employees", "Industry"]
        title: Form title. If empty, LLM generates one.
        description: Form description. If empty, LLM generates one.
        llm_client: LLM client instance. If None, creates a default one.
        notes_field: Include an optional "Additional Notes" textarea.

    Returns:
        Form schema dict: {title, description, fields, notes_field}
    """
    if not questions:
        return generate_form_schema(title=title or "Form", description=description, notes_field=notes_field)

    # Filter to strings only
    questions = [q for q in questions if isinstance(q, str) and q.strip()]
    if not questions:
        return generate_form_schema(title=title or "Form", description=description, notes_field=notes_field)

    client = llm_client or _get_llm_client()
    if not client:
        logger.warning("No LLM client available, falling back to text fields")
        fields = [{"name": f"q{i+1}", "label": q, "type": "text", "required": True, "options": None}
                  for i, q in enumerate(questions)]
        return {
            "title": (title or "Form")[:100],
            "description": description,
            "fields": fields,
            "notes_field": notes_field,
        }

    questions_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))

    title_instruction = ""
    if not title:
        title_instruction = '- "title": a short title for this form (max 100 chars)\n'
    if not description:
        title_instruction += '- "description": brief description of the form\'s purpose (1-2 sentences)\n'

    prompt = f"""You are a form designer. Convert these questions into a JSON form schema.

Questions:
{questions_text}

Return a JSON object with:
{title_instruction}- "fields": array with one field per question, each with:
    - "name": field key in snake_case
    - "label": the question text, cleaned up as a label
    - "type": one of: text, number, date, email, url, textarea, select, checkbox
    - "required": true or false (true if the question seems essential)
    - "options": array of strings ONLY for select type, null for all other types

Rules:
- Infer the best type from each question:
  - "Rate 1-5" or "How satisfied" -> select with numbered options
  - "email" or "e-mail" -> email type
  - "how many" or "quantity" or "count" -> number type
  - "describe" or "comments" or "feedback" or "tell us" -> textarea
  - "agree" or "consent" or "opt-in" -> checkbox
  - "when" or "date" -> date type
  - "website" or "URL" or "link" -> url type
- Default to text if uncertain

Return ONLY the JSON object. No markdown, no explanation."""

    try:
        result = client.complete_json(
            prompt=prompt,
            system="You are a precise form designer. Return only valid JSON.",
            caller="forms.generate_from_questions",
            max_tokens=2048,
        )
        if result and isinstance(result, dict):
            # Inject caller-provided title/description if they specified them
            if title:
                result["title"] = title
            if description:
                result["description"] = description
            return _schema_from_llm_result(result, notes_field)
    except Exception as e:
        logger.warning("LLM form generation from questions failed: %s", e)

    # Fallback: all text fields
    fields = [{"name": f"q{i+1}", "label": q, "type": "text", "required": True, "options": None}
              for i, q in enumerate(questions)]
    return {
        "title": (title or "Form")[:100],
        "description": description,
        "fields": fields,
        "notes_field": notes_field,
    }


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------

def validate_form_response(schema: dict, raw_response: dict) -> FormResponse:
    """Validate and coerce a raw form submission against a schema.

    Args:
        schema: Form schema dict (from any generate_* function).
        raw_response: Raw JSON dict from the form submission.
            All values typically arrive as strings from HTML forms.

    Returns:
        FormResponse with:
            - valid: True if all required fields present and types coerce
            - data: Dict with coerced values (str->int for number, etc.)
            - errors: List of human-readable error messages
    """
    fields = schema.get("fields", [])
    errors = []
    data = {}

    for f in fields:
        name = f.get("name", "")
        ftype = f.get("type", "text")
        required = f.get("required", False)
        label = f.get("label", name)
        raw_value = raw_response.get(name)

        # Check required
        if required and (raw_value is None or str(raw_value).strip() == ""):
            errors.append(f"'{label}' is required")
            continue

        # Skip if not provided and not required
        if raw_value is None or str(raw_value).strip() == "":
            continue

        # Coerce by type
        coerced, err = _coerce_value(raw_value, ftype, label, f.get("options"))
        if err:
            errors.append(err)
        else:
            data[name] = coerced

    # Pass through _notes if present
    notes = raw_response.get("_notes")
    if notes is not None:
        data["_notes"] = str(notes)

    # Pass through any extra fields not in schema (flexibility for callers)
    schema_names = {f.get("name") for f in fields}
    schema_names.add("_notes")
    for key, value in raw_response.items():
        if key not in schema_names and key not in data:
            data[key] = value

    return FormResponse(
        valid=len(errors) == 0,
        data=data,
        errors=errors,
    )


def _coerce_value(raw_value, ftype: str, label: str, options=None):
    """Coerce a raw string value to the expected type.

    Returns (coerced_value, error_string_or_None).
    """
    val = str(raw_value).strip()

    if ftype == "number":
        try:
            # Try int first, then float
            if "." in val:
                return float(val), None
            return int(val), None
        except (ValueError, TypeError):
            return None, f"'{label}' must be a number"

    elif ftype == "checkbox":
        return val.lower() in ("true", "1", "yes", "on"), None

    elif ftype == "select":
        if options and val not in [str(o) for o in options]:
            return None, f"'{label}' must be one of: {', '.join(str(o) for o in options)}"
        return val, None

    elif ftype == "date":
        # Basic format check (YYYY-MM-DD)
        if len(val) >= 8 and val[4:5] == "-":
            return val, None
        return val, None  # Accept as-is, don't block

    elif ftype == "email":
        if "@" not in val:
            return None, f"'{label}' must be a valid email address"
        return val, None

    elif ftype == "url":
        if not val.startswith(("http://", "https://", "/")):
            return None, f"'{label}' must be a valid URL"
        return val, None

    else:
        # text, textarea, and anything else: pass through as string
        return val, None


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def render_form_html(
    form_schema: dict,
    form_id: str = "",
    submit_url: str = "",
    success_title: str = "Response Submitted",
    success_message: str = "Your response has been recorded.",
    footer_text: str = "",
) -> str:
    """Render a self-contained HTML form page.

    Args:
        form_schema: Schema dict from any generate_* function.
        form_id: Identifier shown in footer (e.g. delegation ID, survey ID).
        submit_url: URL the form POSTs to. If empty, uses form_schema["submit_url"].
        success_title: Heading shown after successful submission.
        success_message: Body text shown after successful submission.
        footer_text: Optional footer line. If empty and form_id is set,
                     shows "Form ID: {form_id}".

    Returns:
        Complete HTML page as a string (inline CSS/JS, no external deps).
    """
    title = _escape_html(form_schema.get("title", "Form"))
    description = _escape_html(form_schema.get("description", ""))
    fields = form_schema.get("fields", [])
    notes_field = form_schema.get("notes_field", True)

    if not submit_url:
        submit_url = form_schema.get("submit_url", "")

    fields_html = []
    for f in fields:
        fields_html.append(_render_field(f))

    if notes_field:
        fields_html.append(
            '<div class="field">'
            '<label for="_notes">Additional Notes</label>'
            '<textarea id="_notes" name="_notes" rows="3" '
            'placeholder="Any extra context or comments..."></textarea>'
            '</div>'
        )

    fields_block = "\n      ".join(fields_html)

    if not footer_text and form_id:
        footer_text = f"Form ID: {form_id}"
    footer_html = f'<p class="meta">{_escape_html(footer_text)}</p>' if footer_text else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           background: #f5f5f5; color: #333; padding: 20px; }}
    .container {{ max-width: 640px; margin: 0 auto; background: #fff;
                  border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                  padding: 32px; }}
    h1 {{ font-size: 1.4em; margin-bottom: 8px; color: #1a1a1a; }}
    .desc {{ color: #666; margin-bottom: 24px; line-height: 1.5;
             white-space: pre-wrap; }}
    .field {{ margin-bottom: 16px; }}
    label {{ display: block; font-weight: 600; margin-bottom: 4px; font-size: 0.95em; }}
    input[type="text"], input[type="number"], input[type="date"],
    input[type="email"], input[type="url"], textarea, select {{
      width: 100%; padding: 10px 12px; border: 1px solid #ddd;
      border-radius: 6px; font-size: 0.95em; font-family: inherit;
    }}
    textarea {{ resize: vertical; }}
    .checkbox label {{ display: inline; font-weight: normal; }}
    .btn {{ display: inline-block; background: #2563eb; color: #fff; border: none;
            padding: 12px 32px; border-radius: 6px; font-size: 1em; cursor: pointer;
            margin-top: 16px; }}
    .btn:hover {{ background: #1d4ed8; }}
    .btn:disabled {{ background: #93c5fd; cursor: not-allowed; }}
    .success {{ text-align: center; padding: 40px 20px; }}
    .success h2 {{ color: #16a34a; margin-bottom: 8px; }}
    .error {{ color: #dc2626; margin-top: 8px; font-size: 0.9em; }}
    .meta {{ color: #999; font-size: 0.85em; margin-top: 24px; border-top: 1px solid #eee;
             padding-top: 12px; }}
  </style>
</head>
<body>
  <div class="container" id="form-container">
    <h1>{title}</h1>
    <p class="desc">{description}</p>
    <form id="main-form">
      {fields_block}
      <button type="submit" class="btn" id="submit-btn">Submit Response</button>
      <div class="error" id="error-msg" style="display:none"></div>
    </form>
    {footer_html}
  </div>
  <div class="container success" id="success-container" style="display:none">
    <h2>{_escape_html(success_title)}</h2>
    <p>{_escape_html(success_message)}</p>
  </div>
  <script>
    document.getElementById('main-form').addEventListener('submit', async function(e) {{
      e.preventDefault();
      var btn = document.getElementById('submit-btn');
      var errEl = document.getElementById('error-msg');
      btn.disabled = true;
      btn.textContent = 'Submitting...';
      errEl.style.display = 'none';
      var fd = new FormData(this);
      var data = {{}};
      fd.forEach(function(v, k) {{ data[k] = v; }});
      try {{
        var resp = await fetch('{submit_url}', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify(data),
        }});
        if (resp.ok) {{
          document.getElementById('form-container').style.display = 'none';
          document.getElementById('success-container').style.display = 'block';
        }} else {{
          var body = await resp.text();
          throw new Error(body || 'Submission failed');
        }}
      }} catch(err) {{
        errEl.textContent = 'Error: ' + err.message;
        errEl.style.display = 'block';
        btn.disabled = false;
        btn.textContent = 'Submit Response';
      }}
    }});
  </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_llm_client():
    """Get or create a default LLM client for form generation."""
    try:
        from llm_client import get_default_client
        return get_default_client()
    except Exception as e:
        logger.warning("Could not create LLM client for forms: %s", e)
        return None


def _normalize_fields(fields_def: list) -> list:
    """Normalize a list of field dicts into the canonical schema format."""
    fields = []
    for item in fields_def:
        if not isinstance(item, dict):
            continue
        ftype = item.get("type", "text")
        if ftype not in FIELD_TYPES:
            ftype = "text"
        fields.append({
            "name": item.get("field", item.get("name", f"field_{len(fields)}")),
            "label": item.get("description", item.get("field", f"Field {len(fields) + 1}")),
            "type": ftype,
            "required": item.get("required", False),
            "options": item.get("options"),
        })
    return fields


def _schema_from_llm_result(result: dict, notes_field: bool) -> dict:
    """Convert an LLM-generated JSON result into a validated form schema."""
    title = str(result.get("title", "Form"))[:100]
    description = str(result.get("description", ""))

    raw_fields = result.get("fields", [])
    if not isinstance(raw_fields, list):
        raw_fields = []

    fields = []
    for f in raw_fields:
        if not isinstance(f, dict):
            continue
        ftype = f.get("type", "text")
        if ftype not in FIELD_TYPES:
            ftype = "text"

        name = f.get("name", f"field_{len(fields)}")
        # Sanitize name to valid identifier
        name = "".join(c if c.isalnum() or c == "_" else "_" for c in str(name))

        field_def = {
            "name": name,
            "label": str(f.get("label", name)),
            "type": ftype,
            "required": bool(f.get("required", False)),
            "options": None,
        }
        if ftype == "select":
            opts = f.get("options")
            if isinstance(opts, list):
                field_def["options"] = [str(o) for o in opts]
            else:
                # LLM said select but gave no options -- fall back to text
                field_def["type"] = "text"

        fields.append(field_def)

    if not fields:
        fields = [_default_field()]

    return {
        "title": title,
        "description": description,
        "fields": fields,
        "notes_field": notes_field,
    }


def _default_field() -> dict:
    return {"name": "result", "label": "Result", "type": "textarea", "required": True}


def _render_field(f: dict) -> str:
    """Render a single form field to HTML."""
    fname = _escape_html(f.get("name", ""))
    flabel = _escape_html(f.get("label", fname))
    ftype = f.get("type", "text")
    freq = f.get("required", False)
    req_attr = " required" if freq else ""
    req_star = " *" if freq else ""

    if ftype == "textarea":
        return (
            f'<div class="field">'
            f'<label for="{fname}">{flabel}{req_star}</label>'
            f'<textarea id="{fname}" name="{fname}" rows="4"{req_attr}></textarea>'
            f'</div>'
        )
    elif ftype == "select":
        options = f.get("options", [])
        opts_html = '<option value="">-- Select --</option>'
        for opt in (options or []):
            opt_val = _escape_html(str(opt))
            opts_html += f'<option value="{opt_val}">{opt_val}</option>'
        return (
            f'<div class="field">'
            f'<label for="{fname}">{flabel}{req_star}</label>'
            f'<select id="{fname}" name="{fname}"{req_attr}>{opts_html}</select>'
            f'</div>'
        )
    elif ftype == "checkbox":
        return (
            f'<div class="field checkbox">'
            f'<label><input type="checkbox" id="{fname}" name="{fname}" value="true"> {flabel}</label>'
            f'</div>'
        )
    elif ftype in ("number", "date", "email", "url"):
        return (
            f'<div class="field">'
            f'<label for="{fname}">{flabel}{req_star}</label>'
            f'<input type="{ftype}" id="{fname}" name="{fname}"{req_attr}>'
            f'</div>'
        )
    else:
        return (
            f'<div class="field">'
            f'<label for="{fname}">{flabel}{req_star}</label>'
            f'<input type="text" id="{fname}" name="{fname}"{req_attr}>'
            f'</div>'
        )


def _escape_html(text: str) -> str:
    """Minimal HTML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )

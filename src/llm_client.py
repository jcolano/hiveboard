"""
LLM_CLIENT
==========

LLM integration for the Agentic Loop Framework.

Supports multiple providers:
- Anthropic Claude (default) - Primary provider
- OpenAI GPT - Alternative provider

Features:
- Tool calling support for agentic loops
- Usage tracking (tokens, costs)
- Graceful fallback between providers
- File-based API key loading

Usage:
    from llm_client import get_anthropic_client, get_openai_client

    # Simple completion
    client = get_anthropic_client()
    response = client.complete(
        prompt="Hello, world!",
        system="You are helpful.",
        caller="my_agent"
    )

    # Completion with tools
    response = client.complete_with_tools(
        messages=[{"role": "user", "content": "Read file.txt"}],
        tools=[tool_schema],
        caller="loop_core"
    )

================================================================================
IMPORTANT: MARKDOWN-IN-JSON TRUNCATION BUG
================================================================================

PROBLEM (Discovered Feb 2026):
When asking the LLM to generate markdown content INSIDE a JSON response, the
model frequently stops prematurely with stop_reason="end_turn" at only 15-20%
of the max_tokens limit. The model appears to interpret markdown section breaks
(like "## Section\\n\\n") as completion signals when nested inside JSON strings.

SYMPTOMS:
- JSON repair needed (truncated mid-string)
- stop_reason: "end_turn" (not "max_tokens")
- Token usage: ~15-20% of max_tokens
- Response ends mid-markdown section, often after "\\n\\n"

WRONG APPROACH (causes truncation):
    # DON'T DO THIS - markdown inside JSON causes early termination
    response = client.complete_json(
        prompt="Generate a JSON object with skill_md containing markdown...",
        ...
    )
    # Returns: {"skill_md": "# Title\\n\\n## Section\\n\\n"}  <- TRUNCATED!

CORRECT APPROACH (use raw text for markdown):
    # Step 1: Get structured metadata as JSON (small, no markdown)
    metadata = client.complete_json(
        prompt="Generate skill metadata as JSON (no markdown content)...",
        max_tokens=2048
    )

    # Step 2: Get markdown content as raw text (NOT JSON-wrapped)
    markdown = client.complete(
        prompt="Generate the markdown content. Output raw markdown only...",
        max_tokens=8192
    )

RULE: Never ask for markdown/long-form content inside JSON responses.
      Always generate markdown as raw text using complete(), then combine
      programmatically with structured data from complete_json().

See: skills/editor.py for working implementation of this pattern.
================================================================================
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any
from enum import Enum

# Configure module logger for LLM debug logging
_llm_debug_logger: Optional[logging.Logger] = None
_llm_usage_logger: Optional[logging.Logger] = None
_truncation_log: List[Dict[str, Any]] = []  # In-memory log of truncations


# ============================================================================
# API KEY LOADING (from files, not environment variables)
# ============================================================================

def _find_project_root() -> Path:
    """
    Find the project root directory (loopCore, not loopCore/src).

    Looks for data/CONFIG/config.json as the definitive marker,
    since this only exists at the true project root.
    """
    current = Path(__file__).resolve().parent

    # Walk up looking for the config file (definitive marker)
    for _ in range(5):
        config_file = current / "data" / "CONFIG" / "config.json"
        if config_file.exists():
            return current
        current = current.parent

    # Fallback: if llm_client.py is in src/, return parent of src
    src_path = Path(__file__).resolve().parent
    if src_path.name == "src":
        return src_path.parent

    # Last resort fallback
    return Path(__file__).resolve().parent.parent


def _get_apikeys_dir() -> Optional[Path]:
    """
    Get the apikeys directory from config or default location.

    Expected structure:
        parent_folder/
            apikeys/
                api_anthropic.key
                api_openai.key
            project_root/
                data/
                    CONFIG/
                        config.json
                src/
                    llm_client.py
    """
    root = _find_project_root()
    
    print(f"[DEBUG] Project root found at: {root}")

    # Try to load from config
    config_file = root / "data" / "CONFIG" / "config.json"
    if config_file.exists():
        try:
            import json
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            apikeys_path = config.get("paths", {}).get("apikeys_dir", "../../apikeys")
            print(f"[DEBUG] Loading apikeys from config path: {apikeys_path}")
            resolved = (root / apikeys_path).resolve()
            if resolved.exists():
                return resolved
        except Exception:
            pass

    # Fallback to default location (one level above project root)
    default = root.parent / "apikeys"
    if default.exists():
        return default

    return None


def _load_api_key(filename: str) -> Optional[str]:
    """
    Load API key from file in apikeys folder, with env var fallback.

    Args:
        filename: Key file name (e.g., 'api_anthropic.key')

    Returns:
        API key string or None if not found

    Priority:
        1. File in apikeys folder (preferred)
        2. Environment variable as fallback
    """
    import os

    # Try file-based key first (preferred)
    apikeys_dir = _get_apikeys_dir()
    if apikeys_dir:
        key_file = apikeys_dir / filename
        if key_file.exists():
            try:
                with open(key_file, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except Exception as e:
                print(f"[WARN] Could not read {filename}: {e}")

    # Fallback to environment variable
    env_var_map = {
        'api_anthropic.key': 'ANTHROPIC_API_KEY',
        'api_openai.key': 'OPENAI_API_KEY',
    }
    env_var = env_var_map.get(filename)
    if env_var:
        env_value = os.environ.get(env_var)
        if env_value:
            return env_value.strip()

    return None


# ============================================================================
# PROVIDER AVAILABILITY
# ============================================================================

ANTHROPIC_AVAILABLE = False
OPENAI_AVAILABLE = False

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    pass

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    pass


# ============================================================================
# PROVIDERS & MODELS
# ============================================================================

class LLMProvider(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


# Default models
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_OPENAI_MODEL = "gpt-4o"


# ============================================================================
# PRICING (per 1M tokens)
# ============================================================================

PRICING = {
    # Anthropic Claude models
    "claude-sonnet-4-5-20250929": {"input": 3.0, "output": 15.0},
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0},
    "claude-3-5-sonnet-20241022": {"input": 3.0, "output": 15.0},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    # OpenAI GPT models
    "gpt-4o": {"input": 2.5, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0},
}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost for a request."""
    prices = PRICING.get(model, {"input": 5.0, "output": 20.0})
    input_cost = (input_tokens / 1_000_000) * prices["input"]
    output_cost = (output_tokens / 1_000_000) * prices["output"]
    return input_cost + output_cost


# ============================================================================
# LLM DEBUG LOGGING
# ============================================================================

def setup_llm_debug_logging(log_dir: Optional[Path] = None, enabled: bool = True) -> None:
    """
    Setup debug logging for LLM requests/responses.

    Logs are written to: {log_dir}/llm_debug_{date}.jsonl

    Args:
        log_dir: Directory for log files (default: data/LOGS)
        enabled: Whether to enable debug logging
    """
    global _llm_debug_logger

    if not enabled:
        _llm_debug_logger = None
        return

    if log_dir is None:
        log_dir = _find_project_root() / "data" / "LOGS"

    log_dir.mkdir(parents=True, exist_ok=True)

    # Create a dedicated logger for LLM debug
    _llm_debug_logger = logging.getLogger("llm_debug")
    _llm_debug_logger.setLevel(logging.DEBUG)
    _llm_debug_logger.handlers.clear()

    # File handler with date-based filename - use write mode to ensure fresh file
    log_file = log_dir / f"llm_debug_{datetime.now().strftime('%Y%m%d')}.jsonl"
    handler = logging.FileHandler(log_file, encoding='utf-8', mode='a')
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter('%(message)s'))
    _llm_debug_logger.addHandler(handler)

    # Disable propagation to avoid duplicate logs
    _llm_debug_logger.propagate = False

    print(f"[DEBUG] LLM debug logging enabled: {log_file}")


def log_llm_interaction(
    caller: str,
    prompt: str,
    system: str,
    response: str,
    input_tokens: int,
    output_tokens: int,
    max_tokens: int,
    model: str,
    truncation_repaired: bool = False,
    repair_details: Optional[Dict] = None
) -> None:
    """
    Log an LLM interaction for debugging.

    Args:
        caller: Who made the request
        prompt: The prompt sent
        system: System prompt
        response: Raw response received
        input_tokens: Tokens in prompt
        output_tokens: Tokens in response
        max_tokens: Max tokens requested
        model: Model used
        truncation_repaired: Whether JSON repair was needed
        repair_details: Details about the repair if any
    """
    if _llm_debug_logger is None:
        return

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "caller": caller,
        "model": model,
        "max_tokens": max_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "prompt_length": len(prompt),
        "response_length": len(response) if response else 0,
        "truncation_repaired": truncation_repaired,
        "repair_details": repair_details,
        # Store first/last of prompt and response for context
        "prompt_preview": prompt[:500] + "..." if len(prompt) > 500 else prompt,
        "response_preview": response[:500] + "..." if response and len(response) > 500 else response,
        "response_tail": response[-200:] if response and len(response) > 200 else response,
    }

    try:
        _llm_debug_logger.debug(json.dumps(entry))
        # Force flush to ensure data is written
        for handler in _llm_debug_logger.handlers:
            handler.flush()
    except Exception as e:
        print(f"[WARN] LLM logging failed: {e}")  # Don't silently fail


def log_truncation_event(
    caller: str,
    response_preview: str,
    repair_type: str,
    open_braces: int,
    open_brackets: int,
    in_string: bool,
    output_tokens: Optional[int] = None,
    max_tokens: Optional[int] = None,
    stop_reason: Optional[str] = None
) -> Dict[str, Any]:
    """
    Log a JSON truncation/repair event with detailed context.

    Returns the log entry for inspection.
    """
    import traceback

    # Get call stack for context (skip this function and _try_repair_json)
    stack = traceback.extract_stack()
    call_context = []
    for frame in stack[-6:-2]:  # Get a few relevant frames
        call_context.append(f"{frame.filename.split('/')[-1]}:{frame.lineno} in {frame.name}")

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "caller": caller,
        "repair_type": repair_type,
        "open_braces": open_braces,
        "open_brackets": open_brackets,
        "unclosed_string": in_string,
        "output_tokens": output_tokens,
        "max_tokens": max_tokens,
        "stop_reason": stop_reason,
        "response_length": len(response_preview) if response_preview else 0,
        "response_tail": response_preview[-300:] if response_preview else "",
        "call_stack": call_context,
    }

    _truncation_log.append(entry)

    # Print detailed debug info
    print(f"[DEBUG] JSON Truncation Repair:")
    print(f"  Caller: {caller}")
    print(f"  Repair: {repair_type}")
    print(f"  Missing: {open_braces} braces, {open_brackets} brackets, string_open={in_string}")
    if output_tokens and max_tokens:
        print(f"  Tokens: {output_tokens}/{max_tokens} ({100*output_tokens/max_tokens:.1f}%)")
    if stop_reason:
        print(f"  Stop reason: {stop_reason}")
    print(f"  Response tail: ...{response_preview[-100:] if response_preview else 'N/A'}")
    print(f"  Stack: {' -> '.join(call_context[-3:])}")

    # Also log to file if enabled
    if _llm_debug_logger:
        try:
            _llm_debug_logger.debug(json.dumps({"type": "truncation", **entry}))
            for handler in _llm_debug_logger.handlers:
                handler.flush()
        except Exception as e:
            print(f"[WARN] Truncation logging failed: {e}")

    return entry


def get_truncation_log() -> List[Dict[str, Any]]:
    """Get the in-memory truncation log for analysis."""
    return _truncation_log.copy()


def clear_truncation_log() -> None:
    """Clear the in-memory truncation log."""
    global _truncation_log
    _truncation_log = []


# ============================================================================
# LLM USAGE LOGGING (centralized JSONL)
# ============================================================================

def _get_usage_logger():
    """Get or create the usage JSONL logger."""
    global _llm_usage_logger
    if _llm_usage_logger is None:
        log_dir = _find_project_root() / "data" / "LOGS"
        log_dir.mkdir(parents=True, exist_ok=True)
        _llm_usage_logger = logging.getLogger("llm_usage")
        _llm_usage_logger.setLevel(logging.DEBUG)
        _llm_usage_logger.handlers.clear()
        log_file = log_dir / f"llm_usage_{datetime.now().strftime('%Y%m%d')}.jsonl"
        handler = logging.FileHandler(log_file, encoding='utf-8', mode='a')
        handler.setFormatter(logging.Formatter('%(message)s'))
        _llm_usage_logger.addHandler(handler)
        _llm_usage_logger.propagate = False
    return _llm_usage_logger


def _log_usage_entry(entry: Dict) -> None:
    """Append a usage entry to the daily JSONL file."""
    try:
        logger = _get_usage_logger()
        logger.debug(json.dumps(entry))
        for handler in logger.handlers:
            handler.flush()
    except Exception:
        pass  # Never fail the LLM call due to logging


# ============================================================================
# DATA STRUCTURES FOR TOOL CALLING
# ============================================================================

@dataclass
class ToolCall:
    """A tool call requested by the LLM."""
    id: str
    name: str
    parameters: Dict[str, Any]

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "parameters": self.parameters
        }


@dataclass
class TokenUsage:
    """Token usage for a request."""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> Dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total": self.total
        }


@dataclass
class LLMResponse:
    """Response from LLM, supporting both text and tool calls."""
    text: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)

    def to_dict(self) -> Dict:
        return {
            "text": self.text,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "usage": self.usage.to_dict()
        }


# ============================================================================
# BASE LLM CLIENT
# ============================================================================

@dataclass
class LLMCall:
    """Record of a single LLM call."""
    timestamp: str
    caller: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float


class BaseLLMClient:
    """Base class for LLM clients with common tracking functionality."""

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        self.call_history: List[Dict] = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.is_initialized = False
        self.agent_id: Optional[str] = None
        self.agent_name: Optional[str] = None
        self.system_source: str = "loopCore"
        self.skill_id: Optional[str] = None
        self.debug_prompts: bool = False
        self._prompt_counter: int = 0

    def set_context(self, agent_id: str = None, agent_name: str = None, system_source: str = None, skill_id: str = None, debug_prompts: bool = None):
        """Set agent context for usage attribution."""
        if agent_id is not None:
            self.agent_id = agent_id
        if agent_name is not None:
            self.agent_name = agent_name
        if system_source is not None:
            self.system_source = system_source
        if skill_id is not None:
            self.skill_id = skill_id
        if debug_prompts is not None:
            self.debug_prompts = debug_prompts

    def _dump_prompt(self, caller: str, request: Dict, response: Dict, input_tokens: int, output_tokens: int) -> None:
        """Save full request+response to a JSON file for debugging."""
        if not self.debug_prompts:
            return
        try:
            self._prompt_counter += 1
            log_dir = _find_project_root() / "data" / "LOGS" / "prompts"
            log_dir.mkdir(parents=True, exist_ok=True)
            cost = calculate_cost(self.model, input_tokens, output_tokens)
            dump = {
                "seq": self._prompt_counter,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent_id": self.agent_id,
                "agent_name": self.agent_name,
                "caller": caller,
                "model": self.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost, 6),
                "request": request,
                "response": response,
            }
            filename = f"{self._prompt_counter:04d}_{self.agent_id or 'unknown'}_{caller}.json"
            (log_dir / filename).write_text(json.dumps(dump, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass  # Never fail the LLM call due to logging

    def _track_usage(self, caller: str, input_tokens: int, output_tokens: int) -> float:
        """Track usage from a call."""
        cost = calculate_cost(self.model, input_tokens, output_tokens)
        prices = PRICING.get(self.model, {"input": 5.0, "output": 20.0})
        input_cost = (input_tokens / 1_000_000) * prices["input"]
        output_cost = (output_tokens / 1_000_000) * prices["output"]

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost += cost

        self.call_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "caller": caller,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
        })

        # Persist to centralized JSONL
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system": self.system_source,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "caller": caller,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_cost": round(input_cost, 6),
            "output_cost": round(output_cost, 6),
            "total_cost": round(cost, 6),
        }
        if self.skill_id:
            entry["skill_id"] = self.skill_id
        _log_usage_entry(entry)

        return cost

    def get_usage_summary(self) -> Dict:
        """Get usage summary."""
        return {
            "provider": self.provider,
            "model": self.model,
            "total_calls": len(self.call_history),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": self.total_cost,
        }

    def reset_tracking(self) -> None:
        """Reset usage tracking."""
        self.call_history = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0

    def complete(
        self,
        prompt: str,
        system: str = "",
        caller: str = "unknown",
        max_tokens: int = 4096,
        temperature: float = 0.0
    ) -> Optional[str]:
        """
        Send a simple completion request.

        Args:
            prompt: User prompt
            system: System prompt
            caller: Identifier for tracking
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature

        Returns:
            Response text or None on error
        """
        raise NotImplementedError

    def complete_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        caller: str = "unknown",
        max_tokens: int = 4096,
        temperature: float = 0.0
    ) -> Optional[LLMResponse]:
        """
        Send completion with tool definitions for agentic loops.

        Args:
            messages: Conversation messages
            tools: Tool schemas (JSON Schema format)
            caller: Identifier for tracking
            max_tokens: Maximum tokens
            temperature: Sampling temperature

        Returns:
            LLMResponse with text and optional tool_calls
        """
        raise NotImplementedError

    def complete_json(
        self,
        prompt: str,
        system: str = "",
        caller: str = "unknown",
        max_tokens: int = 4096
    ) -> Optional[Dict]:
        """Send completion expecting JSON response."""
        # Store max_tokens for truncation logging
        self._last_max_tokens = max_tokens

        response = self.complete(
            prompt=prompt,
            system=system,
            caller=caller,
            max_tokens=max_tokens,
        )

        if not response:
            return None

        raw_response = response  # Keep original for logging

        try:
            # Handle markdown code blocks
            if "```json" in response:
                start = response.find("```json") + 7
                end = response.find("```", start)
                if end > start:
                    response = response[start:end].strip()
                else:
                    response = response[start:].strip()
            elif "```" in response:
                start = response.find("```") + 3
                end = response.find("```", start)
                if end > start:
                    response = response[start:end].strip()
                else:
                    response = response[start:].strip()

            result = json.loads(response)

            # Log successful interaction if debug logging enabled
            log_llm_interaction(
                caller=caller,
                prompt=prompt,
                system=system,
                response=raw_response,
                input_tokens=getattr(self, '_last_input_tokens', 0),
                output_tokens=getattr(self, '_last_output_tokens', 0),
                max_tokens=max_tokens,
                model=self.model,
                truncation_repaired=False
            )

            return result

        except json.JSONDecodeError as e:
            # Try to extract JSON object
            if "Extra data" in str(e):
                extracted = self._extract_json_object(response)
                if extracted:
                    return extracted

            # Try to repair truncated JSON
            repaired = self._try_repair_json(response, caller=caller)
            if repaired:
                # Log the interaction with truncation info
                log_llm_interaction(
                    caller=caller,
                    prompt=prompt,
                    system=system,
                    response=raw_response,
                    input_tokens=getattr(self, '_last_input_tokens', 0),
                    output_tokens=getattr(self, '_last_output_tokens', 0),
                    max_tokens=max_tokens,
                    model=self.model,
                    truncation_repaired=True,
                    repair_details={"error": str(e), "response_length": len(raw_response)}
                )
                return repaired

            stop = getattr(self, '_last_stop_reason', '?')
            out_tok = getattr(self, '_last_output_tokens', '?')
            print(f"[WARN] Failed to parse JSON response: {e} | caller={caller} max_tokens={max_tokens} output_tokens={out_tok} stop_reason={stop} model={self.model}")
            return None

    def _extract_json_object(self, response: str) -> Optional[Dict]:
        """Extract the first complete JSON object from response."""
        start = response.find('{')
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape_next = False

        for i, char in enumerate(response[start:], start):
            if escape_next:
                escape_next = False
                continue

            if char == '\\' and in_string:
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    json_str = response[start:i+1]
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        return None

        return None

    def _try_repair_json(self, response: str, caller: str = "unknown") -> Optional[Dict]:
        """Attempt to repair truncated JSON with detailed logging."""
        response = response.strip()

        open_braces = response.count('{') - response.count('}')
        open_brackets = response.count('[') - response.count(']')

        in_string = False
        for i, char in enumerate(response):
            if char == '"' and (i == 0 or response[i-1] != '\\'):
                in_string = not in_string

        repairs = []
        repair_descriptions = []

        if in_string:
            base = response + '"'
            repairs.append(base + '}' * open_braces + ']' * open_brackets)
            repair_descriptions.append(f'close_string + {open_braces} braces + {open_brackets} brackets')
            repairs.append(base + '"}' * max(1, open_braces))
            repair_descriptions.append(f'close_string + {max(1, open_braces)} "}} patterns')
        else:
            repairs.append(response + '}' * open_braces + ']' * open_brackets)
            repair_descriptions.append(f'{open_braces} braces + {open_brackets} brackets')

        repairs.extend([
            response + '"}',
            response + '"]',
            response + '"}]',
            response + '}',
            response + ']}',
        ])
        repair_descriptions.extend([
            'add "}',
            'add "]',
            'add "}]',
            'add }',
            'add ]}',
        ])

        for attempt, desc in zip(repairs, repair_descriptions):
            try:
                result = json.loads(attempt)
                # Log detailed truncation event
                log_truncation_event(
                    caller=caller,
                    response_preview=response,
                    repair_type=desc,
                    open_braces=open_braces,
                    open_brackets=open_brackets,
                    in_string=in_string,
                    output_tokens=getattr(self, '_last_output_tokens', None),
                    max_tokens=getattr(self, '_last_max_tokens', None),
                    stop_reason=getattr(self, '_last_stop_reason', None)
                )
                return result
            except json.JSONDecodeError:
                continue

        return None


# ============================================================================
# ANTHROPIC CLIENT
# ============================================================================

class AnthropicClient(BaseLLMClient):
    """Anthropic Claude client for agentic operations."""

    def __init__(self, model: str = DEFAULT_ANTHROPIC_MODEL):
        super().__init__(provider="anthropic", model=model)
        self.client = None
        self._initialize()

    def _initialize(self) -> bool:
        """Initialize the Anthropic client."""
        if not ANTHROPIC_AVAILABLE:
            print("[ERROR] anthropic package not available. Install with: pip install anthropic")
            return False

        api_key = _load_api_key("api_anthropic.key")
        if not api_key:
            apikeys_dir = _get_apikeys_dir()
            print("[ERROR] Could not load Anthropic API key.")
            print("  Options:")
            print("    1. Set ANTHROPIC_API_KEY environment variable")
            print(f"    2. Create file: {apikeys_dir / 'api_anthropic.key' if apikeys_dir else 'apikeys/api_anthropic.key'}")
            if not apikeys_dir:
                print(f"  Note: apikeys directory not found. Expected at: {_find_project_root().parent / 'apikeys'}")
            return False

        try:
            self.client = anthropic.Anthropic(api_key=api_key)
            self.is_initialized = True
            print(f"[OK] Anthropic client initialized ({self.model})")

            # Auto-enable LLM debug logging
            setup_llm_debug_logging(enabled=True)

            return True
        except Exception as e:
            print(f"[ERROR] Failed to initialize Anthropic client: {e}")
            return False

    def complete(
        self,
        prompt: str,
        system: str = "",
        caller: str = "unknown",
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Optional[str]:
        """Send a completion request to Claude."""
        if not self.client:
            print("[ERROR] Anthropic client not initialized")
            return None

        try:
            messages = [{"role": "user", "content": prompt}]

            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system
            if temperature > 0:
                kwargs["temperature"] = temperature

            response = self.client.messages.create(**kwargs)

            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            self._track_usage(caller, input_tokens, output_tokens)

            # Store for truncation logging
            self._last_input_tokens = input_tokens
            self._last_output_tokens = output_tokens
            self._last_stop_reason = response.stop_reason

            # Debug prompt dump
            result_text = response.content[0].text if response.content else None
            self._dump_prompt(caller,
                request={"system": system, "messages": messages, "max_tokens": max_tokens},
                response={"text": result_text, "stop_reason": response.stop_reason},
                input_tokens=input_tokens, output_tokens=output_tokens)

            # Log stop_reason if it's not end_turn (normal completion)
            if response.stop_reason != "end_turn":
                print(f"[WARN] LLM stop_reason: {response.stop_reason} (tokens: {output_tokens}/{max_tokens}) caller={caller} model={self.model}")

            if result_text:
                return result_text

            return None

        except Exception as e:
            print(f"[ERROR] Anthropic API call failed: {e}")
            return None

    def complete_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        caller: str = "unknown",
        max_tokens: int = 4096,
        temperature: float = 0.0,
        system: str = ""
    ) -> Optional[LLMResponse]:
        """
        Send completion with tool definitions.

        Anthropic tool format:
        {
            "name": "tool_name",
            "description": "Tool description",
            "input_schema": { JSON Schema }
        }
        """
        if not self.client:
            print("[ERROR] Anthropic client not initialized")
            return None

        try:
            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": messages,
            }

            if system:
                kwargs["system"] = system

            if temperature > 0:
                kwargs["temperature"] = temperature

            # Convert tools to Anthropic format if needed
            anthropic_tools = []
            for tool in tools:
                if "function" in tool:
                    # OpenAI format -> Anthropic format
                    func = tool["function"]
                    anthropic_tools.append({
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {"type": "object", "properties": {}})
                    })
                else:
                    # Already Anthropic format
                    anthropic_tools.append(tool)

            if anthropic_tools:
                kwargs["tools"] = anthropic_tools

            response = self.client.messages.create(**kwargs)

            # Track usage
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            self._track_usage(caller, input_tokens, output_tokens)

            # Parse response
            text_parts = []
            tool_calls = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(ToolCall(
                        id=block.id,
                        name=block.name,
                        parameters=block.input if hasattr(block, 'input') else {}
                    ))

            # Debug prompt dump
            self._dump_prompt(caller,
                request={"system": system, "messages": messages, "tools": anthropic_tools, "max_tokens": max_tokens},
                response={"text": " ".join(text_parts),
                          "tool_calls": [{"name": tc.name, "parameters": tc.parameters} for tc in tool_calls],
                          "stop_reason": response.stop_reason},
                input_tokens=input_tokens, output_tokens=output_tokens)

            return LLMResponse(
                text=" ".join(text_parts),
                tool_calls=tool_calls,
                usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
            )

        except Exception as e:
            print(f"[ERROR] Anthropic API call with tools failed: {e}")
            return None


# ============================================================================
# OPENAI CLIENT
# ============================================================================

class OpenAIClient(BaseLLMClient):
    """OpenAI GPT client for agentic operations."""

    def __init__(self, model: str = DEFAULT_OPENAI_MODEL):
        super().__init__(provider="openai", model=model)
        self.client = None
        self._initialize()

    def _initialize(self) -> bool:
        """Initialize the OpenAI client."""
        if not OPENAI_AVAILABLE:
            print("[ERROR] openai package not available. Install with: pip install openai")
            return False

        api_key = _load_api_key("api_openai.key")
        if not api_key:
            print("[ERROR] Could not load apikeys/api_openai.key")
            return False

        try:
            self.client = openai.OpenAI(api_key=api_key)
            self.is_initialized = True
            print(f"[OK] OpenAI client initialized ({self.model})")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to initialize OpenAI client: {e}")
            return False

    def complete(
        self,
        prompt: str,
        system: str = "",
        caller: str = "unknown",
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Optional[str]:
        """Send a completion request to OpenAI."""
        if not self.client:
            print("[ERROR] OpenAI client not initialized")
            return None

        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            usage = response.usage
            if usage:
                self._track_usage(caller, usage.prompt_tokens, usage.completion_tokens)
                # Store for truncation logging
                self._last_input_tokens = usage.prompt_tokens
                self._last_output_tokens = usage.completion_tokens
            else:
                self._last_input_tokens = 0
                self._last_output_tokens = 0

            if response.choices and len(response.choices) > 0:
                return response.choices[0].message.content

            return None

        except Exception as e:
            print(f"[ERROR] OpenAI API call failed: {e}")
            return None

    def complete_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        caller: str = "unknown",
        max_tokens: int = 4096,
        temperature: float = 0.0,
        system: str = ""
    ) -> Optional[LLMResponse]:
        """
        Send completion with tool definitions.

        OpenAI tool format:
        {
            "type": "function",
            "function": {
                "name": "tool_name",
                "description": "...",
                "parameters": { JSON Schema }
            }
        }
        """
        if not self.client:
            print("[ERROR] OpenAI client not initialized")
            return None

        try:
            # Build messages with optional system
            full_messages = []
            if system:
                full_messages.append({"role": "system", "content": system})
            full_messages.extend(messages)

            # Convert tools to OpenAI format if needed
            openai_tools = []
            for tool in tools:
                if "type" in tool and tool["type"] == "function":
                    # Already OpenAI format
                    openai_tools.append(tool)
                elif "name" in tool:
                    # Anthropic format -> OpenAI format
                    openai_tools.append({
                        "type": "function",
                        "function": {
                            "name": tool["name"],
                            "description": tool.get("description", ""),
                            "parameters": tool.get("input_schema", {"type": "object", "properties": {}})
                        }
                    })

            kwargs = {
                "model": self.model,
                "messages": full_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            if openai_tools:
                kwargs["tools"] = openai_tools

            response = self.client.chat.completions.create(**kwargs)

            # Track usage
            usage = response.usage
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0
            self._track_usage(caller, input_tokens, output_tokens)

            # Parse response
            choice = response.choices[0] if response.choices else None
            if not choice:
                return LLMResponse(text="", tool_calls=[], usage=TokenUsage())

            message = choice.message
            text = message.content or ""
            tool_calls = []

            if message.tool_calls:
                for tc in message.tool_calls:
                    try:
                        params = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except json.JSONDecodeError:
                        params = {}

                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        parameters=params
                    ))

            return LLMResponse(
                text=text,
                tool_calls=tool_calls,
                usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
            )

        except Exception as e:
            print(f"[ERROR] OpenAI API call with tools failed: {e}")
            return None


# ============================================================================
# MULTI-PROVIDER CLIENT
# ============================================================================

class MultiProviderClient:
    """
    Multi-provider LLM client.

    Manages both Anthropic and OpenAI clients with separate tracking.
    """

    def __init__(
        self,
        anthropic_model: str = DEFAULT_ANTHROPIC_MODEL,
        openai_model: str = DEFAULT_OPENAI_MODEL,
    ):
        global _anthropic_client, _openai_client
        if _anthropic_client is None:
            _anthropic_client = AnthropicClient(model=anthropic_model)
        if _openai_client is None:
            _openai_client = OpenAIClient(model=openai_model)
        self.anthropic = _anthropic_client
        self.openai = _openai_client

    @property
    def is_dual_mode(self) -> bool:
        """Check if both providers are available."""
        return self.anthropic.is_initialized and self.openai.is_initialized

    def get_combined_usage(self) -> Dict:
        """Get combined usage from both providers."""
        anthropic_usage = self.anthropic.get_usage_summary()
        openai_usage = self.openai.get_usage_summary()

        return {
            "anthropic": anthropic_usage,
            "openai": openai_usage,
            "total_cost": anthropic_usage["total_cost"] + openai_usage["total_cost"],
            "total_calls": anthropic_usage["total_calls"] + openai_usage["total_calls"],
        }

    def reset_all_tracking(self) -> None:
        """Reset tracking for both providers."""
        self.anthropic.reset_tracking()
        self.openai.reset_tracking()


# ============================================================================
# GLOBAL CLIENTS (Singleton Pattern)
# ============================================================================

_anthropic_client: Optional[AnthropicClient] = None
_openai_client: Optional[OpenAIClient] = None
_multi_client: Optional[MultiProviderClient] = None


def get_anthropic_client(model: str = DEFAULT_ANTHROPIC_MODEL) -> AnthropicClient:
    """Get or create the global Anthropic client."""
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = AnthropicClient(model=model)
    return _anthropic_client


def get_openai_client(model: str = DEFAULT_OPENAI_MODEL) -> OpenAIClient:
    """Get or create the global OpenAI client."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAIClient(model=model)
    return _openai_client


def get_multi_client(
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL,
    openai_model: str = DEFAULT_OPENAI_MODEL,
) -> MultiProviderClient:
    """Get or create the global multi-provider client."""
    global _multi_client
    if _multi_client is None:
        _multi_client = MultiProviderClient(
            anthropic_model=anthropic_model,
            openai_model=openai_model,
        )
    return _multi_client


def get_default_client() -> BaseLLMClient:
    """Get the default LLM client (Anthropic Claude)."""
    return get_anthropic_client()


# Model-keyed client cache for atomic loop Phase 2
_model_clients: Dict[str, AnthropicClient] = {}


def get_client_for_model(model: str) -> AnthropicClient:
    """Get or create an Anthropic client for a specific model.

    Used by the atomic agentic loop to create a separate client for Phase 2
    (parameter generation) when a different model is configured.

    Args:
        model: Model identifier (e.g. "claude-haiku-4-5-20251001")

    Returns:
        AnthropicClient configured for the requested model
    """
    # If it matches the default singleton, reuse it
    if _anthropic_client is not None and _anthropic_client.model == model:
        return _anthropic_client

    # Check cache
    if model not in _model_clients:
        _model_clients[model] = AnthropicClient(model=model)

    return _model_clients[model]


# ============================================================================
# MAIN BLOCK (Test & Demo)
# ============================================================================

if __name__ == "__main__":
    print("Agentic Loop LLM Client")
    print("=" * 60)

    # Show apikeys location
    apikeys_dir = _find_apikeys_dir()
    print(f"API keys directory: {apikeys_dir or 'NOT FOUND'}")

    print("\n--- Anthropic Client ---")
    anthropic_client = get_anthropic_client()
    if anthropic_client.is_initialized:
        response = anthropic_client.complete(
            prompt="What is 2+2? Reply with just the number.",
            caller="test",
            max_tokens=50,
        )
        print(f"Response: {response}")
        print(f"Usage: {anthropic_client.get_usage_summary()}")
    else:
        print("Anthropic client not initialized - check apikeys/api_anthropic.key")

    print("\n--- OpenAI Client ---")
    openai_client = get_openai_client()
    if openai_client.is_initialized:
        response = openai_client.complete(
            prompt="What is 2+2? Reply with just the number.",
            caller="test",
            max_tokens=50,
        )
        print(f"Response: {response}")
        print(f"Usage: {openai_client.get_usage_summary()}")
    else:
        print("OpenAI client not initialized - check apikeys/api_openai.key")

    print("\n--- Multi-Provider Client ---")
    multi_client = get_multi_client()
    print(f"Dual mode available: {multi_client.is_dual_mode}")
    print(f"Combined usage: {multi_client.get_combined_usage()}")

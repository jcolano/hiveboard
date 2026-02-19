#!/usr/bin/env python3
"""
HiveBoard + Claude Agent SDK — Integration Demo
================================================

A runnable demo showing how to make Claude Agent SDK agents fully
observable through HiveBoard, using the HiveLoop Python SDK.

Covers all integration layers:
  Layer 0  — Agent registration + heartbeat
  Layer 1  — Task lifecycle (start/complete/fail)
  Layer 2a — LLM cost + token tracking (via ResultMessage)
  Layer 2b — Automatic tool tracking (via PreToolUse/PostToolUse hooks)
  Layer 2c — Custom MCP tools with business-level events

Usage:
    1. Fill in the configuration section below
    2. Run:  python hiveboard_claude_agent_sdk_demo.py
    3. Watch the HiveBoard dashboard light up

Requirements:
    pip install hiveloop claude-agent-sdk

Architecture:
    Claude Agent SDK  (query / ClaudeSDKClient)
        ↓  hooks intercept every tool call
    HiveLoop SDK  (task context, llm_call, events)
        ↓  batched HTTP
    HiveBoard Server
        ↓  real-time
    Dashboard  (Fleet · Timeline · Cost Explorer)
"""

import asyncio
import os
import sys
import time
from typing import Any

# ─────────────────────────────────────────────────────────────
# CONFIGURATION — Fill these in before running
# ─────────────────────────────────────────────────────────────

HIVEBOARD_ENDPOINT = os.environ.get("HIVEBOARD_ENDPOINT", "http://localhost:8451")
HIVEBOARD_API_KEY  = os.environ.get("HIVEBOARD_API_KEY",  "hb_live_YOUR_KEY_HERE")
HIVEBOARD_PROJECT  = os.environ.get("HIVEBOARD_PROJECT",  "claude-agent-sdk-demo")

# The Claude Agent SDK reads ANTHROPIC_API_KEY from the environment.
# Set it here if it's not already exported:
# os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────

import hiveloop
from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    tool,
    create_sdk_mcp_server,
)


# ═════════════════════════════════════════════════════════════
# LAYER 0 — Init + Agent Registration
# ═════════════════════════════════════════════════════════════
#
# This gives you: heartbeats, online/offline status, stuck detection.
# Your agent appears on the Fleet View immediately.

def setup_hiveboard():
    """Initialize HiveLoop and register agents.

    Returns a dict of agent handles keyed by role name.
    """
    hb = hiveloop.init(
        api_key=HIVEBOARD_API_KEY,
        endpoint=HIVEBOARD_ENDPOINT,
        environment="production",   # ← Must match dashboard filter
        debug=True,                 # ← Prints HTTP traffic; disable in prod
    )

    agents = {
        "researcher": hb.agent(
            agent_id="demo-researcher",
            type="research",
            version="1.0.0",
            framework="claude-agent-sdk",
            heartbeat_interval=30,
            stuck_threshold=300,
        ),
        "coder": hb.agent(
            agent_id="demo-coder",
            type="engineering",
            version="1.0.0",
            framework="claude-agent-sdk",
            heartbeat_interval=30,
            stuck_threshold=300,
        ),
    }
    return agents


# ═════════════════════════════════════════════════════════════
# LAYER 2a — Token + Cost Extraction
# ═════════════════════════════════════════════════════════════
#
# The Claude Agent SDK's ResultMessage contains total_cost_usd (already
# accounting for prompt caching) and a usage dict with three input-token
# fields that must be summed for the real total.

def extract_usage(result_msg: ResultMessage | None) -> dict:
    """Extract token counts and cost from a ResultMessage.

    Claude's prompt caching splits input tokens into three buckets:
      - input_tokens:                 non-cached
      - cache_creation_input_tokens:  written to cache this request
      - cache_read_input_tokens:      read from cache

    Total input = sum of all three.
    """
    if not result_msg:
        return {"tokens_in": 0, "tokens_out": 0, "cost": 0.0}

    usage = result_msg.usage or {}
    tokens_in = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
    return {
        "tokens_in": tokens_in,
        "tokens_out": usage.get("output_tokens", 0),
        "cost": result_msg.total_cost_usd or 0.0,
    }


def report_llm_call(
    task,
    name: str,
    model: str | None,
    result_msg: ResultMessage | None,
    duration_ms: float,
    prompt: str | None = None,
    response_text: str | None = None,
):
    """Report an LLM call to HiveLoop from a ResultMessage.

    Parameters
    ----------
    task : hiveloop Task context
    name : Descriptive label shown in Timeline (e.g. "research-query", NOT the model name)
    model : Model identifier (e.g. "claude-opus-4-6")
    result_msg : The ResultMessage from the Agent SDK
    duration_ms : Wall-clock duration of the entire agent run
    prompt : Optional — first 300 chars of the user prompt (consider PII)
    response_text : Optional — first 500 chars of agent response (consider PII)
    """
    u = extract_usage(result_msg)
    try:
        task.llm_call(
            name,
            model=model or "unknown",
            tokens_in=u["tokens_in"],
            tokens_out=u["tokens_out"],
            cost=u["cost"],
            duration_ms=round(duration_ms),
            prompt_preview=prompt[:300] if prompt else None,
            response_preview=response_text[:500] if response_text else None,
        )
        print(f"  ✅ LLM call reported: {name} | {model} | "
              f"{u['tokens_in']} in / {u['tokens_out']} out | ${u['cost']:.4f}")
    except Exception as e:
        print(f"  ⚠️  LLM call report failed: {e}")


# ═════════════════════════════════════════════════════════════
# LAYER 2b — Automatic Tool Tracking via Hooks
# ═════════════════════════════════════════════════════════════
#
# Hooks intercept EVERY tool call — built-in tools (Bash, Read, Write)
# and custom MCP tools — with precise start/end timing.
#
# Requires ClaudeSDKClient (not bare query()).

# Shared state between hooks and the main code
_tool_timings: dict[str, dict] = {}
_hook_task = None   # The current HiveLoop task; hooks read this


async def pre_tool_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context,
) -> dict[str, Any]:
    """Fires BEFORE every tool execution.

    Records start time and emits a tool_start event to HiveLoop.
    """
    tool_name = input_data.get("tool_name", "unknown")
    tool_input = input_data.get("tool_input", {})

    _tool_timings[tool_use_id] = {
        "name": tool_name,
        "start": time.perf_counter(),
    }

    print(f"  🔧 [PRE]  {tool_name}")

    if _hook_task:
        try:
            _hook_task.event("custom", {
                "kind": "tool_start",
                "tool": tool_name,
                "tool_use_id": tool_use_id,
                "input_preview": str(tool_input)[:300],
            })
        except Exception:
            pass

    return {}   # Return empty dict = don't modify the tool execution


async def post_tool_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context,
) -> dict[str, Any]:
    """Fires AFTER every tool execution.

    Calculates duration, detects errors, and emits a tool_complete event.
    """
    tool_name = input_data.get("tool_name", "unknown")
    tool_output = str(input_data.get("tool_result", ""))

    timing = _tool_timings.pop(tool_use_id, None)
    duration_ms = round((time.perf_counter() - timing["start"]) * 1000) if timing else 0
    is_error = "error" in tool_output.lower() or "traceback" in tool_output.lower()

    print(f"  ✅ [POST] {tool_name} | {duration_ms}ms | error={is_error}")

    if _hook_task:
        try:
            _hook_task.event("custom", {
                "kind": "tool_complete",
                "tool": tool_name,
                "tool_use_id": tool_use_id,
                "duration_ms": duration_ms,
                "is_error": is_error,
                "output_preview": tool_output[:200],
            })
        except Exception:
            pass

    return {}   # Don't modify the result


def make_hook_options(**extra) -> ClaudeAgentOptions:
    """Create ClaudeAgentOptions with PreToolUse/PostToolUse hooks wired in.

    Pass additional options as keyword arguments.
    """
    return ClaudeAgentOptions(
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="*", hooks=[pre_tool_hook]),
            ],
            "PostToolUse": [
                HookMatcher(matcher="*", hooks=[post_tool_hook]),
            ],
        },
        **extra,
    )


# ═════════════════════════════════════════════════════════════
# LAYER 2c — Custom MCP Tools with Business Events
# ═════════════════════════════════════════════════════════════
#
# Custom tools defined via @tool emit business-level events directly
# into HiveLoop from inside the tool handler. This adds domain context
# beyond generic tool tracking.

@tool("lookup_customer", "Look up a customer by name", {"name": str})
async def lookup_customer(args: dict[str, Any]) -> dict[str, Any]:
    """Simulated customer database lookup."""
    name = args["name"]
    await asyncio.sleep(0.3)    # Simulate DB latency

    customers = {
        "alice": {"id": "C-1001", "name": "Alice Johnson", "plan": "enterprise", "mrr": 4500, "health": "green"},
        "bob":   {"id": "C-1002", "name": "Bob Smith",     "plan": "pro",        "mrr": 890,  "health": "yellow"},
        "carol": {"id": "C-1003", "name": "Carol Davis",   "plan": "starter",    "mrr": 49,   "health": "red"},
    }

    key = name.lower().split()[0] if name else ""
    customer = customers.get(key)

    if customer and _hook_task:
        _hook_task.event("custom", {
            "kind": "customer_lookup",
            "customer_id": customer["id"],
            "plan": customer["plan"],
            "health": customer["health"],
        })

    if customer:
        return {"content": [{"type": "text", "text": f"Customer found: {customer}"}]}
    return {"content": [{"type": "text", "text": f"No customer found for '{name}'"}]}


@tool(
    "calculate_churn_risk",
    "Calculate churn risk score for a customer",
    {"customer_id": str, "mrr": float, "health": str},
)
async def calculate_churn_risk(args: dict[str, Any]) -> dict[str, Any]:
    """Simulated churn risk scoring."""
    await asyncio.sleep(0.2)

    health_scores = {"green": 0.1, "yellow": 0.45, "red": 0.82}
    risk = health_scores.get(args["health"], 0.5)
    if args["mrr"] > 1000:
        risk *= 0.8     # Enterprise customers get more support

    result = {
        "customer_id": args["customer_id"],
        "risk_score": round(risk, 3),
        "risk_level": "high" if risk > 0.6 else "medium" if risk > 0.3 else "low",
        "action": "immediate_outreach" if risk > 0.6 else "monitor" if risk > 0.3 else "none",
    }

    if _hook_task:
        _hook_task.event("custom", {
            "kind": "churn_risk_calculated",
            "customer_id": args["customer_id"],
            "risk_score": result["risk_score"],
            "risk_level": result["risk_level"],
        })

    return {"content": [{"type": "text", "text": f"Churn analysis: {result}"}]}


@tool(
    "send_alert",
    "Send an alert to the customer success team",
    {"customer_id": str, "message": str, "priority": str},
)
async def send_alert(args: dict[str, Any]) -> dict[str, Any]:
    """Simulated alert dispatch."""
    await asyncio.sleep(0.1)

    if _hook_task:
        _hook_task.event("custom", {
            "kind": "alert_sent",
            "customer_id": args["customer_id"],
            "priority": args["priority"],
            "message_preview": args["message"][:200],
        })

    return {"content": [{"type": "text", "text": f"Alert sent: priority={args['priority']}"}]}


# Bundle into an in-process MCP server
cs_tools_server = create_sdk_mcp_server(
    name="customer-success",
    version="1.0.0",
    tools=[lookup_customer, calculate_churn_risk, send_alert],
)


# ═════════════════════════════════════════════════════════════
# DEMO SCENARIOS
# ═════════════════════════════════════════════════════════════

async def demo_simple_query(agents: dict):
    """Demo 1: Simple question — Layer 0 + 1 + 2a only.

    Shows: agent heartbeat, task lifecycle, LLM cost tracking.
    Uses bare query() — no hooks, no custom tools.
    """
    print("\n" + "=" * 60)
    print("DEMO 1 — Simple Query (Layers 0–2a)")
    print("=" * 60)

    agent = agents["researcher"]
    prompt = "What are three benefits of AI agent observability? Be concise."

    model_name = None
    result_msg = None
    collected_text = []

    with agent.task("demo-simple-001", project=HIVEBOARD_PROJECT, type="research") as task:
        print(f"📋 Task started: demo-simple-001\n")
        t_start = time.perf_counter()

        # LAYER 1: Task wrapping
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(max_turns=3, max_budget_usd=0.30),
        ):
            if isinstance(message, AssistantMessage):
                model_name = getattr(message, "model", None) or model_name
                for block in message.content:
                    if isinstance(block, TextBlock):
                        collected_text.append(block.text)
                        print(f"  💬 {block.text[:150]}")
            elif isinstance(message, ResultMessage):
                result_msg = message

        duration_ms = (time.perf_counter() - t_start) * 1000

        # LAYER 2a: LLM cost tracking
        report_llm_call(
            task, "research-query", model_name, result_msg, duration_ms,
            prompt=prompt,
            response_text="\n".join(collected_text),
        )

    hiveloop.flush()
    _print_summary("Demo 1", model_name, result_msg)


async def demo_hooks_tool_tracking(agents: dict):
    """Demo 2: Multi-tool task with hooks — Layer 2b.

    Shows: automatic tool_start/tool_complete events with timing.
    Requires ClaudeSDKClient for hook support.
    """
    global _hook_task
    print("\n" + "=" * 60)
    print("DEMO 2 — Hook-Based Tool Tracking (Layer 2b)")
    print("=" * 60)

    agent = agents["coder"]
    prompt = (
        "Use bash to: (1) create /tmp/hiveboard_test.txt with 'HiveBoard rocks', "
        "(2) read it with cat, (3) count words with wc -w. Report all results."
    )

    model_name = None
    result_msg = None
    collected_text = []

    options = make_hook_options(
        allowed_tools=["Bash", "Read"],
        permission_mode="acceptEdits",
        max_turns=5,
        max_budget_usd=0.50,
    )

    with agent.task("demo-hooks-001", project=HIVEBOARD_PROJECT, type="coding") as task:
        _hook_task = task
        print(f"📋 Task started: demo-hooks-001")
        print(f"   Hooks: PreToolUse + PostToolUse on all tools\n")

        t_start = time.perf_counter()

        # LAYER 2b: ClaudeSDKClient with hooks
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)

            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    model_name = getattr(message, "model", None) or model_name
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            collected_text.append(block.text)
                            print(f"  💬 {block.text[:150]}")
                elif isinstance(message, ResultMessage):
                    result_msg = message

        duration_ms = (time.perf_counter() - t_start) * 1000
        _hook_task = None

        report_llm_call(
            task, "multi-tool-run", model_name, result_msg, duration_ms,
            prompt=prompt,
            response_text="\n".join(collected_text),
        )

    hiveloop.flush()
    _print_summary("Demo 2", model_name, result_msg)


async def demo_custom_mcp_tools(agents: dict):
    """Demo 3: Custom MCP tools with business events — Layer 2c.

    Shows: custom tool calls tracked by hooks + business events
    emitted from inside the tool handlers.
    """
    global _hook_task
    print("\n" + "=" * 60)
    print("DEMO 3 — Custom MCP Tools + Business Events (Layer 2c)")
    print("=" * 60)

    agent = agents["researcher"]
    prompt = (
        "Analyze churn risk for Carol Davis. Steps: "
        "(1) look up her customer info, "
        "(2) calculate churn risk, "
        "(3) if risk is high, send an urgent alert to CS. "
        "Summarize findings."
    )

    model_name = None
    result_msg = None
    collected_text = []

    options = make_hook_options(
        mcp_servers={"cs": cs_tools_server},
        allowed_tools=[
            "mcp__cs__lookup_customer",
            "mcp__cs__calculate_churn_risk",
            "mcp__cs__send_alert",
        ],
        max_turns=8,
        max_budget_usd=0.50,
    )

    with agent.task("demo-churn-001", project=HIVEBOARD_PROJECT, type="churn-analysis") as task:
        _hook_task = task
        print(f"📋 Task started: demo-churn-001")
        print(f"   MCP tools: lookup_customer, calculate_churn_risk, send_alert\n")

        # Optional: declare a plan
        try:
            task.plan("Analyze churn risk for Carol Davis", [
                "Look up customer info",
                "Calculate churn risk score",
                "Send alert if high risk",
                "Summarize findings",
            ])
        except Exception:
            pass

        t_start = time.perf_counter()

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)

            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    model_name = getattr(message, "model", None) or model_name
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            collected_text.append(block.text)
                            print(f"  💬 {block.text[:200]}")
                elif isinstance(message, ResultMessage):
                    result_msg = message

        duration_ms = (time.perf_counter() - t_start) * 1000
        _hook_task = None

        report_llm_call(
            task, "churn-analysis-run", model_name, result_msg, duration_ms,
            prompt=prompt,
            response_text="\n".join(collected_text),
        )

    hiveloop.flush()
    _print_summary("Demo 3", model_name, result_msg)


# ═════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════

def _print_summary(label: str, model_name: str | None, result_msg: ResultMessage | None):
    """Print a summary after each demo."""
    u = extract_usage(result_msg)
    turns = result_msg.num_turns if result_msg else "N/A"
    print(f"\n📊 {label} Summary:")
    print(f"   Model:  {model_name}")
    print(f"   Turns:  {turns}")
    print(f"   Tokens: {u['tokens_in']:,} in / {u['tokens_out']:,} out")
    print(f"   Cost:   ${u['cost']:.4f}")


# ═════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════

async def main():
    # Preflight checks
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY not set. Export it or set it in the config section.")
        sys.exit(1)
    if HIVEBOARD_API_KEY == "hb_live_YOUR_KEY_HERE":
        print("❌ Set HIVEBOARD_API_KEY in the config section or as an environment variable.")
        sys.exit(1)

    print("🐝 HiveBoard + Claude Agent SDK — Integration Demo")
    print(f"   Endpoint:  {HIVEBOARD_ENDPOINT}")
    print(f"   Project:   {HIVEBOARD_PROJECT}")
    print()

    # Layer 0: Init + register agents
    agents = setup_hiveboard()
    print(f"✅ Agents registered: {', '.join(agents.keys())}")
    time.sleep(2)   # Let heartbeats propagate

    # Run demos sequentially
    total_cost = 0.0

    await demo_simple_query(agents)
    await demo_hooks_tool_tracking(agents)
    await demo_custom_mcp_tools(agents)

    # Final flush
    hiveloop.flush()
    time.sleep(3)

    # Fleet summary
    print("\n" + "=" * 60)
    print("🐝 ALL DEMOS COMPLETE")
    print("=" * 60)
    print("\nOpen HiveBoard and verify:")
    print("  Fleet View     — 2 agents (demo-researcher, demo-coder)")
    print("  Task Table     — 3 tasks (research, coding, churn-analysis)")
    print("  Timeline       — LLM nodes, tool events, business events")
    print("  Cost Explorer  — Per-agent, per-model cost breakdown")
    print("  Activity Stream — tool_start, tool_complete, customer_lookup, alert_sent")

    # Cleanup
    try:
        hiveloop.shutdown(timeout=5)
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""BrightPath Solutions - Ultimate Simulator for HiveBoard.

6 AI agents powering a digital agency: lead generation, sales, support,
marketing, data ops, and coordination. Generates realistic multi-agent
telemetry through the HiveLoop SDK.

Usage:
    python examples/brightpath.py
    python examples/brightpath.py --endpoint http://localhost:8000 --api-key hb_live_xxx
    python examples/brightpath.py --endpoint https://mlbackend.net/loophive --api-key hb_live_xxx
    python examples/brightpath.py --fast
    python examples/brightpath.py --speed 3
    python examples/brightpath.py --agents scout,harper,dispatch
    python examples/brightpath.py --haiku                        # real Haiku LLM calls
    python examples/brightpath.py --haiku --anthropic-key sk-... # explicit API key
"""

import argparse
import random
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import requests

import hiveloop
from hiveloop import tool_payload

# Optional: Anthropic SDK for live Haiku calls (--haiku flag)
try:
    import anthropic as _anthropic_mod
except ImportError:
    _anthropic_mod = None

# Module-level Haiku client — set in main() when --haiku is passed
_haiku_client = None
HAIKU_MODEL_ID = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# Data Pools
# ---------------------------------------------------------------------------

CUSTOMERS = [
    {"name": "TechNova Inc", "industry": "SaaS", "size": 150, "stage": "Series B"},
    {"name": "Meridian Health", "industry": "Healthcare", "size": 80, "stage": "bootstrapped"},
    {"name": "Atlas Logistics", "industry": "Shipping", "size": 300, "stage": "enterprise"},
    {"name": "Pinnacle Finance", "industry": "FinTech", "size": 45, "stage": "seed"},
    {"name": "Redwood Manufacturing", "industry": "Industrial", "size": 500, "stage": "public"},
    {"name": "Coastal Media Group", "industry": "Media/Ads", "size": 25, "stage": "boutique"},
    {"name": "Summit Education", "industry": "EdTech", "size": 120, "stage": "Series A"},
    {"name": "Ironbridge Consulting", "industry": "Consulting", "size": 60, "stage": "bootstrapped"},
    {"name": "NovaStar Robotics", "industry": "AI/Robotics", "size": 200, "stage": "Series C"},
    {"name": "Drift Analytics", "industry": "Data analytics", "size": 35, "stage": "seed"},
]

CONTACTS = [
    {"name": "Sarah Chen", "title": "VP of Sales", "company": "TechNova Inc"},
    {"name": "Marcus Rivera", "title": "CTO", "company": "Meridian Health"},
    {"name": "Elena Kowalski", "title": "Head of Ops", "company": "Atlas Logistics"},
    {"name": "James Okafor", "title": "CEO", "company": "Pinnacle Finance"},
    {"name": "Priya Sharma", "title": "CMO", "company": "Redwood Manufacturing"},
    {"name": "David Park", "title": "Marketing Director", "company": "Coastal Media Group"},
    {"name": "Amara Johnson", "title": "COO", "company": "Summit Education"},
    {"name": "Michael Torres", "title": "VP Engineering", "company": "Ironbridge Consulting"},
    {"name": "Yuki Tanaka", "title": "Product Lead", "company": "NovaStar Robotics"},
    {"name": "Rachel Novak", "title": "Data Lead", "company": "Drift Analytics"},
]

TICKET_CATEGORIES = [
    {"id": "billing", "desc": "Invoice disputes, payment failures, plan changes"},
    {"id": "technical", "desc": "API errors, integration issues, performance problems"},
    {"id": "feature_request", "desc": "New feature asks, enhancement suggestions"},
    {"id": "bug_report", "desc": "Bugs in the product, unexpected behavior"},
    {"id": "onboarding", "desc": "Setup help, getting started, configuration"},
    {"id": "account", "desc": "Access issues, password resets, permission changes"},
]

CAMPAIGN_NAMES = [
    "Spring Growth Accelerator",
    "Q1 Product Launch Blitz",
    "Customer Win-Back Wave",
    "Enterprise Tier Upsell",
    "Webinar: AI in Healthcare",
    "Case Study: TechNova Success",
    "Holiday Season Promo",
    "Free Trial Extension Push",
    "Partner Channel Activation",
    "Annual Review Outreach",
]

LOOPCOLONY_TOPICS = [
    "#daily-standup", "#deals", "#support-escalations",
    "#campaign-results", "#data-alerts", "#general",
]

# ---------------------------------------------------------------------------
# Models, Projects, Time Patterns
# ---------------------------------------------------------------------------

MODELS = {
    "claude-sonnet-4-20250514": {"cost_in": 0.003, "cost_out": 0.015},
    "claude-haiku-4-5-20251001": {"cost_in": 0.00025, "cost_out": 0.00125},
    "gpt-4o": {"cost_in": 0.005, "cost_out": 0.015},
    "gpt-4o-mini": {"cost_in": 0.00015, "cost_out": 0.0006},
}

AGENT_MODELS = {
    "scout": "claude-sonnet-4-20250514",
    "closer": "claude-sonnet-4-20250514",
    "harper": "claude-sonnet-4-20250514",
    "campaigner": "gpt-4o",
    "archivist": "claude-haiku-4-5-20251001",
    "dispatch": "claude-haiku-4-5-20251001",
}

TIME_PATTERNS = {
    "scout":      {"morning": 0.5, "afternoon": 1.0, "evening": 2.0},
    "closer":     {"morning": 1.0, "afternoon": 0.5, "evening": 2.0},
    "harper":     {"morning": 1.0, "afternoon": 0.5, "evening": 2.0},
    "campaigner": {"morning": 0.5, "afternoon": 1.0, "evening": 2.0},
    "archivist":  {"morning": 1.0, "afternoon": 1.0, "evening": 0.5},
    "dispatch":   {"morning": 0.5, "afternoon": 1.0, "evening": 1.0},
}

PROJECTS = [
    {"slug": "lead-gen", "name": "Lead Generation"},
    {"slug": "sales-pipeline", "name": "Sales Pipeline"},
    {"slug": "customer-success", "name": "Customer Success"},
    {"slug": "marketing-ops", "name": "Marketing Ops"},
    {"slug": "back-office", "name": "Back Office"},
]

# ---------------------------------------------------------------------------
# Virtual Clock
# ---------------------------------------------------------------------------


class VirtualClock:
    """Tracks simulated time of day for activity pattern modulation."""

    def __init__(self, speed: float = 1.0):
        self._start = time.time()
        self._speed = speed
        self._start_hour = 9.0  # 9:00 AM

    def current_hour(self) -> float:
        elapsed = time.time() - self._start
        sim_hours = (elapsed * self._speed * 60) / 3600
        return (self._start_hour + sim_hours) % 24

    def time_block(self) -> str:
        h = self.current_hour()
        if 9 <= h < 12:
            return "morning"
        elif 12 <= h < 17:
            return "afternoon"
        return "evening"

    def sleep_multiplier(self, agent_id: str) -> float:
        block = self.time_block()
        return TIME_PATTERNS.get(agent_id, {}).get(block, 1.0)


# ---------------------------------------------------------------------------
# Story Arc Engine
# ---------------------------------------------------------------------------


class StoryArcEngine:
    """Manages multi-cycle story arcs that cascade across agents."""

    ARC_DEFS = {
        "crm_outage":     {"prob": 0.15, "dur": (3, 5)},
        "lead_flood":     {"prob": 0.10, "dur": (4, 6)},
        "angry_customer": {"prob": 0.20, "dur": (2, 3)},
        "data_migration": {"prob": 0.05, "dur": (5, 8)},
        "campaign_launch": {"prob": 0.10, "dur": (6, 10)},
    }

    def __init__(self):
        self._active: dict[str, int] = {}  # arc -> remaining ticks
        self._lock = threading.Lock()
        self._cycle = 0

    def tick(self):
        with self._lock:
            self._cycle += 1
            if self._cycle % 20 == 0:
                self._maybe_trigger()
            expired = [k for k, v in self._active.items() if v <= 1]
            for k in expired:
                del self._active[k]
            for k in list(self._active):
                self._active[k] -= 1

    def _maybe_trigger(self):
        for name, d in self.ARC_DEFS.items():
            if name not in self._active and random.random() < d["prob"]:
                self._active[name] = random.randint(*d["dur"]) * 20
                break

    def is_active(self, arc: str) -> bool:
        with self._lock:
            return arc in self._active

    def active_arcs(self) -> list[str]:
        with self._lock:
            return list(self._active)


# ---------------------------------------------------------------------------
# Issue Tracker
# ---------------------------------------------------------------------------


class IssueTracker:
    """Tracks issues across agents with escalating severity."""

    def __init__(self):
        self._issues: dict[str, dict] = {}
        self._lock = threading.Lock()

    def report(self, agent, issue_id: str, summary: str, category: str | None = None):
        with self._lock:
            if issue_id not in self._issues:
                self._issues[issue_id] = {"occurrences": 0, "summary": summary}
            self._issues[issue_id]["occurrences"] += 1
            occ = self._issues[issue_id]["occurrences"]
        sev = "low" if occ < 4 else "medium" if occ < 9 else "high"
        agent.report_issue(summary, sev, issue_id=issue_id, category=category,
                           occurrence_count=occ)

    def resolve(self, agent, issue_id: str, summary: str | None = None):
        with self._lock:
            info = self._issues.pop(issue_id, None)
        msg = summary or ((info["summary"] + " - resolved") if info else f"{issue_id} resolved")
        agent.resolve_issue(msg, issue_id=issue_id)

    def is_active(self, issue_id: str) -> bool:
        with self._lock:
            return issue_id in self._issues


# ---------------------------------------------------------------------------
# Agent Counters (for heartbeat payloads)
# ---------------------------------------------------------------------------


class AgentCounters:
    """Thread-safe counters for heartbeat payload data."""

    def __init__(self, model: str):
        self._start = time.time()
        self.tasks_completed = 0
        self.tasks_failed = 0
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.total_cost = 0.0
        self.consecutive_failures = 0
        self.current_model = model
        self.extras: dict = {}
        self._lock = threading.Lock()

    def record_llm(self, tok_in: int, tok_out: int, cost: float, model: str):
        with self._lock:
            self.total_tokens_in += tok_in
            self.total_tokens_out += tok_out
            self.total_cost += cost
            self.current_model = model

    def record_success(self):
        with self._lock:
            self.tasks_completed += 1
            self.consecutive_failures = 0

    def record_failure(self):
        with self._lock:
            self.tasks_failed += 1
            self.consecutive_failures += 1

    def snapshot(self) -> dict:
        with self._lock:
            d = {
                "uptime_seconds": int(time.time() - self._start),
                "tasks_completed": self.tasks_completed,
                "tasks_failed": self.tasks_failed,
                "total_tokens_in": self.total_tokens_in,
                "total_tokens_out": self.total_tokens_out,
                "total_cost_usd": round(self.total_cost, 4),
                "consecutive_failures": self.consecutive_failures,
                "current_model": self.current_model,
            }
            d.update(self.extras)
            return d


# ---------------------------------------------------------------------------
# Simulation Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Prompt Templates (for --haiku live mode)
# ---------------------------------------------------------------------------

LLM_PROMPTS = {
    "lead_scoring": (
        "Score this B2B lead 0-100. Company: {company}, Industry: {industry}, "
        "Size: {size} employees, Stage: {stage}. Contact: {contact_name}, "
        "{contact_title}. Reply with score and one-sentence justification."
    ),
    "draft_outreach_email": (
        "Write a 3-sentence cold outreach email to {contact_name}, "
        "{contact_title} at {company} ({industry}). Mention how AI automation "
        "could help. Keep it professional and concise."
    ),
    "review_lead_brief": (
        "Review this lead: {company} ({industry}, {size} employees, {stage}). "
        "Contact: {contact_name}, {contact_title}. In 2 sentences, assess "
        "readiness and recommend next step."
    ),
    "draft_proposal": (
        "Draft a one-paragraph proposal for {company} ({industry}). "
        "Offer: AI agent automation, estimated ARR: ${deal_value}. "
        "Include 2 bullet-point benefits."
    ),
    "classify_ticket": (
        "Classify this support ticket. Customer: {company}. "
        "Category: {category}. Issue: {category_desc}. "
        "Return: priority (low/medium/high) and suggested approach in 2 sentences."
    ),
    "draft_support_response": (
        "Draft a 3-sentence support response for a {category} ticket from "
        "{contact_name} at {company}. Issue: {category_desc}. "
        "Be professional and helpful."
    ),
    "design_campaign": (
        "Design a campaign called '{campaign_name}' targeting {industry} "
        "companies. In 2 sentences: target audience, key message, primary channel."
    ),
    "generate_copy": (
        "Write 2 email subject line variants for campaign '{campaign_name}' "
        "targeting {industry} professionals. Include a 2-sentence body for each."
    ),
    "analyze_results": (
        "Campaign '{campaign_name}': sent {sent}, open rate {open_rate}%, "
        "CTR {ctr}%, conversions {converted}. In 2 sentences: performance "
        "summary and one optimization."
    ),
    "validate_records": (
        "CRM data quality check: {company}, {size} records, {industry} sector. "
        "Check: duplicate risk, completeness, GDPR. Summarize in 2 sentences."
    ),
    "generate_report": (
        "Daily data quality summary. Synced: {synced}, duplicates: {dupes}, "
        "errors: {errors}. One paragraph highlighting concerns."
    ),
    "route_work": (
        "Team workload: Scout queue {scout_depth}, Closer {closer_depth}, "
        "Harper {harper_depth}. Total: {total_depth}. "
        "In 2 sentences recommend priority routing."
    ),
    "review_escalations": (
        "Review escalation: {summary}. In 1-2 sentences recommend: "
        "approve, reject, or reassign with reasoning."
    ),
}


def _live_llm(ctx, name: str, counters: AgentCounters, context: dict):
    """Make a real Haiku API call and record actual metrics."""
    template = LLM_PROMPTS.get(name, "Complete the following task: {task}")
    prompt = template.format_map(defaultdict(str, context))

    start = time.time()
    response = _haiku_client.messages.create(
        model=HAIKU_MODEL_ID,
        max_tokens=250,
        messages=[{"role": "user", "content": prompt}],
    )
    duration_ms = int((time.time() - start) * 1000)

    tok_in = response.usage.input_tokens
    tok_out = response.usage.output_tokens
    info = MODELS[HAIKU_MODEL_ID]
    cost = (tok_in / 1000) * info["cost_in"] + (tok_out / 1000) * info["cost_out"]
    response_text = response.content[0].text if response.content else ""

    ctx.llm_call(
        name, HAIKU_MODEL_ID,
        tokens_in=tok_in, tokens_out=tok_out,
        cost=round(cost, 6), duration_ms=duration_ms,
        prompt_preview=prompt[:500],
        response_preview=response_text[:500],
    )
    counters.record_llm(tok_in, tok_out, cost, HAIKU_MODEL_ID)


def _sim_llm(ctx, name: str, model: str, counters: AgentCounters,
             context: dict | None = None):
    """LLM call: real Haiku when --haiku is active, simulated otherwise."""
    if _haiku_client and context:
        return _live_llm(ctx, name, counters, context)
    info = MODELS[model]
    tok_in = random.randint(200, 4000)
    tok_out = random.randint(50, 1200)
    cost = (tok_in / 1000) * info["cost_in"] + (tok_out / 1000) * info["cost_out"]
    dur = random.randint(300, 5000)
    ctx.llm_call(name, model, tokens_in=tok_in, tokens_out=tok_out,
                 cost=round(cost, 6), duration_ms=dur)
    counters.record_llm(tok_in, tok_out, cost, model)
    time.sleep(random.uniform(0.05, 0.15))


def _sim_tool(agent, action: str, *, args=None, result=None,
              category=None, success=True, error=None):
    """Simulate a tool call using track_context."""
    with agent.track_context(action) as ctx:
        time.sleep(random.uniform(0.03, 0.12))
        ctx.set_payload(tool_payload(
            args=args or {}, result=result or "ok", success=success,
            error=error, duration_ms=random.randint(50, 500),
            tool_category=category))


def _colony_post(agent, agent_id: str, topic: str, content: str,
                 mentions: list[str] | None = None, priority: str = "normal"):
    """Emit a LoopColony custom event."""
    agent.event("custom", payload={
        "kind": "loopcolony",
        "data": {
            "action": "post", "topic": topic, "author": agent_id,
            "content": content, "mentions": mentions or [],
            "thread_id": f"thread-{uuid.uuid4().hex[:6]}",
            "priority": priority,
        },
    })


def _wait(stop: threading.Event, seconds: float):
    """Sleep in small increments, respecting stop_event."""
    end = time.time() + seconds
    while time.time() < end and not stop.is_set():
        time.sleep(min(0.5, end - time.time()))


def _cycle_sleep(stop: threading.Event, lo: float, hi: float,
                 speed: float, clock: VirtualClock, agent_id: str):
    """Sleep between cycles with speed + time-of-day adjustment."""
    base = random.uniform(lo, hi) / speed
    mult = clock.sleep_multiplier(agent_id)
    _wait(stop, base * mult)


def _next_run(interval: str) -> str:
    """Generate a next_run ISO timestamp for scheduled work."""
    hours = {"30m": 0.5, "1h": 1, "2h": 2, "6h": 6, "daily": 24, "weekly": 168}
    dt = datetime.now(timezone.utc) + timedelta(hours=hours.get(interval, 1))
    return dt.isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Agent 1: Scout - Lead Hunter
# ---------------------------------------------------------------------------


def _run_scout(agent, counters: AgentCounters, speed: float,
               clock: VirtualClock, arcs: StoryArcEngine,
               issues: IssueTracker, stop: threading.Event):
    model = AGENT_MODELS["scout"]
    while not stop.is_set():
        arcs.tick()
        cust = random.choice(CUSTOMERS)
        contact = random.choice(CONTACTS)
        score = random.randint(20, 95)
        tid = f"lead-{_uid()}"

        try:
            lctx = {"company": cust["name"], "industry": cust["industry"],
                    "size": str(cust["size"]), "stage": cust["stage"],
                    "contact_name": contact["name"], "contact_title": contact["title"]}
            with agent.task(tid, project="lead-gen", type="lead_qualification") as task:
                steps = ["Score lead", "Enrich company data",
                         "Draft outreach email", "Route to closer", "Update CRM"]
                task.plan(f"Process lead: {cust['name']}", steps)

                # Step 0: Score lead
                task.plan_step(0, "started", "Scoring lead")
                _sim_llm(task, "lead_scoring", model, counters, lctx)
                task.plan_step(0, "completed", f"Lead scored {score}",
                               tokens=random.randint(1000, 3000))

                # Step 1: Enrich data
                task.plan_step(1, "started", "Enriching company data")
                enrich_fail = random.random() < 0.15 or arcs.is_active("crm_outage")

                if enrich_fail and random.random() < 0.12:
                    # Plan revision path
                    task.plan_step(1, "failed", "Clearbit API timeout after 3 retries")
                    task.plan(f"Process lead: {cust['name']}",
                              ["Score lead", "Draft outreach (basic)",
                               "Route to closer", "Update CRM"], revision=1)
                    task.plan_step(0, "completed", "Score lead (already done)")
                    task.plan_step(1, "started", "Draft outreach with basic data")
                    _sim_llm(task, "draft_outreach_email", model, counters, lctx)
                    task.plan_step(1, "completed", "Outreach drafted (basic)")
                    _finish_scout_route(agent, task, cust, contact, score, arcs, issues, 2, 3)
                elif enrich_fail:
                    task.retry("Clearbit API timeout, retrying", attempt=1, backoff_seconds=2)
                    issues.report(agent, "enrichment-api", "Clearbit API timeout",
                                  category="connectivity")
                    time.sleep(0.3)
                    _sim_tool(agent, "enrich_company_data",
                              args={"company": cust["name"]},
                              result={"industry": cust["industry"]}, category="enrichment")
                    issues.resolve(agent, "enrichment-api", "Clearbit API recovered")
                    task.plan_step(1, "completed", f"Enriched {cust['name']} (after retry)")
                    _finish_scout_draft_and_route(agent, task, cust, contact, score,
                                                 model, counters, arcs, issues)
                else:
                    _sim_tool(agent, "enrich_company_data",
                              args={"company": cust["name"]},
                              result={"industry": cust["industry"], "size": cust["size"]},
                              category="enrichment")
                    task.plan_step(1, "completed", f"Enriched {cust['name']}")
                    _finish_scout_draft_and_route(agent, task, cust, contact, score,
                                                 model, counters, arcs, issues)

            counters.record_success()
            counters.extras["leads_scored"] = counters.extras.get("leads_scored", 0) + 1
            counters.extras["avg_lead_score"] = score
        except Exception as e:
            counters.record_failure()
            agent.report_issue(f"Scout error: {e}", "high", category="internal")

        _cycle_sleep(stop, 3, 8, speed, clock, "scout")


def _finish_scout_draft_and_route(agent, task, cust, contact, score,
                                  model, counters, arcs, issues):
    """Steps 2-4 of scout's normal path."""
    lctx = {"company": cust["name"], "industry": cust["industry"],
            "contact_name": contact["name"], "contact_title": contact["title"]}
    task.plan_step(2, "started", "Drafting outreach email")
    _sim_llm(task, "draft_outreach_email", model, counters, lctx)
    task.plan_step(2, "completed", "Outreach email drafted")
    _finish_scout_route(agent, task, cust, contact, score, arcs, issues, 3, 4)


def _finish_scout_route(agent, task, cust, contact, score, arcs, issues,
                        route_idx: int, crm_idx: int):
    """Route to closer + CRM update steps (shared by normal and revision paths)."""
    task.plan_step(route_idx, "started", "Routing to closer")
    if score > 60:
        _colony_post(agent, "scout", "#deals",
                     f"New qualified lead: {cust['name']} (score: {score}). "
                     f"Routing to @closer.", mentions=["closer"])
        agent.todo(f"todo-{_uid()}", "created",
                   f"Follow up with {contact['name']} at {cust['name']}",
                   priority="high", source="agent_decision")
    task.plan_step(route_idx, "completed", f"Lead routed (score: {score})")

    task.plan_step(crm_idx, "started", "Updating CRM")
    if arcs.is_active("crm_outage"):
        issues.report(agent, "crm-outage", "CRM API unreachable", category="connectivity")
        _sim_tool(agent, "update_crm_lead", args={"lead": cust["name"]},
                  success=False, error="CRM API timeout", category="crm")
    else:
        _sim_tool(agent, "update_crm_lead",
                  args={"lead": cust["name"], "score": score},
                  result="lead_updated", category="crm")
    task.plan_step(crm_idx, "completed", "CRM updated")


# ---------------------------------------------------------------------------
# Agent 2: Closer - Sales Follow-up
# ---------------------------------------------------------------------------


def _run_closer(agent, counters: AgentCounters, speed: float,
                clock: VirtualClock, arcs: StoryArcEngine,
                issues: IssueTracker, stop: threading.Event):
    model = AGENT_MODELS["closer"]
    deals_closed = 0
    deals_lost = 0
    pipeline_value = 0

    while not stop.is_set():
        arcs.tick()
        cust = random.choice(CUSTOMERS)
        contact = random.choice(CONTACTS)
        deal_value = random.randint(8000, 60000)
        tid = f"deal-{_uid()}"

        try:
            lctx = {"company": cust["name"], "industry": cust["industry"],
                    "size": str(cust["size"]), "stage": cust["stage"],
                    "contact_name": contact["name"], "contact_title": contact["title"],
                    "deal_value": str(deal_value)}
            with agent.task(tid, project="sales-pipeline", type="sales_followup") as task:
                steps = ["Review lead brief", "Research company",
                         "Draft proposal", "Send proposal", "Await response"]
                task.plan(f"Follow up: {cust['name']}", steps)

                # Step 0: Review lead brief
                task.plan_step(0, "started", "Reviewing lead brief")
                _sim_llm(task, "review_lead_brief", model, counters, lctx)
                task.plan_step(0, "completed", f"Brief reviewed for {cust['name']}")

                # Step 1: Research company
                task.plan_step(1, "started", "Researching company")
                _sim_tool(agent, "research_company",
                          args={"company": cust["name"]},
                          result={"industry": cust["industry"], "stage": cust["stage"]},
                          category="research")
                task.plan_step(1, "completed", f"Researched {cust['name']}")

                # Step 2: Draft proposal
                task.plan_step(2, "started", "Drafting proposal")
                _sim_llm(task, "draft_proposal", model, counters, lctx)
                task.plan_step(2, "completed",
                               f"Proposal drafted: ${deal_value:,} ARR")

                # Step 3: Send proposal
                task.plan_step(3, "started", "Sending proposal")
                _sim_tool(agent, "send_email",
                          args={"to": contact["name"], "subject": f"Proposal for {cust['name']}"},
                          result="email_sent", category="email")
                task.plan_step(3, "completed", "Proposal sent")

                # Step 4: Decision gate
                task.plan_step(4, "started", "Awaiting response")
                roll = random.random()
                if roll < 0.60:
                    deals_closed += 1
                    pipeline_value += deal_value
                    _colony_post(agent, "closer", "#deals",
                                 f"Deal CLOSED: {cust['name']} - ${deal_value:,} ARR!",
                                 mentions=["archivist"])
                    agent.todo(f"todo-{_uid()}", "created",
                               f"Update CRM for closed deal: {cust['name']}",
                               priority="normal", source="agent_decision")
                    task.plan_step(4, "completed", f"Deal closed: ${deal_value:,}")
                elif roll < 0.85:
                    agent.todo(f"todo-{_uid()}", "created",
                               f"Follow up with {contact['name']} in 3 days",
                               priority="normal", source="agent_decision")
                    task.plan_step(4, "completed", "Follow-up scheduled")
                elif roll < 0.95:
                    task.escalate(f"Need approval for custom pricing: {cust['name']}",
                                 assigned_to="sales-lead", reason="custom pricing request")
                    task.request_approval(f"Custom pricing for {cust['name']}: ${deal_value:,}",
                                         approver="sales-lead")
                    time.sleep(random.uniform(0.2, 0.5))
                    task.approval_received(f"Custom pricing approved for {cust['name']}",
                                          approved_by="sales-lead", decision="approved")
                    deals_closed += 1
                    pipeline_value += deal_value
                    task.plan_step(4, "completed", "Deal closed (after approval)")
                else:
                    deals_lost += 1
                    _colony_post(agent, "closer", "#deals",
                                 f"Deal LOST: {cust['name']} - {contact['name']} declined.")
                    task.plan_step(4, "completed", "Deal lost")

            counters.record_success()
            counters.extras.update({
                "deals_closed": deals_closed,
                "deals_lost": deals_lost,
                "pipeline_value_usd": pipeline_value,
            })
        except Exception as e:
            counters.record_failure()
            agent.report_issue(f"Closer error: {e}", "high", category="internal")

        _cycle_sleep(stop, 5, 12, speed, clock, "closer")


# ---------------------------------------------------------------------------
# Agent 3: Harper - Support Rep
# ---------------------------------------------------------------------------


def _run_harper(agent, counters: AgentCounters, speed: float,
                clock: VirtualClock, arcs: StoryArcEngine,
                issues: IssueTracker, stop: threading.Event):
    model = AGENT_MODELS["harper"]
    resolved = 0
    escalated = 0

    while not stop.is_set():
        arcs.tick()
        cust = random.choice(CUSTOMERS)
        contact = random.choice(CONTACTS)
        cat = random.choice(TICKET_CATEGORIES)
        ticket_num = random.randint(1000, 9999)
        tid = f"ticket-{ticket_num}"
        is_angry = arcs.is_active("angry_customer") and random.random() < 0.5

        try:
            lctx = {"company": cust["name"], "category": cat["id"],
                    "category_desc": cat["desc"],
                    "contact_name": contact["name"], "contact_title": contact["title"]}
            with agent.task(tid, project="customer-success", type="ticket_triage") as task:
                steps = ["Classify ticket", "Search knowledge base",
                         "Draft response", "Resolve or escalate"]
                task.plan(f"Resolve ticket #{ticket_num}", steps)

                # Step 0: Classify
                task.plan_step(0, "started", "Classifying ticket")
                _sim_llm(task, "classify_ticket", model, counters, lctx)
                sentiment = "angry" if is_angry else random.choice(
                    ["neutral", "satisfied", "confused", "frustrated"])
                task.plan_step(0, "completed",
                               f"Category: {cat['id']}, sentiment: {sentiment}")

                # Step 1: Search KB
                task.plan_step(1, "started", "Searching knowledge base")
                _sim_tool(agent, "search_kb",
                          args={"query": cat["desc"], "category": cat["id"]},
                          result={"articles_found": random.randint(0, 5)},
                          category="knowledge_base")
                kb_found = random.random() > 0.15
                task.plan_step(1, "completed",
                               "KB articles found" if kb_found else "No KB articles found")

                # Plan revision on KB miss + angry customer
                if not kb_found and is_angry and random.random() < 0.5:
                    task.plan_step(2, "failed",
                                  "No KB articles, customer sentiment: frustrated")
                    task.plan(f"Resolve ticket #{ticket_num}",
                              ["Classify ticket", "Search KB",
                               "Escalate to senior", "Await approval", "Apply credit"],
                              revision=1)
                    task.plan_step(2, "started", "Escalating to senior support")
                    escalated += 1
                    task.escalate(
                        f"Ticket #{ticket_num}: {cat['id']} - {cust['name']} (angry)",
                        assigned_to="senior-support", reason="No KB match + angry customer")
                    _colony_post(agent, "harper", "#support-escalations",
                                 f"Ticket #{ticket_num} escalated: {cust['name']} "
                                 f"{cat['id']}. Needs attention.",
                                 mentions=["dispatch"], priority="high")
                    task.plan_step(2, "completed", "Escalated to senior support")
                    task.plan_step(3, "started", "Awaiting approval")
                    task.request_approval(f"Credit request for {cust['name']}",
                                         approver="dispatch")
                    time.sleep(random.uniform(0.2, 0.4))
                    task.approval_received("Credit approved", approved_by="dispatch")
                    task.plan_step(3, "completed", "Approval received")
                    task.plan_step(4, "started", "Applying credit")
                    _sim_tool(agent, "apply_credit",
                              args={"customer": cust["name"], "amount": random.randint(50, 500)},
                              result="credit_applied", category="billing")
                    task.plan_step(4, "completed", "Credit applied")
                    resolved += 1
                else:
                    # Step 2: Draft response
                    task.plan_step(2, "started", "Drafting response")
                    _sim_llm(task, "draft_support_response", model, counters, lctx)
                    task.plan_step(2, "completed", "Response drafted")

                    # Step 3: Resolution gate
                    task.plan_step(3, "started", "Resolving or escalating")
                    roll = random.random()
                    if roll < 0.70:
                        resolved += 1
                        task.plan_step(3, "completed", "Ticket resolved")
                    elif roll < 0.85:
                        agent.todo(f"todo-{_uid()}", "created",
                                   f"Request clarification: ticket #{ticket_num}",
                                   priority="normal", source="agent_decision")
                        task.plan_step(3, "completed", "Awaiting customer clarification")
                    elif roll < 0.95:
                        escalated += 1
                        task.escalate(
                            f"Ticket #{ticket_num}: {cat['id']} - {cust['name']}",
                            assigned_to="senior-support",
                            reason=f"Complex {cat['id']} issue")
                        _colony_post(agent, "harper", "#support-escalations",
                                     f"Ticket #{ticket_num} escalated: {cust['name']} "
                                     f"{cat['id']}.", mentions=["dispatch"])
                        task.plan_step(3, "completed", "Escalated to senior support")
                    else:
                        # CRM failure
                        if arcs.is_active("crm_outage"):
                            issues.report(agent, "crm-outage",
                                          "CRM API unreachable", category="connectivity")
                        _sim_tool(agent, "submit_to_crm",
                                  args={"ticket_id": ticket_num},
                                  success=False, error="CRM write failed", category="crm")
                        task.plan_step(3, "completed", "CRM submission failed")

            counters.record_success()
            counters.extras.update({
                "tickets_resolved": resolved,
                "tickets_escalated": escalated,
                "avg_resolution_seconds": random.randint(5, 15),
            })
        except Exception as e:
            counters.record_failure()
            agent.report_issue(f"Harper error: {e}", "high", category="internal")

        _cycle_sleep(stop, 4, 10, speed, clock, "harper")


# ---------------------------------------------------------------------------
# Agent 4: Campaigner - Marketing Ops
# ---------------------------------------------------------------------------


def _run_campaigner(agent, counters: AgentCounters, speed: float,
                    clock: VirtualClock, arcs: StoryArcEngine,
                    issues: IssueTracker, stop: threading.Event):
    model = AGENT_MODELS["campaigner"]
    campaigns_launched = 0
    open_rates = []

    while not stop.is_set():
        arcs.tick()
        cname = random.choice(CAMPAIGN_NAMES)
        cust = random.choice(CUSTOMERS)
        tid = f"campaign-{_uid()}"

        try:
            lctx = {"campaign_name": cname, "industry": cust["industry"],
                    "company": cust["name"]}
            with agent.task(tid, project="marketing-ops",
                            type="campaign_management") as task:
                steps = ["Design campaign", "Generate content",
                         "Set up A/B test", "Monitor metrics", "Analyze & report"]
                task.plan(f"Campaign: {cname}", steps)

                # Step 0: Design campaign
                task.plan_step(0, "started", "Designing campaign")
                _sim_llm(task, "design_campaign", model, counters, lctx)
                task.plan_step(0, "completed", f"Campaign designed: {cname}")

                # Step 1: Generate content
                task.plan_step(1, "started", "Generating content")
                _sim_llm(task, "generate_copy", model, counters, lctx)
                task.plan_step(1, "completed", "Email copy generated (A/B variants)")

                # Step 2: A/B test setup
                task.plan_step(2, "started", "Setting up A/B test")
                _sim_tool(agent, "setup_ab_test",
                          args={"campaign": cname, "variants": 2},
                          result={"test_id": f"ab-{_uid()}", "status": "active"},
                          category="marketing")
                task.plan_step(2, "completed", "A/B test configured")

                # Step 3: Monitor metrics
                task.plan_step(3, "started", "Monitoring metrics")
                sent = random.randint(500, 5000)
                open_rate = round(random.uniform(15, 45), 1)
                ctr = round(random.uniform(2, 12), 1)
                converted = random.randint(5, 50)
                _sim_tool(agent, "fetch_campaign_metrics",
                          args={"campaign": cname},
                          result={"sent": sent, "open_rate": open_rate,
                                  "ctr": ctr, "converted": converted},
                          category="analytics")
                task.plan_step(3, "completed",
                               f"Metrics: {open_rate}% open, {ctr}% CTR")

                # Step 4: Analyze & report
                task.plan_step(4, "started", "Analyzing results")
                lctx.update({"sent": str(sent), "open_rate": str(open_rate),
                             "ctr": str(ctr), "converted": str(converted)})
                _sim_llm(task, "analyze_results", model, counters, lctx)
                revenue = converted * random.randint(100, 500)
                cost = random.randint(200, 2000)
                roi = round(((revenue - cost) / max(cost, 1)) * 100, 1)
                _colony_post(agent, "campaigner", "#campaign-results",
                             f'"{cname}": {open_rate}% open, {ctr}% CTR, '
                             f'{converted} conversions. ROI: {roi}%')
                task.plan_step(4, "completed", f"Report posted. ROI: {roi}%")

                campaigns_launched += 1
                open_rates.append(open_rate)

                # If campaign_launch arc is active, signal lead flood
                if arcs.is_active("campaign_launch"):
                    _colony_post(agent, "campaigner", "#deals",
                                 f"Campaign '{cname}' driving high inbound! "
                                 f"Expect lead spike.", mentions=["scout", "dispatch"],
                                 priority="high")

            counters.record_success()
            counters.extras.update({
                "campaigns_launched": campaigns_launched,
                "avg_open_rate": round(sum(open_rates) / len(open_rates), 1)
                if open_rates else 0,
            })
        except Exception as e:
            counters.record_failure()
            agent.report_issue(f"Campaigner error: {e}", "high", category="internal")

        _cycle_sleep(stop, 8, 15, speed, clock, "campaigner")


# ---------------------------------------------------------------------------
# Agent 5: Archivist - Data & CRM
# ---------------------------------------------------------------------------


def _run_archivist(agent, counters: AgentCounters, speed: float,
                   clock: VirtualClock, arcs: StoryArcEngine,
                   issues: IssueTracker, stop: threading.Event):
    model = AGENT_MODELS["archivist"]
    synced = 0
    merged = 0
    sync_errs = 0

    while not stop.is_set():
        arcs.tick()
        cust = random.choice(CUSTOMERS)
        tid = f"batch-{_uid()}"

        # Faster cycles during data_migration arc
        speed_adj = speed * (2.0 if arcs.is_active("data_migration") else 1.0)

        try:
            with agent.task(tid, project="back-office",
                            type="data_operations") as task:
                steps = ["Sync CRM", "Validate data", "Deduplicate contacts",
                         "Generate report", "Compliance scan"]
                task.plan(f"Data ops: {cust['name']}", steps)

                # Step 0: CRM sync
                task.plan_step(0, "started", "Syncing CRM")
                if arcs.is_active("crm_outage"):
                    issues.report(agent, "crm-outage",
                                  "CRM API unreachable", category="connectivity")
                    _sim_tool(agent, "sync_crm", args={"source": "salesforce"},
                              success=False, error="Connection refused", category="crm")
                    sync_errs += 1
                    task.plan_step(0, "completed", "CRM sync FAILED")
                else:
                    batch_size = random.randint(10, 80)
                    _sim_tool(agent, "sync_crm",
                              args={"source": "salesforce", "batch": batch_size},
                              result={"records_pulled": batch_size}, category="crm")
                    synced += batch_size
                    task.plan_step(0, "completed", f"Synced {batch_size} records")

                # Step 1: Validate data
                task.plan_step(1, "started", "Validating data")
                lctx = {"company": cust["name"], "size": str(cust["size"]),
                        "industry": cust["industry"]}
                _sim_llm(task, "validate_records", model, counters, lctx)
                dupes = random.randint(0, 5)
                task.plan_step(1, "completed",
                               f"Validated. {dupes} potential duplicates found")

                # Step 2: Deduplicate
                task.plan_step(2, "started", "Deduplicating contacts")
                if dupes > 0:
                    _sim_tool(agent, "deduplicate_contacts",
                              args={"candidates": dupes},
                              result={"merged": dupes}, category="data_quality")
                    merged += dupes
                    if dupes >= 3:
                        _colony_post(agent, "archivist", "#data-alerts",
                                     f"CRM alert: {dupes} duplicate contacts merged "
                                     f"in {cust['name']} account.")
                task.plan_step(2, "completed",
                               f"{dupes} duplicates merged" if dupes else "No duplicates")

                # Step 3: Generate report
                task.plan_step(3, "started", "Generating report")
                _sim_llm(task, "generate_report", model, counters,
                         {"synced": str(synced), "dupes": str(dupes),
                          "errors": str(sync_errs)})
                task.plan_step(3, "completed", "Data quality report generated")

                # Step 4: Compliance scan
                task.plan_step(4, "started", "Running compliance scan")
                _sim_tool(agent, "compliance_scan",
                          args={"scope": cust["name"]},
                          result={"gdpr_ok": True, "retention_ok": random.random() > 0.1},
                          category="compliance")
                task.plan_step(4, "completed", "Compliance scan complete")

                # Data migration arc: report stale data issue
                if arcs.is_active("data_migration"):
                    issues.report(agent, "stale-data",
                                  "Stale records found during migration",
                                  category="data_quality")

                # Explicit queue snapshot
                agent.queue_snapshot(
                    random.randint(0, 10),
                    oldest_age_seconds=random.randint(10, 300))

            counters.record_success()
            counters.extras.update({
                "records_synced": synced,
                "duplicates_merged": merged,
                "sync_errors": sync_errs,
            })
        except Exception as e:
            counters.record_failure()
            agent.report_issue(f"Archivist error: {e}", "high", category="internal")

        _cycle_sleep(stop, 6, 14, speed_adj, clock, "archivist")


# ---------------------------------------------------------------------------
# Agent 6: Dispatch - Coordinator
# ---------------------------------------------------------------------------


def _run_dispatch(agent, counters: AgentCounters, speed: float,
                  clock: VirtualClock, arcs: StoryArcEngine,
                  issues: IssueTracker, stop: threading.Event):
    model = AGENT_MODELS["dispatch"]
    cycle_num = 0

    while not stop.is_set():
        arcs.tick()
        cycle_num += 1
        tid = f"coord-{_uid()}"

        try:
            with agent.task(tid, project="lead-gen",
                            type="coordination") as task:
                steps = ["Check queues", "Route incoming work", "Post standup",
                         "Create assignments", "Review escalations"]
                task.plan(f"Coordination cycle #{cycle_num}", steps)

                # Step 0: Check queues
                task.plan_step(0, "started", "Checking agent queues")
                queue_depths = {
                    "scout": random.randint(0, 8),
                    "closer": random.randint(0, 5),
                    "harper": random.randint(0, 6),
                    "campaigner": random.randint(0, 3),
                    "archivist": random.randint(0, 10),
                }
                if arcs.is_active("lead_flood"):
                    queue_depths["scout"] = random.randint(10, 18)
                    queue_depths["closer"] = random.randint(5, 12)
                total_depth = sum(queue_depths.values())
                _sim_tool(agent, "check_agent_queues",
                          args={"agents": list(queue_depths.keys())},
                          result=queue_depths, category="monitoring")
                task.plan_step(0, "completed", f"Total queue depth: {total_depth}")

                # Step 1: Route work
                task.plan_step(1, "started", "Routing incoming work")
                _sim_llm(task, "route_work", model, counters,
                         {"scout_depth": str(queue_depths["scout"]),
                          "closer_depth": str(queue_depths["closer"]),
                          "harper_depth": str(queue_depths["harper"]),
                          "total_depth": str(total_depth)})
                task.plan_step(1, "completed", "Work routed")

                # Step 2: Post standup
                task.plan_step(2, "started", "Posting standup")
                active = arcs.active_arcs()
                arc_note = f" Active arcs: {', '.join(active)}." if active else ""
                _colony_post(
                    agent, "dispatch", "#daily-standup",
                    f"Morning brief: queue depth {total_depth}, "
                    f"{queue_depths['scout']} leads, "
                    f"{queue_depths['harper']} tickets.{arc_note}",
                    mentions=["scout", "closer", "harper", "campaigner", "archivist"])
                _sim_tool(agent, "post_to_loopcolony",
                          args={"topic": "#daily-standup"},
                          result="posted", category="communication")
                task.plan_step(2, "completed", "Standup posted")

                # Step 3: Create assignments
                task.plan_step(3, "started", "Creating assignments")
                if total_depth > 15:
                    agent.todo(f"todo-{_uid()}", "created",
                               "High queue alert: redistribute work",
                               priority="high", source="agent_decision")
                    _colony_post(agent, "dispatch", "#general",
                                 f"Queue depth alert: {total_depth} items. "
                                 f"Redistributing work.", priority="high")
                task.plan_step(3, "completed", "Assignments created")

                # Step 4: Review escalations
                task.plan_step(4, "started", "Reviewing escalations")
                open_esc = random.randint(0, 4)
                if open_esc > 0:
                    _sim_llm(task, "review_escalations", model, counters,
                             {"summary": f"{open_esc} pending escalations from support"})
                    for _ in range(min(open_esc, 2)):
                        decision = random.choice(["approved", "approved", "rejected"])
                        task.approval_received(
                            f"Escalation reviewed: {decision}",
                            approved_by="dispatch", decision=decision)
                task.plan_step(4, "completed",
                               f"{open_esc} escalations reviewed")

            counters.record_success()
            counters.extras.update({
                "agents_online": 6,
                "total_queue_depth": total_depth,
                "open_escalations": open_esc,
            })
        except Exception as e:
            counters.record_failure()
            agent.report_issue(f"Dispatch error: {e}", "high", category="internal")

        _cycle_sleep(stop, 10, 20, speed, clock, "dispatch")


# ---------------------------------------------------------------------------
# Startup Helpers
# ---------------------------------------------------------------------------


def _ensure_projects(endpoint: str, api_key: str):
    """Create all projects if they don't exist."""
    for p in PROJECTS:
        try:
            requests.post(
                f"{endpoint}/v1/projects/",
                json={"name": p["name"], "slug": p["slug"]},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=2,
            )
        except Exception:
            pass


def _emit_startup(agents: dict, hb):
    """Emit config_snapshot, runtime events, and scheduled work for each agent."""
    # Config snapshot per agent
    for aid, ag in agents.items():
        ag.event("custom", payload={
            "kind": "config_snapshot",
            "summary": f"{aid} configuration at startup",
            "data": {
                "agent_id": aid,
                "model": AGENT_MODELS[aid],
                "framework": "brightpath",
                "version": {
                    "scout": "2.3.0", "closer": "1.8.0", "harper": "3.1.0",
                    "campaigner": "1.4.0", "archivist": "2.0.1", "dispatch": "1.2.0",
                }[aid],
            },
        })

    # Scheduled work
    sched = {
        "scout": [
            {"id": "sched-rescore", "name": "Batch lead re-scoring",
             "next_run": _next_run("1h"), "interval": "1h",
             "enabled": True, "last_status": "success"},
            {"id": "sched-stale-cleanup", "name": "Stale lead cleanup",
             "next_run": _next_run("daily"), "interval": "daily",
             "enabled": True, "last_status": "success"},
        ],
        "closer": [
            {"id": "sched-followup-check", "name": "Follow-up reminder check",
             "next_run": _next_run("2h"), "interval": "2h",
             "enabled": True, "last_status": "success"},
        ],
        "harper": [
            {"id": "sched-kb-refresh", "name": "Knowledge base refresh",
             "next_run": _next_run("6h"), "interval": "6h",
             "enabled": True, "last_status": "success"},
        ],
        "campaigner": [
            {"id": "sched-metrics-pull", "name": "Campaign metrics pull",
             "next_run": _next_run("30m"), "interval": "30m",
             "enabled": True, "last_status": "success"},
            {"id": "sched-weekly-report", "name": "Weekly ROI report",
             "next_run": _next_run("weekly"), "interval": "weekly",
             "enabled": True, "last_status": "success"},
        ],
        "archivist": [
            {"id": "sched-crm-sync", "name": "CRM hourly sync",
             "next_run": _next_run("1h"), "interval": "1h",
             "enabled": True, "last_status": "success"},
            {"id": "sched-daily-report", "name": "Daily data quality report",
             "next_run": _next_run("daily"), "interval": "daily",
             "enabled": True, "last_status": "success"},
            {"id": "sched-weekly-compliance", "name": "Weekly compliance scan",
             "next_run": _next_run("weekly"), "interval": "weekly",
             "enabled": True, "last_status": "success"},
        ],
        "dispatch": [
            {"id": "sched-morning-brief", "name": "Morning standup brief",
             "next_run": _next_run("daily"), "interval": "daily",
             "enabled": True, "last_status": "success"},
            {"id": "sched-eod-summary", "name": "End-of-day summary",
             "next_run": _next_run("daily"), "interval": "daily",
             "enabled": True, "last_status": "success"},
        ],
    }
    for aid, items in sched.items():
        if aid in agents:
            agents[aid].scheduled(items=items)

    # Runtime event
    for aid, ag in agents.items():
        ag.event("custom", payload={
            "kind": "runtime",
            "summary": f"{aid} started",
            "data": {"event": "agent_started", "timestamp": datetime.now(timezone.utc).isoformat()},
        })


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

AGENT_DEFS = {
    "scout":      {"type": "sales",       "version": "2.3.0"},
    "closer":     {"type": "sales",       "version": "1.8.0"},
    "harper":     {"type": "support",     "version": "3.1.0"},
    "campaigner": {"type": "marketing",   "version": "1.4.0"},
    "archivist":  {"type": "operations",  "version": "2.0.1"},
    "dispatch":   {"type": "coordinator", "version": "1.2.0"},
}

AGENT_RUNNERS = {
    "scout": _run_scout,
    "closer": _run_closer,
    "harper": _run_harper,
    "campaigner": _run_campaigner,
    "archivist": _run_archivist,
    "dispatch": _run_dispatch,
}


def _load_anthropic_key(explicit_key: str | None) -> str | None:
    """Resolve Anthropic API key: explicit arg > key file > env var."""
    if explicit_key:
        return explicit_key
    # Check key file at sibling directory ../apikeys/api_anthropic.key
    from pathlib import Path
    key_file = Path(__file__).resolve().parent.parent.parent / "apikeys" / "api_anthropic.key"
    if key_file.exists():
        key = key_file.read_text().strip()
        if key:
            return key
    # Fall back to env var (anthropic SDK reads ANTHROPIC_API_KEY automatically)
    import os
    return os.environ.get("ANTHROPIC_API_KEY")


def main():
    global _haiku_client

    parser = argparse.ArgumentParser(description="BrightPath Solutions Simulator")
    parser.add_argument("--endpoint", default="http://localhost:8000",
                        help="HiveBoard API endpoint (default: http://localhost:8000)")
    parser.add_argument("--api-key",
                        default="hb_live_dev000000000000000000000000000000",
                        help="HiveBoard API key")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Simulation speed multiplier (default: 1.0)")
    parser.add_argument("--fast", action="store_true",
                        help="Run at 5x speed")
    parser.add_argument("--agents", type=str, default=None,
                        help="Comma-separated agent subset (e.g. scout,harper,dispatch)")
    parser.add_argument("--haiku", action="store_true",
                        help="Enable real Haiku LLM calls (requires Anthropic API key)")
    parser.add_argument("--anthropic-key", default=None,
                        help="Anthropic API key (or set ANTHROPIC_API_KEY env var)")
    args = parser.parse_args()

    speed = 5.0 if args.fast else args.speed
    agent_filter = (args.agents.split(",") if args.agents
                    else list(AGENT_DEFS.keys()))

    # Initialize Haiku client if requested
    haiku_mode = "off"
    if args.haiku:
        if _anthropic_mod is None:
            print("ERROR: --haiku requires 'anthropic' package. "
                  "Install with: pip install anthropic")
            return
        api_key = _load_anthropic_key(args.anthropic_key)
        if not api_key:
            print("ERROR: --haiku requires an Anthropic API key. Provide via:")
            print("  --anthropic-key sk-...")
            print("  or file: C:\\code\\apikeys\\api_anthropic.key")
            print("  or env: ANTHROPIC_API_KEY")
            return
        _haiku_client = _anthropic_mod.Anthropic(api_key=api_key)
        haiku_mode = "live (claude-haiku-4-5)"

    print(f"BrightPath Solutions Simulator")
    print(f"  Endpoint : {args.endpoint}")
    print(f"  Speed    : {speed}x")
    print(f"  Agents   : {', '.join(agent_filter)}")
    print(f"  LLM mode : {haiku_mode}")
    print()

    # Initialize SDK
    hb = hiveloop.init(
        api_key=args.api_key,
        endpoint=args.endpoint,
        environment="production",
        group="brightpath",
        flush_interval=2.0,
        batch_size=50,
        debug=False,
    )

    # Create projects
    print("Creating projects...")
    _ensure_projects(args.endpoint, args.api_key)

    # Shared engines
    clock = VirtualClock(speed)
    arcs = StoryArcEngine()
    issue_tracker = IssueTracker()
    stop_event = threading.Event()

    # Register agents + counters
    agents = {}
    counters_map = {}
    for aid in agent_filter:
        defn = AGENT_DEFS[aid]
        c = AgentCounters(AGENT_MODELS[aid])
        counters_map[aid] = c

        queue_prov = None
        if aid == "scout":
            def _sq(c=c):
                depth = random.randint(0, 8)
                return {"depth": depth, "oldest_age_seconds": random.randint(5, 120)}
            queue_prov = _sq
        elif aid == "archivist":
            def _aq(c=c):
                depth = random.randint(0, 10)
                return {"depth": depth, "oldest_age_seconds": random.randint(10, 300)}
            queue_prov = _aq

        agents[aid] = hb.agent(
            aid,
            type=defn["type"],
            version=defn["version"],
            framework="brightpath",
            heartbeat_interval=30.0,
            stuck_threshold=300,
            heartbeat_payload=lambda c=c: c.snapshot(),
            queue_provider=queue_prov,
        )

    # Emit startup events
    print("Emitting startup events...")
    _emit_startup(agents, hb)

    # Launch agent threads
    threads = []
    for aid in agent_filter:
        runner = AGENT_RUNNERS[aid]
        t = threading.Thread(
            target=runner,
            args=(agents[aid], counters_map[aid], speed, clock,
                  arcs, issue_tracker, stop_event),
            name=f"agent-{aid}",
            daemon=True,
        )
        t.start()
        threads.append(t)
        print(f"  Started {aid}")

    print(f"\nAll {len(threads)} agents running. Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        stop_event.set()
        for t in threads:
            t.join(timeout=5)
        hiveloop.shutdown(timeout=10)
        print("Done.")


if __name__ == "__main__":
    main()

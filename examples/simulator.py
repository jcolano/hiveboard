#!/usr/bin/env python3
"""HiveLoop Agent Simulator — realistic multi-agent workload generator.

Runs 3 agents with different profiles, generating realistic telemetry:
- lead-qualifier: Sales agent — scores and routes leads, uses LLM heavily
- support-triage: Support agent — categorizes tickets, occasional errors
- data-pipeline: ETL agent — batch processing, queue-driven, scheduled work

Usage:
    python examples/simulator.py                           # defaults: localhost:8000
    python examples/simulator.py --endpoint http://host:port --api-key hb_live_xxx
    python examples/simulator.py --fast                    # 5x speed for demo
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time

# Ensure sdk.hiveloop is importable as hiveloop
sys.path.insert(0, ".")
import sdk  # noqa: E402 — triggers the sys.modules alias

import hiveloop  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("simulator")

# ---------------------------------------------------------------------------
# LLM call templates (realistic model names and token ranges)
# ---------------------------------------------------------------------------

LLM_MODELS = [
    ("claude-sonnet-4-20250514", 0.003, 0.015),     # cost per 1k in, per 1k out
    ("claude-haiku-4-5-20251001", 0.00025, 0.00125),
    ("gpt-4o", 0.005, 0.015),
    ("gpt-4o-mini", 0.00015, 0.0006),
]

LLM_CALL_NAMES = [
    "reasoning", "classification", "extraction", "summarization",
    "evaluation", "routing", "generation", "validation",
]


def _sim_llm_call(task_or_agent, name: str | None = None):
    """Simulate an LLM call with realistic parameters."""
    model_name, cost_in, cost_out = random.choice(LLM_MODELS)
    tokens_in = random.randint(200, 4000)
    tokens_out = random.randint(50, 1200)
    cost = round((tokens_in / 1000) * cost_in + (tokens_out / 1000) * cost_out, 6)
    duration_ms = random.randint(300, 5000)
    call_name = name or random.choice(LLM_CALL_NAMES)

    task_or_agent.llm_call(
        call_name,
        model_name,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
        duration_ms=duration_ms,
        prompt_preview=f"You are a {call_name} assistant. Analyze the following...",
        response_preview=f"Based on my analysis, I recommend...",
    )


def _sim_sleep(base: float, speed: float):
    """Sleep with speed multiplier."""
    time.sleep(base / speed)


# ---------------------------------------------------------------------------
# Agent 1: Lead Qualifier (Sales)
# ---------------------------------------------------------------------------

def run_lead_qualifier(hb: hiveloop.HiveBoard, speed: float):
    """Simulate a sales lead qualification agent."""
    agent = hb.agent(
        "lead-qualifier",
        type="sales",
        version="2.1.0",
        framework="custom",
        heartbeat_interval=max(5, 30 / speed),
        stuck_threshold=300,
        queue_provider=lambda: {
            "depth": random.randint(0, 8),
            "oldest_age_seconds": random.randint(0, 300),
        },
    )

    # Report scheduled work
    agent.scheduled(items=[
        {"id": "sched-rescore", "name": "Batch lead re-scoring",
         "next_run": "2026-02-12T15:00:00Z", "interval": "1h",
         "enabled": True, "last_status": "success"},
        {"id": "sched-cleanup", "name": "Stale lead cleanup",
         "next_run": "2026-02-13T08:00:00Z", "interval": "daily",
         "enabled": True, "last_status": None},
    ])

    lead_num = 4800
    while True:
        lead_num += 1
        task_id = f"task_lead-{lead_num}"
        project = "sales-pipeline"

        try:
            with agent.task(task_id, project=project, type="lead_processing") as task:
                # Create plan
                steps = ["Score lead", "Enrich data", "Route to rep"]
                task.plan(f"Process lead #{lead_num}", steps)

                # Step 0: Score lead
                task.plan_step(0, "started", "Scoring lead")
                _sim_sleep(0.5, speed)

                @agent.track("score_lead")
                def score_lead():
                    _sim_llm_call(task, "lead_scoring")
                    _sim_sleep(0.3, speed)
                    return random.randint(10, 95)

                score = score_lead()
                task.plan_step(0, "completed", f"Lead scored {score}", turns=1, tokens=random.randint(1500, 4000))

                # Step 1: Enrich data
                task.plan_step(1, "started", "Enriching lead data")
                _sim_sleep(0.3, speed)

                # Occasional enrichment failure (15% chance)
                if random.random() < 0.15:
                    task.plan_step(1, "failed", "Enrichment API timeout")
                    agent.report_issue(
                        summary="Clearbit API timeout",
                        severity=random.choice(["medium", "high"]),
                        issue_id="clearbit-timeout",
                        category="connectivity",
                        context={"api": "clearbit", "timeout_ms": 5000},
                    )
                    task.event("retry_started", payload={
                        "summary": "Retrying enrichment",
                        "data": {"attempt": 1, "backoff_seconds": 2.0},
                    })
                    _sim_sleep(0.5, speed)
                    task.plan_step(1, "completed", "Enrichment succeeded on retry")
                    agent.resolve_issue("Clearbit API recovered", issue_id="clearbit-timeout")
                else:
                    @agent.track("enrich_lead")
                    def enrich():
                        _sim_llm_call(task, "data_extraction")
                        _sim_sleep(0.2, speed)

                    enrich()
                    task.plan_step(1, "completed", "Lead enriched", turns=1, tokens=random.randint(800, 2000))

                # Step 2: Route to rep
                task.plan_step(2, "started", "Routing lead")
                _sim_sleep(0.2, speed)

                @agent.track("route_lead")
                def route():
                    _sim_llm_call(task, "routing")

                route()
                task.plan_step(2, "completed", f"Routed to rep (score={score})")

            logger.info("[lead-qualifier] Completed %s (score=%d)", task_id, score)

        except Exception as e:
            logger.error("[lead-qualifier] Task %s failed: %s", task_id, e)

        _sim_sleep(random.uniform(2, 6), speed)


# ---------------------------------------------------------------------------
# Agent 2: Support Triage
# ---------------------------------------------------------------------------

def run_support_triage(hb: hiveloop.HiveBoard, speed: float):
    """Simulate a support ticket triage agent."""
    agent = hb.agent(
        "support-triage",
        type="support",
        version="1.5.0",
        framework="custom",
        heartbeat_interval=max(5, 30 / speed),
        heartbeat_payload=lambda: {
            "kind": "heartbeat_status",
            "summary": "Triage engine healthy",
            "data": {"uptime_seconds": int(time.time()) % 86400, "memory_mb": random.randint(180, 350)},
        },
    )

    # Initial TODOs
    agent.todo("todo-kb-update", "created", "Update knowledge base with new product FAQ",
               priority="normal", source="agent_decision")

    ticket_num = 1000
    while True:
        ticket_num += 1
        task_id = f"ticket-{ticket_num}"

        try:
            with agent.task(task_id, project="customer-support", type="ticket_triage") as task:
                # Classify ticket
                @agent.track("classify_ticket")
                def classify():
                    _sim_llm_call(task, "classification")
                    _sim_sleep(0.3, speed)
                    return random.choice(["billing", "technical", "feature_request", "bug_report"])

                category = classify()

                # Generate response
                @agent.track("draft_response")
                def draft():
                    _sim_llm_call(task, "generation")
                    _sim_sleep(0.4, speed)

                draft()

                # 10% chance of escalation
                if random.random() < 0.10:
                    task.event("escalated", payload={
                        "summary": f"Ticket #{ticket_num} escalated — complex {category} issue",
                        "data": {"assigned_to": "senior-support"},
                    })
                    task.event("approval_requested", payload={
                        "summary": "Approval needed for account credit",
                        "data": {"approver": "support-lead"},
                    })
                    _sim_sleep(1, speed)
                    task.event("approval_received", payload={
                        "summary": "Credit approved by support-lead",
                        "data": {"approved_by": "support-lead", "decision": "approved"},
                    })

                # 5% chance of failure — inside @track so it emits action_failed
                if random.random() < 0.05:
                    @agent.track("submit_to_crm")
                    def submit_crm():
                        raise RuntimeError(f"CRM API error processing ticket-{ticket_num}")

                    submit_crm()

            logger.info("[support-triage] Completed %s (%s)", task_id, category)

        except RuntimeError as e:
            logger.error("[support-triage] Task %s failed: %s", task_id, e)
            agent.report_issue(
                summary=str(e),
                severity="high",
                category="connectivity",
            )

        _sim_sleep(random.uniform(3, 8), speed)


# ---------------------------------------------------------------------------
# Agent 3: Data Pipeline
# ---------------------------------------------------------------------------

def run_data_pipeline(hb: hiveloop.HiveBoard, speed: float):
    """Simulate a data pipeline / ETL agent."""
    agent = hb.agent(
        "data-pipeline",
        type="etl",
        version="3.0.1",
        framework="custom",
        heartbeat_interval=max(5, 30 / speed),
        queue_provider=lambda: {
            "depth": random.randint(0, 15),
            "oldest_age_seconds": random.randint(0, 600),
            "items": [
                {"id": f"batch-{random.randint(100,999)}", "priority": "normal",
                 "source": "scheduled", "summary": f"Process batch #{random.randint(1,50)}"}
                for _ in range(random.randint(0, 3))
            ],
        },
    )

    # Scheduled work
    agent.scheduled(items=[
        {"id": "sched-etl-hourly", "name": "Hourly ETL sync",
         "next_run": "2026-02-12T15:00:00Z", "interval": "1h",
         "enabled": True, "last_status": "success"},
        {"id": "sched-daily-report", "name": "Daily analytics report",
         "next_run": "2026-02-13T06:00:00Z", "interval": "daily",
         "enabled": True, "last_status": "success"},
        {"id": "sched-weekly-cleanup", "name": "Weekly data cleanup",
         "next_run": "2026-02-17T02:00:00Z", "interval": "weekly",
         "enabled": True, "last_status": None},
    ])

    batch_num = 200
    while True:
        batch_num += 1
        task_id = f"etl-batch-{batch_num}"

        try:
            with agent.task(task_id, project="data-warehouse", type="etl_batch") as task:
                num_steps = random.randint(3, 6)
                step_names = ["Extract from source", "Validate schema", "Transform records",
                              "Deduplicate", "Load to warehouse", "Update indexes"][:num_steps]
                task.plan(f"ETL batch #{batch_num}", step_names)

                for i, step_name in enumerate(step_names):
                    task.plan_step(i, "started", step_name)
                    _sim_sleep(random.uniform(0.2, 0.8), speed)

                    # Some steps use LLM for data quality
                    if step_name in ("Validate schema", "Deduplicate"):
                        @agent.track(step_name.lower().replace(" ", "_"))
                        def process_step():
                            _sim_llm_call(task, "validation")
                            _sim_sleep(0.2, speed)

                        process_step()

                    # 8% chance of step failure with retry
                    if random.random() < 0.08:
                        task.plan_step(i, "failed", f"{step_name} — connection reset")
                        task.event("retry_started", payload={
                            "summary": f"Retry step {i}: {step_name}",
                            "data": {"attempt": 1, "backoff_seconds": 1.0},
                        })
                        _sim_sleep(0.3, speed)

                    task.plan_step(i, "completed", step_name,
                                   turns=random.randint(1, 3),
                                   tokens=random.randint(500, 3000))

                # Report queue state explicitly at end of batch
                agent.queue_snapshot(
                    depth=random.randint(0, 10),
                    oldest_age_seconds=random.randint(0, 300),
                )

            logger.info("[data-pipeline] Completed %s (%d steps)", task_id, num_steps)

        except Exception as e:
            logger.error("[data-pipeline] Task %s failed: %s", task_id, e)

        _sim_sleep(random.uniform(4, 10), speed)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="HiveLoop Agent Simulator")
    parser.add_argument("--endpoint", default="http://localhost:8000",
                        help="HiveBoard API endpoint")
    parser.add_argument("--api-key", default="hb_live_simulator_key_00000000",
                        help="API key")
    parser.add_argument("--fast", action="store_true",
                        help="Run at 5x speed for demos")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Speed multiplier (higher = faster)")
    args = parser.parse_args()

    speed = 5.0 if args.fast else args.speed

    logger.info("Starting simulator: endpoint=%s, speed=%.1fx", args.endpoint, speed)

    hb = hiveloop.init(
        api_key=args.api_key,
        endpoint=args.endpoint,
        environment="production",
        group="demo",
        flush_interval=2.0,
        batch_size=50,
        debug=False,
    )

    import threading

    threads = [
        threading.Thread(target=run_lead_qualifier, args=(hb, speed), daemon=True, name="lead-qualifier"),
        threading.Thread(target=run_support_triage, args=(hb, speed), daemon=True, name="support-triage"),
        threading.Thread(target=run_data_pipeline, args=(hb, speed), daemon=True, name="data-pipeline"),
    ]

    for t in threads:
        t.start()
        logger.info("Started agent: %s", t.name)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down simulator...")
        hiveloop.shutdown(timeout=10)
        logger.info("Done.")


if __name__ == "__main__":
    main()

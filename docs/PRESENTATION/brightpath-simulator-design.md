# BrightPath Digital — The Ultimate HiveBoard Simulator

## The Story

**BrightPath Digital** is a small AI-native agency that helps SaaS startups grow. Founded by a solo entrepreneur, the company runs entirely on five AI agents supervised by one human operator. The agents handle everything: qualifying incoming leads, supporting existing customers, running marketing campaigns, keeping the CRM clean, and coordinating work across the team through their internal platform, **LoopColony**.

Every day is a loop. Leads come in. Tickets arrive. Campaigns run. Data gets cleaned. The team talks in LoopColony. Some days are smooth; some days the CRM API goes down, a customer threatens to churn, or a marketing email gets flagged as spam. The agents handle it — or escalate to the human when they can't.

This simulator makes all of that visible on HiveBoard.

---

## The Five Agents

| Agent ID | Name | Role | Type | Project | Personality |
|----------|------|------|------|---------|-------------|
| `scout` | **Scout** | Sales Development Rep | `sales` | `sales-pipeline` | Energetic, optimistic. Scores leads fast, follows up relentlessly. |
| `sage` | **Sage** | Customer Support Lead | `support` | `customer-success` | Patient, thorough. Resolves most tickets solo, escalates wisely. |
| `spark` | **Spark** | Marketing Coordinator | `marketing` | `marketing-ops` | Creative, data-driven. Runs campaigns, analyzes metrics, A/B tests. |
| `ledger` | **Ledger** | CRM & Operations Manager | `operations` | `crm-operations` | Meticulous, reliable. Keeps data clean, generates reports, syncs integrations. |
| `relay` | **Relay** | Comms & Collaboration Hub | `communications` | `loop-colony` | Organized, proactive. Manages LoopColony, routes messages, assigns work. |

---

## Projects

| Slug | Name | Primary Agent | Description |
|------|------|---------------|-------------|
| `sales-pipeline` | Sales Pipeline | Scout | Lead qualification, scoring, follow-ups, conversions |
| `customer-success` | Customer Success | Sage | Support tickets, escalations, CSAT, knowledge base |
| `marketing-ops` | Marketing Operations | Spark | Campaigns, email blasts, analytics, A/B testing |
| `crm-operations` | CRM Operations | Ledger | Data hygiene, deduplication, reports, integrations |
| `loop-colony` | LoopColony | Relay | Internal comms: topics, posts, comments, assignments |

---

## Agent Details

### Scout — Sales Development Rep

**Scheduled work:**
- Hourly: Check for new inbound leads from website forms
- Every 2 hours: Follow-up sequence for warm leads
- Daily: Lead scoring re-calibration

**Task types:**
- `lead_qualification` — Score a new lead (1-100), enrich data, decide: nurture / fast-track / disqualify
- `lead_followup` — Draft personalized follow-up email, log in CRM
- `lead_handoff` — Prepare qualified lead package for human sales call

**Tools/Skills (tracked actions):**
- `score_lead` — LLM analyzes company size, industry, intent signals
- `enrich_company` — Pull firmographic data (simulated API)
- `draft_email` — LLM generates personalized outreach email
- `search_linkedin` — Simulated LinkedIn lookup for decision makers
- `update_crm` — Push lead data to CRM (occasional API failures)
- `check_duplicates` — Verify lead doesn't already exist

**LLM calls:**
- `lead_scoring` — "Analyze this lead and score 1-100 based on..."
- `email_generation` — "Draft a personalized follow-up email for..."
- `company_research` — "Summarize what this company does and their likely needs..."
- `intent_classification` — "Classify the intent level of this lead: hot/warm/cold..."

**Failure modes:**
- Enrichment API timeout (15%)
- CRM API errors (8%)
- Duplicate lead detected (10%)

**Email templates (prompt previews):**
```
INITIAL_OUTREACH: "Hi {first_name}, I noticed {company} is scaling its {department}. 
  BrightPath has helped similar companies reduce churn by 30%..."
FOLLOWUP_WARM: "Hi {first_name}, just checking in on my previous note. I'd love to 
  show you how {company} could benefit from..."  
FOLLOWUP_COLD: "Hi {first_name}, I know you're busy. Quick question: is {pain_point} 
  still a priority for {company} this quarter?"
HANDOFF_INTERNAL: "Qualified lead ready: {company} ({score}/100). Decision maker: 
  {contact}. Key pain points: {pain_points}. Recommended approach: {strategy}"
```

---

### Sage — Customer Support Lead

**Scheduled work:**
- Every 30 min: Check unresolved ticket queue
- Daily: Generate CSAT summary report
- Weekly: Update knowledge base from resolved tickets

**Task types:**
- `ticket_resolution` — Classify, research, respond to a support ticket
- `ticket_escalation` — Complex issue requiring human or cross-agent help
- `csat_survey` — Follow up on resolved tickets for satisfaction score
- `kb_update` — Add new solution to knowledge base from resolved ticket

**Tools/Skills (tracked actions):**
- `classify_ticket` — LLM categorizes: billing / technical / bug / feature_request / account
- `search_knowledge_base` — Look up existing solutions
- `draft_response` — LLM generates customer-facing response
- `escalate_to_human` — Route to human operator with context summary
- `check_account_status` — Verify customer's subscription and history
- `apply_credit` — Issue account credit (requires approval)
- `log_resolution` — Record resolution in ticketing system

**LLM calls:**
- `ticket_classification` — "Classify this support ticket into one of: billing, technical..."
- `solution_lookup` — "Given this issue, search our knowledge base for relevant solutions..."
- `response_generation` — "Draft a helpful, empathetic response to this customer..."
- `escalation_summary` — "Summarize this ticket for escalation: key issue, steps tried..."
- `sentiment_analysis` — "Analyze the customer's sentiment: frustrated/neutral/satisfied..."

**Failure modes:**
- Knowledge base search returns no results (20%)
- Account API timeout (5%)
- Customer sentiment is angry — requires careful response (15%)

**Customer scenarios (randomized):**
```
BILLING: "I was charged twice for my subscription this month."
TECHNICAL: "The API keeps returning 503 errors since yesterday."
BUG: "When I export to CSV, the dates are formatted incorrectly."
FEATURE: "Can you add webhook support for real-time notifications?"
ACCOUNT: "I need to transfer ownership of our team account."
URGENT: "Our entire production pipeline is down, we need immediate help."
CHURN_RISK: "We're evaluating alternatives. Your competitor offers X for less."
```

**Email templates (response previews):**
```
RESOLUTION: "Hi {name}, thanks for reaching out. I've looked into this and {solution}. 
  Your {resource} should now be {expected_state}. Let me know if you need anything else."
ESCALATION_ACK: "Hi {name}, I understand this is urgent. I've escalated your case to 
  our senior team with full context. You'll hear back within {sla_hours} hours."
CREDIT_APPLIED: "Hi {name}, I've applied a ${amount} credit to your account for the 
  inconvenience. This will reflect on your next billing cycle."
CHURN_SAVE: "Hi {name}, I completely understand your concerns. Let me connect you with 
  our team to discuss how we can better serve {company}'s needs..."
```

---

### Spark — Marketing Coordinator

**Scheduled work:**
- Daily: Campaign performance metrics collection
- Every 3 hours: Social media engagement check
- Weekly: A/B test results analysis
- Monthly: Campaign ROI report generation

**Task types:**
- `campaign_launch` — Create and launch email/social campaign
- `campaign_analysis` — Analyze campaign performance metrics
- `ab_test` — Set up and evaluate A/B test variants
- `content_creation` — Draft blog post, social media copy, or ad copy
- `audience_segmentation` — Segment audience for targeted campaigns

**Tools/Skills (tracked actions):**
- `generate_copy` — LLM creates marketing copy
- `analyze_metrics` — Process campaign analytics data
- `segment_audience` — LLM identifies audience segments
- `schedule_campaign` — Set up campaign delivery schedule
- `create_ab_variants` — Generate A/B test variants
- `evaluate_ab_results` — Statistical analysis of A/B test outcomes
- `generate_report` — Create performance report

**LLM calls:**
- `copy_generation` — "Write compelling email copy for a campaign about..."
- `subject_line_generation` — "Generate 5 email subject lines for..."
- `audience_analysis` — "Analyze this audience segment and recommend targeting..."
- `performance_summary` — "Summarize these campaign metrics and recommend optimizations..."
- `content_ideation` — "Generate 3 blog post ideas about..."

**Campaign types (randomized):**
```
PRODUCT_LAUNCH: "Introducing our new {feature} — here's how it changes everything"
NURTURE_SEQUENCE: "Week {n} of your onboarding journey: {topic}"
REENGAGEMENT: "We miss you! Here's what's new at BrightPath"
SEASONAL: "{season} special: Get {discount}% off annual plans"
CASE_STUDY: "How {customer} achieved {result} with BrightPath"
WEBINAR: "Join us: {topic} — Live on {date}"
```

**Metrics (simulated):**
- Open rate: 15-45%
- Click-through rate: 2-12%
- Conversion rate: 0.5-5%
- Unsubscribe rate: 0.1-2%
- Bounce rate: 1-8%

---

### Ledger — CRM & Operations Manager

**Scheduled work:**
- Hourly: Sync CRM with external data sources
- Every 4 hours: Duplicate detection scan
- Daily: Data quality report
- Weekly: Full CRM audit and cleanup

**Task types:**
- `crm_sync` — Sync records between CRM and external sources
- `data_cleanup` — Deduplicate, validate, normalize records
- `report_generation` — Generate operational reports
- `integration_check` — Verify health of integrations (Stripe, HubSpot, etc.)
- `contact_enrichment` — Enrich contact records with missing data

**Tools/Skills (tracked actions):**
- `sync_records` — Pull/push records between systems
- `detect_duplicates` — LLM-assisted fuzzy matching
- `validate_records` — Check data quality rules
- `normalize_fields` — Standardize formats (phone, address, etc.)
- `generate_report` — Compile operational metrics
- `check_integration` — Health check external API connections
- `merge_records` — Merge duplicate records

**LLM calls:**
- `duplicate_detection` — "Compare these two records and determine if they're duplicates..."
- `data_extraction` — "Extract structured contact data from this unstructured text..."
- `anomaly_detection` — "Review these records for data quality anomalies..."
- `report_narrative` — "Generate a summary narrative for this operational report..."

**Failure modes:**
- CRM API rate limiting (10%)
- Integration sync failure (8%)
- Merge conflict — ambiguous duplicate (5%)
- Data validation failure — corrupt records (3%)

---

### Relay — Communications & Collaboration Hub

**Scheduled work:**
- Every 15 min: Check for unread LoopColony messages
- Hourly: Generate activity digest for LoopColony
- Daily: Create daily standup summary
- Weekly: Archive inactive topics

**Task types:**
- `create_topic` — Create a new discussion topic in LoopColony
- `post_update` — Post an update inside a topic
- `comment_on_post` — Add a comment to an existing post
- `assign_task` — Assign work from one agent to another
- `daily_digest` — Generate and post daily activity summary
- `route_message` — Route a message to the right agent/channel

**Tools/Skills (tracked actions):**
- `create_lc_topic` — Create LoopColony topic
- `post_to_topic` — Write and publish a post
- `add_comment` — Add comment to a post
- `assign_to_agent` — Create and route assignment
- `generate_digest` — LLM summarizes day's activity
- `route_to_channel` — Determine correct channel for message
- `archive_topic` — Archive stale topics

**LLM calls:**
- `digest_generation` — "Summarize today's activities across all agents..."
- `message_routing` — "Determine which agent should handle this message..."
- `standup_summary` — "Generate a standup-style summary of each agent's progress..."
- `topic_categorization` — "Categorize this topic: ops / sales / support / marketing / general..."

**LoopColony Topics (rotating pool):**
```
TOPICS:
- "#daily-standup" — Daily activity summaries
- "#leads-pipeline" — Scout posts lead updates here
- "#support-queue" — Sage posts ticket summaries
- "#campaign-results" — Spark posts campaign metrics
- "#data-health" — Ledger posts CRM status reports
- "#announcements" — Cross-team announcements
- "#bugs-and-issues" — Technical issues and resolutions
- "#ideas-and-feedback" — Agent suggestions for improvements
```

**Assignment patterns:**
```
SCOUT → SAGE: "New customer from qualified lead needs onboarding support"
SAGE → SCOUT: "Churning customer identified — attempt win-back outreach"
SPARK → SCOUT: "Campaign generated 12 new MQLs — prioritize qualification"
LEDGER → SAGE: "Found 3 duplicate customer accounts — need resolution"
RELAY → ALL: "Daily digest posted — 47 tasks completed, 3 issues open"
SAGE → SPARK: "Feature request trending — 8 tickets this week about webhooks"
LEDGER → SPARK: "Email bounce rate spike detected on last campaign segment"
```

---

## Simulation Loop Architecture

### Time Model

The simulator runs in **cycles**. Each cycle represents roughly one "business hour" of compressed activity. A full day consists of 8 cycles. After 8 cycles, the simulator loops back to the start of a new day.

```
CYCLE STRUCTURE (each ~60 seconds real time at 1x speed):

  [CYCLE START]
     ├─ Relay: Daily digest (if cycle 0)
     ├─ Scout: 2-3 lead tasks
     ├─ Sage: 2-4 ticket tasks
     ├─ Spark: 1 campaign task (every other cycle)
     ├─ Ledger: 1-2 operations tasks
     ├─ Relay: 3-5 LoopColony posts/comments
     ├─ [CROSS-AGENT INTERACTIONS: assignments, escalations]
  [CYCLE END]
```

### Randomization Strategy

**Per-cycle randomization:**
- Number of tasks per agent: ±1 from base count
- Task types: weighted random from agent's type pool
- Failure probability: per-action configurable rates
- Customer names/companies: drawn from pools
- Metric values: gaussian around realistic means

**Story arcs (multi-cycle):**
Every 8 cycles (one "day"), the simulator may trigger a **story arc** — a multi-cycle event that affects multiple agents:

| Arc | Probability | Duration | Effect |
|-----|-------------|----------|--------|
| `api_outage` | 10% per day | 2-3 cycles | CRM/enrichment APIs fail. Ledger reports issues. Sage/Scout retry. |
| `campaign_spike` | 15% per day | 1-2 cycles | Successful campaign → Scout gets flood of leads. |
| `angry_customer` | 20% per day | 2 cycles | High-value customer escalation → Sage + Scout + Relay involved. |
| `data_migration` | 5% per day | 3-4 cycles | Ledger does heavy cleanup. Other agents see stale data warnings. |
| `product_launch` | 10% per day | Full day | All agents in high-activity mode. Spark launches campaign, Scout gets leads, etc. |

---

## Cross-Agent Interactions

The simulator includes realistic inter-agent workflows:

1. **Lead → Customer pipeline:** Scout qualifies lead → Relay assigns onboarding to Sage → Sage creates support ticket → Ledger adds to CRM
2. **Campaign → Sales pipeline:** Spark launches campaign → metrics show MQLs → Relay notifies Scout → Scout prioritizes new leads
3. **Support → Product feedback:** Sage sees recurring feature request → Relay posts in #ideas-and-feedback → Spark tracks for messaging
4. **Data quality alert:** Ledger finds issues → Relay alerts affected agents → agents acknowledge in LoopColony
5. **Escalation chain:** Sage can't resolve → escalates to human → Relay posts in #support-queue → resolution logged

---

## Data Pools

### People (Customers/Leads)

```python
FIRST_NAMES = ["Alex", "Jordan", "Casey", "Morgan", "Taylor", "Riley", "Quinn", "Avery",
               "Dakota", "Reese", "Cameron", "Peyton", "Skyler", "Finley", "Rowan", "Blake"]

LAST_NAMES = ["Chen", "Patel", "Smith", "Garcia", "Kim", "Nguyen", "Okafor", "Müller",
              "Santos", "Johansson", "Dubois", "Tanaka", "Ali", "Cohen", "Berg", "Rivera"]

COMPANIES = ["NovaTech", "PeakFlow", "Luminary AI", "ClearStack", "DataForge", "SkylineOps",
             "Meridian Labs", "Quantum Pulse", "EverBridge", "Nexus Cloud", "ApexWare",
             "Cobalt Systems", "TerraScale", "ZenithPlatform", "PrismLogic", "VaultStream"]

INDUSTRIES = ["fintech", "healthtech", "edtech", "devtools", "cybersecurity", "martech",
              "logistics", "e-commerce", "HR tech", "legal tech"]

PAIN_POINTS = ["high churn rate", "slow onboarding", "poor data visibility",
               "manual reporting overhead", "integration complexity", "scaling support team",
               "lead conversion bottleneck", "campaign attribution gaps"]
```

### Support Ticket Subjects

```python
TICKET_SUBJECTS = {
    "billing": [
        "Double charge on invoice #{num}",
        "Need to update payment method",
        "Requesting refund for downtime period",
        "Invoice doesn't match agreed pricing",
        "Cancel auto-renewal on annual plan",
    ],
    "technical": [
        "API returning 503 errors intermittently",
        "Webhook deliveries failing since {date}",
        "Dashboard loading extremely slow (>30s)",
        "SSO integration broken after update",
        "Rate limiting too aggressive for our usage",
    ],
    "bug": [
        "CSV export missing columns since v2.1",
        "Date picker shows wrong timezone",
        "Search returns stale results",
        "Mobile app crashes on report generation",
        "Notification emails arriving 4+ hours late",
    ],
    "feature_request": [
        "Need webhook support for real-time events",
        "Request: bulk import via API",
        "Add role-based access control",
        "Need audit log for compliance",
        "Request: custom dashboard widgets",
    ],
    "account": [
        "Transfer account ownership to new admin",
        "Need to merge two team accounts",
        "Requesting data export under GDPR",
        "Upgrade from starter to enterprise plan",
        "Add 5 new team member seats",
    ],
}
```

---

## Dashboard Visibility Map

What each simulator component lights up on HiveBoard:

| Simulator Feature | Dashboard Component |
|---|---|
| 5 agents running concurrently | The Hive — 5 agent cards with heartbeats |
| Task context managers | Task Table — rows with status, duration, type |
| Tracked actions (@track) | Timeline — action nodes with durations |
| LLM calls (task.llm_call) | Cost Explorer — model breakdown, token usage |
| Escalations (task.escalate) | Activity Stream — escalation events |
| Approvals (request/received) | Timeline — WAITING badge, approval nodes |
| Issues (report/resolve) | Agent cards — issue badges, Pipeline tab |
| TODOs (todo lifecycle) | Pipeline tab — work items |
| Queue snapshots | Pipeline tab — queue depth charts |
| Scheduled work | Pipeline tab — scheduled items |
| Plans (plan + plan_step) | Timeline — progress bars above actions |
| LoopColony posts | Custom events in Stream |
| Cross-agent assignments | Custom events with agent references |
| Email templates | prompt_preview / response_preview fields |
| Campaign metrics | Custom event payloads with metrics data |
| Retry attempts | Timeline — retry nodes |
| Failure + recovery | Action failed → retry → success patterns |

---

## File Structure

```
simulator_ultimate.py          — Main simulator file
├── Data pools (names, companies, tickets, templates)
├── Email/prompt templates
├── Agent runner functions (one per agent)
├── Story arc engine
├── Cross-agent interaction manager
├── LoopColony simulation layer
└── Main loop with threading
```

---

## Speed Modes

| Flag | Speed | Cycle Duration | Use Case |
|------|-------|---------------|----------|
| (default) | 1x | ~60 seconds | Background running, realistic pacing |
| `--fast` | 5x | ~12 seconds | Live demos |
| `--turbo` | 20x | ~3 seconds | Quick data population |
| `--speed N` | Nx | 60/N seconds | Custom |

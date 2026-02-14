# HiveBoard — 3-Minute Demo Video
## Definitive Shot-by-Shot Production Script

**Runtime:** 3:00 (180 seconds)
**Speaker:** Juan (on camera + voiceover throughout)
**Format:** You're talking the entire time. Visuals change between your face and screen content.

---

## VISUAL ASSETS INVENTORY

| ID | Asset | Source | Used in |
|----|-------|--------|---------|
| **CAM** | Juan on camera | Recording | Beats 1, 7, 8, 9, 10 |
| **DASH** | Live dashboard | hiveboard.net/static/... | Beats 2, 3, 4, 5 |
| **COST** | Cost Explorer view | Dashboard tab | Beat 5 |
| **DOCS** | Documentation site | hiveboard.net/docs/user-manual.html | Beat 6 |
| **WEB** | Website homepage | hiveboard.net | Beat 6 |
| **REPO** | GitHub README | github.com/jcolano/hiveboard | Beat 6 |
| **OP-J** | THE JOURNEY One-Pager | HTML file (screenshot/screen rec) | Beat 8 |
| **OP-H** | The Hive Method One-Pager | HTML file (screenshot/screen rec) | Beat 8 |
| **OP-W** | What HiveBoard Sees One-Pager | HTML file (screenshot/screen rec) | Beat 9 |
| **LOGO** | HiveBoard logo + end card | hiveboard.net/logo/hiveboard-logo.png | Beat 11 |
| **OVR** | Text overlays (stats, quotes) | Added in editing | Various |

---

## THE SCRIPT

---

### BEAT 1 — THE HOOK
⏱ **0:00–0:15** (15 sec)

| Time | Visual | Audio (Juan speaking) |
|------|--------|----------------------|
| 0:00 | **CAM** — You, direct to lens. Clean background. Good light. | "If you're deploying AI agents, you need a tool that doesn't exist yet." |
| 0:04 | **CAM** — Same shot, natural delivery | "Every team hits the same wall — the agent works on your laptop, then goes to production, and you go blind." |
| 0:09 | **CAM** — Slight lean in or gesture | "Is it stuck? Why did it fail? Why is it costing forty dollars an hour when it should cost eight?" |
| 0:13 | **CAM** — Confident, direct | "We built that missing tool. This is HiveBoard." |

**Cut to screen on "This is HiveBoard."**

---

### BEAT 2 — THE REVEAL
⏱ **0:15–0:25** (10 sec)

| Time | Visual | Audio |
|------|--------|-------|
| 0:15 | **DASH** — Full Mission Control. 5 agents visible. Heartbeats pulsing. Activity stream flowing. Let it breathe for 2 sec before talking. | *(1-2 sec of just the dashboard, no voice — let the visual land)* |
| 0:17 | **DASH** — Slow hover across agent cards: Scout, Sage, Spark, Ledger, Relay | "This is live, right now. You're looking at BrightPath Digital — a company running five AI agents." |
| 0:21 | **DASH** — Continue hovering across cards | "Scout handles sales. Sage runs support. Spark does marketing. Ledger keeps the CRM clean. Relay coordinates them all." |

---

### BEAT 3 — THE GLANCE
⏱ **0:25–0:35** (10 sec)

| Time | Visual | Audio |
|------|--------|-------|
| 0:25 | **DASH** — Pull back to show full dashboard. Point cursor at attention badge. | "The design principle is the two-second glance test." |
| 0:28 | **DASH** — Hover over Stats Ribbon (active, stuck, error counters) | "Red floats to the top. Green sinks. The attention badge tells me if something needs me." |
| 0:32 | **DASH** — Hover over mini-charts briefly | "I glance at this screen and know the state of the entire fleet." |

---

### BEAT 4 — THE INVESTIGATION
⏱ **0:35–1:05** (30 sec)

This is the heart of the demo. Slow down. Let each click breathe.

| Time | Visual | Audio |
|------|--------|-------|
| 0:35 | **DASH** — Move cursor to an agent card in error/stuck state (ideally Sage) | "Sage has a problem. I click in." |
| 0:38 | **DASH** — Click agent. Timeline view opens. Let it render for 1 sec. | *(brief pause as timeline appears)* |
| 0:39 | **DASH** — Timeline visible. Slowly hover left to right across nodes. | "Here's the task timeline — every step this agent took, rendered as a visual story." |
| 0:44 | **DASH** — Hover over initial nodes (classification, KB search) | "The ticket classification... the knowledge base search..." |
| 0:47 | **DASH** — Hover over LLM call node (purple) | "...the LLM call that drafted the response..." |
| 0:50 | **DASH** — Hover over red failure node | "...and right here — the failure." |
| 0:52 | **DASH** — Click the failed node. Detail panel opens. | "Email delivery failed. The agent retried twice, then escalated to a human." |
| 0:56 | **DASH** — Point at error message and retry nodes in detail | "I can see the exact error, the exact moment, every retry attempt." |
| 1:00 | **DASH** — Click permalink button | "And this timeline has a permalink. Someone asks 'what happened?' in Slack —" |
| 1:03 | **DASH** — Show copied URL or permalink view | "— paste the link. Full investigation, fifteen seconds." |

---

### BEAT 5 — THE COST REVEAL
⏱ **1:05–1:22** (17 sec)

| Time | Visual | Audio |
|------|--------|-------|
| 1:05 | **DASH** — Click "Costs" tab. Cost Explorer loads. | "Now the view that pays for itself." |
| 1:08 | **COST** — Full Cost Explorer visible. Tables loading. | "Cost Explorer. Every dollar, broken down by model and by agent." |
| 1:12 | **COST** — Hover over Cost by Model table. Point at most expensive model. | "I can see Scout is spending on Sonnet for tasks that Haiku handles at a tenth of the cost." |
| 1:17 | **COST** — Hover over Cost by Agent table or timeseries chart. | "This kind of visibility took real agent operations from forty dollars an hour to eight. Eighty percent reduction." |
| 1:21 | **OVR** — Text overlay appears: **$40/hr → $8/hr · 80% reduction from visibility alone** | "Not from better code. Just from seeing." |

---

### BEAT 6 — THE DEVELOPER EXPERIENCE
⏱ **1:22–1:38** (16 sec)

| Time | Visual | Audio |
|------|--------|-------|
| 1:22 | **DOCS** — Switch to docs site. User Manual page visible. | "Getting all of this starts with three lines of code." |
| 1:25 | **DOCS** — Scroll to or show the Quick Start code block on the docs page | *(let the 3-line code snippet be visible for 3 seconds)* |
| 1:28 | **DOCS** — Same view, code visible | "Your agent appears on the dashboard with a live heartbeat. Add decorators for timelines. Sprinkle events for the full story. Each layer is optional." |
| 1:33 | **DOCS** — Scroll through sidebar navigation showing all guides | "And we have full documentation — SDK manual, integration guide, dashboard guide —" |
| 1:35 | **WEB** — Quick 1.5-sec flash of hiveboard.net homepage | "— a complete website —" |
| 1:36 | **REPO** — Quick 1.5-sec flash of GitHub README (top section with logo, badges, live demo link) | "— and an open-source repo." |
| 1:38 | **OVR** — Text overlay: **3 lines → heartbeat · Decorators → timelines · Events → full story** | *(overlay holds into next beat)* |

---

### BEAT 7 — THE BRIDGE
⏱ **1:38–1:48** (10 sec)

| Time | Visual | Audio |
|------|--------|-------|
| 1:38 | **CAM** — Cut back to you. Different angle or slightly different framing from Beat 1. | "That's HiveBoard — the product." |
| 1:41 | **CAM** — Natural pause, then shift energy slightly | "Now here's the part I didn't plan for." |
| 1:44 | **CAM** — Direct to lens | "The process of building it turned into something worth sharing on its own." |

---

### BEAT 8 — THE HIVE METHOD
⏱ **1:48–2:12** (24 sec)

| Time | Visual | Audio |
|------|--------|-------|
| 1:48 | **CAM** — You, delivering the headline | "I built this entire product during the hackathon using three Claude Opus instances —" |
| 1:52 | **OVR** over **CAM** — Text: **1 human · 3 Claude Opus 4.6 instances · from scratch** | "— not as code generators, but as a development team." |
| 1:55 | **OP-H** — Flash The Hive Method One-Pager. Full page visible for ~3 sec. The 5 principles, workflow, metrics bar, team structure — all visible at once. | "I used one instance as a PM — it wrote specifications, designed audits, and synthesized decisions." |
| 1:59 | **OP-H** — Zoom slowly into the Team Structure card (bottom left of one-pager) | "Two other instances were dev teams. One in the CLI, one in the cloud." |
| 2:02 | **OP-H** → **CAM** — Cut back to you | "Same model, different environment — and they developed genuinely different working styles." |
| 2:06 | **OP-J** — Flash THE JOURNEY One-Pager. Full page visible for ~3 sec. The timeline strip showing Prologue → Kill → Revelation → Build → Audit → Redesign → Ship is the visual hook. | "The CLI instance was fast and aggressive. Cloud was more careful, more spec-compliant. I assigned work to their strengths." |
| 2:10 | **OP-J** — Hold on the metrics bar at the bottom: 48hrs, ~2hrs coding, 450+, 12 | *(visual holds, audio continues into Beat 9)* |

---

### BEAT 9 — THE PROOF
⏱ **2:12–2:35** (23 sec)

| Time | Visual | Audio |
|------|--------|-------|
| 2:12 | **CAM** — You, building energy | "After each phase, Team 1 audited Team 2's code, and Team 2 audited Team 1's. Adversarial cross-review against the spec." |
| 2:17 | **OP-H** — Flash The Hive Method One-Pager metrics bar: **450+ · 12 · 100% · +58% · ~4%** — hold for 3 sec | "Four hundred fifty checkpoints. Twelve critical bugs caught —" |
| 2:21 | **OVR** over **CAM** — Text: **12 critical bugs · 72 passing tests missed them all** | "— bugs that were completely invisible to seventy-two passing unit tests." |
| 2:25 | **OP-J** — Flash THE JOURNEY One-Pager timeline strip, zoomed to "5 Ideas → Kill" step | "We started with five product ideas. One was built and killed in one session." |
| 2:28 | **OVR** over **CAM** — Text: **~4% coding · 96% specs, audits, design** | "Only four percent of time was spent coding. The rest was specs, design, and cross-audits." |
| 2:31 | **OP-W** — Flash What HiveBoard Sees One-Pager. The 4-column "38 questions" layout visible for ~2 sec. | "We documented everything. The product capabilities. The development methodology. The complete journey." |
| 2:33 | **CAM** — Back to you | "We call it The Hive Method. It's in the repo, and it's replicable." |

---

### BEAT 10 — THE CLOSE
⏱ **2:35–2:52** (17 sec)

| Time | Visual | Audio |
|------|--------|-------|
| 2:35 | **CAM** — Direct to lens. Slower pace. Let it land. | "HiveBoard is the tool that should exist for every team deploying AI agents." |
| 2:39 | **CAM** — Same shot | "It's live. It's open source. It's documented. And you can try it right now." |
| 2:43 | **CAM** — Slight pause, then the finale | "The Hive Method is how one developer and three Opus instances built a production product from scratch." |
| 2:47 | **CAM** — Direct, final statement | "A single AI is a tool. Multiple AI agents are a team." |
| 2:50 | **CAM** — Beat. Let it breathe. | "The value isn't in any single agent. It's in the orchestration." |

---

### BEAT 11 — END CARD
⏱ **2:52–3:00** (8 sec)

| Time | Visual | Audio |
|------|--------|-------|
| 2:52 | **LOGO** — Clean end card fades in. Centered logo. Links below. Hold for full 8 seconds. | *(silence, or very subtle music fade)* |

**End card layout:**
```
        🐝
     HiveBoard
  The Datadog for AI Agents

  ▶ Try It Live     hiveboard.net
  📖 Documentation   hiveboard.net/docs
  💻 GitHub          github.com/jcolano/hiveboard

  Built with The Hive Method
  48 hours · 1 human · 3 Claude Opus 4.6 instances

  Open Source · MIT License
  Anthropic Hackathon 2026 · Problem Statement One
```

---

## TIME BUDGET FINAL

| Beat | Sec | Visual | What Judges See |
|------|-----|--------|-----------------|
| 1. Hook | 15 | Camera | The problem, your conviction |
| 2. Reveal | 10 | Dashboard | Live product, 5 real agents |
| 3. Glance | 10 | Dashboard | Design principle, instant value |
| 4. Investigation | 30 | Dashboard | The WOW moment — timeline deep dive |
| 5. Cost | 17 | Dashboard | $40→$8, the business impact |
| 6. Dev Experience | 16 | Docs + Web + Repo | 3-line SDK, full docs, open source |
| 7. Bridge | 10 | Camera | Energy pivot |
| 8. Hive Method | 24 | Camera + One-Pagers | Opus 4.6 multi-agent orchestration |
| 9. Proof | 23 | Camera + One-Pagers + Overlays | 450 checkpoints, 12 bugs, 4% coding |
| 10. Close | 17 | Camera | The summary punch |
| 11. End Card | 8 | Static card | Links, credits |
| **TOTAL** | **180** | | |

---

## ONE-PAGER USAGE MAP

| One-Pager | Beat | Duration on screen | What it shows |
|-----------|------|--------------------|---------------|
| **The Hive Method** | Beat 8 | ~4 sec (full) + ~3 sec (zoom team) | 5 principles, workflow, team structure |
| **The Hive Method** | Beat 9 | ~3 sec (metrics bar) | 450+, 12, 100%, +58%, ~4% |
| **THE JOURNEY** | Beat 8 | ~3 sec (full) | Timeline strip: Prologue → Ship |
| **THE JOURNEY** | Beat 9 | ~2 sec (zoom to "Kill" step) | FormsFlow killed |
| **What HiveBoard Sees** | Beat 9 | ~2 sec (full) | 38 questions, 4 columns |

**Total one-pager screen time: ~17 seconds across Beats 8-9**

These serve as visual proof that the methodology and product thinking are documented, designed, and real — not just talked about.

---

## RECORDING ORDER (Suggested)

Record in this order for efficiency — not chronological:

**Session 1: Screen recordings**
1. Dashboard demo (Beats 2, 3, 4, 5) — continuous recording, edit later
2. Docs site (Beat 6) — quick scroll through
3. Website homepage (Beat 6) — 2-sec capture
4. GitHub README (Beat 6) — 2-sec capture
5. One-pagers open in browser (Beats 8, 9) — full page + zoomed sections

**Session 2: On-camera segments**
1. Beat 1 (Hook) — multiple takes, pick best
2. Beat 7 (Bridge) — multiple takes
3. Beats 8-9 (Hive Method + Proof) — can be one continuous take
4. Beat 10 (Close) — multiple takes, this one matters most

**Session 3: Editing**
1. Assemble in timeline order
2. Add text overlays
3. Create end card (Beat 11)
4. Audio leveling
5. Final timing check: ≤ 3:00

---

## WHAT NOT TO DO

- Don't memorize the script word-for-word. Hit the beats. Use your own words.
- Don't narrate every click. Let the dashboard have silent moments.
- Don't rush Beat 4 (Investigation). This is where judges fall in love.
- Don't skip the one-pager flashes. They prove depth that other teams can't match.
- Don't forget to say "you can try it right now" — the live link is your killer advantage.
- Don't apologize for anything. You built a production product in 48 hours. Own it.

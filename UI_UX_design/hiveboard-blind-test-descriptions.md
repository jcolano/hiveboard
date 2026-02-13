# HiveBoard Dashboard — Blind Comparison Descriptions

Two dashboard designs for an AI agent observability platform. Both monitor the same data: AI agents processing tasks in real time. Both use the same three-panel layout and the same light color theme. The descriptions below are factual — what is visible on screen when each is opened in a browser at full viewport size.

---

## Design A

### Overall Impression

A three-column layout filling the full browser window. The background is warm off-white, like unbleached paper. All text uses a rounded sans-serif font for labels and a clean monospace font for data values. The overall feeling is spacious — there is visible breathing room between every element. No dark backgrounds anywhere. Borders are thin and pale, almost the color of pencil lines on cream paper.

### Top Bar

A 56-pixel-tall white horizontal bar across the full width of the screen. On the left side, a small hexagonal icon in burnt orange sits next to the word "HiveBoard" in dark bold text, with "Board" colored in burnt orange. To the right of the logo, a small rounded pill reads "production" in light gray monospace type. Further right, two tab buttons sit inside a subtle off-white capsule: "Dashboard" (active, white background, burnt orange text, with a small four-square grid icon) and "Costs" (inactive, gray text, with a dollar sign icon). On the far right of the bar, a rounded pill shows a small pulsing green dot next to the word "Connected" in gray monospace, and beside it a dropdown selector reads "production."

### Left Panel — Agents (300 pixels wide)

A white-background column with a header that reads "Agents" in bold sans-serif. Next to it, a small red pill pulses gently and reads "1 ⚠". On the far right of the header, a gray rounded pill reads "3 agents."

Below the header, three stacked cards, each separated by 8 pixels of space:

**First card (sales agent):** Has a faint red glow around its border — a soft pulsing box-shadow in red. The top line shows the name "sales" in bold dark text on the left, and a small pill-shaped badge on the right reading "ERROR" in red uppercase letters on a faint red background. Below that, two small items: a gray tag reading "sales" in monospace, and a heartbeat indicator showing an amber dot next to "2m ago." Below that, two inline indicators: "Q:8" in amber (indicating a queue depth of 8) and a small red dot next to the text "1 issue." Below that, a line reading "↳ task-lead-acme-corp" in monospace, indicating the current task. At the bottom, a row of 12 tiny vertical bars forming a miniature sparkline chart. The bars are red, and their heights rise and fall — the leftmost bars are taller, the rightmost bars are nearly flat, suggesting declining activity.

**Second card (main agent):** This card has a burnt-orange border and a very faint orange tint to its background, indicating it is the selected card. Its badge reads "PROCESSING" in blue. The heartbeat shows a green dot and "12s ago." Queue reads "Q:2." Current task reads "↳ task-lead-4801." Its sparkline bars are blue, with a fairly even distribution of heights, suggesting steady activity.

**Third card (support agent):** No special border treatment. Badge reads "WAITING" in amber. Heartbeat: green dot, "8s ago." Queue: "Q:1." No current task line displayed. Sparkline bars are blue, short, and relatively flat.

Each card has rounded 10-pixel corners. On hover, the background shifts to a slightly warmer off-white and a faint shadow appears.

### Center Panel — Dashboard View

This column fills all remaining horizontal space between the left and right panels.

**Summary Bar:** A horizontal strip of 8 equally-sized cells separated by thin 1-pixel borders. Each cell has a small uppercase gray label on top and a large bold number below. From left to right: "Total Agents: 3" (dark), "Processing: 1" (blue), "Waiting: 1" (amber), "Stuck: 0" (dark), "Errors: 1" (red), "Success Rate (1h): 87%" (green), "Avg Duration: 18.4s" (dark), "Cost (1h): $4.72" (purple). The numbers are large — roughly 22 pixels — in bold sans-serif with tight letter spacing.

**Metrics Row:** Directly below the summary, four cells of equal width, each containing a small uppercase label and a miniature bar chart made of 16 thin vertical bars. The four charts are: "Throughput (1h)" in blue, "Success Rate" in green, "Errors" in red, "LLM Cost/Task" in purple. The bars are semi-transparent, about 28 pixels tall at their maximum. They give a visual sense of the trend over the last hour without showing explicit axis labels or numbers.

**Timeline Section:** Below the metrics, taking up roughly 40-45% of the center panel's remaining height. It has its own header bar on a white background: the bold label "Timeline", then a task ID "task-lead-4801" in burnt-orange monospace, then four metadata items in gray monospace: "⏱ 14.2s", "🤖 sales", "✓ completed" (green), "◆ 5 LLM" (purple). On the far right, a button reading "⧉ Permalink."

Below the header, a plan bar shows "Plan · 4 steps" with "3/4 completed." Four equal-width horizontal bars sit side by side: two green (completed), one red (failed), one outlined gray (pending). Each is about 20 pixels tall with rounded corners.

Below the plan bar, the timeline canvas: a horizontal scrollable area with a warm off-white background. Ten circular nodes are arranged left-to-right in a single horizontal line, connected by thin gradient lines. Each node is a 16-pixel circle with a colored border — the color corresponds to its type: gray for system events, blue for actions, purple for LLM calls (these nodes are slightly square with 5px border-radius, measuring 18×18 pixels), red for errors (filled solid), amber for warnings, green for success (filled solid). Above each node, a short monospace label in the matching color: "task started", "crm_search", "phase 1", "crm_write", "phase 2", "email_send", "reflection", "phase 3", "dm_notify", "completed." Below each node, a timestamp like "09:15:01." Between the nodes, small monospace text above the connecting line shows the duration of the next step: "0.8s", "1.2s", etc. Hovering over any node scales it up by 35%. The entire track can scroll horizontally if it overflows.

**Tasks Table:** Below the timeline, filling the remaining vertical space. A standard HTML table with 8 columns: Task ID, Agent, Type, Status, Duration, LLM, Cost, Time. The header row has sticky positioning with small uppercase gray labels. The table body shows 5 rows of data in monospace. The first row has a very faint orange background indicating it is selected. Task IDs are in burnt orange. Status cells show a small colored dot before the status word. The LLM column shows purple pill-shaped badges reading "◆ 5", "◆ 12", etc. Rows highlight on hover with a warm off-white background.

### Right Panel — Activity Stream (340 pixels wide)

A white column. The header shows "Activity" in bold, a green pulsing "Live" badge with a green dot, and a gray pill reading "24 events."

Below the header, a row of pill-shaped filter chips: "all" (active — burnt orange text on faint orange background with orange border), "task", "action", "error", "llm", "pipeline", "human" (all inactive — gray text with gray borders). Each is fully rounded (20-pixel border-radius).

Below the filters, a scrollable list of 8 event cards. Each card has two lines:

- **Top line:** A small colored dot, the event type name in the matching color (e.g., "task_completed" in green, "◆ llm_call" in purple, "action_failed" in red, "⚑ issue" in red, "approval_requested" in amber, "⊞ queue_snapshot" in gray), and a gray timestamp on the far right ("just now", "2m ago", "5m ago", etc.).
- **Bottom line:** The agent name and optional task ID in gray monospace (e.g., "sales › task-lead-4801"), then a one-line text summary (e.g., "Processed Greenleaf lead", "crm_search → 403 Forbidden", "Credit $450 for BlueStar").

Each card is separated by a subtle border. Cards highlight on hover. There is no additional detail beyond these two lines per event.

### Animations

Three subtle animations run continuously: the green "Connected" dot pulses by fading and expanding a green shadow; the red attention badge in the agent panel header pulses with a red shadow; the "stuck" status badge (not present on any current agent but defined in the CSS) blinks by fading to 40% opacity and back.

---

## Design B

### Overall Impression

The same three-column layout, same warm off-white background, same fonts, same color palette, same 56-pixel top bar, same panel widths. At first glance, the outer shell is nearly identical to Design A. The differences become apparent as you look into the center panel and the details within each panel.

### Top Bar

Identical to Design A except for one addition: there are now three tab buttons instead of two. "Dashboard" (active, with grid icon), "Costs" (inactive, with dollar icon), and a third tab reading "Pipeline" with a horizontal-lines icon. The Pipeline tab has a small amber-colored number badge reading "4" immediately to the right of the word, inside the tab button. This badge is 9-pixel font, white text on amber background, with pill-shaped rounding.

### Left Panel — Agents (300 pixels wide)

Same header layout as Design A: "Agents" label, pulsing red "1 ⚠" badge, "3 agents" count.

The three agent cards have the same overall structure — name, status badge, type tag, heartbeat, queue indicators, sparkline — but each card now contains one additional row of information not present in Design A:

**First card (sales):** Same red glow border, same "ERROR" badge, same "sales" type tag, same stale heartbeat "2m ago", same "Q:8" amber queue badge and "1 issue" indicator. However, the "↳ task-lead-acme-corp" current-task line from Design A is gone. In its place, a new row of three small rectangular tags appears: "loopcore v2.1", "Python 3.12", "sdk 0.4.2." These tags are in tiny 10-pixel monospace text, with a pale background and a thin subtle border. They display the agent's framework name and version, runtime language and version, and SDK version. The sparkline appears below these tags.

**Second card (main):** Same selected state (orange border, faint orange background), same "PROCESSING" badge. Same change: no current-task line, replaced by three metadata tags: "loopcore v2.1", "Python 3.12", "sdk 0.4.2."

**Third card (support):** Same "WAITING" badge. Metadata tags read: "loopcore v2.0", "Python 3.11", "sdk 0.4.1." Note that the version numbers here differ from the other two agents — a different framework version, different Python version, different SDK version.

The removal of the current-task line and addition of the metadata tags is the only structural difference in the agent cards.

### Center Panel — Dashboard View

**Summary Bar:** Identical to Design A — same 8 cells, same labels, same values, same colors.

**Metrics Row:** Identical to Design A — same 4 mini bar charts.

**Timeline Section:** This is where Design B diverges significantly from Design A.

The timeline header is similar but has two additions on the right side. Next to the "⧉ Permalink" button, there is a small toggle control: two side-by-side buttons reading "Tree" and "Flat" inside a subtle off-white capsule. "Tree" is active (white background, burnt orange text). "Flat" is inactive (gray). This toggle does not exist in Design A.

Below the header, a new horizontal section appears that does not exist in Design A: a **duration breakdown** panel. It has a label "Time Breakdown" on the left and "Total: 14.2s" on the right. Below that, three horizontal bar rows stacked vertically:

- "LLM" label on the left (60px wide, right-aligned), then a horizontal track (a pale rounded rectangle) containing a purple filled bar extending to roughly 66% of the track width, then "9.4s (66%)" on the right.
- "Tools" label, a blue filled bar at roughly 31% width, "4.4s (31%)."
- "Other" label, a gray filled bar at roughly 3% width, "0.4s (3%)."

Each bar is 10 pixels tall with rounded ends. The tracks have a pale off-white background. This section is approximately 80 pixels tall total.

Below the duration breakdown, the **plan bar** is identical to Design A: "Plan · 4 steps", "2/4 completed" (note: Design A says 3/4 — this value differs), four colored segments.

Below the plan bar, the timeline rendering is fundamentally different from Design A. Instead of a horizontal chain of connected dots, the timeline is a **vertical indented tree**.

The tree starts with a root node: a gray circle containing "▶", then "task-lead-4801" in bold with "14.2s" in gray, then "Process Greenleaf Organics lead" as a subtitle, then "✓ completed" in green on the far right.

Indented below the root are 8 child nodes, each on its own row. Each row contains:

- A vertical gray guide line on the left connecting it to its siblings (1.5px wide, following the left edge of the indentation).
- A small 22-pixel circular icon indicating the node type. The icon backgrounds and symbols vary: purple rounded-square with "◆" for LLM calls, blue circle with "⚡" for tool actions, red circle with "✗" for failures, amber circle with "💭" for reflection/warning.
- The node name in bold 13px sans-serif (e.g., "LLM · reasoning", "crm_search", "email_send").
- The duration in gray monospace (e.g., "1.2s", "0.8s").
- A status indicator on the far right: green "✓" for completed, red "✗ failed" for failures.

For LLM-type nodes, a second line appears below the name showing:
- A small purple tag with the model name ("claude-sonnet-4-5").
- A pair of small horizontal bars side by side — a lighter purple bar and a darker purple bar — representing tokens in and tokens out, sized proportionally to each other. Next to the bars, text like "842→156" showing the exact token counts.
- A small cost figure like "$0.008."

For tool-action nodes, the second line shows context like: 'query="Greenleaf Organics" → found: false' or 'action="create_contact" → id: contact_8821.'

**The fifth node (email_send) is a failed action.** Its row has a very faint red background tint. The name "email_send" is rendered in red. Below the name, a special error panel appears: a monospace line reading "ConnectionError: smtp.example.com refused connection on port 587" on a very faint red background with a 2.5-pixel solid red left border. Below that, context text: 'to="jane@greenleaf.com" · 2 retries attempted.'

This failed node has its own nested children — two sub-rows indented further:
- "retry #1" with "1.0s" and its own red-bordered error line reading "ConnectionError: smtp.example.com refused."
- "retry #2" with "1.0s" and the same error message.

Both retry nodes have small red circular icons with a "↻" (loop arrow) symbol and red "✗" status markers.

After the failed node and its retries, the tree continues with three more sibling nodes: "LLM · reflection" (amber icon, with a note "→ pivot to DM"), "dm_notify" (blue action icon), and "LLM · wrap-up" (purple LLM icon with token bars and cost).

The entire tree is vertically scrollable within its container. Rows highlight with a warm off-white background on hover.

**Tasks Table:** Below the tree, structurally identical to Design A but with 4 rows instead of 5 (the "task-lead-4800" row present in Design A is absent). Same 8 columns, same styling.

### Right Panel — Activity Stream (340 pixels wide)

Same header layout as Design A: "Activity" label, green "Live" badge, "24 events" count. Same filter chips in the same order.

The event cards are structurally different from Design A. Each card now has **three sections** instead of two:

- **Top line:** Same as Design A — colored dot, event type name, timestamp.
- **Middle section:** Same as Design A — agent/task path in gray monospace, one-line summary.
- **Bottom line (new):** A row of small inline tags providing structured detail pulled from the event payload. These tags are in 10-11px monospace text with subtle rounded backgrounds.

The specific detail tags vary by event type:

- For an LLM call event: four tags — model name on a faint purple background ("claude-sonnet-4-5"), token counts in gray ("842 in → 156 out"), cost in purple ("$0.008"), and duration in gray ("1.2s").
- For a task completion event: three tags — duration ("14.2s"), total cost ("$0.04 total"), and LLM call count ("5 LLM calls").
- For a failed action event: two tags — error description on faint red background ("403 Forbidden"), duration ("0.8s").
- For an issue event: three tags — severity level in red bold on faint red background ("high"), category in gray ("category: permissions"), occurrence count ("×8 occurrences").
- For an approval request event: one tag — approver name ("approver: support-lead").
- For a completed action event: one tag — duration ("0.5s").
- For a queue snapshot event: two tags — queue depth ("depth: 8"), and oldest item age in amber ("oldest: 47m").

The stream contains 7 event cards (versus 8 in Design A).

### Animations

Same three animations as Design A: green connected dot pulse, red attention badge pulse, stuck-badge blink.

---

## Structural Comparison — What Is Identical

- Three-panel grid layout (300px | flexible | 340px).
- Light warm color theme: off-white backgrounds, white cards, pale borders.
- Fonts: rounded sans-serif for labels, clean monospace for data.
- Logo, workspace badge, connection indicator, environment selector.
- Agent card structure: name, status badge, type tag, heartbeat dot, queue badges, sparklines.
- Summary bar: 8 metric cells with identical labels and values.
- Metrics row: 4 mini bar charts with identical data.
- Tasks table: same columns, same styling, same interaction patterns.
- Activity stream: same header, same filter chips, same basic card structure (top line with type + time, middle line with agent + summary).

## Structural Comparison — What Differs

| Element | Design A | Design B |
|---------|----------|----------|
| Top bar tabs | 2 tabs (Dashboard, Costs) | 3 tabs (Dashboard, Costs, Pipeline with "4" badge) |
| Agent card info | Current task name shown ("↳ task-lead-acme-corp") | Current task name removed; three metadata tags shown instead (framework version, runtime version, SDK version) |
| Agent metadata tags | Not present | Present on every card: small monospace tags showing framework, language runtime, and SDK versions |
| Timeline header controls | Permalink button only | Permalink button + Tree/Flat view toggle |
| Duration breakdown | Not present | Present: three horizontal bars showing LLM time (66%), Tool time (31%), and Other (3%) with percentages and absolute seconds |
| Timeline visualization | Horizontal left-to-right chain of 10 circular dots connected by gradient lines, with labels above and timestamps below | Vertical indented tree with 8 top-level nodes plus 2 nested retry sub-nodes; each node shows an icon, name, duration, and status on one line, with contextual detail on a second line |
| LLM call information | Node appears as a slightly-squared purple-bordered dot; model name extractable only from the stream | Each LLM node shows model name tag, visual token in/out bar, exact token counts, and cost on a detail line |
| Error presentation | A solid red filled dot with the label "email_send" — the error message is not visible | Red-tinted row with the error name in red, a red-bordered error panel showing the exception type and message, context text, and two nested retry sub-nodes each with their own error message |
| Tool action context | Dot with label (e.g., "crm_search") — no input/output visible | Each tool node shows a detail line with parameters and results (e.g., 'query="Greenleaf Organics" → found: false') |
| Stream event cards | Two sections: type+time on top, agent+summary on bottom | Three sections: type+time on top, agent+summary in middle, structured detail tags on bottom (model, tokens, cost, duration, severity, category, etc.) |
| Stream card detail tags | Not present | Present: varies by event type — LLM events show model+tokens+cost+duration; errors show error code; issues show severity+category+count; approvals show approver name |
| Tasks table rows | 5 rows | 4 rows |
| Plan bar progress | "3/4 completed" | "2/4 completed" |

## Information Density Comparison

Design A displays information at a consistent shallow depth. Every element — agent cards, timeline nodes, stream events — shows one layer of information: a name, a status, a summary line. Accessing deeper detail (what error occurred, what model was used, how many tokens were consumed, what parameters a tool received) would require clicking into a detail view that is not shown in this mockup.

Design B displays information at varying depths depending on the element type. LLM nodes show model, tokens, and cost without clicking. Failed actions show the exception message and retry history without clicking. Tool actions show their inputs and outputs without clicking. Stream events show structured metadata tags without clicking. The agent cards show software version information without clicking. A new duration breakdown section answers "where did the time go?" without clicking. The additional Pipeline tab suggests a fleet-wide view of all agent queues and issues. The Tree/Flat toggle offers two ways to read the same timeline data.

The vertical tree in Design B takes more vertical space than the horizontal dot chain in Design A. Design A's timeline is compact and fits in a fixed-height band; Design B's tree requires scrolling for tasks with many steps. However, Design B's tree can represent parent-child relationships and nesting (the retries are visually children of the failed action), which the horizontal chain cannot.

Design A's stream cards are uniform in height — roughly two lines each. Design B's stream cards vary in height based on how many detail tags are present — an LLM event card is taller than an action completion card.

---

*These descriptions are written from direct inspection of the HTML source and CSS of each file. No value judgments are included. Both designs render the same scenario: 3 AI agents, one in error state, one processing, one waiting, with a selected task showing its execution timeline.*

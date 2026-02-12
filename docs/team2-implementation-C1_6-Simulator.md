&nbsp;The simulator is missing. The breakdown goes C1.1 through C1.5 but there's no C1.6 (Agent Simulator). We added this in the drill-down specifically because both teams need it — Team 1 needs it to generate realistic data against their server, Team 2 needs it to populate the dashboard during development, and it becomes the demo tool. I'd add it after C1.4, since it exercises all the convenience methods. It doesn't need to be complex — 3 agents, realistic task processing with timing, LLM calls, occasional errors and retries, running in a loop.



&nbsp;C2.5 says "polling fallback" — worth clarifying scope. The spec mentions SSE as a fallback for WebSocket, not polling. Polling would work fine for MVP, and it's simpler to implement, so if that's the intent it's a pragmatic call. Just want to make sure the Team 2 lead is making that choice consciously rather than planning to implement SSE. For the first integration, polling every 2–3 seconds is perfectly adequate, and WebSocket can be wired in once Team 1's B2.4 is stable.


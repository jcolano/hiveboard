"""
SEARCH_TOOLS
=============

Web search tool for the Agentic Loop Framework.

``web_search``
    Search the web using DuckDuckGo. Supports general and news searches
    with optional time range filtering. Returns formatted results with
    titles, URLs, and snippets.

    Requires the ``duckduckgo-search`` package. If not installed, fails
    gracefully with an error message.

Usage::

    tool = WebSearchTool()
    result = tool.execute(query="Python FastAPI best practices", max_results=5)
"""

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class WebSearchTool(BaseTool):
    """Search the web using DuckDuckGo."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="web_search",
            description=(
                "Search the web for current information. Returns titles, URLs, "
                "and snippets. Use for market research, competitor analysis, "
                "news monitoring, or finding documentation."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Search query string",
                ),
                ToolParameter(
                    name="max_results",
                    type="integer",
                    description="Number of results to return (1-10)",
                    required=False,
                    default=5,
                ),
                ToolParameter(
                    name="search_type",
                    type="string",
                    description="Type of search to perform",
                    required=False,
                    enum=["general", "news"],
                    default="general",
                ),
                ToolParameter(
                    name="time_range",
                    type="string",
                    description="Filter results by recency",
                    required=False,
                    enum=["day", "week", "month", "year"],
                ),
            ],
        )

    def execute(
        self,
        query: str,
        max_results: int = 5,
        search_type: str = "general",
        time_range: str = None,
        **kwargs,
    ) -> ToolResult:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return ToolResult(
                success=False,
                output="",
                error=(
                    "duckduckgo-search package not installed. "
                    "Run: pip install duckduckgo-search"
                ),
            )

        max_results = max(1, min(10, max_results))

        try:
            with DDGS() as ddgs:
                if search_type == "news":
                    raw = list(ddgs.news(
                        keywords=query,
                        max_results=max_results,
                        timelimit=time_range,
                    ))
                else:
                    raw = list(ddgs.text(
                        keywords=query,
                        max_results=max_results,
                        timelimit=time_range,
                    ))

            if not raw:
                return ToolResult(
                    success=True,
                    output="No results found.",
                    metadata={"result_count": 0, "query": query},
                )

            lines = []
            for i, r in enumerate(raw, 1):
                title = r.get("title", "No title")
                url = r.get("href") or r.get("url", "")
                body = r.get("body") or r.get("excerpt", "")
                lines.append(f"{i}. {title}\n   URL: {url}\n   {body}")

            output = "\n\n".join(lines)
            return ToolResult(
                success=True,
                output=output,
                metadata={"result_count": len(raw), "query": query},
            )

        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Web search failed: {str(e)}",
            )

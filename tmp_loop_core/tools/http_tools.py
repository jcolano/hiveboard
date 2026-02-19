"""
HTTP_TOOLS
==========

HTTP call and web fetch tools for the Agentic Loop Framework.

Two tools for external communication:

``http_request``
    General-purpose HTTP client (GET, POST, PUT, PATCH, DELETE).
    Used by skills to interact with APIs (e.g. loopColony REST API).
    Response capped at 50KB. Returns status_code, headers, body.

``webpage_fetch``
    Fetches web pages and extracts content in three modes:
    - "text": Plain text extraction (default)
    - "markdown": Basic HTML-to-markdown conversion
    - "html": Raw HTML
    Max 15KB by default (configurable). Includes a built-in HTML-to-markdown
    converter. User-Agent: "AgenticLoop/1.0 (Python)".

Both tools require the ``requests`` library. If not installed, they fail
gracefully with an error message.

Usage::

    http_tool = HttpCallTool()
    result = http_tool.execute(method="POST", url="https://api.example.com/data",
                               body='{"key": "value"}',
                               headers='{"Authorization": "Bearer xxx"}')

    web_tool = WebFetchTool()
    result = web_tool.execute(url="https://example.com/article", extract_mode="text")
"""

import json
import re
from typing import Dict, Optional
from html.parser import HTMLParser

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


# ============================================================================
# HTTP AVAILABILITY CHECK
# ============================================================================

REQUESTS_AVAILABLE = False
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    pass


# ============================================================================
# SIMPLE HTML TO TEXT CONVERTER
# ============================================================================

class HTMLTextExtractor(HTMLParser):
    """Simple HTML to text converter."""

    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.skip_tags = {'script', 'style', 'head', 'meta', 'link', 'noscript'}
        self.current_skip = False
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self.current_skip = True
            self.skip_depth += 1
        elif tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr'):
            self.text_parts.append('\n')

    def handle_endtag(self, tag):
        if tag in self.skip_tags:
            self.skip_depth -= 1
            if self.skip_depth == 0:
                self.current_skip = False
        elif tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.text_parts.append('\n')

    def handle_data(self, data):
        if not self.current_skip:
            self.text_parts.append(data)

    def get_text(self) -> str:
        text = ''.join(self.text_parts)
        # Clean up whitespace
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        return text.strip()


def html_to_text(html: str) -> str:
    """Convert HTML to plain text."""
    parser = HTMLTextExtractor()
    try:
        parser.feed(html)
        return parser.get_text()
    except Exception:
        # Fallback: just strip tags
        return re.sub(r'<[^>]+>', '', html)


def html_to_markdown(html: str) -> str:
    """
    Convert HTML to basic markdown.

    This is a simplified conversion for common HTML elements.
    """
    text = html

    # Remove script and style
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Headers
    for i in range(1, 7):
        text = re.sub(rf'<h{i}[^>]*>(.*?)</h{i}>', rf'\n{"#" * i} \1\n', text, flags=re.DOTALL | re.IGNORECASE)

    # Bold and italic
    text = re.sub(r'<(strong|b)[^>]*>(.*?)</\1>', r'**\2**', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<(em|i)[^>]*>(.*?)</\1>', r'*\2*', text, flags=re.DOTALL | re.IGNORECASE)

    # Links
    text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.DOTALL | re.IGNORECASE)

    # Lists
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'\n- \1', text, flags=re.DOTALL | re.IGNORECASE)

    # Paragraphs and breaks
    text = re.sub(r'<p[^>]*>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<br[^>]*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<div[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)

    # Remove remaining tags
    text = re.sub(r'<[^>]+>', '', text)

    # Decode entities
    text = text.replace('&nbsp;', ' ')
    text = text.replace('&amp;', '&')
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&quot;', '"')
    text = text.replace('&#39;', "'")

    # Clean whitespace
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    text = re.sub(r' +', ' ', text)

    return text.strip()


# ============================================================================
# HTTP CALL TOOL
# ============================================================================

class HttpCallTool(BaseTool):
    """Make HTTP requests to APIs."""

    def __init__(self, default_timeout: int = 30):
        self.default_timeout = default_timeout

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="http_request",
            description="Make an HTTP request to an API endpoint. Use for REST APIs, webhooks, etc.",
            parameters=[
                ToolParameter(
                    name="method",
                    type="string",
                    description="HTTP method",
                    enum=["GET", "POST", "PUT", "PATCH", "DELETE"]
                ),
                ToolParameter(
                    name="url",
                    type="string",
                    description="Full URL to call"
                ),
                ToolParameter(
                    name="headers",
                    type="object",
                    description="HTTP headers as key-value pairs",
                    required=False
                ),
                ToolParameter(
                    name="body",
                    type="string",
                    description="Request body (for POST/PUT/PATCH). Can be JSON string.",
                    required=False
                ),
                ToolParameter(
                    name="timeout_seconds",
                    type="integer",
                    description="Request timeout in seconds",
                    required=False,
                    default=30
                )
            ]
        )

    def execute(
        self,
        method: str,
        url: str,
        headers: Dict = None,
        body: str = None,
        timeout_seconds: int = None
    ) -> ToolResult:
        """
        Execute HTTP request.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full URL to call
            headers: Optional HTTP headers
            body: Optional request body
            timeout_seconds: Request timeout

        Returns:
            ToolResult with response details
        """
        if not REQUESTS_AVAILABLE:
            return ToolResult(
                success=False,
                output="",
                error="requests package not available. Install with: pip install requests"
            )

        timeout = timeout_seconds or self.default_timeout

        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=headers or {},
                data=body,
                timeout=timeout
            )

            # Limit response size
            max_size = 50000
            body_text = response.text[:max_size]
            if len(response.text) > max_size:
                body_text += f"\n\n[TRUNCATED - response exceeds {max_size} characters]"

            result = {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body_text
            }

            return ToolResult(
                success=True,
                output=json.dumps(result, indent=2),
                metadata={
                    "status_code": response.status_code,
                    "content_length": len(response.text),
                    "content_type": response.headers.get("content-type", "")
                }
            )

        except requests.exceptions.Timeout:
            return ToolResult(
                success=False,
                output="",
                error=f"Request timed out after {timeout} seconds"
            )
        except requests.exceptions.ConnectionError as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Connection error: {str(e)}"
            )
        except requests.exceptions.RequestException as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Request failed: {str(e)}"
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Unexpected error: {str(e)}"
            )


# ============================================================================
# WEB FETCH TOOL
# ============================================================================

class WebFetchTool(BaseTool):
    """Fetch and extract content from web pages."""

    def __init__(self, max_length: int = 15000, default_timeout: int = 30):
        self.max_length = max_length
        self.default_timeout = default_timeout

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="webpage_fetch",
            description="Fetch a web page and extract its text content. Good for reading articles, documentation, etc.",
            parameters=[
                ToolParameter(
                    name="url",
                    type="string",
                    description="URL of the web page to fetch"
                ),
                ToolParameter(
                    name="extract_mode",
                    type="string",
                    description="How to extract content: 'text' (plain text), 'markdown' (basic markdown), or 'html' (raw HTML)",
                    required=False,
                    enum=["text", "markdown", "html"],
                    default="markdown"
                ),
                ToolParameter(
                    name="max_length",
                    type="integer",
                    description="Maximum characters to return",
                    required=False,
                    default=15000
                )
            ]
        )

    def execute(
        self,
        url: str,
        extract_mode: str = "markdown",
        max_length: int = None
    ) -> ToolResult:
        """
        Fetch and extract web page content.

        Args:
            url: URL to fetch
            extract_mode: "text", "markdown", or "html"
            max_length: Max characters to return

        Returns:
            ToolResult with extracted content
        """
        if not REQUESTS_AVAILABLE:
            return ToolResult(
                success=False,
                output="",
                error="requests package not available. Install with: pip install requests"
            )

        max_len = max_length or self.max_length

        try:
            response = requests.get(
                url,
                timeout=self.default_timeout,
                headers={
                    "User-Agent": "AgenticLoop/1.0 (Python)"
                }
            )
            response.raise_for_status()

            html = response.text

            # Extract content based on mode
            if extract_mode == "html":
                content = html[:max_len]
            elif extract_mode == "text":
                content = html_to_text(html)[:max_len]
            else:  # markdown
                content = html_to_markdown(html)[:max_len]

            truncated = len(content) >= max_len

            return ToolResult(
                success=True,
                output=content,
                metadata={
                    "url": url,
                    "length": len(content),
                    "extract_mode": extract_mode,
                    "truncated": truncated,
                    "content_type": response.headers.get("content-type", "")
                }
            )

        except requests.exceptions.HTTPError as e:
            return ToolResult(
                success=False,
                output="",
                error=f"HTTP error: {e.response.status_code} {e.response.reason}"
            )
        except requests.exceptions.Timeout:
            return ToolResult(
                success=False,
                output="",
                error=f"Request timed out after {self.default_timeout} seconds"
            )
        except requests.exceptions.ConnectionError as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Connection error: {str(e)}"
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Error fetching URL: {str(e)}"
            )


# ============================================================================
# FACTORY FUNCTIONS
# ============================================================================

def create_http_request_tool(config: dict = None) -> HttpCallTool:
    """Create an HttpCallTool with configuration."""
    timeout = 30
    if config:
        timeout = config.get("timeout_seconds", 30)
    return HttpCallTool(default_timeout=timeout)


def create_webpage_fetch_tool(config: dict = None) -> WebFetchTool:
    """Create a WebFetchTool with configuration."""
    max_length = 15000
    if config:
        max_length = config.get("max_length", 15000)
    return WebFetchTool(max_length=max_length)


# ============================================================================
# MAIN BLOCK (Test & Demo)
# ============================================================================

if __name__ == "__main__":
    print("Agentic Loop HTTP Tools")
    print("=" * 60)

    if not REQUESTS_AVAILABLE:
        print("[ERROR] requests package not available")
        print("Install with: pip install requests")
        exit(1)

    # Test HTTP call tool
    print("\n--- HTTP Call Tool ---")
    http_tool = HttpCallTool()
    print(f"Tool: {http_tool.name}")

    # Simple GET request
    print("\nTesting GET request...")
    result = http_tool.execute(
        method="GET",
        url="https://httpbin.org/get",
        timeout_seconds=10
    )
    print(f"Success: {result.success}")
    if result.success:
        print(f"Status code: {result.metadata.get('status_code')}")
    else:
        print(f"Error: {result.error}")

    # Test web fetch tool
    print("\n--- Web Fetch Tool ---")
    web_tool = WebFetchTool()
    print(f"Tool: {web_tool.name}")

    # Fetch a simple page
    print("\nTesting web fetch...")
    result = web_tool.execute(
        url="https://example.com",
        extract_mode="text",
        max_length=500
    )
    print(f"Success: {result.success}")
    if result.success:
        print(f"Content length: {result.metadata.get('length')}")
        print(f"Content preview:\n{result.output[:200]}...")
    else:
        print(f"Error: {result.error}")

    # Show schema
    print("\n--- HTTP Call Schema ---")
    print(json.dumps(http_tool.get_schema(), indent=2))

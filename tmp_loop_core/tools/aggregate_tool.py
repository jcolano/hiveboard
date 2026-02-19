"""
AGGREGATE_TOOL
==============

Generic aggregation tool for the Agentic Loop Framework.

Queries counts, sums, and averages from any HTTP data source. Returns
compact numeric results instead of raw records, preventing the LLM from
fetching entire datasets just to compute totals.

The tool fetches records via HTTP (using the same pattern as crm_search)
and computes aggregations locally. The LLM never sees the raw records --
only the aggregated numbers.

Supported operations:
- count: Number of matching records
- sum:<field>: Sum of a numeric field across records
- avg:<field>: Average of a numeric field across records
- min:<field>: Minimum value of a numeric field
- max:<field>: Maximum value of a numeric field

Usage::

    aggregate(
        entity="deals",
        operations=["count", "sum:amount", "avg:amount"],
        filters={"stage": "won"},
        base_url="https://mlbackend.net/loop/api/v1",
        auth_token="lc_xxx",
        workspace_id="ws_123"
    )
    # Returns: {"count": 7, "sum_amount": 541352.0, "avg_amount": 77336.0}
"""

import json
from typing import Dict, List, Optional, Any

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


# Reuse entity path mapping from crm_tools
try:
    from .crm_tools import ENTITY_PATHS
except ImportError:
    ENTITY_PATHS = {}


class AggregateTool(BaseTool):
    """Query counts, sums, averages from any data source."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="data_aggregate",
            description="Query counts, sums, averages from any data source. Returns numbers, not raw records.",
            parameters=[
                ToolParameter(
                    name="entity",
                    type="string",
                    description="Entity type to query (e.g. 'deals', 'contacts', 'tickets')"
                ),
                ToolParameter(
                    name="operations",
                    type="array",
                    description='List of operations: "count", "sum:<field>", "avg:<field>", "min:<field>", "max:<field>"',
                    items={"type": "string"},
                ),
                ToolParameter(
                    name="filters",
                    type="object",
                    description="Optional filters to narrow the query (e.g. {\"stage\": \"won\"})",
                    required=False,
                ),
                ToolParameter(
                    name="base_url",
                    type="string",
                    description="Base URL of the loopColony API",
                ),
                ToolParameter(
                    name="auth_token",
                    type="string",
                    description="Bearer auth token for the API",
                ),
                ToolParameter(
                    name="workspace_id",
                    type="string",
                    description="Workspace ID for multi-tenant scoping",
                ),
            ],
        )

    def execute(
        self,
        entity: str,
        operations: List[str],
        base_url: str,
        auth_token: str,
        workspace_id: str,
        filters: Optional[Dict] = None,
    ) -> ToolResult:
        try:
            import requests
        except ImportError:
            return ToolResult(
                success=False,
                output="",
                error="requests library not installed",
            )

        # Resolve entity to URL path
        path = ENTITY_PATHS.get(entity, entity)
        url = f"{base_url.rstrip('/')}/{path}"

        # Build query params
        params = {"workspace_id": workspace_id}
        if filters:
            params.update(filters)
        # Request a large limit to get all records for aggregation
        params["limit"] = 500

        headers = {"Authorization": f"Bearer {auth_token}"}

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code != 200:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"HTTP {resp.status_code}: {resp.text[:500]}",
                )

            data = resp.json()
        except requests.RequestException as e:
            return ToolResult(success=False, output="", error=str(e))
        except json.JSONDecodeError:
            return ToolResult(success=False, output="", error="Invalid JSON response")

        # Extract the records list from the response
        # loopColony wraps lists: {"success": true, "deals": [...]} or {"items": [...]}
        records = None
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            # Try common wrapper keys
            for key in [entity, "items", "results", "data"]:
                if key in data and isinstance(data[key], list):
                    records = data[key]
                    break
            # Fallback: look for any list value
            if records is None:
                for v in data.values():
                    if isinstance(v, list):
                        records = v
                        break

        if records is None:
            return ToolResult(
                success=False,
                output="",
                error=f"Could not find records list in response for entity '{entity}'",
            )

        # Compute aggregations
        result = {}
        for op in operations:
            if op == "count":
                result["count"] = len(records)
            elif ":" in op:
                op_type, field_name = op.split(":", 1)
                values = []
                for record in records:
                    val = record.get(field_name)
                    if val is not None:
                        try:
                            values.append(float(val))
                        except (ValueError, TypeError):
                            pass

                key = f"{op_type}_{field_name}"
                if not values:
                    result[key] = None
                elif op_type == "sum":
                    result[key] = round(sum(values), 2)
                elif op_type == "avg":
                    result[key] = round(sum(values) / len(values), 2)
                elif op_type == "min":
                    result[key] = round(min(values), 2)
                elif op_type == "max":
                    result[key] = round(max(values), 2)
                else:
                    result[key] = f"Unknown operation: {op_type}"
            else:
                result[op] = f"Unknown operation format: {op}"

        result["_record_count"] = len(records)
        return ToolResult(
            success=True,
            output=json.dumps(result),
            metadata={"entity": entity, "record_count": len(records)},
        )

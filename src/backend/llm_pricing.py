"""LLM Pricing Engine — server-side cost estimation for HiveBoard.

Maintains a canonical pricing table and estimates costs for llm_call events
that arrive without a cost field. Model matching is case-insensitive with
exact-match priority and longest-prefix fallback.

Issue #15: Server-Side LLM Cost Estimation Spec
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ───────────────────────────────────────────────────────────────────
#  PRICING TABLE SCHEMA
# ───────────────────────────────────────────────────────────────────

_DEFAULT_PRICING: list[dict[str, Any]] = [
    # Anthropic
    {"model_pattern": "claude-opus-4", "provider": "anthropic", "input_per_m": 15.0, "output_per_m": 75.0},
    {"model_pattern": "claude-sonnet-4", "provider": "anthropic", "input_per_m": 3.0, "output_per_m": 15.0},
    {"model_pattern": "claude-3-7-sonnet", "provider": "anthropic", "input_per_m": 3.0, "output_per_m": 15.0},
    {"model_pattern": "claude-3-5-sonnet", "provider": "anthropic", "input_per_m": 3.0, "output_per_m": 15.0},
    {"model_pattern": "claude-3-5-haiku", "provider": "anthropic", "input_per_m": 0.80, "output_per_m": 4.0},
    {"model_pattern": "claude-3-opus", "provider": "anthropic", "input_per_m": 15.0, "output_per_m": 75.0},
    {"model_pattern": "claude-3-haiku", "provider": "anthropic", "input_per_m": 0.25, "output_per_m": 1.25},
    {"model_pattern": "claude-sonnet-4-5", "provider": "anthropic", "input_per_m": 3.0, "output_per_m": 15.0},
    {"model_pattern": "claude-haiku-4-5", "provider": "anthropic", "input_per_m": 0.80, "output_per_m": 4.0},
    # OpenAI
    {"model_pattern": "gpt-4o", "provider": "openai", "input_per_m": 2.50, "output_per_m": 10.0},
    {"model_pattern": "gpt-4o-mini", "provider": "openai", "input_per_m": 0.15, "output_per_m": 0.60},
    {"model_pattern": "gpt-4-turbo", "provider": "openai", "input_per_m": 10.0, "output_per_m": 30.0},
    {"model_pattern": "gpt-4", "provider": "openai", "input_per_m": 30.0, "output_per_m": 60.0},
    {"model_pattern": "o1", "provider": "openai", "input_per_m": 15.0, "output_per_m": 60.0},
    {"model_pattern": "o3-mini", "provider": "openai", "input_per_m": 1.10, "output_per_m": 4.40},
    # Google
    {"model_pattern": "gemini-2.0-flash", "provider": "google", "input_per_m": 0.10, "output_per_m": 0.40},
    {"model_pattern": "gemini-1.5-pro", "provider": "google", "input_per_m": 1.25, "output_per_m": 5.0},
    {"model_pattern": "gemini-1.5-flash", "provider": "google", "input_per_m": 0.075, "output_per_m": 0.30},
    # Mistral
    {"model_pattern": "mistral-large", "provider": "mistral", "input_per_m": 2.0, "output_per_m": 6.0},
    {"model_pattern": "mistral-small", "provider": "mistral", "input_per_m": 0.20, "output_per_m": 0.60},
    {"model_pattern": "codestral", "provider": "mistral", "input_per_m": 0.30, "output_per_m": 0.90},
    # Meta (typical hosted pricing)
    {"model_pattern": "llama-3.1-405b", "provider": "meta", "input_per_m": 3.0, "output_per_m": 3.0},
    {"model_pattern": "llama-3.1-70b", "provider": "meta", "input_per_m": 0.90, "output_per_m": 0.90},
    {"model_pattern": "llama-3.1-8b", "provider": "meta", "input_per_m": 0.10, "output_per_m": 0.10},
]


class LlmPricingEngine:
    """In-memory LLM pricing cache with file persistence."""

    def __init__(self, data_dir: str | None = None):
        self._data_dir = data_dir or os.environ.get("HIVEBOARD_DATA", "./data")
        self._pricing_file = Path(self._data_dir) / "llm_pricing.json"
        self._entries: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Load pricing from disk, or seed with defaults."""
        if self._pricing_file.exists():
            try:
                raw = self._pricing_file.read_text()
                self._entries = json.loads(raw)
            except (json.JSONDecodeError, OSError):
                self._entries = list(_DEFAULT_PRICING)
                self._persist()
        else:
            self._entries = list(_DEFAULT_PRICING)
            self._persist()

    def _persist(self) -> None:
        self._pricing_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(self._pricing_file) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._entries, f, indent=2)
        os.replace(tmp, str(self._pricing_file))

    # ───────────────────────────────────────────────────────────────
    #  MODEL MATCHING
    # ───────────────────────────────────────────────────────────────

    def match_model(self, model_name: str) -> dict[str, Any] | None:
        """Find best pricing entry for a model string.

        Priority:
        1. Exact match (case-insensitive)
        2. Longest prefix match (case-insensitive)
        """
        lower = model_name.lower()

        # Exact match
        for entry in self._entries:
            if entry["model_pattern"].lower() == lower:
                return entry

        # Longest prefix match
        best: dict[str, Any] | None = None
        best_len = 0
        for entry in self._entries:
            pattern = entry["model_pattern"].lower()
            if lower.startswith(pattern) and len(pattern) > best_len:
                best = entry
                best_len = len(pattern)

        return best

    def estimate_cost(
        self,
        model: str,
        tokens_in: int | None,
        tokens_out: int | None,
    ) -> tuple[float | None, str | None]:
        """Estimate cost for a model + token counts.

        Returns (estimated_cost, matched_pattern) or (None, None) if unknown.
        """
        if not model or (not tokens_in and not tokens_out):
            return None, None

        entry = self.match_model(model)
        if entry is None:
            return None, None

        t_in = tokens_in or 0
        t_out = tokens_out or 0
        cost = (t_in * entry["input_per_m"] / 1_000_000) + (
            t_out * entry["output_per_m"] / 1_000_000
        )
        return round(cost, 6), entry["model_pattern"]

    # ───────────────────────────────────────────────────────────────
    #  EVENT PROCESSING
    # ───────────────────────────────────────────────────────────────

    def process_llm_event(self, payload: dict) -> dict:
        """Process an llm_call event payload, estimating cost if needed.

        Mutates and returns the payload dict with cost_source and
        cost_model_matched fields added.

        Priority rules:
        1. Developer-provided cost > 0 → "reported"
        2. Missing/null/zero cost WITH model + tokens → "estimated"
        3. Unknown model → cost stays null, no source
        """
        if not isinstance(payload, dict):
            return payload

        kind = payload.get("kind")
        if kind != "llm_call":
            return payload

        data = payload.get("data")
        if not isinstance(data, dict):
            return payload

        cost = data.get("cost")
        model = data.get("model")

        # Rule 1: Developer-provided cost > 0
        if cost is not None and cost > 0:
            data["cost_source"] = "reported"
            return payload

        # Rule 2: Estimate if we have model + tokens
        if model:
            tokens_in = data.get("tokens_in")
            tokens_out = data.get("tokens_out")
            estimated, pattern = self.estimate_cost(model, tokens_in, tokens_out)
            if estimated is not None:
                data["cost"] = estimated
                data["cost_source"] = "estimated"
                data["cost_model_matched"] = pattern
                return payload

        # Rule 3: Unknown model or no tokens — leave as-is
        if cost is not None and cost == 0:
            data["cost_source"] = None
        return payload

    # ───────────────────────────────────────────────────────────────
    #  ADMIN CRUD
    # ───────────────────────────────────────────────────────────────

    async def list_entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    async def add_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            self._entries.append(entry)
            self._persist()
        return entry

    async def update_entry(
        self, pattern: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        async with self._lock:
            for e in self._entries:
                if e["model_pattern"].lower() == pattern.lower():
                    e.update(updates)
                    self._persist()
                    return e
        return None

    async def delete_entry(self, pattern: str) -> bool:
        async with self._lock:
            for i, e in enumerate(self._entries):
                if e["model_pattern"].lower() == pattern.lower():
                    self._entries.pop(i)
                    self._persist()
                    return True
        return False

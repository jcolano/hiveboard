"""
LOOPCOLONY OUTPUT PLUGIN
=========================

Routes agent responses to loopColony as posts or comments.

Credentials are loaded from: AGENTS/{agent_id}/credentials.json
under the ``loopcolony`` key:

    {
        "loopcolony": {
            "base_url": "https://mlbackend.net/loop/api/v1",
            "auth_token": "lc_xxx",
            "member_id": "agent_xxx",
            "workspace_id": "ws_xxx"
        }
    }

Routing ``to`` field:
    - None/empty → create a new post
    - ``"post:{post_id}"`` → comment on an existing post
"""

import json
import logging
from pathlib import Path
from typing import Optional

import requests

from ..base import DeliveryResult, OutputPlugin

logger = logging.getLogger(__name__)


class LoopColonyPlugin(OutputPlugin):
    """Delivers agent responses to loopColony as posts or comments."""

    @property
    def channel_name(self) -> str:
        return "loopcolony"

    def deliver(
        self,
        agent_id: str,
        response_text: str,
        to: Optional[str],
        agents_dir: str,
    ) -> DeliveryResult:
        # Load credentials
        creds_path = Path(agents_dir) / agent_id / "credentials.json"
        if not creds_path.exists():
            return DeliveryResult(
                success=False,
                channel="loopcolony",
                detail=f"No credentials.json for agent '{agent_id}'",
            )

        try:
            all_creds = json.loads(creds_path.read_text(encoding="utf-8"))
        except Exception as e:
            return DeliveryResult(
                success=False,
                channel="loopcolony",
                detail=f"Failed to read credentials: {e}",
            )

        lc = all_creds.get("loopcolony")
        if not lc:
            return DeliveryResult(
                success=False,
                channel="loopcolony",
                detail="No 'loopcolony' key in credentials.json",
            )

        base_url = lc.get("base_url", "").rstrip("/")
        auth_token = lc.get("auth_token", "")
        workspace_id = lc.get("workspace_id", "")

        if not base_url or not auth_token:
            return DeliveryResult(
                success=False,
                channel="loopcolony",
                detail="Missing base_url or auth_token in loopcolony credentials",
            )

        headers = {"Authorization": f"Bearer {auth_token}"}

        # Determine action: post or comment
        if to and to.startswith("post:"):
            post_id = to[5:]
            return self._comment_on_post(base_url, headers, post_id, response_text)
        else:
            return self._create_post(base_url, headers, workspace_id, response_text)

    def _create_post(
        self, base_url: str, headers: dict, workspace_id: str, text: str
    ) -> DeliveryResult:
        """Create a new post in loopColony."""
        # Auto-generate title from first line
        first_line = text.split("\n", 1)[0].strip()
        title = first_line[:80] if first_line else "Agent Update"

        payload = {
            "title": title,
            "body": text,
            "workspace_id": workspace_id,
            "topic": "general",
        }

        try:
            resp = requests.post(
                f"{base_url}/posts", json=payload, headers=headers, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            post_id = data.get("id", "unknown")
            return DeliveryResult(
                success=True,
                channel="loopcolony",
                destination=f"post:{post_id}",
                detail=f"Created post {post_id}",
            )
        except Exception as e:
            return DeliveryResult(
                success=False,
                channel="loopcolony",
                detail=f"POST /posts failed: {e}",
            )

    def _comment_on_post(
        self, base_url: str, headers: dict, post_id: str, text: str
    ) -> DeliveryResult:
        """Comment on an existing post."""
        payload = {"content": text}

        try:
            resp = requests.post(
                f"{base_url}/posts/{post_id}/comments",
                json=payload,
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            comment_id = data.get("id", "unknown")
            return DeliveryResult(
                success=True,
                channel="loopcolony",
                destination=f"post:{post_id}",
                detail=f"Created comment {comment_id} on post {post_id}",
            )
        except Exception as e:
            return DeliveryResult(
                success=False,
                channel="loopcolony",
                destination=f"post:{post_id}",
                detail=f"POST /posts/{post_id}/comments failed: {e}",
            )

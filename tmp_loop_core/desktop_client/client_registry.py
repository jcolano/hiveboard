"""
Client Registry for Desktop Clients.

Manages connected desktop clients, their authentication state,
and granted capabilities.
"""

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from threading import Lock

from .models import (
    Capability,
    CapabilityType,
    CapabilityScope,
    CapabilityConstraints,
    ClientInfo,
    ClientDetailInfo,
    SystemStatus,
)


class AuthToken:
    """Represents an authentication token for a client."""

    def __init__(
        self,
        token: str,
        refresh_token: str,
        expires_at: datetime,
        client_id: str
    ):
        self.token = token
        self.refresh_token = refresh_token
        self.expires_at = expires_at
        self.client_id = client_id

    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    def is_expiring_soon(self, threshold_minutes: int = 5) -> bool:
        return datetime.utcnow() > (self.expires_at - timedelta(minutes=threshold_minutes))


class ConnectedClient:
    """Represents a connected desktop client."""

    def __init__(
        self,
        client_id: str,
        platform: str,
        client_version: str,
        device_fingerprint: Optional[str] = None
    ):
        self.client_id = client_id
        self.platform = platform
        self.client_version = client_version
        self.device_fingerprint = device_fingerprint
        self.connected_at = datetime.utcnow()
        self.last_seen_at = datetime.utcnow()
        self.is_online = True
        self.capabilities: Dict[str, Capability] = {}
        self.system_status: Optional[SystemStatus] = None
        self.auth_token: Optional[AuthToken] = None

    def update_last_seen(self):
        """Update the last seen timestamp."""
        self.last_seen_at = datetime.utcnow()
        self.is_online = True

    def mark_offline(self):
        """Mark client as offline."""
        self.is_online = False

    def add_capability(self, capability: Capability):
        """Add a capability to this client."""
        self.capabilities[capability.id] = capability

    def remove_capability(self, capability_id: str) -> bool:
        """Remove a capability. Returns True if it existed."""
        if capability_id in self.capabilities:
            del self.capabilities[capability_id]
            return True
        return False

    def get_capability(self, capability_id: str) -> Optional[Capability]:
        """Get a capability by ID."""
        return self.capabilities.get(capability_id)

    def get_capabilities_for_agent(self, agent_id: str) -> List[Capability]:
        """Get all capabilities granted for a specific agent."""
        return [c for c in self.capabilities.values() if c.agent_id == agent_id]

    def revoke_agent_capabilities(self, agent_id: str) -> List[str]:
        """Revoke all capabilities for an agent. Returns revoked capability IDs."""
        to_revoke = [c.id for c in self.capabilities.values() if c.agent_id == agent_id]
        for cap_id in to_revoke:
            del self.capabilities[cap_id]
        return to_revoke

    def to_info(self) -> ClientInfo:
        """Convert to ClientInfo model."""
        return ClientInfo(
            client_id=self.client_id,
            platform=self.platform,
            client_version=self.client_version,
            connected_at=self.connected_at,
            last_seen_at=self.last_seen_at,
            is_online=self.is_online,
            active_capabilities_count=len(self.capabilities)
        )

    def to_detail_info(self, pending_requests_count: int = 0) -> ClientDetailInfo:
        """Convert to ClientDetailInfo model."""
        return ClientDetailInfo(
            client_id=self.client_id,
            platform=self.platform,
            client_version=self.client_version,
            device_fingerprint=self.device_fingerprint,
            connected_at=self.connected_at,
            last_seen_at=self.last_seen_at,
            is_online=self.is_online,
            capabilities=list(self.capabilities.values()),
            pending_requests_count=pending_requests_count,
            system_status=self.system_status
        )


class ClientRegistry:
    """
    Registry for managing connected desktop clients.

    Thread-safe singleton that tracks all connected clients,
    their authentication state, and granted capabilities.
    """

    _instance: Optional["ClientRegistry"] = None
    _lock = Lock()

    # Token configuration
    TOKEN_EXPIRY_HOURS = 24
    REFRESH_TOKEN_EXPIRY_DAYS = 30

    # Client timeout (mark offline if no heartbeat)
    CLIENT_TIMEOUT_MINUTES = 2

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._clients: Dict[str, ConnectedClient] = {}
        self._tokens: Dict[str, AuthToken] = {}  # token -> AuthToken
        self._refresh_tokens: Dict[str, str] = {}  # refresh_token -> client_id
        self._client_lock = Lock()
        self._initialized = True

    def _generate_token(self) -> str:
        """Generate a secure random token."""
        return secrets.token_urlsafe(32)

    def authenticate_client(
        self,
        client_id: str,
        platform: str,
        client_version: str,
        device_fingerprint: Optional[str] = None
    ) -> AuthToken:
        """
        Authenticate a client and return auth tokens.

        If client already exists, updates its info and generates new tokens.
        """
        with self._client_lock:
            # Create or update client
            if client_id in self._clients:
                client = self._clients[client_id]
                client.platform = platform
                client.client_version = client_version
                client.device_fingerprint = device_fingerprint
                client.update_last_seen()

                # Invalidate old token if exists
                if client.auth_token:
                    self._tokens.pop(client.auth_token.token, None)
                    self._refresh_tokens.pop(client.auth_token.refresh_token, None)
            else:
                client = ConnectedClient(
                    client_id=client_id,
                    platform=platform,
                    client_version=client_version,
                    device_fingerprint=device_fingerprint
                )
                self._clients[client_id] = client

            # Generate new tokens
            token = self._generate_token()
            refresh_token = self._generate_token()
            expires_at = datetime.utcnow() + timedelta(hours=self.TOKEN_EXPIRY_HOURS)

            auth_token = AuthToken(
                token=token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                client_id=client_id
            )

            client.auth_token = auth_token
            self._tokens[token] = auth_token
            self._refresh_tokens[refresh_token] = client_id

            return auth_token

    def validate_token(self, token: str) -> Optional[str]:
        """
        Validate a token and return the client_id if valid.
        Returns None if token is invalid or expired.
        """
        auth_token = self._tokens.get(token)
        if auth_token is None:
            return None

        if auth_token.is_expired():
            # Clean up expired token
            self._tokens.pop(token, None)
            return None

        # Update last seen
        client = self._clients.get(auth_token.client_id)
        if client:
            client.update_last_seen()

        return auth_token.client_id

    def refresh_token(self, refresh_token: str) -> Optional[AuthToken]:
        """
        Refresh an authentication token.
        Returns new AuthToken or None if refresh token is invalid.
        """
        with self._client_lock:
            client_id = self._refresh_tokens.get(refresh_token)
            if client_id is None:
                return None

            client = self._clients.get(client_id)
            if client is None:
                return None

            # Invalidate old tokens
            if client.auth_token:
                self._tokens.pop(client.auth_token.token, None)
                self._refresh_tokens.pop(client.auth_token.refresh_token, None)

            # Generate new tokens
            new_token = self._generate_token()
            new_refresh_token = self._generate_token()
            expires_at = datetime.utcnow() + timedelta(hours=self.TOKEN_EXPIRY_HOURS)

            auth_token = AuthToken(
                token=new_token,
                refresh_token=new_refresh_token,
                expires_at=expires_at,
                client_id=client_id
            )

            client.auth_token = auth_token
            self._tokens[new_token] = auth_token
            self._refresh_tokens[new_refresh_token] = client_id

            return auth_token

    def get_client(self, client_id: str) -> Optional[ConnectedClient]:
        """Get a client by ID."""
        return self._clients.get(client_id)

    def get_client_by_token(self, token: str) -> Optional[ConnectedClient]:
        """Get a client by its auth token."""
        client_id = self.validate_token(token)
        if client_id:
            return self._clients.get(client_id)
        return None

    def list_clients(self, online_only: bool = False) -> List[ClientInfo]:
        """List all registered clients."""
        clients = list(self._clients.values())
        if online_only:
            clients = [c for c in clients if c.is_online]
        return [c.to_info() for c in clients]

    def disconnect_client(self, client_id: str, revoke_capabilities: bool = False) -> bool:
        """
        Disconnect a client.

        If revoke_capabilities is True, all capabilities are revoked.
        Otherwise, client is just marked offline.
        """
        with self._client_lock:
            client = self._clients.get(client_id)
            if client is None:
                return False

            # Invalidate tokens
            if client.auth_token:
                self._tokens.pop(client.auth_token.token, None)
                self._refresh_tokens.pop(client.auth_token.refresh_token, None)
                client.auth_token = None

            if revoke_capabilities:
                client.capabilities.clear()

            client.mark_offline()
            return True

    def grant_capability(
        self,
        client_id: str,
        agent_id: str,
        capability_type: CapabilityType,
        scope: CapabilityScope,
        constraints: Optional[CapabilityConstraints] = None,
        expires_at: Optional[datetime] = None
    ) -> Optional[Capability]:
        """
        Grant a capability to a client for a specific agent.
        Returns the granted Capability or None if client not found.
        """
        client = self._clients.get(client_id)
        if client is None:
            return None

        capability = Capability(
            id=str(uuid.uuid4()),
            type=capability_type,
            scope=scope,
            constraints=constraints,
            agent_id=agent_id,
            granted_at=datetime.utcnow(),
            expires_at=expires_at,
            usage_count=0
        )

        client.add_capability(capability)
        return capability

    def revoke_capability(self, client_id: str, capability_id: str) -> bool:
        """Revoke a specific capability."""
        client = self._clients.get(client_id)
        if client is None:
            return False
        return client.remove_capability(capability_id)

    def revoke_agent_capabilities(self, client_id: str, agent_id: str) -> List[str]:
        """
        Revoke all capabilities for an agent on a client.
        Returns list of revoked capability IDs.
        """
        client = self._clients.get(client_id)
        if client is None:
            return []
        return client.revoke_agent_capabilities(agent_id)

    def revoke_agent_capabilities_all_clients(self, agent_id: str) -> Dict[str, List[str]]:
        """
        Revoke all capabilities for an agent across all clients.
        Returns dict of client_id -> list of revoked capability IDs.
        """
        result = {}
        for client_id, client in self._clients.items():
            revoked = client.revoke_agent_capabilities(agent_id)
            if revoked:
                result[client_id] = revoked
        return result

    def update_heartbeat(
        self,
        client_id: str,
        active_capabilities: List[str],
        system_status: Optional[SystemStatus] = None
    ) -> List[str]:
        """
        Update client heartbeat.

        Returns list of capability IDs that should be revoked
        (e.g., expired or server-side revoked).
        """
        client = self._clients.get(client_id)
        if client is None:
            return []

        client.update_last_seen()
        client.system_status = system_status

        # Check for capabilities that should be revoked
        to_revoke = []
        now = datetime.utcnow()

        for cap_id in list(client.capabilities.keys()):
            cap = client.capabilities[cap_id]
            # Check if expired
            if cap.expires_at and cap.expires_at < now:
                to_revoke.append(cap_id)
                del client.capabilities[cap_id]
            # Check if client claims it but we don't have it
            elif cap_id not in active_capabilities:
                # Client doesn't have this capability anymore
                del client.capabilities[cap_id]

        return to_revoke

    def check_timeouts(self) -> List[str]:
        """
        Check for clients that have timed out.
        Returns list of client IDs that were marked offline.
        """
        timeout_threshold = datetime.utcnow() - timedelta(minutes=self.CLIENT_TIMEOUT_MINUTES)
        timed_out = []

        for client_id, client in self._clients.items():
            if client.is_online and client.last_seen_at < timeout_threshold:
                client.mark_offline()
                timed_out.append(client_id)

        return timed_out

    def cleanup_expired_tokens(self):
        """Remove expired tokens from the registry."""
        with self._client_lock:
            now = datetime.utcnow()
            expired_tokens = [
                token for token, auth in self._tokens.items()
                if auth.expires_at < now
            ]
            for token in expired_tokens:
                auth = self._tokens.pop(token, None)
                if auth:
                    self._refresh_tokens.pop(auth.refresh_token, None)


# Singleton accessor
_registry: Optional[ClientRegistry] = None


def get_client_registry() -> ClientRegistry:
    """Get the singleton ClientRegistry instance."""
    global _registry
    if _registry is None:
        _registry = ClientRegistry()
    return _registry

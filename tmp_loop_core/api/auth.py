"""
Authentication & Multi-Tenant Module
====================================

Provides authentication and multi-tenant filtering for the Agentic Loop API.

Authentication Flow:
1. Login (POST /api/auth/login) - Validates credentials, returns token
2. Token stored in user record (auth_token field) and set as cookie
3. Every protected API call validates token via _get_current_user()
4. Logout clears the auth_token

Multi-Tenant Filtering:
- User record contains company_id
- platform_admin role sees all data
- Other roles see only their company's data
"""

import hashlib
import secrets
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# ============================================================================
# DATA DIRECTORY
# ============================================================================

def get_data_dir() -> Path:
    """Get the data directory for storing users and companies."""
    # Use absolute path based on project root to avoid issues with working directory
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    data_dir = project_root / "data" / "loopCore" / "AUTH"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# ============================================================================
# PASSWORD HASHING
# ============================================================================

def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Hash password using PBKDF2-SHA256 with 100k iterations."""
    if salt is None:
        salt = secrets.token_hex(16)

    hashed = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000  # 100k iterations
    ).hex()

    return hashed, salt


def verify_password(password: str, hashed: str, salt: str) -> bool:
    """Verify a password against its hash."""
    computed_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(computed_hash, hashed)


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class Company:
    """Company/tenant record."""
    company_id: str
    name: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    plan_tier: str = "free"  # free, pro, enterprise
    quota_agents: int = 2
    quota_tasks_per_agent: int = 5
    quota_runs_per_day: int = 100
    quota_skills: int = 3
    enabled: bool = True
    settings: dict = field(default_factory=dict)


@dataclass
class User:
    """User record."""
    user_id: str
    email: str
    company_id: str
    role: str  # platform_admin, admin, editor, viewer
    password_hash: str = ""
    password_salt: str = ""
    auth_token: Optional[str] = None
    auth_token_created: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_login: Optional[str] = None
    display_name: Optional[str] = None

    @property
    def is_platform_admin(self) -> bool:
        return self.role == "platform_admin"

    @property
    def is_company_admin(self) -> bool:
        return self.role in ["platform_admin", "admin"]

    @property
    def can_edit(self) -> bool:
        return self.role in ["platform_admin", "admin", "editor"]


# ============================================================================
# STORES
# ============================================================================

class CompanyStore:
    """Persistent store for companies."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or get_data_dir()
        self.file_path = self.data_dir / "companies.json"
        self._companies: dict[str, Company] = {}
        self._load()

    def _load(self):
        """Load companies from disk."""
        if self.file_path.exists():
            try:
                data = json.loads(self.file_path.read_text())
                for company_data in data.get("companies", []):
                    company = Company(**company_data)
                    self._companies[company.company_id] = company
            except Exception as e:
                logger.error(f"Failed to load companies: {e}")

    def _save(self):
        """Save companies to disk."""
        data = {
            "companies": [asdict(c) for c in self._companies.values()]
        }
        self.file_path.write_text(json.dumps(data, indent=2))

    def get(self, company_id: str) -> Optional[Company]:
        """Get a company by ID."""
        return self._companies.get(company_id)

    def get_all(self) -> List[Company]:
        """Get all companies."""
        return list(self._companies.values())

    def create(self, company: Company) -> Company:
        """Create a new company."""
        if company.company_id in self._companies:
            raise ValueError(f"Company {company.company_id} already exists")
        self._companies[company.company_id] = company
        self._save()
        return company

    def update(self, company: Company) -> Company:
        """Update an existing company."""
        if company.company_id not in self._companies:
            raise ValueError(f"Company {company.company_id} not found")
        self._companies[company.company_id] = company
        self._save()
        return company

    def delete(self, company_id: str) -> bool:
        """Delete a company."""
        if company_id in self._companies:
            del self._companies[company_id]
            self._save()
            return True
        return False


class UserStore:
    """Persistent store for users."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or get_data_dir()
        self.file_path = self.data_dir / "users.json"
        self._users: dict[str, User] = {}
        self._load()

    def _load(self):
        """Load users from disk."""
        if self.file_path.exists():
            try:
                data = json.loads(self.file_path.read_text())
                for user_data in data.get("users", []):
                    user = User(**user_data)
                    self._users[user.user_id] = user
            except Exception as e:
                logger.error(f"Failed to load users: {e}")

    def _save(self):
        """Save users to disk."""
        data = {
            "users": [asdict(u) for u in self._users.values()]
        }
        self.file_path.write_text(json.dumps(data, indent=2))

    def get(self, user_id: str) -> Optional[User]:
        """Get a user by ID."""
        return self._users.get(user_id)

    def get_by_email(self, email: str) -> Optional[User]:
        """Get a user by email."""
        email_lower = email.lower()
        for user in self._users.values():
            if user.email.lower() == email_lower:
                return user
        return None

    def get_by_token(self, token: str) -> Optional[User]:
        """Get a user by auth token."""
        for user in self._users.values():
            if user.auth_token and secrets.compare_digest(user.auth_token, token):
                return user
        return None

    def get_by_company(self, company_id: str) -> List[User]:
        """Get all users for a company."""
        return [u for u in self._users.values() if u.company_id == company_id]

    def get_all(self) -> List[User]:
        """Get all users."""
        return list(self._users.values())

    def create(self, user: User, password: str) -> User:
        """Create a new user with password."""
        if user.user_id in self._users:
            raise ValueError(f"User {user.user_id} already exists")
        if self.get_by_email(user.email):
            raise ValueError(f"Email {user.email} already in use")

        # Hash password
        user.password_hash, user.password_salt = hash_password(password)
        self._users[user.user_id] = user
        self._save()
        return user

    def update(self, user: User) -> User:
        """Update an existing user."""
        if user.user_id not in self._users:
            raise ValueError(f"User {user.user_id} not found")
        self._users[user.user_id] = user
        self._save()
        return user

    def set_password(self, user_id: str, password: str) -> bool:
        """Set a user's password."""
        user = self.get(user_id)
        if not user:
            return False
        user.password_hash, user.password_salt = hash_password(password)
        self._save()
        return True

    def delete(self, user_id: str) -> bool:
        """Delete a user."""
        if user_id in self._users:
            del self._users[user_id]
            self._save()
            return True
        return False


# ============================================================================
# SINGLETON STORES
# ============================================================================

_company_store: Optional[CompanyStore] = None
_user_store: Optional[UserStore] = None


def get_company_store() -> CompanyStore:
    """Get the singleton company store."""
    global _company_store
    if _company_store is None:
        _company_store = CompanyStore()
    return _company_store


def get_user_store() -> UserStore:
    """Get the singleton user store."""
    global _user_store
    if _user_store is None:
        _user_store = UserStore()
    return _user_store


# ============================================================================
# AUTH SERVICE
# ============================================================================

class AuthService:
    """Authentication service."""

    def __init__(self):
        self.user_store = get_user_store()
        self.company_store = get_company_store()

    def login(self, email: str, password: str) -> tuple[Optional[User], Optional[str]]:
        """
        Authenticate user and generate token.
        Returns (user, token) on success, (None, error_message) on failure.
        """
        user = self.user_store.get_by_email(email)
        if not user:
            return None, "Invalid email or password"

        if not verify_password(password, user.password_hash, user.password_salt):
            return None, "Invalid email or password"

        # Check if company is enabled
        company = self.company_store.get(user.company_id)
        if company and not company.enabled:
            return None, "Account disabled"

        # Generate new auth token
        token = secrets.token_hex(32)
        user.auth_token = token
        user.auth_token_created = datetime.now().isoformat()
        user.last_login = datetime.now().isoformat()
        self.user_store.update(user)

        return user, None

    def logout(self, user: User) -> bool:
        """Clear user's auth token."""
        user.auth_token = None
        user.auth_token_created = None
        self.user_store.update(user)
        return True

    def validate_token(self, token: str) -> Optional[User]:
        """Validate an auth token and return the user."""
        if not token:
            return None
        return self.user_store.get_by_token(token)

    def get_user_info(self, user: User) -> dict:
        """Get user info for API response (excludes sensitive fields)."""
        company = self.company_store.get(user.company_id)
        return {
            "user_id": user.user_id,
            "email": user.email,
            "display_name": user.display_name or user.email.split("@")[0],
            "company_id": user.company_id,
            "company_name": company.name if company else None,
            "role": user.role,
            "is_platform_admin": user.is_platform_admin,
            "is_company_admin": user.is_company_admin,
            "can_edit": user.can_edit,
        }


# ============================================================================
# FASTAPI INTEGRATION
# ============================================================================

def setup_auth_routes(app):
    """Add authentication routes to FastAPI app."""
    from fastapi import Request, Response, HTTPException, Cookie
    from pydantic import BaseModel

    auth_service = AuthService()

    class LoginRequest(BaseModel):
        email: str
        password: str

    class LoginResponse(BaseModel):
        token: str
        user: dict

    @app.post("/api/auth/login", tags=["Auth"])
    async def login(request: LoginRequest, response: Response):
        """Authenticate user and return token."""
        user, error = auth_service.login(request.email, request.password)
        if error:
            raise HTTPException(status_code=401, detail=error)

        # Set cookie
        response.set_cookie(
            key="auth_token",
            value=user.auth_token,
            httponly=True,
            secure=False,  # Set to True in production with HTTPS
            samesite="lax",
            max_age=86400 * 7  # 7 days
        )

        return {
            "token": user.auth_token,
            "user": auth_service.get_user_info(user)
        }

    @app.post("/api/auth/logout", tags=["Auth"])
    async def logout(
        response: Response,
        auth_token: Optional[str] = Cookie(None)
    ):
        """Logout current user."""
        if auth_token:
            user = auth_service.validate_token(auth_token)
            if user:
                auth_service.logout(user)

        response.delete_cookie("auth_token")
        return {"status": "ok"}

    @app.get("/api/auth/me", tags=["Auth"])
    async def get_current_user_info(
        auth_token: Optional[str] = Cookie(None)
    ):
        """Get current user info."""
        if not auth_token:
            raise HTTPException(status_code=401, detail="Not authenticated")

        user = auth_service.validate_token(auth_token)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")

        return auth_service.get_user_info(user)


def get_current_user(auth_token: Optional[str] = None) -> Optional[User]:
    """
    Get the current authenticated user from token.
    Use this in API endpoints to get tenant context.
    """
    if not auth_token:
        return None
    return get_user_store().get_by_token(auth_token)


def get_current_user_dependency():
    """
    FastAPI dependency that extracts current user from cookie or header.
    Returns None if not authenticated (for optional auth).
    """
    from fastapi import Cookie, Header

    async def _get_current_user(
        auth_token: Optional[str] = Cookie(None),
        authorization: Optional[str] = Header(None)
    ) -> Optional[User]:
        # Try cookie first
        if auth_token:
            user = get_user_store().get_by_token(auth_token)
            if user:
                return user

        # Try Authorization header (Bearer token)
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
            user = get_user_store().get_by_token(token)
            if user:
                return user

        return None

    return _get_current_user


def require_auth_dependency():
    """
    FastAPI dependency that requires authentication.
    Raises 401 if not authenticated.
    """
    from fastapi import Cookie, Header, HTTPException

    async def _require_auth(
        auth_token: Optional[str] = Cookie(None),
        authorization: Optional[str] = Header(None)
    ) -> User:
        # Try cookie first
        if auth_token:
            user = get_user_store().get_by_token(auth_token)
            if user:
                return user

        # Try Authorization header (Bearer token)
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
            user = get_user_store().get_by_token(token)
            if user:
                return user

        raise HTTPException(status_code=401, detail="Not authenticated")

    return _require_auth


def require_platform_admin_dependency():
    """
    FastAPI dependency that requires platform_admin role.
    Raises 401 if not authenticated, 403 if not platform_admin.
    """
    from fastapi import Cookie, Header, HTTPException

    async def _require_platform_admin(
        auth_token: Optional[str] = Cookie(None),
        authorization: Optional[str] = Header(None)
    ) -> User:
        user = None

        # Try cookie first
        if auth_token:
            user = get_user_store().get_by_token(auth_token)

        # Try Authorization header (Bearer token)
        if not user and authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
            user = get_user_store().get_by_token(token)

        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")

        if not user.is_platform_admin:
            raise HTTPException(status_code=403, detail="Platform admin access required")

        return user

    return _require_platform_admin


# ============================================================================
# MULTI-TENANT FILTER HELPER
# ============================================================================

def apply_tenant_filter(user: Optional[User], items: list, company_id_field: str = "company_id") -> list:
    """
    Filter items by tenant (company_id).
    Platform admins see all items, others see only their company's items.

    Args:
        user: Current authenticated user
        items: List of items to filter
        company_id_field: Name of the company_id field on items

    Returns:
        Filtered list of items
    """
    if not user:
        return []

    if user.is_platform_admin:
        return items

    return [
        item for item in items
        if getattr(item, company_id_field, None) == user.company_id
        or (isinstance(item, dict) and item.get(company_id_field) == user.company_id)
    ]


# ============================================================================
# BOOTSTRAP HELPER
# ============================================================================

def bootstrap_default_users():
    """
    Create default platform admin and demo company/user if they don't exist.
    Call this on startup to ensure there's always a way to login.
    """
    company_store = get_company_store()
    user_store = get_user_store()

    # Create default company if none exist
    if not company_store.get_all():
        default_company = Company(
            company_id="default",
            name="Default Company",
            plan_tier="enterprise",
            quota_agents=100,
            quota_tasks_per_agent=100,
            quota_runs_per_day=10000,
            quota_skills=100
        )
        company_store.create(default_company)
        logger.info("Created default company")

    # Create platform admin if none exist
    platform_admins = [u for u in user_store.get_all() if u.is_platform_admin]
    if not platform_admins:
        admin_user = User(
            user_id="admin",
            email="admin@localhost",
            company_id="default",
            role="platform_admin",
            display_name="Platform Admin"
        )
        user_store.create(admin_user, "admin")  # Default password: admin
        logger.info("Created default platform admin (admin@localhost / admin)")

    return True

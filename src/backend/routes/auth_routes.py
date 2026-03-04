"""Auth, user, invite, and API key endpoints."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from backend.routes.helpers import _require_role, _user_to_safe
from shared.models import (
    AcceptInviteRequest,
    AdminResetPasswordRequest,
    ApiKeyCreateRequest,
    InviteRequest,
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    QuickstartClaimRequest,
    RegisterRequest,
    UserCreate,
    UserSafe,
    UserUpdate,
)

router = APIRouter(tags=["auth"])


# ═══════════════════════════════════════════════════════════════════════════
#  AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/v1/auth/login")
async def login(body: LoginRequest, request: Request, tenant_id: str = Query(...)):
    """Email+password login. Returns JWT token."""
    from backend.auth import verify_password, create_token
    storage = request.app.state.storage
    user = await storage.get_user_by_email(tenant_id, body.email)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, {
            "error": "authentication_failed",
            "message": "Invalid email or password",
            "status": 401,
        })
    token, expires_in = create_token(user.user_id, user.tenant_id, user.role)
    # Update last_login_at
    await storage.update_user(
        user.tenant_id, user.user_id,
        last_login_at=datetime.now(timezone.utc),
    )
    safe = UserSafe(
        user_id=user.user_id, tenant_id=user.tenant_id,
        email=user.email, name=user.name, role=user.role,
        is_active=user.is_active, created_at=user.created_at,
        updated_at=user.updated_at, last_login_at=user.last_login_at,
        settings=user.settings,
    )
    tenant = await storage.get_tenant(user.tenant_id)
    return LoginResponse(
        token=token, expires_in=expires_in, user=safe,
        tenant_name=tenant.name if tenant else None,
        tenant_slug=tenant.slug if tenant else None,
    ).model_dump(mode="json")


@router.post("/v1/auth/register", status_code=201)
async def register(body: RegisterRequest, request: Request):
    """Register a new tenant + owner user + default project + API key."""
    from backend.auth import generate_api_key, hash_password
    from shared.enums import DEFAULT_ACCESS_ID_LABEL
    storage = request.app.state.storage
    logger = logging.getLogger("hiveboard.auth")

    # Check email not already registered
    existing = await storage.get_user_by_email_global(body.email)
    if existing:
        raise HTTPException(409, {
            "error": "email_exists",
            "message": "Email already registered",
            "status": 409,
        })

    # Check for pending invite
    for row in storage._tables.get("invites", []):
        if (
            row["email"].lower() == body.email.lower()
            and not row.get("is_accepted", False)
        ):
            from backend.storage_json import _parse_dt as _sj_parse_dt, _now_utc
            exp = _sj_parse_dt(row["expires_at"])
            if exp and exp > _now_utc():
                raise HTTPException(409, {
                    "error": "pending_invite",
                    "message": "You have a pending invite. Use accept-invite instead.",
                    "status": 409,
                })

    # Generate slug from tenant_name and check uniqueness
    slug = body.tenant_name.lower().replace(" ", "-")
    existing_tenant = await storage.get_tenant_by_slug(slug)
    if existing_tenant:
        raise HTTPException(409, {
            "error": "slug_exists",
            "message": f"A workspace with this name already exists (slug: {slug})",
            "status": 409,
        })

    tenant_id = str(uuid4())
    user_id = str(uuid4())

    # Create tenant (auto-creates default project)
    tenant = await storage.create_tenant(tenant_id, body.tenant_name, slug)

    # Create owner user
    user = await storage.create_user(
        user_id=user_id,
        tenant_id=tenant_id,
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        role="owner",
    )

    # Generate API key
    raw_key, key_hash, key_prefix = generate_api_key("live")
    key_id = str(uuid4())
    await storage.create_api_key(
        key_id=key_id,
        tenant_id=tenant_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        key_type="live",
        label="Default API Key",
        created_by_user_id=user_id,
    )

    # Generate read-only access_id for dashboard
    raw_access_id, access_hash, access_prefix = generate_api_key("read")
    access_key_id = str(uuid4())
    await storage.create_api_key(
        key_id=access_key_id,
        tenant_id=tenant_id,
        key_hash=access_hash,
        key_prefix=access_prefix,
        key_type="read",
        label=DEFAULT_ACCESS_ID_LABEL,
        created_by_user_id=user_id,
    )

    logger.info("New registration: %s (tenant: %s)", body.email, slug)

    return JSONResponse(
        status_code=201,
        content={
            "user": _user_to_safe(user),
            "tenant": {"tenant_id": tenant_id, "name": body.tenant_name, "slug": slug},
            "api_key": raw_key,
            "access_id": raw_access_id,
        },
    )


@router.get("/v1/auth/check-slug")
async def check_slug(slug: str, request: Request):
    """Check if a tenant slug is available. Public endpoint for registration form validation."""
    storage = request.app.state.storage
    normalized = slug.lower().replace(" ", "-")
    existing = await storage.get_tenant_by_slug(normalized)
    return {"slug": normalized, "available": existing is None}


@router.get("/v1/access-id")
async def get_access_id(request: Request):
    """Return the active read key prefix + metadata for the current tenant."""
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    rec = await storage.get_active_read_key(tenant_id)
    if rec is None:
        return {"access_id": None}
    return {
        "access_id_prefix": rec.key_prefix,
        "key_id": rec.key_id,
        "label": rec.label,
        "created_at": rec.created_at.isoformat() if hasattr(rec.created_at, "isoformat") else str(rec.created_at),
    }


@router.post("/v1/access-id/regenerate")
async def regenerate_access_id(request: Request):
    """Revoke all active read keys and create a new one. JWT owner/admin only."""
    from backend.auth import generate_api_key
    from shared.enums import DEFAULT_ACCESS_ID_LABEL

    auth_type = getattr(request.state, "auth_type", None)
    if auth_type != "jwt":
        raise HTTPException(403, {
            "error": "jwt_required",
            "message": "Access ID regeneration requires JWT authentication",
            "status": 403,
        })
    _require_role(request, ["owner", "admin"])

    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    user_id = request.state.user_id

    # Revoke all active read keys
    for row in list(storage._tables["api_keys"]):
        if row["tenant_id"] == tenant_id and row["key_type"] == "read" and row.get("is_active", True):
            await storage.revoke_api_key(tenant_id, row["key_id"])

    # Create new read key
    raw_access_id, access_hash, access_prefix = generate_api_key("read")
    access_key_id = str(uuid4())
    await storage.create_api_key(
        key_id=access_key_id,
        tenant_id=tenant_id,
        key_hash=access_hash,
        key_prefix=access_prefix,
        key_type="read",
        label=DEFAULT_ACCESS_ID_LABEL,
        created_by_user_id=user_id,
    )

    return {"access_id": raw_access_id}


@router.post("/v1/auth/quickstart", status_code=201)
async def quickstart(request: Request):
    """1-click workspace creation — no user account required.

    Creates a tenant, default project, and API key. Returns a claim_token
    that can be used later to attach a real user via POST /v1/auth/claim.
    """
    from secrets import token_hex
    from backend.auth import generate_api_key, generate_invite_token
    from shared.enums import QUICKSTART_CLAIM_EXPIRY_SECONDS, DEFAULT_ACCESS_ID_LABEL

    storage = request.app.state.storage
    logger = logging.getLogger("hiveboard.auth")

    tenant_id = str(uuid4())
    slug = "quickstart-" + token_hex(4)

    # Create tenant (auto-creates default project)
    tenant = await storage.create_tenant(tenant_id, slug, slug)

    # Generate API key
    raw_key, key_hash, key_prefix = generate_api_key("live")
    key_id = str(uuid4())
    await storage.create_api_key(
        key_id=key_id,
        tenant_id=tenant_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        key_type="live",
        label="Quickstart Key",
        created_by_user_id=None,
    )

    # Generate read-only access_id for dashboard
    raw_access_id, access_hash, access_prefix = generate_api_key("read")
    access_key_id = str(uuid4())
    await storage.create_api_key(
        key_id=access_key_id,
        tenant_id=tenant_id,
        key_hash=access_hash,
        key_prefix=access_prefix,
        key_type="read",
        label=DEFAULT_ACCESS_ID_LABEL,
        created_by_user_id=None,
    )

    # Generate claim token
    raw_token, token_hash = generate_invite_token()
    claim_id = str(uuid4())
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=QUICKSTART_CLAIM_EXPIRY_SECONDS)

    await storage.create_pending_claim(
        claim_id=claim_id,
        tenant_id=tenant_id,
        claim_token_hash=token_hash,
        expires_at=expires_at,
    )

    logger.info("Quickstart workspace created: %s (tenant: %s)", slug, tenant_id)

    return {
        "tenant_id": tenant_id,
        "tenant_name": slug,
        "tenant_slug": slug,
        "api_key": raw_key,
        "access_id": raw_access_id,
        "claim_token": raw_token,
    }


@router.post("/v1/auth/claim")
async def claim_workspace(body: QuickstartClaimRequest, request: Request):
    """Claim a quickstart workspace by providing credentials.

    Attaches a real user (owner) to the previously-created tenant.
    Returns the same shape as login/accept-invite (LoginResponse).
    """
    from backend.auth import hash_password, create_token

    storage = request.app.state.storage

    # Hash token and look up claim
    token_hash = hashlib.sha256(body.claim_token.encode()).hexdigest()
    claim = await storage.get_pending_claim_by_token_hash(token_hash)
    if claim is None:
        raise HTTPException(404, {
            "error": "not_found",
            "message": "Claim token not found or expired",
            "status": 404,
        })

    # Check email not already registered
    existing = await storage.get_user_by_email_global(body.email)
    if existing:
        raise HTTPException(409, {
            "error": "email_exists",
            "message": "Email already registered",
            "status": 409,
        })

    # Create owner user in the claim's tenant
    user_id = str(uuid4())
    user = await storage.create_user(
        user_id=user_id,
        tenant_id=claim.tenant_id,
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        role="owner",
    )

    # Mark claim as used
    await storage.mark_claim_used(claim.claim_id)

    # Create JWT
    token, expires_in = create_token(user.user_id, user.tenant_id, user.role)
    safe = UserSafe(
        user_id=user.user_id, tenant_id=user.tenant_id,
        email=user.email, name=user.name, role=user.role,
        is_active=user.is_active, created_at=user.created_at,
        updated_at=user.updated_at, last_login_at=user.last_login_at,
        settings=user.settings,
    )

    # Look up tenant for response
    tenant = await storage.get_tenant(claim.tenant_id)

    return LoginResponse(
        token=token, expires_in=expires_in, user=safe,
        tenant_name=tenant.name if tenant else None,
        tenant_slug=tenant.slug if tenant else None,
    ).model_dump(mode="json")


@router.post("/v1/auth/accept-invite")
async def accept_invite(body: AcceptInviteRequest, request: Request):
    """Accept an invite and join a tenant."""
    from backend.auth import create_token, hash_password
    storage = request.app.state.storage

    # Hash token and lookup invite
    token_hash = hashlib.sha256(body.invite_token.encode()).hexdigest()
    invite = await storage.get_invite_by_token_hash(token_hash)
    if invite is None:
        raise HTTPException(404, {
            "error": "not_found",
            "message": "Invite not found or expired",
            "status": 404,
        })

    # Check email not already registered
    existing = await storage.get_user_by_email_global(invite.email)
    if existing:
        raise HTTPException(409, {
            "error": "email_exists",
            "message": "Email already registered",
            "status": 409,
        })

    # Create user in invite's tenant
    user_id = str(uuid4())
    user = await storage.create_user(
        user_id=user_id,
        tenant_id=invite.tenant_id,
        email=invite.email,
        password_hash=hash_password(body.password),
        name=body.name,
        role=invite.role,
    )

    # Mark invite accepted
    await storage.mark_invite_accepted(invite.invite_id)

    # Create JWT
    token, expires_in = create_token(user.user_id, user.tenant_id, user.role)
    safe = UserSafe(
        user_id=user.user_id, tenant_id=user.tenant_id,
        email=user.email, name=user.name, role=user.role,
        is_active=user.is_active, created_at=user.created_at,
        updated_at=user.updated_at, last_login_at=user.last_login_at,
        settings=user.settings,
    )
    return LoginResponse(
        token=token, expires_in=expires_in, user=safe,
    ).model_dump(mode="json")


@router.post("/v1/auth/invite", status_code=201)
async def invite_user(body: InviteRequest, request: Request):
    """Owner/admin invites a user by email."""
    from backend.auth import generate_invite_token
    from shared.enums import INVITE_EXPIRY_SECONDS
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    logger = logging.getLogger("hiveboard.auth")

    # Role escalation check
    caller_role = getattr(request.state, "user_role", None)
    auth_type = getattr(request.state, "auth_type", None)
    if body.role in ("owner", "admin"):
        if auth_type == "jwt" and caller_role != "owner":
            raise HTTPException(403, {
                "error": "role_escalation",
                "message": "Only owners can invite as owner or admin",
                "status": 403,
            })

    # Check email not already in this tenant
    existing_in_tenant = await storage.get_user_by_email(tenant_id, body.email)
    if existing_in_tenant:
        raise HTTPException(409, {
            "error": "email_exists",
            "message": "Email already registered in this organization",
            "status": 409,
        })

    # Check email not registered elsewhere
    existing_global = await storage.get_user_by_email_global(body.email)
    if existing_global:
        raise HTTPException(409, {
            "error": "email_exists",
            "message": "Email registered with another organization",
            "status": 409,
        })

    # Check no pending invite
    pending = await storage.get_pending_invite(tenant_id, body.email)
    if pending:
        raise HTTPException(400, {
            "error": "invite_exists",
            "message": "A pending invite already exists for this email",
            "status": 400,
        })

    # Generate invite token
    raw_token, token_hash = generate_invite_token()
    invite_id = str(uuid4())
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=INVITE_EXPIRY_SECONDS)

    caller_user_id = getattr(request.state, "user_id", None) or "api_key"
    invite = await storage.create_invite(
        invite_id=invite_id,
        tenant_id=tenant_id,
        email=body.email,
        role=body.role,
        name=body.name,
        invite_token_hash=token_hash,
        created_by_user_id=caller_user_id,
        expires_at=expires_at,
    )

    logger.info("Invite created for %s (token: %s)", body.email, raw_token)

    return JSONResponse(
        status_code=201,
        content={
            "invite_id": invite_id,
            "email": body.email,
            "role": body.role,
            "tenant_id": tenant_id,
            "expires_at": invite.expires_at.isoformat() if hasattr(invite.expires_at, 'isoformat') else str(invite.expires_at),
            "invite_token": raw_token,
        },
    )


@router.post("/v1/auth/change-password")
async def change_password(body: PasswordChangeRequest, request: Request):
    """Change password for the currently authenticated JWT user."""
    from backend.auth import verify_password, hash_password
    auth_type = getattr(request.state, "auth_type", None)
    if auth_type != "jwt":
        raise HTTPException(403, {
            "error": "jwt_required",
            "message": "Password change requires JWT authentication",
            "status": 403,
        })
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    user_id = request.state.user_id
    user = await storage.get_user(tenant_id, user_id)
    if user is None:
        raise HTTPException(404, {"error": "not_found", "message": "User not found", "status": 404})
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(401, {
            "error": "authentication_failed",
            "message": "Current password is incorrect",
            "status": 401,
        })
    await storage.update_user(
        tenant_id, user_id,
        password_hash=hash_password(body.new_password),
    )
    return {"status": "password_changed"}


@router.post("/v1/auth/reset-password/{user_id}")
async def admin_reset_password(user_id: str, body: AdminResetPasswordRequest, request: Request):
    """Owner/admin force-resets a user's password. No current password needed."""
    from backend.auth import hash_password
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    user = await storage.get_user(tenant_id, user_id)
    if user is None:
        raise HTTPException(404, {"error": "not_found", "message": "User not found", "status": 404})
    await storage.update_user(
        tenant_id, user_id,
        password_hash=hash_password(body.new_password),
    )
    return {"status": "password_reset", "user_id": user_id, "email": user.email}


# ═══════════════════════════════════════════════════════════════════════════
#  API KEY CRUD ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/api-keys")
async def list_api_keys_endpoint(request: Request):
    """List API keys. Owner/admin see all; others see own keys only."""
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    user_role = getattr(request.state, "user_role", None)
    user_id = getattr(request.state, "user_id", None)
    auth_type = getattr(request.state, "auth_type", None)

    if auth_type == "api_key" or user_role in ("owner", "admin"):
        keys = await storage.list_api_keys(tenant_id)
    else:
        keys = await storage.list_api_keys_by_user(tenant_id, user_id) if user_id else []

    # Omit key_hash, show metadata
    result = []
    for k in keys:
        result.append({
            "key_id": k.key_id,
            "key_prefix": k.key_prefix,
            "key_type": k.key_type,
            "label": k.label,
            "created_by_user_id": k.created_by_user_id,
            "created_at": k.created_at.isoformat() if hasattr(k.created_at, 'isoformat') else str(k.created_at),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at and hasattr(k.last_used_at, 'isoformat') else str(k.last_used_at) if k.last_used_at else None,
            "is_active": k.is_active,
        })
    return {"data": result}


@router.post("/v1/api-keys", status_code=201)
async def create_api_key_endpoint(body: ApiKeyCreateRequest, request: Request):
    """Create a new API key."""
    from backend.auth import generate_api_key
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    user_id = getattr(request.state, "user_id", None)
    user_role = getattr(request.state, "user_role", None)

    # Validate key_type based on role
    if user_role == "viewer" and body.key_type != "read":
        raise HTTPException(403, {
            "error": "insufficient_permissions",
            "message": "Viewers can only create read keys",
            "status": 403,
        })

    raw_key, key_hash, key_prefix = generate_api_key(body.key_type)
    key_id = str(uuid4())
    rec = await storage.create_api_key(
        key_id=key_id,
        tenant_id=tenant_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        key_type=body.key_type,
        label=body.label,
        created_by_user_id=user_id,
    )

    return JSONResponse(
        status_code=201,
        content={
            "key_id": key_id,
            "key_prefix": key_prefix,
            "key_type": body.key_type,
            "label": body.label,
            "raw_key": raw_key,
            "created_at": rec.created_at.isoformat() if hasattr(rec.created_at, 'isoformat') else str(rec.created_at),
        },
    )


@router.delete("/v1/api-keys/{key_id}")
async def revoke_api_key_endpoint(key_id: str, request: Request):
    """Revoke an API key."""
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    user_role = getattr(request.state, "user_role", None)
    user_id = getattr(request.state, "user_id", None)
    auth_type = getattr(request.state, "auth_type", None)

    # Non-owner/admin can only revoke own keys
    if auth_type == "jwt" and user_role not in ("owner", "admin"):
        # Check if key belongs to user
        user_keys = await storage.list_api_keys_by_user(tenant_id, user_id) if user_id else []
        if not any(k.key_id == key_id for k in user_keys):
            raise HTTPException(403, {
                "error": "insufficient_permissions",
                "message": "Can only revoke your own keys",
                "status": 403,
            })

    ok = await storage.revoke_api_key(tenant_id, key_id)
    if not ok:
        raise HTTPException(404, {"error": "not_found", "message": "API key not found", "status": 404})
    return {"status": "revoked"}


# ═══════════════════════════════════════════════════════════════════════════
#  INVITE MANAGEMENT ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/invites")
async def list_invites_endpoint(request: Request):
    """List pending invites for tenant (owner/admin only)."""
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    invites = await storage.list_invites(tenant_id)
    result = []
    for inv in invites:
        result.append({
            "invite_id": inv.invite_id,
            "email": inv.email,
            "role": inv.role,
            "name": inv.name,
            "is_accepted": inv.is_accepted,
            "created_at": inv.created_at.isoformat() if hasattr(inv.created_at, 'isoformat') else str(inv.created_at),
            "expires_at": inv.expires_at.isoformat() if hasattr(inv.expires_at, 'isoformat') else str(inv.expires_at),
            "accepted_at": inv.accepted_at.isoformat() if inv.accepted_at and hasattr(inv.accepted_at, 'isoformat') else str(inv.accepted_at) if inv.accepted_at else None,
        })
    return {"data": result}


@router.delete("/v1/invites/{invite_id}")
async def cancel_invite(invite_id: str, request: Request):
    """Cancel a pending invite (owner/admin only)."""
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    # Find and remove the invite
    async with storage._locks["invites"]:
        before = len(storage._tables["invites"])
        storage._tables["invites"] = [
            r for r in storage._tables["invites"]
            if not (
                r["tenant_id"] == tenant_id
                and r["invite_id"] == invite_id
                and not r.get("is_accepted", False)
            )
        ]
        if len(storage._tables["invites"]) < before:
            storage._persist("invites")
            return {"status": "cancelled"}
    raise HTTPException(404, {"error": "not_found", "message": "Invite not found", "status": 404})


# ═══════════════════════════════════════════════════════════════════════════
#  USER ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/users")
async def list_users(
    request: Request,
    role: str | None = None,
    is_active: bool | None = None,
):
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    users = await storage.list_users(tenant_id, role=role, is_active=is_active)
    return {"data": [_user_to_safe(u) for u in users]}


@router.post("/v1/users", status_code=201)
async def create_user(body: UserCreate, request: Request):
    from backend.auth import hash_password
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    # Role escalation protection: only owner can create owner/admin
    if body.role in ("owner", "admin"):
        caller_role = getattr(request.state, "user_role", None)
        auth_type = getattr(request.state, "auth_type", None)
        if auth_type == "jwt" and caller_role != "owner":
            raise HTTPException(403, {
                "error": "role_escalation",
                "message": "Only owners can create owner or admin users",
                "status": 403,
            })

    user_id = str(uuid4())
    pw_hash = hash_password(body.password)
    try:
        user = await storage.create_user(
            user_id=user_id,
            tenant_id=tenant_id,
            email=body.email,
            password_hash=pw_hash,
            name=body.name,
            role=body.role,
        )
    except ValueError as e:
        raise HTTPException(409, {
            "error": "duplicate_email",
            "message": str(e),
            "status": 409,
        })
    return JSONResponse(content=_user_to_safe(user), status_code=201)


@router.get("/v1/users/me")
async def get_current_user(request: Request):
    """Get current user profile (JWT only)."""
    auth_type = getattr(request.state, "auth_type", None)
    if auth_type != "jwt":
        raise HTTPException(403, {
            "error": "jwt_required",
            "message": "This endpoint requires JWT authentication",
            "status": 403,
        })
    storage = request.app.state.storage
    user = await storage.get_user(request.state.tenant_id, request.state.user_id)
    if user is None:
        raise HTTPException(404, {"error": "not_found", "message": "User not found", "status": 404})
    return _user_to_safe(user)


@router.get("/v1/users/{user_id}")
async def get_user_endpoint(user_id: str, request: Request):
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    user = await storage.get_user(tenant_id, user_id)
    if user is None:
        raise HTTPException(404, {"error": "not_found", "message": "User not found", "status": 404})
    return _user_to_safe(user)


@router.put("/v1/users/{user_id}")
async def update_user_endpoint(user_id: str, body: UserUpdate, request: Request):
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    # Role escalation protection
    if body.role in ("owner", "admin"):
        caller_role = getattr(request.state, "user_role", None)
        auth_type = getattr(request.state, "auth_type", None)
        if auth_type == "jwt" and caller_role != "owner":
            raise HTTPException(403, {
                "error": "role_escalation",
                "message": "Only owners can assign owner or admin roles",
                "status": 403,
            })

    kwargs = {}
    if body.email is not None:
        kwargs["email"] = body.email
    if body.name is not None:
        kwargs["name"] = body.name
    if body.role is not None:
        kwargs["role"] = body.role
    if body.settings is not None:
        kwargs["settings"] = body.settings

    try:
        user = await storage.update_user(tenant_id, user_id, **kwargs)
    except ValueError as e:
        raise HTTPException(409, {
            "error": "duplicate_email",
            "message": str(e),
            "status": 409,
        })
    if user is None:
        raise HTTPException(404, {"error": "not_found", "message": "User not found", "status": 404})
    return _user_to_safe(user)


@router.delete("/v1/users/{user_id}")
async def deactivate_user_endpoint(user_id: str, request: Request):
    """Soft-delete a user (deactivate). Can't self-deactivate."""
    _require_role(request, ["owner", "admin"])
    # Block self-deactivation
    caller_user_id = getattr(request.state, "user_id", None)
    if caller_user_id == user_id:
        raise HTTPException(400, {
            "error": "self_deactivation",
            "message": "Cannot deactivate your own account",
            "status": 400,
        })
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    ok = await storage.deactivate_user(tenant_id, user_id)
    if not ok:
        raise HTTPException(404, {"error": "not_found", "message": "User not found", "status": 404})
    return {"status": "deactivated"}


@router.post("/v1/users/{user_id}/reactivate")
async def reactivate_user_endpoint(user_id: str, request: Request):
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    ok = await storage.reactivate_user(tenant_id, user_id)
    if not ok:
        raise HTTPException(404, {"error": "not_found", "message": "User not found or already active", "status": 404})
    return {"status": "reactivated"}

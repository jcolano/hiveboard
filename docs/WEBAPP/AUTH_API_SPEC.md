# HiveBoard Frontend API Spec

**Date:** 2026-02-14
**Version:** 2.0

---

## Table of Contents

1. [Global Rules](#1-global-rules)
2. [Auth Endpoints](#2-auth-endpoints) — Login, Register, Check Slug, Accept Invite
3. [Invite Management](#3-invite-management) — Send, List, Cancel invites
4. [API Key Management](#4-api-key-management) — Create, List, Revoke keys
5. [Project Management](#5-project-management) — CRUD, Archive, Merge
6. [Frontend Pages Required](#6-frontend-pages-required)
7. [Removed Endpoints](#7-removed-endpoints)

---

## 1. Global Rules

### 1.1 One Email = One Tenant

**A single email address can only belong to one workspace (tenant).** This is enforced globally across the entire system.

| Scenario | What happens | Error key | Frontend message |
|---|---|---|---|
| User registers with an email that already exists | 409 rejected | `email_exists` | *"This email is already registered. If you already have an account, please sign in instead."* |
| User registers, but email has a pending invite from another tenant | 409 rejected | `pending_invite` | *"You have a pending invitation to join a workspace. Please check your email and accept the invite instead."* |
| Owner invites an email that is registered in another tenant | 409 rejected | `email_exists` | *"This email is already registered with another organization. The person must use a different email address to join your workspace."* |
| Owner invites an email already in their own tenant | 409 rejected | `email_exists` | *"This email is already a member of your workspace."* |
| Invited user accepts, but email was registered elsewhere in the meantime | 409 rejected | `email_exists` | *"This email is already registered with another account. Please contact your administrator."* |

### 1.2 Authentication

All authenticated endpoints require an `Authorization` header:

```
Authorization: Bearer {jwt_token_or_api_key}
```

- **JWT tokens** are returned by login and accept-invite endpoints. They expire after 1 hour.
- **API keys** start with `hb_` prefix (e.g. `hb_live_...`). They don't expire but can be revoked.
- Both work interchangeably for most endpoints.

### 1.3 Role Hierarchy

| Role | Can manage users/invites | Can manage API keys | Can manage projects |
|---|---|---|---|
| `owner` | Yes (all roles) | Yes (all) | Yes |
| `admin` | Yes (member/viewer only) | Yes (all) | Yes |
| `member` | No | Own keys only | Yes |
| `viewer` | No | Own read keys only | Read only |

### 1.4 localStorage Keys

| Key | Value | Set when |
|---|---|---|
| `hb_token` | JWT string | Login or accept-invite |
| `hb_token_type` | `"bearer"` | Login or accept-invite |
| `hb_user` | JSON-encoded user object | Login or accept-invite |
| `hb_tenant_id` | Tenant ID string | Registration (from `response.tenant.tenant_id`) |

---

## 2. Auth Endpoints

### 2.1 Login — `POST /v1/auth/login?tenant_id={tid}`

**Public (no auth required).**

#### Request

```
POST /v1/auth/login?tenant_id=dev
Content-Type: application/json

{
  "email": "jane@company.com",
  "password": "their-password"
}
```

> **Note:** `tenant_id` is a required query parameter. For MVP, use the stored `hb_tenant_id` from localStorage or fall back to `"dev"`.

#### Response — 200 OK

```json
{
  "token": "eyJhbGci...",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "user_id": "abc-123",
    "tenant_id": "dev",
    "email": "jane@company.com",
    "name": "Jane Doe",
    "role": "owner",
    "is_active": true,
    "created_at": "2026-02-14T10:00:00Z",
    "updated_at": "2026-02-14T10:00:00Z",
    "last_login_at": "2026-02-14T12:00:00Z",
    "settings": {}
  }
}
```

#### Errors

| Status | Error key | Frontend message |
|---|---|---|
| 401 | `authentication_failed` | *"Invalid email or password."* |

---

### 2.2 Registration — `POST /v1/auth/register`

**Public (no auth required).** Creates a new tenant + owner user + default project + default API key.

#### Request

```
POST /v1/auth/register
Content-Type: application/json

{
  "email": "jane@company.com",
  "password": "min-8-chars-recommended",
  "name": "Jane Doe",
  "tenant_name": "Acme Inc"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string | Yes | Must be globally unique (see section 1.1) |
| `password` | string | Yes | User's chosen password |
| `name` | string | Yes | Display name |
| `tenant_name` | string | Yes | Workspace name (human-friendly, see rules below) |

#### Workspace name rules

The user enters a **human-friendly name** (e.g. `"Acme Inc"`). The backend derives a **slug** from it automatically:

1. Convert to lowercase
2. Replace spaces with hyphens

| User types | Slug generated | Stored display name |
|---|---|---|
| `Acme Inc` | `acme-inc` | `Acme Inc` |
| `My Cool Startup` | `my-cool-startup` | `My Cool Startup` |
| `BIGCORP` | `bigcorp` | `BIGCORP` |

- The **display name** (`tenant_name`) is stored as-is and shown in the UI.
- The **slug** is the unique identifier. Uniqueness is checked against the slug, not the display name.
- The form label should say **"Workspace name"** — the user should NOT need to know about slugs.
- Use `GET /v1/auth/check-slug` (section 2.3) to validate availability in real time.
- If the slug is taken, show: *"This workspace name is already taken. Try a different name."*

#### Response — 201 Created

```json
{
  "user": {
    "user_id": "uuid-here",
    "tenant_id": "uuid-here",
    "email": "jane@company.com",
    "name": "Jane Doe",
    "role": "owner",
    "is_active": true,
    "created_at": "2026-02-14T10:00:00Z",
    "updated_at": "2026-02-14T10:00:00Z",
    "last_login_at": null,
    "settings": {}
  },
  "tenant": {
    "tenant_id": "uuid-here",
    "name": "Acme Inc",
    "slug": "acme-inc"
  },
  "api_key": "hb_live_abcdef1234567890abcdef1234567890"
}
```

#### Errors

| Status | Error key | Frontend message |
|---|---|---|
| 409 | `email_exists` | *"This email is already registered. If you already have an account, please sign in instead."* |
| 409 | `slug_exists` | *"This workspace name is already taken. Try a different name."* |
| 409 | `pending_invite` | *"You have a pending invitation to join a workspace. Please check your email and accept the invite instead."* |

#### Post-registration flow

1. Store `tenant.tenant_id` in localStorage as `hb_tenant_id`
2. Show the API key reveal page — display `api_key` once (it cannot be retrieved again)
3. "Continue" button goes to `login.html` — the user logs in with their new email + password

---

### 2.3 Check Slug Availability — `GET /v1/auth/check-slug?slug={name}`

**Public (no auth required).** For real-time validation on the registration form.

#### Request

```
GET /v1/auth/check-slug?slug=Acme Inc
```

The `slug` parameter is the raw workspace name. The backend normalizes it (lowercase, spaces to hyphens) and checks availability.

#### Response — 200 OK

```json
{
  "slug": "acme-inc",
  "available": true
}
```

If taken:

```json
{
  "slug": "acme-inc",
  "available": false
}
```

#### Frontend usage

```javascript
// Call on blur or with debounce as user types workspace name
async function checkSlugAvailable(tenantName) {
    const res = await fetch(
        `${API_BASE}/v1/auth/check-slug?slug=${encodeURIComponent(tenantName)}`
    );
    return await res.json(); // { slug: "acme-inc", available: true/false }
}
```

---

### 2.4 Accept Invite — `POST /v1/auth/accept-invite`

**Public (no auth required).** Called when a user opens an invite link.

The invite link format is: `https://{host}/accept-invite.html?token={invite_token}`

#### Request

```
POST /v1/auth/accept-invite
Content-Type: application/json

{
  "invite_token": "uuid-from-invite-link",
  "name": "New Team Member",
  "password": "their-chosen-password"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `invite_token` | string | Yes | UUID from the invite link query parameter |
| `name` | string | Yes | Display name (user chooses during acceptance) |
| `password` | string | Yes | User's chosen password |

#### Response — 200 OK

Same shape as login response (includes JWT so user is immediately logged in):

```json
{
  "token": "eyJhbGci...",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "user_id": "...",
    "tenant_id": "...",
    "email": "invited@example.com",
    "name": "New Team Member",
    "role": "member",
    "is_active": true,
    "created_at": "2026-02-14T10:00:00Z",
    "updated_at": "2026-02-14T10:00:00Z",
    "last_login_at": null,
    "settings": {}
  }
}
```

#### Errors

| Status | Error key | Frontend message |
|---|---|---|
| 404 | `not_found` | *"This invitation link is invalid or has expired. Please ask the workspace owner to send a new invite."* |
| 409 | `email_exists` | *"This email is already registered with another account. Please contact your administrator or use a different email address."* |

#### Post-accept flow

1. Store the returned JWT in localStorage (`hb_token`, `hb_user`, etc.)
2. Store `user.tenant_id` as `hb_tenant_id`
3. Redirect to `home.html` — the user is now logged in

---

## 3. Invite Management

These endpoints are used by **owners/admins** to manage team invitations from the dashboard.

### 3.1 Send Invite — `POST /v1/auth/invite`

**Auth required: JWT (owner or admin only).**

#### Request

```
POST /v1/auth/invite
Authorization: Bearer {jwt_token}
Content-Type: application/json

{
  "email": "newguy@company.com",
  "role": "member",
  "name": "New Guy"
}
```

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `email` | string | Yes | — | Email to invite (must not exist in any tenant) |
| `role` | string | No | `"member"` | Role to assign: `member`, `viewer`, `admin`, `owner` |
| `name` | string | No | `null` | Optional display name hint |

#### Role escalation rules

- **Owners** can invite any role (owner, admin, member, viewer)
- **Admins** can only invite as `member` or `viewer` — attempting to invite as `owner` or `admin` returns 403

#### Response — 201 Created

```json
{
  "invite_id": "uuid-here",
  "email": "newguy@company.com",
  "role": "member",
  "tenant_id": "uuid-here",
  "expires_at": "2026-02-21T10:00:00Z",
  "invite_token": "uuid-invite-token"
}
```

> **MVP note:** The `invite_token` is returned in the API response and logged to the server console. In production, this would be sent via email. For now, the admin must manually share the link: `https://{host}/accept-invite.html?token={invite_token}`

#### Errors

| Status | Error key | Frontend message |
|---|---|---|
| 403 | `role_escalation` | *"Only workspace owners can invite admins or owners."* |
| 409 | `email_exists` (message: "...in this organization") | *"This person is already a member of your workspace."* |
| 409 | `email_exists` (message: "...another organization") | *"This email is already registered with another organization. The person must use a different email address to join your workspace."* |
| 400 | `invite_exists` | *"An invitation has already been sent to this email. Cancel the existing invite first if you need to resend."* |

> **Important (1-email-1-tenant rule):** The backend checks BOTH same-tenant AND cross-tenant email conflicts. If the email belongs to another tenant, the invite is rejected. The frontend should display a clear message explaining that the person needs to use a different email.

---

### 3.2 List Invites — `GET /v1/invites`

**Auth required: JWT (owner or admin only).**

#### Request

```
GET /v1/invites
Authorization: Bearer {jwt_token}
```

#### Response — 200 OK

```json
{
  "data": [
    {
      "invite_id": "uuid-here",
      "email": "pending@company.com",
      "role": "member",
      "name": "Pending User",
      "is_accepted": false,
      "created_at": "2026-02-14T10:00:00Z",
      "expires_at": "2026-02-21T10:00:00Z",
      "accepted_at": null
    }
  ]
}
```

---

### 3.3 Cancel Invite — `DELETE /v1/invites/{invite_id}`

**Auth required: JWT (owner or admin only).** Only pending (not yet accepted) invites can be cancelled.

#### Request

```
DELETE /v1/invites/{invite_id}
Authorization: Bearer {jwt_token}
```

#### Response — 200 OK

```json
{
  "status": "cancelled"
}
```

#### Errors

| Status | Error key | When |
|---|---|---|
| 404 | `not_found` | Invite not found or already accepted |

---

## 4. API Key Management

Used from the dashboard to manage API keys for the workspace.

### 4.1 Create API Key — `POST /v1/api-keys`

**Auth required: JWT or API key.**

#### Request

```
POST /v1/api-keys
Authorization: Bearer {jwt_token}
Content-Type: application/json

{
  "label": "Production Key",
  "key_type": "live"
}
```

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `label` | string | Yes | — | Human-readable label for the key |
| `key_type` | string | No | `"live"` | Key type: `"live"`, `"test"`, or `"read"` |

**Key type restrictions by role:**
- `viewer` can only create `"read"` keys
- All other roles can create any key type

#### Response — 201 Created

```json
{
  "key_id": "uuid-here",
  "key_prefix": "hb_live_abcd",
  "key_type": "live",
  "label": "Production Key",
  "raw_key": "hb_live_abcdef1234567890abcdef1234567890",
  "created_at": "2026-02-14T10:00:00Z"
}
```

> **Important:** `raw_key` is returned **only once** at creation time. It is never stored or returned again. The frontend must display it clearly and instruct the user to copy it.

#### Errors

| Status | Error key | When |
|---|---|---|
| 403 | `insufficient_permissions` | Viewer trying to create non-read key |

---

### 4.2 List API Keys — `GET /v1/api-keys`

**Auth required: JWT or API key.**

#### Request

```
GET /v1/api-keys
Authorization: Bearer {jwt_token}
```

#### Response — 200 OK

```json
{
  "data": [
    {
      "key_id": "uuid-here",
      "key_prefix": "hb_live_abcd",
      "key_type": "live",
      "label": "Production Key",
      "created_by_user_id": "uuid-here",
      "created_at": "2026-02-14T10:00:00Z",
      "last_used_at": "2026-02-14T12:00:00Z",
      "is_active": true
    }
  ]
}
```

**Visibility rules:**
- `owner`/`admin` or API-key auth: sees **all** keys in the workspace
- `member`/`viewer`: sees only their **own** keys

> **Note:** The `key_hash` is never returned. Only `key_prefix` (first 12 characters) is shown for identification.

---

### 4.3 Revoke API Key — `DELETE /v1/api-keys/{key_id}`

**Auth required: JWT or API key.**

#### Request

```
DELETE /v1/api-keys/{key_id}
Authorization: Bearer {jwt_token}
```

#### Response — 200 OK

```json
{
  "status": "revoked"
}
```

**Permission rules:**
- `owner`/`admin`: can revoke **any** key in the workspace
- `member`/`viewer`: can only revoke their **own** keys

#### Errors

| Status | Error key | When |
|---|---|---|
| 403 | `insufficient_permissions` | Non-owner trying to revoke someone else's key |
| 404 | `not_found` | Key not found |

---

## 5. Project Management

Projects organize agents and events within a workspace. A "default" project is auto-created with every new tenant.

### 5.1 List Projects — `GET /v1/projects`

**Auth required.**

#### Request

```
GET /v1/projects?include_archived=false
Authorization: Bearer {token}
```

| Param | Type | Default | Notes |
|---|---|---|---|
| `include_archived` | bool | `false` | Set to `true` to include archived projects |

#### Response — 200 OK

```json
{
  "data": [
    {
      "project_id": "uuid-here",
      "tenant_id": "uuid-here",
      "name": "Default",
      "slug": "default",
      "description": null,
      "environment": "production",
      "settings": {},
      "is_archived": false,
      "created_at": "2026-02-14T10:00:00Z",
      "updated_at": "2026-02-14T10:00:00Z",
      "event_count": 42
    }
  ]
}
```

---

### 5.2 Create Project — `POST /v1/projects`

**Auth required.**

#### Request

```
POST /v1/projects
Authorization: Bearer {token}
Content-Type: application/json

{
  "name": "Sales Pipeline",
  "slug": "sales-pipeline",
  "description": "Tracks sales agent events",
  "environment": "production"
}
```

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `name` | string | Yes | — | Display name |
| `slug` | string | Yes | — | URL-friendly identifier (must be unique within tenant) |
| `description` | string | No | `null` | Optional description |
| `environment` | string | No | `"production"` | e.g. `"production"`, `"staging"`, `"development"` |
| `settings` | object | No | `{}` | Arbitrary settings |

#### Response — 201 Created

Returns the full project object.

#### Errors

| Status | Error key | When |
|---|---|---|
| 409 | `slug_exists` | A project with this slug already exists in the tenant |

---

### 5.3 Get Project — `GET /v1/projects/{project_id}`

**Auth required.** The `project_id` parameter can be a UUID or a slug.

#### Response — 200 OK

Returns the full project object.

#### Errors

| Status | Error key | When |
|---|---|---|
| 404 | `not_found` | Project not found |

---

### 5.4 Update Project — `PUT /v1/projects/{project_id}`

**Auth required.**

#### Request

```
PUT /v1/projects/{project_id}
Authorization: Bearer {token}
Content-Type: application/json

{
  "name": "New Name",
  "slug": "new-slug",
  "description": "Updated description"
}
```

All fields are optional — only include the fields you want to change.

| Field | Type | Notes |
|---|---|---|
| `name` | string | New display name |
| `slug` | string | New slug (must be unique within tenant) |
| `description` | string | New description |
| `environment` | string | New environment label |
| `settings` | object | New settings (replaces entire object) |

#### Response — 200 OK

Returns the updated project object.

#### Errors

| Status | Error key | When |
|---|---|---|
| 404 | `not_found` | Project not found |
| 409 | `slug_exists` | New slug conflicts with another project in the tenant |

---

### 5.5 Delete Project — `DELETE /v1/projects/{project_id}`

**Auth required.** Deleting a project reassigns its events to another project and archives it.

#### Request

```
DELETE /v1/projects/{project_id}?reassign_to=default
Authorization: Bearer {token}
```

| Param | Type | Default | Notes |
|---|---|---|---|
| `reassign_to` | string | `"default"` | Slug of the project to reassign events to |

#### Response — 200 OK

```json
{
  "status": "deleted",
  "events_reassigned": 15,
  "reassigned_to": "default"
}
```

#### Errors

| Status | Error key | When |
|---|---|---|
| 400 | `cannot_delete_default` | Cannot delete the "default" project |
| 404 | `not_found` | Project not found |

---

### 5.6 Archive / Unarchive Project

**Auth required.**

```
POST /v1/projects/{project_id}/archive
POST /v1/projects/{project_id}/unarchive
```

#### Response — 200 OK

```json
{ "status": "archived" }
```
```json
{ "status": "unarchived" }
```

---

### 5.7 Merge Projects — `POST /v1/projects/{project_id}/merge`

**Auth required.** Merges the source project into a target: reassigns all events and agent associations, then archives the source.

#### Request

```
POST /v1/projects/{source_project_id}/merge
Authorization: Bearer {token}
Content-Type: application/json

{
  "target_slug": "default"
}
```

#### Response — 200 OK

```json
{
  "status": "merged",
  "source_slug": "old-project",
  "target_slug": "default",
  "events_moved": 42
}
```

#### Errors

| Status | Error key | When |
|---|---|---|
| 400 | `invalid_merge` | Cannot merge a project into itself |
| 404 | `not_found` | Source or target project not found |

---

## 6. Frontend Pages Required

### 6.1 `login.html` — REWRITE

Replace the 2-step email-code flow with a simple email + password form.

**Remove:** Step 2 (code input, dev banner, resend logic), all `send-code`/`verify-code` calls.

**New form:**

```
Email:    [________________________]
Password: [________________________]
          [ Sign In               ]

Don't have an account? [Register]
```

**JS logic:**

```javascript
const res = await apiPost('/v1/auth/login?tenant_id=' + getTenantId(), {
    email, password
});

if (res.ok) {
    localStorage.setItem('hb_token', res.data.token);
    localStorage.setItem('hb_token_type', res.data.token_type || 'bearer');
    localStorage.setItem('hb_user', JSON.stringify(res.data.user));
    window.location.href = 'home.html';
} else if (res.status === 401) {
    showError('Invalid email or password.');
}

function getTenantId() {
    return localStorage.getItem('hb_tenant_id') || 'dev';
}
```

**Pre-fill email:** Check `sessionStorage.getItem('hb_prefill_email')` on load and pre-fill the email field if set (set by registration page after successful registration).

---

### 6.2 `register.html` — ADD PASSWORD FIELD + SLUG VALIDATION

**Add** a password field between email and workspace name.

**Add** real-time workspace name validation using the check-slug endpoint.

**Update** the `apiPost` call:

```javascript
const res = await apiPost('/v1/auth/register', {
    email, password, name, tenant_name: tenantName
});
```

**Handle all error cases:**

```javascript
if (res.status === 409) {
    switch (res.data.error) {
        case 'email_exists':
            showError('This email is already registered. Please sign in instead.');
            break;
        case 'slug_exists':
            showError('This workspace name is already taken. Try a different name.');
            break;
        case 'pending_invite':
            showError('You have a pending invitation. Please check your email and accept the invite instead.');
            break;
    }
}
```

**Post-registration:** Store `tenant_id`, show API key reveal, then redirect to login.

**Update** the footer text in the API key reveal step:

```
BEFORE: "You'll sign in with a code sent to your email — no password needed."
AFTER:  "You'll sign in with your email and password."
```

---

### 6.3 `accept-invite.html` — NEW PAGE

This page does not exist yet and **must be created**. It is the landing page for invite links.

**URL format:** `accept-invite.html?token={invite_token}`

**Form:**

```
You've been invited to join a workspace!

Your name:         [________________________]
Choose a password: [________________________]
                   [ Join Workspace         ]
```

**JS logic:**

```javascript
// Extract token from URL
const token = new URLSearchParams(window.location.search).get('token');

if (!token) {
    showError('Invalid invite link. No invitation token found.');
    return;
}

// On form submit:
const res = await apiPost('/v1/auth/accept-invite', {
    invite_token: token,
    name: name,
    password: password
});

if (res.ok) {
    // User is immediately logged in
    localStorage.setItem('hb_token', res.data.token);
    localStorage.setItem('hb_token_type', res.data.token_type || 'bearer');
    localStorage.setItem('hb_user', JSON.stringify(res.data.user));
    localStorage.setItem('hb_tenant_id', res.data.user.tenant_id);
    window.location.href = 'home.html';
} else if (res.status === 404) {
    showError('This invitation link is invalid or has expired. Please ask the workspace owner to send a new invite.');
} else if (res.status === 409) {
    showError('This email is already registered with another account. Please contact your administrator or use a different email address.');
}
```

---

### 6.4 `home.html` — ADD SETTINGS SECTIONS

The home page needs dashboard sections (or a settings panel) for:

**Team Management (owner/admin only):**
- Invite form: email + role dropdown + "Send Invite" button → `POST /v1/auth/invite`
- Pending invites list → `GET /v1/invites` with "Cancel" button → `DELETE /v1/invites/{id}`
- Handle the 1-email-1-tenant errors clearly (see section 3.1 error table)

**API Key Management:**
- "Create Key" form: label + key type dropdown → `POST /v1/api-keys`
- Key list → `GET /v1/api-keys` — show prefix, label, last used, active status
- "Revoke" button per key → `DELETE /v1/api-keys/{key_id}`
- When a key is created, show the `raw_key` once in a modal with a copy button

**Project Management:**
- Project list → `GET /v1/projects`
- Create project form → `POST /v1/projects`
- Edit project → `PUT /v1/projects/{id}`
- Delete project (with confirmation) → `DELETE /v1/projects/{id}`

---

## 7. Removed Endpoints (DO NOT USE)

These endpoints no longer exist. Remove all frontend code that calls them:

- ~~`POST /v1/auth/send-code`~~ — **REMOVED**
- ~~`POST /v1/auth/verify-code`~~ — **REMOVED**

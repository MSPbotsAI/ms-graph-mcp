# graph-mcp

Microsoft Graph MCP server — exposes Azure Entra ID user and license management as MCP tools over HTTP-SSE.

## Overview

This server implements the [Model Context Protocol](https://modelcontextprotocol.io/) (HTTP-SSE transport) and wraps the Microsoft Graph API. It is designed for **gateway mode**: the caller obtains an Azure access token via OAuth and passes it per-request through a header. The server itself holds no credentials.

## Quick Start

### Docker (recommended)

```bash
docker compose up --build
```

The server starts on `http://localhost:8080`.

### Local (uv)

```bash
uv sync
python -m graph_mcp
```

## Health Check

```bash
curl http://localhost:8080/health
# {"status": "ok", "service": "graph-mcp", "transport": "http"}
```

No token is required for the health endpoint.

## Authentication

Every request to `/mcp` must include a valid Azure access token:

```
X-Ms-Graph-Token: <access_token>
```

The token must be issued for the `https://graph.microsoft.com/.default` scope with the permissions listed per tool below. OAuth acquisition is handled by the caller — this server only forwards the token to Graph API.

Missing or invalid tokens return `401 Unauthorized`.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MCP_HTTP_PORT` | `8080` | Listening port |
| `MCP_HTTP_HOST` | `0.0.0.0` | Listening host |
| `GRAPH_BASE_URL` | `https://graph.microsoft.com/v1.0` | Override for sovereign clouds (GCC High, DoD, China 21Vianet) |

## MCP Endpoint

```
POST http://localhost:8080/mcp
```

Connect your MCP client with:
- Transport: `http` (Streamable HTTP / SSE)
- Header: `X-Ms-Graph-Token: <access_token>`

## Tools

### `graph_check_user_exists`

Check whether an Entra ID user exists by UPN or mail address.

**Required scope:** `User.Read.All` or `Directory.Read.All`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_principal_name` | string | one of | UPN to search for, e.g. `alice@contoso.com` |
| `mail` | string | one of | Primary SMTP address to search for |

**Returns:**
```json
{
  "exists": true,
  "user": {
    "id": "...",
    "displayName": "Alice Smith",
    "userPrincipalName": "alice@contoso.com",
    "mail": "alice@contoso.com",
    "accountEnabled": true
  }
}
```

---

### `graph_create_user`

Create a new Entra ID user.

**Required scope:** `User.ReadWrite.All`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `display_name` | string | yes | Full display name, e.g. `Alice Smith` |
| `user_principal_name` | string | yes | Login UPN, e.g. `alice@contoso.com` |
| `mail_nickname` | string | yes | Mail alias without domain, e.g. `alice` |
| `password` | string | yes | Initial password — must meet tenant complexity policy |
| `usage_location` | string | yes | ISO 3166-1 alpha-2 country code, e.g. `US`, `AU`. Required before license assignment |
| `account_enabled` | boolean | no | Whether the account is active immediately (default `true`) |
| `given_name` | string | no | First name |
| `surname` | string | no | Last name |
| `job_title` | string | no | Job title |
| `department` | string | no | Department name |

**Returns:** The created user object from Graph API. The user's `id` is needed for subsequent group and license assignment calls.

> `forceChangePasswordNextSignIn` is always set to `true` as a security default.

---

### `graph_assign_groups`

Add an Entra ID user to one or more groups. Processes all groups and returns per-group results. Already-a-member is treated as success (idempotent).

**Required scope:** `GroupMember.ReadWrite.All` (standard groups); `Group.ReadWrite.All` (role-assignable groups)

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | string | yes | Object ID of the user to add |
| `group_ids` | string[] | yes | List of group object IDs |

**Returns:**
```json
{
  "user_id": "...",
  "results": [
    { "group_id": "aaa-...", "status": "added" },
    { "group_id": "bbb-...", "status": "already_member" },
    { "group_id": "ccc-...", "status": "error", "detail": "..." }
  ]
}
```

---

### `graph_check_license_stock`

Query the tenant's subscribed SKUs to check license availability. Returns all SKUs when no filter is provided.

**Required scope:** `Organization.Read.All`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `sku_id` | string | no | Filter by SKU GUID |
| `sku_part_number` | string | no | Filter by part number, e.g. `ENTERPRISEPACK` (case-insensitive) |

**Returns:**
```json
{
  "total_skus": 1,
  "skus": [
    {
      "skuId": "6fd2c87f-...",
      "skuPartNumber": "ENTERPRISEPACK",
      "capabilityStatus": "Enabled",
      "prepaidUnits": { "enabled": 100 },
      "consumedUnits": 73,
      "available": 27
    }
  ]
}
```

> `available = prepaidUnits.enabled - consumedUnits`. Use `sku_id` from this response in `graph_assign_license`.

---

### `graph_assign_license`

Assign a license SKU to an Entra ID user. The user must have `usageLocation` set (done by `graph_create_user`).

**Required scope:** `User.ReadWrite.All`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | string | yes | Object ID or UPN of the user |
| `sku_id` | string | yes | SKU GUID to assign (from `graph_check_license_stock`) |
| `disabled_plans` | string[] | no | Service plan GUIDs to disable within the SKU |

**Returns:** The updated user license object from Graph API.

---

## Typical Workflow

```
1. graph_check_user_exists   → check if account already exists
2. graph_create_user         → create account (sets usage_location)
3. graph_assign_groups       → add to required groups
4. graph_check_license_stock → verify available license count
5. graph_assign_license      → assign license SKU to user
```

## Sovereign Cloud Support

Set `GRAPH_BASE_URL` to override the default endpoint:

| Cloud | Base URL |
|---|---|
| Public (default) | `https://graph.microsoft.com/v1.0` |
| US Government GCC High | `https://graph.microsoft.us/v1.0` |
| US Government DoD | `https://dod-graph.microsoft.us/v1.0` |
| China (21Vianet) | `https://microsoftgraph.chinacloudapi.cn/v1.0` |

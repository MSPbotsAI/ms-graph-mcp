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

| Tool | 功能 | Required Scope |
|---|---|---|
| `graph_check_user_exists` | 按 UPN 或邮箱查询 Entra ID 用户是否存在 | `User.Read.All` / `Directory.Read.All` |
| `graph_create_user` | 创建新的 Entra ID 用户，同时设置 usage_location 以便后续分配许可 | `User.ReadWrite.All` |
| `graph_assign_groups` | 将用户加入一个或多个组，已在组内则幂等跳过 | `GroupMember.ReadWrite.All` / `Group.ReadWrite.All` |
| `graph_check_license_stock` | 查询租户已订阅 SKU 的许可库存与剩余数量 | `Organization.Read.All` |
| `graph_assign_license` | 为用户分配指定 SKU 许可，可选择禁用部分服务计划 | `User.ReadWrite.All` |
| `graph_send_mail` | 以指定用户身份发送邮件，支持 To / CC / BCC 及 HTML 正文 | `Mail.Send` |

## Typical Workflow

```
1. graph_check_user_exists   → 查重，确认账号不存在
2. graph_create_user         → 建号并设置 usage_location
3. graph_assign_groups       → 分配组
4. graph_check_license_stock → 检查许可库存
5. graph_assign_license      → 配许可
6. graph_send_mail           → 发送通知邮件（可选）
```

## Sovereign Cloud Support

Set `GRAPH_BASE_URL` to override the default endpoint:

| Cloud | Base URL |
|---|---|
| Public (default) | `https://graph.microsoft.com/v1.0` |
| US Government GCC High | `https://graph.microsoft.us/v1.0` |
| US Government DoD | `https://dod-graph.microsoft.us/v1.0` |
| China (21Vianet) | `https://microsoftgraph.chinacloudapi.cn/v1.0` |

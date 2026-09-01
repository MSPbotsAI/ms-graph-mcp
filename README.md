# graph-mcp

Microsoft Graph MCP server — exposes Azure Entra ID user, group, and license management, mail sending, and SharePoint document read/write as MCP tools over HTTP-SSE, covering the full user onboarding/offboarding lifecycle plus basic SharePoint document access.

## Overview

This server implements the [Model Context Protocol](https://modelcontextprotocol.io/) (HTTP-SSE transport) and wraps the Microsoft Graph API. It exposes 20 tools spanning user management (create / read / update / disable, password reset, session revocation, manager assignment, MFA method listing), group membership (add / remove / list / search), license inventory and assignment, sending mail, and SharePoint sites/document libraries (find a site, browse a document library, read/write small text files). It is designed for **gateway mode**: the caller obtains an Azure access token via OAuth and passes it per-request through a header. The server itself holds no credentials.

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
| `graph_get_user` | 读取用户完整资料，含 manager 与已分配许可 | `User.Read.All` / `Directory.Read.All` |
| `graph_update_user` | 更新用户属性（仅传入的字段会改动）；`account_enabled=false` 用于禁用账号（离职场景） | `User.ReadWrite.All` |
| `graph_reset_password` | 管理员重置用户密码，下次登录强制改密 | `User.ReadWrite.All` + Password/User Administrator 角色 |
| `graph_revoke_sessions` | 注销用户所有登录会话，强制重新登录 | `User.ReadWrite.All` |
| `graph_assign_manager` | 设置用户的 manager | `User.ReadWrite.All` |
| `graph_list_auth_methods` | 列出用户已注册的 MFA 认证方式 | `UserAuthenticationMethod.Read.All` |
| `graph_assign_groups` | 将用户加入一个或多个组，已在组内则幂等跳过 | `GroupMember.ReadWrite.All` / `Group.ReadWrite.All` |
| `graph_remove_group_member` | 将用户移出一个或多个组，不在组内则幂等跳过 | `GroupMember.ReadWrite.All` / `Group.ReadWrite.All` |
| `graph_list_user_groups` | 列出用户直接所属的组 | `GroupMember.Read.All` / `Directory.Read.All` |
| `graph_list_groups` | 按显示名搜索/列出 Entra ID 组 | `Group.Read.All` / `Directory.Read.All` |
| `graph_check_license_stock` | 查询租户已订阅 SKU 的许可库存与剩余数量 | `Organization.Read.All` |
| `graph_assign_license` | 为用户分配和/或移除指定 SKU 许可（Graph 的 assignLicense 接口一次调用同时支持增删，两者合并进这一个 tool） | `User.ReadWrite.All` |
| `graph_send_mail` | 以指定用户身份发送邮件，支持 To / CC / BCC 及 HTML 正文 | `Mail.Send` |
| `graph_search_sites` | 按名称/关键词搜索 SharePoint 站点，结果自动带出每个站点默认文档库的 driveId（最多补前5条） | `Sites.Read.All` |
| `graph_list_drive_items` | 列出文档库根目录或某个文件夹下的文件/文件夹（不递归） | `Sites.Read.All` / `Files.Read.All` |
| `graph_get_file` | 获取文件元数据（名称/大小/MIME类型）+ 一个临时的预授权直接下载链接 | `Sites.Read.All` / `Files.Read.All` |
| `graph_read_file_text` | 读取小体积纯文本文件（.txt/.md/.csv/.json等）的实际内容，超过200,000字节或非UTF-8可解码（即二进制Office文档）会拒绝并提示改用 downloadUrl | `Sites.Read.All` / `Files.Read.All` |
| `graph_write_file_text` | 整篇覆盖一个已存在的纯文本文件内容（非patch，必须传完整内容），目标文件当前MIME类型看着不像文本会拒绝写入 | `Sites.ReadWrite.All` / `Files.ReadWrite.All` |

## Typical Workflows

Onboarding:
```
1. graph_check_user_exists   → 查重，确认账号不存在
2. graph_create_user         → 建号并设置 usage_location
3. graph_assign_groups       → 分配组
4. graph_check_license_stock → 检查许可库存
5. graph_assign_license      → 配许可
6. graph_send_mail           → 发送通知邮件（可选）
```

Offboarding：
```
1. graph_update_user(account_enabled=false) → 立即禁止新登录
2. graph_revoke_sessions                    → 注销已有会话（旧 token 在到期前仍可能短暂有效，两步搭配才是彻底离职）
3. graph_assign_license(remove_sku_ids=...) → 收回许可
4. graph_remove_group_member                → 移出各个组
```

## Known Gaps

- `graph_remove_group_member`、`graph_revoke_sessions`、`graph_update_user` 的 `account_enabled` 参数、`graph_assign_license` 的 `remove_sku_ids` 参数都是新加的，尚未随真实 Graph 租户测试过——上线前建议先用一个可牺牲的测试账号走一遍完整离职流程再信任。
- `graph_revoke_sessions` 不是瞬时生效：调用前已签发的 access token 在过期前仍然有效（通常 ~1 小时），所以离职场景务必同时调 `graph_update_user(account_enabled=false)`，不要只调一个。
- **SharePoint 工具（`graph_search_sites`/`graph_list_drive_items`/`graph_get_file`/`graph_read_file_text`/`graph_write_file_text`）只覆盖纯文本文件**（.txt/.md/.csv/.json），刻意不支持二进制 Office 文档（.docx/.xlsx/.pdf）——把这类文件内容内联塞进工具返回值意味着让调用方（大模型）自己的上下文窗口去扛一个 base64 编码后的大 blob，跟这整个 fleet 统一的 ~20,000 字符返回值上限直接冲突。`graph_get_file` 返回的 `downloadUrl` 是给二进制文件用的逃生舱口——调用方可以绕开这个 MCP 自己直接去下载，但**怎么把下载/上传的字节流跟大模型对话流程接起来**（尤其是"写"方向：用户在聊天界面里给的文件，agent 最终怎么变成上传给 SharePoint 的字节）是一个尚未验证的平台侧集成问题，不是这几个工具本身能解决的——本仓库目前只保证"文本内容能通过tool_call参数正常传递"这条路径，不保证聊天前端到MCP之间存在绕开大模型上下文的文件通道。
- **SharePoint 工具尚未随真实租户/token测试过**——权限（`Sites.Read.All`/`Sites.ReadWrite.All`）需要先在对应 Azure AD 应用注册里加上并完成 admin consent，本次交付只做了本地 schema/单元测试验证。
- **`graph_write_file_text` 只能覆盖已存在的文件**，没有新建文件的能力（Graph 的简单上传接口本身支持对不存在的路径直接建文件，但这里为了保持行为可预测，要求 `item_id` 必须已存在）。

## Sovereign Cloud Support

Set `GRAPH_BASE_URL` to override the default endpoint:

| Cloud | Base URL |
|---|---|
| Public (default) | `https://graph.microsoft.com/v1.0` |
| US Government GCC High | `https://graph.microsoft.us/v1.0` |
| US Government DoD | `https://dod-graph.microsoft.us/v1.0` |
| China (21Vianet) | `https://microsoftgraph.chinacloudapi.cn/v1.0` |

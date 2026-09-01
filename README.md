# graph-mcp

Microsoft Graph MCP server — exposes Azure Entra ID user, group, and license management, mail sending, and SharePoint document read/write as MCP tools over HTTP-SSE, covering the full user onboarding/offboarding lifecycle plus basic SharePoint document access.

## Overview

This server implements the [Model Context Protocol](https://modelcontextprotocol.io/) (HTTP-SSE transport) and wraps the Microsoft Graph API. It exposes 25 tools spanning user management (create / read / update / disable, password reset, session revocation, manager assignment, MFA method listing), group membership and ownership (add / remove / list / search, owned-groups orphan check), license inventory and assignment, sending mail, SharePoint sites/document libraries (find a site, browse a document library, create/read/write/delete small text files), and Intune-managed devices (list / remove a user's enrolled devices). It is designed for **gateway mode**: the caller obtains an Azure access token via OAuth and passes it per-request through a header. The server itself holds no credentials.

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
| `graph_check_user_exists` | 按 UPN 或邮箱查询 Entra ID 用户是否存在 | `User.Read.All` |
| `graph_create_user` | 创建新的 Entra ID 用户，同时设置 usage_location 以便后续分配许可 | `User.ReadWrite.All` |
| `graph_get_user` | 读取用户完整资料，含 manager、已分配许可、以及每条许可的 `licenseAssignmentStates`（`assignedByGroup` 为 null=直接分配 / 非null=组分配继承） | `User.Read.All` |
| `graph_update_user` | 更新用户属性（仅传入的字段会改动）；`account_enabled=false` 用于禁用账号（离职场景） | `User.ReadWrite.All` |
| `graph_reset_password` | 管理员重置用户密码，下次登录强制改密 | `User.ReadWrite.All` + Password/User Administrator 角色 |
| `graph_revoke_sessions` | 注销用户所有登录会话，强制重新登录 | `User.ReadWrite.All` |
| `graph_assign_manager` | 设置用户的 manager | `User.ReadWrite.All` |
| `graph_list_auth_methods` | 列出用户已注册的 MFA 认证方式 | `UserAuthenticationMethod.Read.All` |
| `graph_assign_groups` | 将用户加入一个或多个组，已在组内则幂等跳过 | `GroupMember.ReadWrite.All` |
| `graph_remove_group_member` | 将用户移出一个或多个组，不在组内则幂等跳过 | `GroupMember.ReadWrite.All` |
| `graph_list_user_groups` | 列出用户直接所属的组 | `GroupMember.Read.All`（注意：`GroupMember.ReadWrite.All` 不在该端点的受理列表里，必须单独申请 Read 版） |
| `graph_list_groups` | 按显示名搜索/列出 Entra ID 组 | `Group.Read.All` |
| `graph_list_owned_groups` | 列出用户拥有的组，每个组附带总owner数——离职场景判断"这个用户是不是唯一owner"（`owner_count==1` 代表移除后立刻变孤儿组） | `Group.Read.All` |
| `graph_check_license_stock` | 查询租户已订阅 SKU 的许可库存与剩余数量 | `Organization.Read.All` |
| `graph_assign_license` | 为用户分配和/或移除指定 SKU 许可（Graph 的 assignLicense 接口一次调用同时支持增删，两者合并进这一个 tool） | `User.ReadWrite.All` |
| `graph_send_mail` | 以指定用户身份发送邮件，支持 To / CC / BCC 及 HTML 正文 | `Mail.Send`；带 `sender_id`（代他人/共享邮箱发信）另需 `Mail.Send.Shared` |
| `graph_search_sites` | 按名称/关键词搜索 SharePoint 站点，结果自动带出每个站点默认文档库的 driveId（最多补前5条） | `Sites.Read.All` |
| `graph_list_drive_items` | 列出文档库根目录或某个文件夹下的文件/文件夹（不递归） | `Sites.Read.All` |
| `graph_get_file` | 获取文件元数据（名称/大小/MIME类型）+ 一个临时的预授权直接下载链接 | `Sites.Read.All` |
| `graph_read_file_text` | 读取小体积纯文本文件（.txt/.md/.csv/.json等）的实际内容，超过200,000字节或非UTF-8可解码（即二进制Office文档）会拒绝并提示改用 downloadUrl | `Sites.Read.All` |
| `graph_write_file_text` | 整篇覆盖一个已存在的纯文本文件内容（非patch，必须传完整内容），目标文件当前MIME类型看着不像文本会拒绝写入 | `Sites.ReadWrite.All`（够用，无需额外加`Files.ReadWrite.All`——见下方说明） |
| `graph_create_file_text` | 在指定路径新建一个纯文本文件；**如果该路径已存在文件会直接报错拒绝**，绝不会静默覆盖——要覆盖已有文件用 `graph_write_file_text` | `Sites.ReadWrite.All`（够用，无需额外加`Files.ReadWrite.All`——见下方说明） |
| `graph_delete_file` | 永久删除一个文件（进站点回收站，跟SharePoint网页里删除等效）；幂等，删一个已经不存在的item id也返回成功 | `Sites.ReadWrite.All`（够用，无需额外加`Files.ReadWrite.All`——见下方说明） |
| `graph_list_managed_devices` | 列出用户的 Intune 托管设备；租户没开 Intune license 时不报错，返回空列表 | `DeviceManagementManagedDevices.Read.All` |
| `graph_remove_managed_device` | 永久删除一个设备的 Intune 管理记录（不是选择性的公司数据擦除/retire，是直接删记录）；幂等，删已经不存在的device id也返回成功 | `DeviceManagementManagedDevices.ReadWrite.All` |

> **权限说明**：本文件里所有 SharePoint 工具全部只调用 Graph 的 `/sites/*` 和 `/drives/*` 端点，从不触碰 `/me/drive` 或 `/users/{id}/drive`。这类站点文档库驱动器接口，`Sites.*` 和 `Files.*` 是二选一的替代权限组，不是叠加要求——所以只需要 `Sites.Read.All`（只读工具）+ `Sites.ReadWrite.All`（写/建/删工具），完全不需要额外申请 `Files.ReadWrite.All`。

> **实际申请的 scope**：上表逐个工具列的是各端点**最小**受理权限，便于按需裁剪；平台侧（MCP-Management-Service 的 `auth/oauth/vendors/msgraph.py`）实际向 Entra 申请的是能覆盖全部 25 个工具的并集：
>
> ```
> offline_access openid profile
> User.ReadWrite.All UserAuthenticationMethod.Read.All
> Group.Read.All GroupMember.Read.All GroupMember.ReadWrite.All
> Organization.Read.All Mail.Send Mail.Send.Shared
> Sites.ReadWrite.All Files.Read.All Files.ReadWrite.All
> DeviceManagementManagedDevices.ReadWrite.All
> ```
>
> 其中 `User.ReadWrite.All` 覆盖建/改用户、改密、分配许可、注销会话（这些端点各自的最小权限分散在 `User-PasswordProfile.ReadWrite.All`、`User.EnableDisableAccount.All`、`LicenseAssignment.ReadWrite.All`、`User.RevokeSessions.All`，用一条覆盖比申请五条更诚实）；`Sites.ReadWrite.All` 包含 `Sites.Read.All` 故不重复列；`DeviceManagementManagedDevices.ReadWrite.All` 同理包含 `.Read.All`，覆盖 `graph_list_managed_devices` + `graph_remove_managed_device` 两个工具；**不申请** `Directory.Read.All`（没有工具需要通读目录）和 `Group.ReadWrite.All`（没有工具建组/删组/改组属性）。
>
> `Files.Read.All` / `Files.ReadWrite.All` 是运维决定一并申请的：如上一条所述，对本文件用到的 `/sites/*`、`/drives/*` 端点，`Files.*` 与 `Sites.*` 是**二选一**的替代权限组，工具本身不需要它——加上是为了让"管理员只同意了其中一组"的租户也能落到可用状态。代价是 `Files.*` 同时覆盖每个用户的 OneDrive，而这里没有任何工具会去碰它。

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
3. graph_list_owned_groups                  → 查该用户是不是某些组的唯一owner，先重新指派再离职
4. graph_list_managed_devices               → 查是否还有Intune托管设备
5. graph_remove_managed_device              → 有则删除管理记录
6. graph_assign_license(remove_sku_ids=...) → 收回许可（licenseAssignmentStates里assignedByGroup非null的会随下一步组移除自动清）
7. graph_remove_group_member                → 移出各个组
```

## Known Gaps

- `graph_remove_group_member`、`graph_revoke_sessions`、`graph_update_user` 的 `account_enabled` 参数、`graph_assign_license` 的 `remove_sku_ids` 参数都是新加的，尚未随真实 Graph 租户测试过——上线前建议先用一个可牺牲的测试账号走一遍完整离职流程再信任。
- `graph_revoke_sessions` 不是瞬时生效：调用前已签发的 access token 在过期前仍然有效（通常 ~1 小时），所以离职场景务必同时调 `graph_update_user(account_enabled=false)`，不要只调一个。
- **SharePoint 工具（`graph_search_sites`/`graph_list_drive_items`/`graph_get_file`/`graph_read_file_text`/`graph_write_file_text`/`graph_create_file_text`/`graph_delete_file`）只覆盖纯文本文件**（.txt/.md/.csv/.json），刻意不支持二进制 Office 文档（.docx/.xlsx/.pdf）——把这类文件内容内联塞进工具返回值意味着让调用方（大模型）自己的上下文窗口去扛一个 base64 编码后的大 blob，跟这整个 fleet 统一的 ~20,000 字符返回值上限直接冲突。`graph_get_file` 返回的 `downloadUrl` 是给二进制文件用的逃生舱口——调用方可以绕开这个 MCP 自己直接去下载，但**怎么把下载/上传的字节流跟大模型对话流程接起来**（尤其是"写"方向：用户在聊天界面里给的文件，agent 最终怎么变成上传给 SharePoint 的字节）是一个尚未验证的平台侧集成问题，不是这几个工具本身能解决的——本仓库目前只保证"文本内容能通过tool_call参数正常传递"这条路径，不保证聊天前端到MCP之间存在绕开大模型上下文的文件通道。
- **只读 SharePoint 工具（`graph_search_sites`/`graph_list_drive_items`/`graph_get_file`）已在 INT 用真实租户+真实agent对话实测通过**（2026-09-01，`jexettechnologies537.sharepoint.com`）：真实返回了4个站点、真实文件列表、真实PDF元数据+downloadUrl，且agent正确识别PDF不是文本文件、没有误调`graph_read_file_text`。`Sites.Read.All` 权限确认在生产环境生效。
- **`graph_read_file_text`/`graph_write_file_text`/`graph_create_file_text`/`graph_delete_file` 尚未真实调用验证**——实测时该租户里能找到的现成文件全是pdf/docx/xlsx/JPG，没有可用的纯文本文件；`graph_write_file_text`同时因为是真实客户数据，没有贸然覆盖测试。`graph_create_file_text`/`graph_delete_file` 这两个工具正是为了解决"没有安全的测试文件"这个问题后补的（新建走独立path、不存在才成功；删除幂等），但补上后还没有拿真实token走完一次create→write→read→delete的完整链路。
- **`graph_list_owned_groups`/`graph_list_managed_devices`/`graph_remove_managed_device` 是新加的，尚未随真实租户测试过**（PRD-17403，2026-09-01）——`graph_list_managed_devices`/`graph_remove_managed_device` 额外要求租户开通 Intune license，没有 Intune 的租户上会一直返回空列表/404，这本身是预期行为而非 bug，但也意味着这条路径没有被真实验证过。上线前建议先用一个开了 Intune 的可牺牲测试租户走一遍。

## Sovereign Cloud Support

Set `GRAPH_BASE_URL` to override the default endpoint:

| Cloud | Base URL |
|---|---|
| Public (default) | `https://graph.microsoft.com/v1.0` |
| US Government GCC High | `https://graph.microsoft.us/v1.0` |
| US Government DoD | `https://dod-graph.microsoft.us/v1.0` |
| China (21Vianet) | `https://microsoftgraph.chinacloudapi.cn/v1.0` |

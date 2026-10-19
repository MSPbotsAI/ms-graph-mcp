# 模块：`src/graph_mcp/tools/offboarding.py`（OneDrive 归档共享 + 目录同步）

本模块文档的第一条记录——文件本身就是本单新建的。

## Cloud Sync 发现逻辑修复 · 2026-10-19

- `_discover_cloud_sync_service_principal` 改成服务端过滤：`GET /servicePrincipals?$filter=applicationTemplateId eq '1a4721b3-e57f-4451-ae87-ef078703ec94' and accountEnabled eq true`（带 `ConsistencyLevel: eventual` 和 `$count=true`）。每个 Entra Cloud Sync 配置都是从这个应用模板创建的服务主体（Microsoft Learn「How to programmatically configure cloud sync using MS Graph API」），`applicationTemplateId` 支持 `$filter eq`。
- 取代原来照 Rewst 做的「`homepage` 含 `AD2AADProvisioning`」匹配。`homepage` 不能服务端过滤，只能拉下来本地筛：只读第一页（100 条）时大租户找不到，翻完全部页在 MSPbots 租户要 90 秒以上。同步作业那一步（`templateId == AD2AADProvisioning`、schedule 为 `Active`）不变。
- 测试：`tests/test_tools.py::test_start_cloud_sync_filters_service_principals_server_side`。

## PRD-20422 返工 · 2026-10-16

替代下面 2026-10-14 那条里关于 `graph_transfer_onedrive` 的说明；`graph_start_cloud_sync` 没变。

- INT 测试（Tom Lan，2026-10-16）没过，两个原因：(1) 旧的 `_resolve_drive` 吞掉所有 `GraphError`，一律返回 `drive_not_found`「No OneDrive provisioned」，INT 日志里 Graph 对 `/users/{id}/drive` 实际返回的是 **403**；(2) 委托的 `ms-graph` 连接器下，登录的管理员（哪怕是全局管理员）不是对方 OneDrive 的网站集管理员就打不开，而复制方案要同时访问两个 drive。
- v1 改成 Ginko 在用的 Rewst 做法（Mandy Du 2026-10-14 的评论）：在**离职用户自己的** OneDrive 里找到或新建 `Offboarding_Transfer`（按名字复用，不改名），把根目录其它项逐个 `PATCH` 移进去，再 `invite` 接收人（`write`、`requireSignIn`、`sendInvitation`）。不碰接收人的 drive。参数改为 `recipient_user_id`，去掉了 `destination_user_id` / `destination_folder_name`。
- 错误：先解析两个用户（`user_not_found`，带 `role`）；drive 404 返回 `status: no_onedrive`（跳过，和 Rewst 一致）；各 drive 步骤的 401/403 返回 `access_denied`，带 `step`、`graph_status` 和提示「加网站集管理员，或改用 ms-graph-app」；其它错误走 `GraphError.to_envelope()`。
- 重试：已经移进文件夹的不在根目录里，再调一次只移剩下的；文件夹权限里接收人已有 `write`/`owner` 就不再邀请。有项移动失败或邀请失败时返回 `status: partial`，逐项带 Graph 状态码。
- 两种连接器都能用。`ms-graph-app` 授予应用权限 `Files.ReadWrite.All` 后不用逐个用户配置，离职场景推荐用它。

## PRD-20422 · 2026-10-14

- 新模块（`src/graph_mcp/tools/offboarding.py` @ `eb38295`），在 `server.py:115,125` 和其它八个工具模块一起注册。延期自 PRD-20393 的 item 7-8。
- `graph_transfer_onedrive`（`:165-264` @ `eb38295`）天生无状态、幂等：每次调用都用 `_list_all_children`（`:62-75`）重新列出源和目标 OneDrive 的根/文件夹内容，按 `name`+`size` 做差异比对，只对目标端还没有的项发起 Graph `/drives/{id}/items/{id}/copy`。刻意不设独立的"查进度"工具——再调一次 `graph_transfer_onedrive` 本身就是查进度（`_rollup_transfer_status`，`:99-112`，把每条item的动作集合映射成 `started`/`in_progress`/`completed`）。
- `graph_start_cloud_sync`（`:271-368` @ `eb38295`）只支持 Azure AD Connect **云同步**，这是 Microsoft Graph 唯一能触发的目录同步机制（`/servicePrincipals/{id}/synchronization/jobs/{id}/start`）。经典本地 AD Connect 的租户会收到 `no_sync_job_configured`，并指明 `Start-ADSyncSyncCycle` 这条替代路径——这条路径 Graph 完全没有 API 能碰到，是硬性能力边界，不是功能缺失。
- **发现过滤条件有实锤证据，不是猜的**：`_CLOUD_SYNC_AGENT_HOMEPAGE_FRAGMENT` / `_CLOUD_SYNC_JOB_TEMPLATE_ID`（`:41-42` @ `eb38295`，都是 `"AD2AADProvisioning"`）来自 Ginko「08 User Offboarding」真实的 Rewst GraphQL 快照（99 个 workflow，与 EMD W3 交叉核对过），完整引用见 `docs/tickets/PRD-20422.zh.md` 的「工单往来」一节。Rewst 自己那条跑得通的 `[REWST - TASK] M365: Start Entra Cloud Sync` 子流程就是先按 `homepage` 含这个片段（且 `accountEnabled`）筛服务主体，再按该主体的同步任务 `templateId` 匹配且 `schedule.state == "Active"` 筛任务——本模块的 `_discover_cloud_sync_service_principal`（`:115-133`）和 `_discover_cloud_sync_job`（`:136-150`）做的是同一套两段式过滤。这条过滤条件在这份证据出现之前，在已冻结的技术设计里还是一个未确认的 Open Question；现在仍然没有对照 Microsoft 官方 API 文档逐条核实过（见 README 的 Known Gaps）。
- **同一条评论里还有一个相关但不同的问题，本单刻意没有采纳**：同一条评论显示 Rewst 的 OneDrive 那一步从来不是真正的内容转移（只是原地归档+共享），并建议把"v1 到底该做什么"这个仍然悬而未决的问题收窄。但已批准的技术设计在这份证据出现之前，就已经选定了"把内容复制进一个新的目标文件夹"这个方案（一项产品设计决定，已经过了本单的 groom 关口）——本次实现原样交付这个已批准的行为。完整推理见 `docs/tickets/PRD-20422.zh.md` 的「自测」一节「本次自测发现、但未在此处处理的一处出入」；记在这里是为了让以后碰这个模块的人不用重新发现一遍。
- 两个发现辅助函数都返回 `{"candidates": [...]}` 而不是直接解析成单个 id 或直接抛错，这样 `graph_start_cloud_sync` 才能在服务主体和任务两个层级都统一套用 AC10（零候选 → `no_sync_job_configured`）和 AC11（多于一个 → `sync_job_ambiguous`，带 `extra: {"candidates": [...]}`）。
- `graph_transfer_onedrive` 的驱动器解析顺序是固定的（先源后目标），专门是为了让 AC5（源用户没有 OneDrive）在触碰目标之前就短路返回，AC7（目标用户没有 OneDrive）在列出源的根目录之前就短路返回——这里顺序本身就是设计的一部分，不只是"有没有这些检查"。
- 没有改动 `api_client.py`——两个工具全靠解析后的 JSON 响应体工作（见技术设计「方案选型」一节 "api_client change" 一行，已否决）。`graph_transfer_onedrive` 是本仓库第一个真正用到此前一直沉睡的 `Files.ReadWrite.All` 授权的工具（README 权限说明已在本单更新）。
- 测试覆盖：`tests/test_tools.py::test_transfer_onedrive_starts_copy_for_new_items`、`::test_transfer_onedrive_is_idempotent_when_all_items_already_present`、`::test_transfer_onedrive_does_not_recopy_items_mid_transfer`、`::test_transfer_onedrive_reports_drive_not_found_for_source`、`::test_transfer_onedrive_reports_no_content_for_empty_source`、`::test_transfer_onedrive_reports_drive_not_found_for_destination`、`::test_start_cloud_sync_starts_job_for_synced_user`、`::test_start_cloud_sync_is_not_applicable_for_cloud_only_user`、`::test_start_cloud_sync_reports_no_sync_job_configured`、`::test_start_cloud_sync_reports_ambiguous_when_multiple_jobs`、`::test_start_cloud_sync_discovery_is_not_cached_across_tenants`（均为本单新增，在 `eb38295` 上全部通过）；`test_tools_list_snapshot` 为工具数提升（39→41）做了相应扩展。
- 自动测试覆盖不到的（需要一个真实配置了 Azure AD Connect cloud sync 的租户——见 `docs/tickets/PRD-20422.zh.md` 的「自测」一节，TC13/AC8）：这条有实锤证据的发现过滤条件是否真的匹配一个真实租户已注册的 agent/job，以及 Graph 自己的 `/start` 接口在并发重试下的行为是否如假设那样。同样未确认的还有：workflow 08/03 背后的租户到底用不用云同步——如果不用，不管这个工具实现得多对，`graph_start_cloud_sync` 都碰不到它们。

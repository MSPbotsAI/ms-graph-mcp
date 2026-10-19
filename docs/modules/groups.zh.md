# 模块：`src/graph_mcp/tools/groups.py`（分组成员 / 路由）

本模块文档的第一条记录。

## PRD-20393 · 2026-10-13

- `graph_list_user_groups` 与 `graph_list_groups`（`src/graph_mcp/tools/groups.py` @ `722e955`）从「内部走完 Graph 所有页、到 max_results 才停」改成单 Graph 页/每次调用，和 `graph_list_group_members` 既有契约一致：`page_token` 传入、`next_page_token` 传出，经由共用的 `_common.skiptoken` 辅助函数（从本文件私有搬出来——现在 `users.py::graph_list_users` 也在用）。原来依赖「一次调用拿到 max_results 以内全部结果」的调用方现在必须改为跟随 `next_page_token`；本仓内没有找到这两个工具的调用方（只暴露给外部 MCP 客户端），所以这个影响面没法仅凭本仓完全列清楚——已写进 `docs/tickets/PRD-20393.md` 的 Open Questions 交给 groom，不是默默假定安全。
- `graph_list_user_groups` 还会给每个组派生 `sync_source`（"on-premises"/"cloud"），依据是 `onPremisesSyncEnabled`，和 `graph_get_group`（`:301-332`）已经在用的派生方式一样——现在本文件里第二处用这个模式；以后任何需要判断一个组同步来源的新工具都应复用同一个 `"on-premises" if obj.get("onPremisesSyncEnabled") else "cloud"` 表达式，不要另造一套说法。
- `_skiptoken` 搬到了 `_common.py`（改名 `skiptoken`，不再私有），因为现在是两个文件共用，不止一个。
- 测试覆盖：`tests/test_tools.py::test_list_user_groups_paginates_with_skiptoken`、`::test_list_user_groups_returns_group_type_mail_enabled_and_sync_source`、`::test_list_groups_paginates_with_skiptoken`、`::test_list_tools_surface_graph_error_instead_of_raising`（参数化，同时覆盖本文件两个工具和 `graph_list_users`）（均为本单新增，在 `722e955` 上全部通过）。
- 自动化测试未覆盖的部分：本文件这次改动没有发现需要特别记录的盲区——分页/sync_source 逻辑已经针对本仓的假 Graph 客户端完整验证；本次没有碰到任何只能在真实租户上验证的行为（对比 `users.py` 的 UPN/manager 改动，那边确实有记录在案的真实租户盲区）。

## PRD-20393-r2 · 2026-10-14

- 2026-10-14 针对真实租户的一次验收测试（comment `90170262303660`）发现 `graph_list_user_groups` 的分页（即上一条记录的「一次 Graph 调用一页」契约）会丢组且永不终止：客户端用来从 `/users/{id}/memberOf` 这个多态原始响应里只留下组对象的那段过滤逻辑，并不影响 `$top`/`$skiptoken`——这两者是由原始页驱动的，所以一页如果所有条目都被过滤掉，游标照样前进，最坏情况下这个遍历永远不会收敛。
- 修复（`src/graph_mcp/tools/groups.py:69-122` @ `ccf3931`，尚未进 `master`——见本单工作分支）：Graph 调用路径从 `/users/{id}/memberOf` 改成 `/users/{id}/memberOf/microsoft.graph.group`（一个标准的 OData 类型转型段），客户端那段按类型过滤的逻辑整段删除——Graph 自己现在每页只会返回组对象，所以「过滤后的集合」和「分页遍历到的集合」在结构上天然就是同一个集合。这只是一次正确性修复；工具自己的请求/响应 JSON 契约（入参 `user_id`、`max_results`、`page_token`；出参 `count`、`groups`、`has_more`、`next_page_token`）不变。
- 对本文件未来修复多态 Graph 导航属性分页问题的先例：**优先在请求 URL 里做 OData 类型转型，而不是在客户端做后置过滤**——后置过滤会悄悄让「被过滤的集合」和「分页游标」脱钩，这正是本次缺陷的成因。
- 测试覆盖：`tests/test_tools.py::test_list_user_groups_paginates_with_skiptoken`（改写为断言转型后的 URL，并走一个三页、无重复的分页场景，在 `ccf3931` 上全部通过）。
- 自动化测试未覆盖的部分：`/memberOf/microsoft.graph.group` 这个转型本身，本轮没有对着真实租户重新验证过（这是一个标准的、有文档记载的 Graph 用法，但本仓没有真实 Graph 租户可供验证——和 `users.py` 里 UPN/manager 那几条记录的盲区是同一类）。见 `docs/tickets/PRD-20393-r2.zh.md` 「自测」一节的「本地盲区」。
## PRD-20480 · 2026-10-14

- 新工具 `graph_add_group_owner`（`src/graph_mcp/tools/groups.py:71` @ `ad9f0e2`），放在 `graph_assign_groups` 之后。`POST /groups/{id}/owners/$ref`，body 用 `_DIRECTORY_OBJECT_URL`（`graph_assign_groups` 给 `/members/$ref` 用的同一个常量）拼出 `@odata.id`——结构上是同一类端点，只是 owner 而不是 member。完全照搬 `graph_assign_groups` 已有的 400「already exist」处理（`:57-59` 的先例），把结果报成 `already_owner` 而不是错误，和这个工具的幂等标注一致。
- 没有对 Teams 和普通 M365 组做区分——Microsoft Teams 在 Graph 里就是靠一个 M365 组撑着，所以假定同一个端点对两者都适用。这是设计文档里标出的一条假设，没有在真实的 Teams 背书组上单独验证过。
- 自身不做任何审批流程——工单原话"Teams 只有经过批准才设为所有者"描述的是一个在调用这个工具**之前**就要完成的 SOP 级步骤；按设计排除在范围之外（见 `docs/tickets/PRD-20480.md` § Non-goals）。
- 测试覆盖：`tests/test_tools.py::test_graph_add_group_owner_adds_new_owner`、`::test_graph_add_group_owner_is_idempotent_for_existing_owner`（均为本单新增，在 `ad9f0e2` 上全部通过）。
- 自动化测试未覆盖的部分（需要真实租户——见 `docs/tickets/PRD-20480.md` § 自测，TC-MSG-005b）：Graph 实际的"已是 owner"错误文本是否和 mock 的 400 处理假设一致（和 `graph_assign_groups` 自己当初钉死 `already exist` 匹配时是同一类开放问题）。另外还依赖上面 `users.md` 条目里记的、`graph_create_tap` 同款的 `Group.ReadWrite.All` 跨仓库 scope 授权排期。

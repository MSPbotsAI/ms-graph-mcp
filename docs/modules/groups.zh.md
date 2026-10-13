# 模块：`src/graph_mcp/tools/groups.py`（分组成员 / 路由）

本模块文档的第一条记录。

## PRD-20393 · 2026-10-13

- `graph_list_user_groups` 与 `graph_list_groups`（`src/graph_mcp/tools/groups.py` @ `722e955`）从「内部走完 Graph 所有页、到 max_results 才停」改成单 Graph 页/每次调用，和 `graph_list_group_members` 既有契约一致：`page_token` 传入、`next_page_token` 传出，经由共用的 `_common.skiptoken` 辅助函数（从本文件私有搬出来——现在 `users.py::graph_list_users` 也在用）。原来依赖「一次调用拿到 max_results 以内全部结果」的调用方现在必须改为跟随 `next_page_token`；本仓内没有找到这两个工具的调用方（只暴露给外部 MCP 客户端），所以这个影响面没法仅凭本仓完全列清楚——已写进 `docs/tickets/PRD-20393.md` 的 Open Questions 交给 groom，不是默默假定安全。
- `graph_list_user_groups` 还会给每个组派生 `sync_source`（"on-premises"/"cloud"），依据是 `onPremisesSyncEnabled`，和 `graph_get_group`（`:301-332`）已经在用的派生方式一样——现在本文件里第二处用这个模式；以后任何需要判断一个组同步来源的新工具都应复用同一个 `"on-premises" if obj.get("onPremisesSyncEnabled") else "cloud"` 表达式，不要另造一套说法。
- `_skiptoken` 搬到了 `_common.py`（改名 `skiptoken`，不再私有），因为现在是两个文件共用，不止一个。
- 测试覆盖：`tests/test_tools.py::test_list_user_groups_paginates_with_skiptoken`、`::test_list_user_groups_returns_group_type_mail_enabled_and_sync_source`、`::test_list_groups_paginates_with_skiptoken`、`::test_list_tools_surface_graph_error_instead_of_raising`（参数化，同时覆盖本文件两个工具和 `graph_list_users`）（均为本单新增，在 `722e955` 上全部通过）。
- 自动化测试未覆盖的部分：本文件这次改动没有发现需要特别记录的盲区——分页/sync_source 逻辑已经针对本仓的假 Graph 客户端完整验证；本次没有碰到任何只能在真实租户上验证的行为（对比 `users.py` 的 UPN/manager 改动，那边确实有记录在案的真实租户盲区）。

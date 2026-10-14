# 模块：`src/graph_mcp/tools/sites.py`（SharePoint 站点/文档库/文件）

本模块文档的第一条记录（本仓此前没有为 `sites.py` 积累过事实）。

## PRD-20413 · 2026-10-13

- 新增 `graph_read_site_file_text`（`src/graph_mcp/tools/sites.py:331-430` @ `2514f69`），作为 `graph_search_sites` → `graph_list_drive_items` → `graph_read_file_text` 三连调用链的单调用替代：服务端完成 site → 默认文档库 → file → UTF-8 content 的解析，调用方全程不需要拿到/传入 `driveId`/item id。直接针对 PRD-20211 被重开的缺陷而建：模型在两个链式调用之间转述那个又长又随机的 `driveId` 时偶发出错，而纯提示词层面的修法（PRD-20211，2026-10-09）已被实测证明无法稳定生效（同一工单，QA 复验，2026-10-10）。
- 三个新增的模块级辅助函数支撑它（`src/graph_mcp/tools/sites.py:46-169` @ `2514f69`）：`_resolve_site`（`:46-96`，有 site_id 就直接按 id 查，否则搜索 + 优先精确 displayName 匹配，无精确匹配时退回唯一的关键词命中；`>1` 个候选且无精确匹配 → `site_ambiguous`）、`_resolve_drive`（`:107-122`，任何失败（含没有默认文档库）都折叠进 `file_not_found`——这个工具的契约里没有单独的"没有库"错误码）、`_resolve_file`（`:125-169`，用 Graph 的 `root:/{path}:` 寻址，和 `graph_create_file_text:317` 已经用的是同一机制；404 时会列出该文件夹内容，填进 `file_not_found` 错误的 `files_in_folder`；解析到的条目带 `folder` facet 时同样算 `file_not_found`，不是单独的错误码）。
- `src/graph_mcp/_json.py` 的 `error_envelope`（`:76-88` @ `2514f69`）新增了可选的第4个参数 `extra: dict | None = None`，用来带结构化的错误上下文（候选站点列表、同目录文件名等）。这是加法式、向后兼容的改动：`mail.py`/`users.py`/`groups.py`/`licenses.py`/`calendar.py`/`sites.py` 里所有既有调用点都不受影响，因为该参数默认省略 `extra`，输出与之前逐字节一致。以后任何工具需要结构化错误上下文，都应该扩展这同一个 `extra` 字典，不要再发明第二套错误信封形状。
- 本工具专属新增了六个结构化错误码（没有回填给原来那三个链式工具——它们在大小/解码失败时仍沿用原来那个单一的通用 `invalid_argument`）：`site_not_found`、`site_ambiguous`、`file_not_found`、`file_empty`、`file_too_large`、`not_text`。全部 `retryable: false`。
- 没有新增 Graph 权限范围——复用既有的 `Sites.Read.All`（与 `graph_search_sites`/`graph_read_file_text` 相同）。只读：这个工具的辅助函数发出的每一个 Graph 请求都是 `GET`（有测试断言，见下），结构上就保证了——辅助函数只会调 `client.get`/`client.get_content`。
- 多租户注意事项（沿用本文件既有设计，非本单新增）：这个工具的解析链不会把 `site_query → site_id` 或 `(drive_id, path) → item_id` 的映射跨调用缓存——每次调用都独立地从自己的参数和自己那次请求的 token 重新解析。未来如果有人为了"省一次往返"给它加缓存，会悄悄破坏租户隔离——别这么做。
- 测试覆盖：`tests/test_tools.py::test_read_site_file_text_*`——14 条测试（对应 `docs/tickets/PRD-20413.ac.json` 里的 TC1–TC14），覆盖主路径、两种站点解析的退路/歧义分支、`file_not_found` 的三种子情形、空文件/超限/边界的大小检查、UTF-8 解码失败分支，以及"全程只发 GET"和"两次调用互不污染状态"这两条结构性断言。
- 自动化测试覆盖不到的部分（需要一个真实、已授权的 Entra/SharePoint 测试租户——见 `docs/tickets/PRD-20413.zh.md` §自测，TC15/TC16）：工单自己定的"连续跑 10 次"这条运营性验收标准，以及 Graph 对一个真实二进制 Office 文件的 `root:/{path}:` 响应里，`file.mimeType` 是否真的会填出一个有意义的值。连接器自身的逻辑已验证；真实租户上的行为尚未验证。

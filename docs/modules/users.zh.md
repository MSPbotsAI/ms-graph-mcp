# 模块：`src/graph_mcp/tools/users.py`（用户资料读写）

本模块文档的第一条记录（PRD-20321 之前这个仓库没有任何已积累的模块事实）。

## PRD-20321 · 2026-10-12

- `graph_get_user` 向 Graph 发起的 `$select`（`src/graph_mcp/tools/users.py:396-406` @ `0714c88`）现在在原有字段之外新增了 `employeeId,givenName,surname`。本单之前这三个字段都没有被请求过，所以不管目录里某账号实际有没有填这些字段，这个工具都读不出来——这是连接器侧的缺口，不是账号数据侧的缺口（这一点是直接读 `$select` 字符串得出的结论，不是靠观察接口返回猜的）。
- `graph_update_user`（`src/graph_mcp/tools/users.py:414-500` @ `0714c88`）新增两个参数：`employee_id: str | None = None`（把 `employeeId` PATCH 成给定值）和 `clear_employee_id: bool = False`（把 `employeeId` PATCH 成 `null`）。同一次调用里两者都传会在调用 Graph **之前**就返回 `invalid_argument` 错误——这跟既有的 `_add_profile_fields`（`:50-82`，`employee_hire_date` 用的那套）先校验再调用的模式是一致的。
- 这个工具现在有了第二个"可选字段、且可显式清空"的先例（第一个是 `employee_hire_date`）——以后如果还要给这个工具加一个需要三态（未设置 / 设为某值 / 显式清空）的字段，应该照 `employee_id`/`clear_employee_id` 这对参数的形状走，不要另造一套。
- 测试覆盖：`tests/test_tools.py::test_update_user_writes_employee_id`、`::test_update_user_clears_employee_id`、`::test_update_user_rejects_employee_id_and_clear_together`、`::test_get_user_returns_given_name_and_surname`（均为本单新增，在 `0714c88` 上全部通过）。
- 自动化测试未覆盖的部分（需要真实 Entra ID 租户才能验证——见 `docs/tickets/PRD-20321.md` 的「自测」一节）：这个租户的目录 schema 到底会拒绝什么样的 `employeeId` 值（如果有的话）；以及这个租户里真实账号的 `givenName`/`surname` 是否确实被填了（连接器侧的修复已验证；账号数据侧没有）。

## PRD-20412 · 2026-10-13

- `graph_create_user`（`src/graph_mcp/tools/users.py:218-324` @ `3570af5`）在本单之前就已经无条件设置了 `passwordProfile.forceChangePasswordNextSignIn = True`（第 284 行，本单未改动）——强制改密码这个行为从来没缺过，缺的只是它的可观测性。本单要解决的纯粹是确认问题，不是行为问题。
- 成功返回 `201` 时，工具的返回逻辑（`:311-324`）现在会在返回前把 `passwordChangeRequiredAtNextSignIn: true` 合并进响应，不再直接透传 Graph 的原始响应。这跟 `graph_reset_password`（`:320-372`，未改动、本来就有）出于同样理由已经在用的"构造自己的确认对象"模式一致：Graph 自己的响应从不回显 `passwordProfile`（已对照 Microsoft 官方 `POST /users` API 参考的示例确认），所以原样透传等于零确认。
- 给这个文件以后做响应形状决策的先例：**不要信任 Graph 的响应体来证明一个 `passwordProfile` 形状的设置生效了——自己构造并返回一个确定性的确认。** 这是本文件里第二个这么做的工具（`graph_reset_password` 是第一个）；`GET ...?$select=passwordProfile` 读回方案被评估过并否决（有文档支撑的不可靠——Microsoft Q&A/论坛报告显示即便显式 `$select` 了，读回仍可能是 null/空），见 `docs/tickets/PRD-20412.zh.md` 的「技术设计 · 方案选型」。
- 新字段纯增量；`graph_create_user` 响应里没有任何既有字段被改名或删除（AC4），合并逻辑每次调用都构造一个全新的 `dict`，没有任何共享/模块级状态（AC5）。
- 测试覆盖：`tests/test_tools.py::test_create_user_response_confirms_forced_password_change`、`::test_create_user_description_documents_forced_password_change`、`::test_create_user_error_envelope_has_no_password_confirmation`、`::test_create_user_response_is_additive_only`、`::test_create_user_confirmation_is_per_call_not_shared_state`（均为本单新增，在 `3570af5` 上全部通过）。
- 自动化测试未覆盖的部分（需要真实/沙箱 Entra ID 租户才能验证——见 `docs/tickets/PRD-20412.zh.md` 的「自测」一节，TC6/AC6）：用这种方式创建的账号，首次登录时 Entra 是否真的会弹出强制改密码界面。这与上面 PRD-20321 记录过的 `employeeId` 目录 schema 缺口属于同一类本地盲区——连接器侧的请求已验证；租户侧的强制执行是 Graph 一个有据可查的既有行为，但本单没有独立重新验证它。

## PRD-20393 · 2026-10-13

- `graph_update_user`（`src/graph_mcp/tools/users.py` @ `722e955`）新增 `user_principal_name`、`display_name`、`given_name`/`clear_given_name`、`surname`/`clear_surname`。`user_principal_name` 的改动在发出任何 PATCH 之前，会先用 `_common.verified_domains(client)`（新辅助函数，查 `/domains?$select=id,isVerified`，与 `graph_list_domains` 自己那个更丰富的查询是分开的）做域名预检。
- 与本单自己的设计文档有意背离的一点：`given_name`/`surname` 用的是 `clear_<field>` 布尔开关形状，不是设计提出的 `_UNSET` 哨兵 + 字面 null 形状——沿用了 PRD-20321 当天已经为 `employee_id` 定下的形状（见上面 PRD-20321 那条："以后如果还要给这个工具加一个需要三态的字段，应该照 employee_id/clear_employee_id 这对参数的形状走，不要另造一套"）。`graph_update_user` 现在对它支持的三个可空字符串字段只有一套三态机制，不是两套。
- `user_principal_name` 与 `display_name` 刻意**没有** clear_* 选项——Microsoft Graph 把这两者都记成 `user` 资源上不可为空的属性；真实租户会把其中任一个的 null PATCH 拒成 400。以后谁要给这个工具加新的可能可空字段，先查 Graph 自己的 schema 是否真的可空，别想当然套用 clear_X 这个形状。
- `graph_get_user` 的默认 `$select`（`:397-434` @ `722e955`）扩充了 `proxyAddresses,identities,onPremisesSyncEnabled,onPremisesLastSyncDateTime,onPremisesDistinguishedName`（常量 `_USER_PROFILE_SELECT`）；新增可选参数 `select: list[str] | None`，传入时会整体替换默认字段列表（同时丢掉默认的 `$expand=manager`）。
- 新工具 `graph_remove_manager`（`DELETE /users/{id}/manager/$ref`），404→`status=no_manager`，与 `groups.py` 里 `graph_remove_group_member` 的 404→`not_a_member` 处理同一个模式。工具数上限 37→38。
- `graph_list_users`（`:151-219` @ `722e955`）从「内部走完所有页直到 max_results」改成单 Graph 页/每次调用（`page_token`/`next_page_token`），和 `graph_list_group_members` 既有契约一致——两个分组列表工具的同类改动见 `groups.py` 的模块文档。
- 测试覆盖：`tests/test_tools.py::test_update_user_omitted_new_fields_are_not_sent`、`::test_update_user_explicit_null_clears_field`、`::test_update_user_rejects_given_name_and_clear_together`、`::test_update_user_rejects_unverified_upn_domain`、`::test_update_user_allows_verified_upn_domain`、`::test_update_user_still_rejects_when_nothing_to_update`、`::test_remove_manager_deletes_manager_ref`、`::test_remove_manager_is_idempotent_on_missing_manager`、`::test_get_user_default_select_includes_new_fields`、`::test_get_user_custom_select_narrows_fields`、`::test_list_users_paginates_with_skiptoken`（均为本单新增，在 `722e955` 上全部通过）。
- 自动化测试未覆盖的部分（需要真实双租户 Entra 环境才能验证——见 `docs/tickets/PRD-20393.zh.md` 的「自测」一节，TC15/AC15）：真正的跨租户隔离。这个服务器本来就没有自己的租户检查逻辑（隔离完全依赖调用方的 bearer token）——不是本单引入的缺口，只是本单第一次把它写成一条明确的 AC。

## PRD-20393-r2 · 2026-10-14

2026-10-14 针对真实租户的一次验收测试（comment `90170262303660`，SOP 225 / Ginko onboarding agent）发现本文件里第一轮（PRD-20393）交付的 3 个工具需要返工。下面三处修复都是**响应结构的破坏性变更**，特别标出是因为这几个工具才上线没几天（不像第一轮自己那些字段扩宽，是加法/纠正性的）。

- **`graph_update_user`**（`src/graph_mcp/tools/users.py:462-712` @ `ccf3931`，尚未进 `master`——见本单工作分支）：一次成功调用现在会在 `PATCH` 之后再发一次 `GET /users/{id}?$select=<改动的字段>`，`updated` 从这次回读构建（`{字段: 值}`），不再是 `list(body.keys())`。第一轮的版本只会把请求自己的字段名原样回显，从未确认过 Graph 实际存了什么。**破坏性**：`updated` 的类型从 `list[str]` 变成 `dict[str, Any]`。
- **`graph_remove_manager`**（`:709-764` @ `ccf3931`）：把第一轮「先 delete 再解读 404」的流程（这个流程假设对一个已经没有 manager 的用户重复 `DELETE` 会 404——验收证明对真实租户这个假设不成立）换成「先 GET manager 再按需 delete」：先 `GET /users/{id}/manager`；这一步 404 就意味着没有 manager，而且**完全不发 `DELETE`**；否则在 `DELETE` 执行之前从这次 GET 捕获 `previous_manager`，而 `DELETE` 本身若收到 404（GET 和 DELETE 之间被并发移除）现在当成功处理，不再往上抛。**破坏性**：整个响应结构被替换，`{"status": "removed"|"no_manager", "user_id"}` → `{"user_id", "removed": bool, "previous_manager": {id, display_name, user_principal_name}|null, "reason": "no_manager"|null}`。
- **`graph_get_user`**（`:35-47`、`:411-460` @ `ccf3931`）：`_USER_PROFILE_SELECT` 新增 `onPremisesImmutableId`、`onPremisesSamAccountName`、`createdDateTime`、`lastPasswordChangeDateTime`（第一轮的默认 `$select` 单纯漏掉了验收要的这四个字段）。另外，`select` 参数里若包含 `"manager"`，现在会路由到 `$expand=manager($select=id,displayName,userPrincipalName)`，不再被 Graph 从 `$select` 里悄悄丢弃（`manager` 是导航属性，不是扁平属性）——第一轮的文档字符串承诺过 `select=["manager"]` 能用，实际不行。非破坏性：对默认响应是纯加法，且 `select` 不含 `"manager"` 时行为和第一轮完全一样（本轮未改动的 `test_get_user_custom_select_narrows_fields` 确认了这一点）。
- 本轮给这个文件留下的先例：**一个工具如果靠「这次调用自己的响应/错误码」去推断『Graph 是不是真的做了 X』，而不是靠一次专门的回读去确认，这是正确性风险，不只是写法偏好**——`graph_update_user` 旧的回显和 `graph_remove_manager` 旧的「DELETE 404 就等于没有 manager」推断，都是这个形状，而且都在对着真实租户时错了，尽管第一轮的每条单元测试都通过。本文件未来需要确认一次写操作实际效果的工具，应该靠回读，不要靠写操作本身的响应去推断。
- 为什么第一轮自己的测试套件一条都没抓到：每个假客户端测试当时都只模拟了各自工具作者设想的那条happy path（一页纯组对象的 `memberOf`，一次恰好 404 的 `DELETE`）。第一轮没有任何测试模拟过混合类型的 Graph 集合，也没有模拟过两次 `DELETE` 的序列——测试覆盖见下，本轮为补这个缺口新增的假客户端（`_ManagerRemovalClient`）见 `docs/tickets/PRD-20393-r2.zh.md` 的「自测」一节。
- 测试覆盖：`tests/test_tools.py::test_update_user_returns_rereads_changed_values`、`::test_remove_manager_returns_previous_manager_when_present`、`::test_remove_manager_returns_no_manager_without_calling_delete`、`::test_remove_manager_handles_concurrent_delete_404_as_removed`、`::test_remove_manager_surfaces_non_404_get_error`、`::test_get_user_default_select_includes_onprem_and_audit_fields`（扩展）、`::test_get_user_select_with_manager_adds_expand`（均为本单新增/改写，在 `ccf3931` 上全部通过）；另有 6 个既有的 `graph_update_user` 测试把 `client.methods` 断言扩展以适配新增的回读调用（行为/body 断言不变）。
- 自动化测试未覆盖的部分：以上四处修复，本轮都没有对着真实 Graph 租户重新验证过（和上面 PRD-20393 自己的 AC15 是同一类盲区）——验收报告自己的证据 run id（`155f15f9-…`、`a409123d-…`、`f397bf60-…`）是目前能拿到的最接近的事实依据，设计建议在本单到达一个可触达的环境之后，让提单人重新跑一遍 comment `90170262303660` 里的原文复现步骤。见 `docs/tickets/PRD-20393-r2.zh.md` 「自测」一节的「本地盲区」。

## PRD-20393-r3 · 2026-10-16

Minnie Zhou 在 INT 上针对 MSPbots 生产租户测试了第二轮的发布（comment `90170263295399`，agent sopId=345），发现第二轮的 4 项修复大体站得住（1.1/1.6 完全通过；1.4/1.5 的写入路径被测试连接器自身的权限阻塞，不是失败），但在第二轮改过的同两个函数上又发现了 1 个新缺陷加 1 个代码评审发现的高风险问题。

- **`graph_remove_manager`**（`src/graph_mcp/tools/users.py:733-769` @ `bd3fe8d`）：第二轮的修复已经正确地不再信任 `DELETE` 的 404 行为，但仍然信任 `GET /users/{id}/manager` 的 404 只代表「没有经理」——Graph 对一个不存在的 `user_id` 在这同一个调用上也返回 404。遇到这个 404 时，工具现在先发起 `GET /users/{id}?$select=id`；该调用 404 则返回 `not_found` 错误信封，只有存在性检查成功时才继续返回 `{"removed": false, "reason": "no_manager"}`。非破坏性：响应形状不变，只是修正了一种此前被误判的输入的分类。
- **`graph_update_user`**（`:462-677` @ `bd3fe8d`）：第二轮的回读一律用 `user_id`，当 `userPrincipalName` 的 PATCH 把它改名后就会 404（如果 `user_id` 本身传的就是旧 UPN 而不是 GUID）——这是代码评审发现，本轮无法在 INT 上实测，因为测试连接器在生产租户没有用户写权限。修法是用 `body.get("userPrincipalName", user_id)` 回读，而不是一律用 `user_id`。另外，PATCH 和回读现在拆成两个独立的 `try`，PATCH 成功后如果回读失败，返回 `{"status": "success", "updated": dict(body), "readback_error": {...}}`，而不是把一次已经成功的写入报成错误信封。非破坏性：成功路径上 `updated` 的形状不变；`readback_error` 是一个新增的可选键，只在回读恰好失败时才出现。
- 低优先级、纯文档改动：`graph_update_user` 的 docstring 现在说明回读值在 Graph 复制延迟下偶尔可能是旧值。本轮考虑过重试并拒绝了（见 `docs/tickets/PRD-20393-r3.zh.md` §技术设计「方案选型」——这个工具接受的若干字段，例如 `employeeHireDate`，会被 Graph 在写入时规范化，朴素的「发送值 vs 回读值」比较在涉及这些字段的每次调用上都会误判为「旧值」）。
- 本轮为这个文件加强的先例（延续上面第二轮自己留下的先例）：**同一个 Graph 错误码背后可能对应不止一种真实原因**——第二轮为 `DELETE` 的 404（已移除 vs. 本来就已经移除过）学到了这一课，本轮在上一层、对 `GET /users/{id}/manager` 的 404（没有经理 vs. 根本不存在）又学了一遍一模一样的教训。未来这个文件里任何按 Graph 错误码分支的工具，在把它当成单一信号之前，都该先问一句「还有什么别的原因会给出同一个码」。
- `_ManagerRemovalClient`（`tests/test_tools.py:1724-1762` @ `bd3fe8d`）现在按请求路径的后缀区分两处可能的 GET 调用点（经理读取 vs. 新增的存在性检查），不需要再写第二个假类——这是一个可复用的写法，供未来任何需要独立控制同一假客户端上两次形状相同调用的测试参考。
- 测试覆盖：`tests/test_tools.py::test_remove_manager_nonexistent_user_returns_not_found`、`::test_update_user_rereads_with_new_upn_after_rename`、`::test_update_user_readback_failure_after_patch_still_reports_success`、`::test_update_user_description_mentions_replication_lag`（均为本轮新增，在 `bd3fe8d` 上全部通过）；`::test_remove_manager_returns_no_manager_without_calling_delete` 为新增的存在性检查调用做了更新（真实的「没有经理」用户的行为不变，只是调用次数增加了）。
- 自动化测试未覆盖的部分：和上面第二轮条目是同一类盲区——两项修复都没有对着真实 Graph 租户重新验证过（本轮依据的那条 comment 对两条相关写入路径的结果都是 BLOCKED，受测试连接器权限限制，不是失败）。见 `docs/tickets/PRD-20393-r3.zh.md` 「自测」一节的「已知的本地盲区」。

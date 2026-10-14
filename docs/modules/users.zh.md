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

## PRD-20480 · 2026-10-14

- 新工具 `graph_create_tap`（`src/graph_mcp/tools/users.py:746` @ `ad9f0e2`），放在 `graph_list_auth_methods` 之后。`POST /users/{id}/authentication/temporaryAccessPassMethods`，默认 `is_usable_once=True`。明确标为**非幂等**（`idempotentHint=False`）——第二次调用 Graph 会让之前的 pass 失效。返回体直接把 Graph 自己返回的 `temporary_access_pass` 透出来；这是本文件里第一个成功响应必须携带密钥类值的写工具（其它写工具要么透出非密钥状态、要么刻意不透出密钥，例如 `graph_create_user`（`:240`）从不透出密码）——这是一个已标出的风险点，不是新开了一个日志落点，Graph 的接口契约没给别的选择（之后没法再把这个 pass 取回来）。
- `preferred_language` 作为可选参数加到了 `graph_create_user`（`:282`，写入在 `:324-325`）与 `graph_update_user`（`:565`，写入在 `:648-649`）——照搬这两个函数里其它可选字段都在用的 `if x is not None: body["..."] = x` 写法，不需要新的辅助函数。
- **这是一个跨仓库的上线排期依赖，不是代码缺口**：`graph_create_tap` 需要 `UserAuthenticationMethod.ReadWrite.All` 这个 Graph scope，而平台共用的 Entra 应用注册目前没有申请它。在 `MCP-Management-Service` 的 `auth/oauth/vendors/msgraph.py`（另一个仓库，不是这个仓）加上这个 scope、且每个已经连接过的租户重新同意之前，这个工具对它们会一直 403——和本仓 README 里记的 `Mail.Read`/`Calendars.ReadWrite` 上线路径一样。排查这里的 403 时先查这个排期问题，别先怀疑代码缺陷（见 `docs/tickets/PRD-20480.md` §8 / TC-MSG-008）。
- 测试覆盖：`tests/test_tools.py::test_graph_create_tap_default_is_usable_once`、`::test_graph_create_tap_honors_explicit_settings`、`::test_graph_create_tap_user_not_found_returns_envelope`、`::test_create_user_sets_preferred_language`、`::test_update_user_sets_preferred_language`（均为本单新增，在 `ad9f0e2` 上全部通过）。
- 自动化测试未覆盖的部分（需要一个已经授权了新 scope 的真实租户——见 `docs/tickets/PRD-20480.md` § 自测，TC-MSG-008）：上线前 403 的具体文本，要确认它和"找不到"的 403 能区分开。这是本地盲区，没有被默默当成安全处理。

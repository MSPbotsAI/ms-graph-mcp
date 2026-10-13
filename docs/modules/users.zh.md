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

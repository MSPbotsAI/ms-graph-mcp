# 模块：`src/graph_mcp/tools/users.py`（用户资料读写）

本模块文档的第一条记录（PRD-20321 之前这个仓库没有任何已积累的模块事实）。

## PRD-20321 · 2026-10-12

- `graph_get_user` 向 Graph 发起的 `$select`（`src/graph_mcp/tools/users.py:396-406` @ `0714c88`）现在在原有字段之外新增了 `employeeId,givenName,surname`。本单之前这三个字段都没有被请求过，所以不管目录里某账号实际有没有填这些字段，这个工具都读不出来——这是连接器侧的缺口，不是账号数据侧的缺口（这一点是直接读 `$select` 字符串得出的结论，不是靠观察接口返回猜的）。
- `graph_update_user`（`src/graph_mcp/tools/users.py:414-500` @ `0714c88`）新增两个参数：`employee_id: str | None = None`（把 `employeeId` PATCH 成给定值）和 `clear_employee_id: bool = False`（把 `employeeId` PATCH 成 `null`）。同一次调用里两者都传会在调用 Graph **之前**就返回 `invalid_argument` 错误——这跟既有的 `_add_profile_fields`（`:50-82`，`employee_hire_date` 用的那套）先校验再调用的模式是一致的。
- 这个工具现在有了第二个"可选字段、且可显式清空"的先例（第一个是 `employee_hire_date`）——以后如果还要给这个工具加一个需要三态（未设置 / 设为某值 / 显式清空）的字段，应该照 `employee_id`/`clear_employee_id` 这对参数的形状走，不要另造一套。
- 测试覆盖：`tests/test_tools.py::test_update_user_writes_employee_id`、`::test_update_user_clears_employee_id`、`::test_update_user_rejects_employee_id_and_clear_together`、`::test_get_user_returns_given_name_and_surname`（均为本单新增，在 `0714c88` 上全部通过）。
- 自动化测试未覆盖的部分（需要真实 Entra ID 租户才能验证——见 `docs/tickets/PRD-20321.md` 的「自测」一节）：这个租户的目录 schema 到底会拒绝什么样的 `employeeId` 值（如果有的话）；以及这个租户里真实账号的 `givenName`/`surname` 是否确实被填了（连接器侧的修复已验证；账号数据侧没有）。

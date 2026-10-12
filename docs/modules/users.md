# Module: `src/graph_mcp/tools/users.py` (user profile read/write)

First entry in this module doc (no prior accumulated facts existed in this repo before PRD-20321).

## PRD-20321 · 2026-10-12

- `graph_get_user`'s Graph `$select` (`src/graph_mcp/tools/users.py:396-406` @ `0714c88`) now includes `employeeId,givenName,surname` in addition to the previously-requested fields. Before this ticket, none of the three were requested, so the tool could never return them regardless of whether the directory had them set — this was a connector-side gap, not an account-data gap (confirmed by reading the `$select` string directly, not by observing the API response).
- `graph_update_user` (`src/graph_mcp/tools/users.py:414-500` @ `0714c88`) gained two parameters: `employee_id: str | None = None` (PATCHes `employeeId` to the given value) and `clear_employee_id: bool = False` (PATCHes `employeeId` to `null`). Passing both in the same call returns an `invalid_argument` error envelope *before* calling Graph — this mirrors the existing `_add_profile_fields` (`:50-82`) validate-before-call pattern used for `employee_hire_date`.
- There is now a second precedent (besides `employee_hire_date`) for "optional profile field with explicit-clear support" on this tool — any future field that needs a tri-state (unset / set-to-value / explicitly-cleared) shape should follow the `employee_id`/`clear_employee_id` pair shape here, not invent a new one.
- Test coverage: `tests/test_tools.py::test_update_user_writes_employee_id`, `::test_update_user_clears_employee_id`, `::test_update_user_rejects_employee_id_and_clear_together`, `::test_get_user_returns_given_name_and_surname` (all added in this ticket, all passing at `0714c88`).
- Not covered by automated tests (needs a live Entra ID tenant — see `docs/tickets/PRD-20321.md` § Self-test): what value shape, if any, this tenant's directory schema rejects for `employeeId`; and whether `givenName`/`surname` are populated for real accounts in this tenant (the connector-side fix is verified; the account-data side is not).

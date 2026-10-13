from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import DEFAULT_BASE_URL, GraphClient, GraphError
from ._common import NO_TOKEN, odata_quote

# Our own ceiling on how many results a list tool returns to the agent
# (SOP: default <=50, hard cap <=200). Separate from Graph's own $top
# per-page maximum, which is documented at 999 for /users and is used
# below purely to size individual page requests.
_DEFAULT_MAX_RESULTS = 50
_HARD_CAP_MAX_RESULTS = 200
_GRAPH_TOP_MAX = 999

# Fields returned for each user in a list result. Deliberately narrower than
# graph_get_user's $select: no license/manager expansion, since those cost an
# extra join per user and a directory listing is a "find the right person"
# step, not a profile read.
_USER_LIST_SELECT = (
    "id,displayName,userPrincipalName,mail,accountEnabled,jobTitle,department"
)

_HIRE_DATE_DESC = (
    'Hire date, "YYYY-MM-DD" or full ISO 8601 date-time, e.g. "2026-10-20" '
    'or "2026-10-20T00:00:00Z". Graph refuses it on app-only connections; '
    "if the call fails on this field, retry without it."
)


def _normalize_hire_date(value: str) -> str | None:
    """Graph's employeeHireDate is a DateTimeOffset and rejects a bare date
    with a 400, so expand "YYYY-MM-DD" to midnight UTC. Returns None if the
    value is neither shape."""
    value = value.strip()
    try:
        if len(value) == 10:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%dT00:00:00Z")
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        return None


def _add_profile_fields(
    body: dict,
    *,
    street_address: str | None,
    city: str | None,
    state: str | None,
    postal_code: str | None,
    company_name: str | None,
    employee_hire_date: str | None,
) -> str | None:
    """Copy the optional address/company/hire-date fields shared by
    graph_create_user and graph_update_user into a Graph body. Returns an
    error envelope if employee_hire_date is malformed, else None."""
    if street_address is not None:
        body["streetAddress"] = street_address
    if city is not None:
        body["city"] = city
    if state is not None:
        body["state"] = state
    if postal_code is not None:
        body["postalCode"] = postal_code
    if company_name is not None:
        body["companyName"] = company_name
    if employee_hire_date is not None:
        normalized = _normalize_hire_date(employee_hire_date)
        if normalized is None:
            return error_envelope(
                "invalid_argument",
                'employee_hire_date must be "YYYY-MM-DD" or an ISO 8601 date-time.',
                False,
            )
        body["employeeHireDate"] = normalized
    return None


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_check_user_exists(
        user_principal_name: Annotated[
            str | None, Field(description="UPN to search for, e.g. alice@contoso.com.")
        ] = None,
        mail: Annotated[
            str | None, Field(description="Primary SMTP address to search for.")
        ] = None,
        proxy_address: Annotated[
            str | None,
            Field(
                description=(
                    'Any address on the user, incl. aliases, e.g. "alice@contoso.com" '
                    'or "smtp:alice@contoso.com". A bare address is matched as smtp:.'
                )
            ),
        ] = None,
    ) -> str:
        """Check whether an Entra ID user exists by UPN, mail or any proxy address.

        Provide exactly one of user_principal_name, mail or proxy_address.
        proxy_address also matches secondary aliases, so use it to check that
        an address is free before creating a user. Returns
        {"exists": true/false, "user": object | null}.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        given = [v for v in (user_principal_name, mail, proxy_address) if v]
        if len(given) != 1:
            return error_envelope(
                "invalid_argument",
                "Provide exactly one of user_principal_name, mail or proxy_address.",
                False,
            )

        params: dict = {
            "$select": "id,displayName,userPrincipalName,mail,accountEnabled,proxyAddresses",
            "$top": "1",
        }
        extra_headers = None
        if user_principal_name:
            params["$filter"] = f"userPrincipalName eq '{odata_quote(user_principal_name)}'"
        elif mail:
            params["$filter"] = f"mail eq '{odata_quote(mail)}'"
        else:
            address = proxy_address.strip()
            if ":" not in address:
                address = f"smtp:{address}"
            # Lambda filters on proxyAddresses are an advanced directory query.
            params["$filter"] = f"proxyAddresses/any(x:x eq '{odata_quote(address)}')"
            params["$count"] = "true"
            extra_headers = {"ConsistencyLevel": "eventual"}

        try:
            result = await client.get("/users", params=params, extra_headers=extra_headers)
            users = result.get("value", [])
            if users:
                return dump_json_capped({"exists": True, "user": users[0]})
            return dump_json_capped({"exists": False, "user": None})
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_list_users(
        display_name: Annotated[
            str | None,
            Field(
                description="Filter by display name; prefix match unless exact=True. "
                "Omit to list all users (still capped by max_results)."
            ),
        ] = None,
        exact: Annotated[
            bool, Field(description="When True, match display_name exactly instead of by prefix.")
        ] = False,
        account_enabled: Annotated[
            bool | None,
            Field(
                description="Keep only enabled (True) or only disabled (False) accounts. "
                "Omit for both."
            ),
        ] = None,
        max_results: Annotated[
            int, Field(description="Max users to return (default 50, hard cap 200).")
        ] = _DEFAULT_MAX_RESULTS,
    ) -> str:
        """List or search Entra ID users by display name.

        Use to browse the directory or resolve a partial name to a user's
        id/UPN for the other user tools — graph_check_user_exists needs an
        exact UPN or mail, so it can't answer "who is called Alice?".
        Returns id, displayName, userPrincipalName, mail, accountEnabled,
        jobTitle and department for each match.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        max_results = min(max_results, _HARD_CAP_MAX_RESULTS)
        params: dict = {
            "$select": _USER_LIST_SELECT,
            "$top": str(min(max_results, _GRAPH_TOP_MAX)),
        }
        clauses: list[str] = []
        if display_name:
            quoted = odata_quote(display_name)
            if exact:
                clauses.append(f"displayName eq '{quoted}'")
            else:
                clauses.append(f"startswith(displayName,'{quoted}')")
        if account_enabled is not None:
            clauses.append(f"accountEnabled eq {str(account_enabled).lower()}")
        if clauses:
            params["$filter"] = " and ".join(clauses)

        try:
            users: list = []
            result = await client.get("/users", params=params)
            has_more = False
            while result:
                users.extend(result.get("value", []))
                if len(users) >= max_results:
                    users = users[:max_results]
                    has_more = True
                    break
                next_link = result.get("@odata.nextLink")
                if not next_link:
                    break
                result = await client.get(next_link)
            return dump_json_capped({"count": len(users), "users": users, "has_more": has_more})
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
    async def graph_create_user(
        display_name: Annotated[str, Field(description='Full display name, e.g. "Alice Smith".')],
        user_principal_name: Annotated[
            str, Field(description='Login UPN, e.g. "alice@contoso.com".')
        ],
        mail_nickname: Annotated[
            str, Field(description='Mail alias without domain, e.g. "alice".')
        ],
        password: Annotated[
            str,
            Field(
                description=(
                    "Initial password; must meet tenant complexity policy. "
                    "Generate a random strong value — never reuse a common/"
                    "predictable password — since force_change_password is "
                    "always set, this is a throwaway credential the user "
                    "changes at first sign-in."
                )
            ),
        ],
        usage_location: Annotated[
            str,
            Field(description='Two-letter ISO 3166-1 alpha-2 country code, e.g. "US", "AU", "CN".'),
        ],
        account_enabled: Annotated[
            bool, Field(description="Whether the account is active immediately.")
        ] = True,
        given_name: Annotated[str | None, Field(description="First name.")] = None,
        surname: Annotated[str | None, Field(description="Last name.")] = None,
        job_title: Annotated[str | None, Field(description="Job title.")] = None,
        department: Annotated[str | None, Field(description="Department name.")] = None,
        office_location: Annotated[str | None, Field(description="Office location.")] = None,
        business_phones: Annotated[
            list[str] | None, Field(description="Business phone numbers (Graph keeps one).")
        ] = None,
        mobile_phone: Annotated[str | None, Field(description="Mobile/cellphone number.")] = None,
        street_address: Annotated[str | None, Field(description="Street address.")] = None,
        city: Annotated[str | None, Field(description="City.")] = None,
        state: Annotated[str | None, Field(description="State or province.")] = None,
        postal_code: Annotated[str | None, Field(description="Postal / ZIP code.")] = None,
        company_name: Annotated[str | None, Field(description="Company name.")] = None,
        employee_hire_date: Annotated[str | None, Field(description=_HIRE_DATE_DESC)] = None,
    ) -> str:
        """Create a new Entra ID user account.

        usage_location is required because it must be set before any license
        can be assigned to the user. Address, phone, company and hire date
        can be set in the same call. Every created account is configured to
        require a password change at the user's next sign-in; this is not
        configurable per call (PRD-20412).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        body: dict = {
            "displayName": display_name,
            "userPrincipalName": user_principal_name,
            "mailNickname": mail_nickname,
            "accountEnabled": account_enabled,
            "usageLocation": usage_location,
            "passwordProfile": {
                "password": password,
                "forceChangePasswordNextSignIn": True,
            },
        }
        if given_name is not None:
            body["givenName"] = given_name
        if surname is not None:
            body["surname"] = surname
        if job_title is not None:
            body["jobTitle"] = job_title
        if department is not None:
            body["department"] = department
        if office_location is not None:
            body["officeLocation"] = office_location
        if business_phones is not None:
            body["businessPhones"] = business_phones
        if mobile_phone is not None:
            body["mobilePhone"] = mobile_phone
        bad = _add_profile_fields(
            body,
            street_address=street_address,
            city=city,
            state=state,
            postal_code=postal_code,
            company_name=company_name,
            employee_hire_date=employee_hire_date,
        )
        if bad:
            return bad

        try:
            result = await client.post("/users", body)
            # Graph's own response never includes passwordProfile (its
            # documented default property set omits it, and a follow-up
            # $select read-back is reported unreliable), so a straight
            # passthrough would give the caller zero evidence that the
            # forced-password-change setting above was requested. A 201
            # means Graph accepted the request body as-is, so echo our own
            # intent back deterministically instead — same pattern
            # graph_reset_password below already uses for its response
            # (PRD-20412).
            merged = dict(result) if isinstance(result, dict) else {"result": result}
            merged["passwordChangeRequiredAtNextSignIn"] = True
            return dump_json_capped(merged)
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)
    )
    async def graph_reset_password(
        user_id: Annotated[
            str,
            Field(
                description=(
                    "Target user's exact id (GUID) or full userPrincipalName. "
                    "Never guess this from a first name or partial username — "
                    "resolve it via graph_check_user_exists/graph_get_user "
                    "first, and if more than one user could match, ask the "
                    "user to disambiguate before resetting anyone's password."
                )
            ),
        ],
        new_password: Annotated[
            str, Field(description="Temporary password; must meet tenant complexity policy.")
        ],
        force_change: Annotated[
            bool, Field(description="Force a password change at next sign-in.")
        ] = True,
        force_change_with_mfa: Annotated[
            bool, Field(description="Also require MFA at that forced next sign-in.")
        ] = False,
    ) -> str:
        """Admin-reset an existing Entra ID user's password.

        The calling identity must additionally hold a Password Administrator
        or User Administrator directory role (higher-privilege targets may
        need Privileged Authentication Administrator).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        password_profile: dict = {
            "password": new_password,
            "forceChangePasswordNextSignIn": force_change,
        }
        if force_change_with_mfa:
            password_profile["forceChangePasswordNextSignInWithMfa"] = True

        body = {"passwordProfile": password_profile}

        try:
            await client.patch(f"/users/{user_id}", body)
            return dump_json_capped(
                {
                    "status": "success",
                    "user_id": user_id,
                    "message": "Password reset; user must change it at next sign-in.",
                }
            )
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_get_user(
        user_id: Annotated[
            str, Field(description="Target user's id (GUID) or userPrincipalName.")
        ],
    ) -> str:
        """Read an Entra ID user's full profile, including manager and licenses.

        Use to inspect an existing user's access profile (e.g. before
        mirroring it onto a new hire), or to verify a newly created user is
        fully provisioned. licenseAssignmentStates[].assignedByGroup: null
        means a license is assigned directly (needs manual removal);
        non-null means group-based (clears once removed from that group).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        try:
            result = await client.get(
                f"/users/{user_id}",
                params={
                    "$select": (
                        "id,displayName,givenName,surname,userPrincipalName,mail,"
                        "accountEnabled,usageLocation,jobTitle,department,mobilePhone,"
                        "businessPhones,officeLocation,streetAddress,city,state,postalCode,"
                        "companyName,employeeHireDate,employeeId,"
                        "provisionedPlans,assignedLicenses,"
                        "licenseAssignmentStates"
                    ),
                    "$expand": "manager($select=id,displayName,userPrincipalName)",
                },
            )
            return dump_json_capped(result)
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
    )
    async def graph_update_user(
        user_id: Annotated[
            str, Field(description="Target user's id (GUID) or userPrincipalName.")
        ],
        account_enabled: Annotated[
            bool | None,
            Field(
                description=(
                    "Set False to disable the account, True to re-enable. "
                    "This is the primary access-cutoff mechanism for "
                    "offboarding — confirm the exact user_id before setting "
                    "False; never guess it from a first name."
                )
            ),
        ] = None,
        usage_location: Annotated[
            str | None, Field(description='Two-letter ISO 3166-1 alpha-2 country code, e.g. "US".')
        ] = None,
        mobile_phone: Annotated[str | None, Field(description="Mobile/cellphone number.")] = None,
        business_phones: Annotated[
            list[str] | None, Field(description="List of business phone numbers.")
        ] = None,
        job_title: Annotated[str | None, Field(description="Job title.")] = None,
        department: Annotated[str | None, Field(description="Department name.")] = None,
        office_location: Annotated[str | None, Field(description="Office location.")] = None,
        street_address: Annotated[str | None, Field(description="Street address.")] = None,
        city: Annotated[str | None, Field(description="City.")] = None,
        state: Annotated[str | None, Field(description="State or province.")] = None,
        postal_code: Annotated[str | None, Field(description="Postal / ZIP code.")] = None,
        company_name: Annotated[str | None, Field(description="Company name.")] = None,
        employee_hire_date: Annotated[str | None, Field(description=_HIRE_DATE_DESC)] = None,
        employee_id: Annotated[
            str | None,
            Field(
                description=(
                    "Employee ID to set. Mutually exclusive with "
                    "clear_employee_id (PRD-20321)."
                )
            ),
        ] = None,
        clear_employee_id: Annotated[
            bool,
            Field(
                description=(
                    "Set True to clear employeeId to null. Mutually "
                    "exclusive with employee_id (PRD-20321)."
                )
            ),
        ] = False,
    ) -> str:
        """Update attributes on an existing Entra ID user.

        Only the fields you provide are changed. Setting account_enabled=False
        blocks new sign-ins immediately, but sessions issued before the change
        stay valid until they expire or are revoked with graph_revoke_sessions.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        if employee_id is not None and clear_employee_id:
            return error_envelope(
                "invalid_argument",
                "Provide at most one of employee_id or clear_employee_id.",
                False,
            )

        body: dict = {}
        if employee_id is not None:
            body["employeeId"] = employee_id
        elif clear_employee_id:
            body["employeeId"] = None
        if account_enabled is not None:
            body["accountEnabled"] = account_enabled
        if usage_location is not None:
            body["usageLocation"] = usage_location
        if mobile_phone is not None:
            body["mobilePhone"] = mobile_phone
        if business_phones is not None:
            body["businessPhones"] = business_phones
        if job_title is not None:
            body["jobTitle"] = job_title
        if department is not None:
            body["department"] = department
        if office_location is not None:
            body["officeLocation"] = office_location
        bad = _add_profile_fields(
            body,
            street_address=street_address,
            city=city,
            state=state,
            postal_code=postal_code,
            company_name=company_name,
            employee_hire_date=employee_hire_date,
        )
        if bad:
            return bad

        if not body:
            return error_envelope("invalid_argument", "Provide at least one attribute to update.", False)

        try:
            await client.patch(f"/users/{user_id}", body)
            return dump_json_capped(
                {"status": "success", "user_id": user_id, "updated": list(body.keys())}
            )
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
    )
    async def graph_assign_manager(
        user_id: Annotated[str, Field(description="Target user's id (GUID) or userPrincipalName.")],
        manager_id: Annotated[
            str, Field(description="Manager's user id (GUID) or userPrincipalName.")
        ],
    ) -> str:
        """Set an Entra ID user's manager.

        Sets/replaces the manager relationship only — there is no option
        here to clear a user's manager entirely (not exposed by this tool).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        body = {"@odata.id": f"{DEFAULT_BASE_URL}/users/{manager_id}"}

        try:
            await client.put(f"/users/{user_id}/manager/$ref", body)
            return dump_json_capped(
                {"status": "success", "user_id": user_id, "manager_id": manager_id}
            )
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_list_auth_methods(
        user_id: Annotated[str, Field(description="Target user's id (GUID) or userPrincipalName.")],
    ) -> str:
        """List a user's registered Entra ID MFA authentication methods.

        Third-party MFA (e.g. Duo) is a separate system and is not reflected
        here.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        try:
            result = await client.get(f"/users/{user_id}/authentication/methods")
            methods = result.get("value", []) if isinstance(result, dict) else []
            return dump_json_capped({"user_id": user_id, "count": len(methods), "methods": methods})
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)
    )
    async def graph_revoke_sessions(
        user_id: Annotated[
            str, Field(description="Object ID or UPN of the user whose sessions to revoke.")
        ],
    ) -> str:
        """Revoke all of a user's sign-in sessions, forcing re-authentication everywhere.

        Not instantaneous: access tokens already issued remain valid until
        they expire (typically ~1 hour). For offboarding, pair with
        graph_update_user(account_enabled=False) so no new sign-in is
        possible while old tokens age out.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        try:
            await client.post(f"/users/{user_id}/revokeSignInSessions")
            return dump_json_capped({"user_id": user_id, "status": "sessions_revoked"})
        except GraphError as e:
            return e.to_envelope()

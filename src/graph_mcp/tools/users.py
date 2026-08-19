from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import DEFAULT_BASE_URL, GraphClient, GraphError
from ._common import NO_TOKEN


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_check_user_exists(
        user_principal_name: Annotated[
            str | None, Field(description="UPN to search for, e.g. alice@contoso.com.")
        ] = None,
        mail: Annotated[
            str | None, Field(description="Primary SMTP address to search for.")
        ] = None,
    ) -> str:
        """Check whether an Entra ID user exists by UPN or mail address.

        Exactly one of user_principal_name or mail must be provided. Returns
        {"exists": true/false, "user": object | null}.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if not user_principal_name and not mail:
            return error_envelope(
                "invalid_argument", "Provide either user_principal_name or mail.", False
            )

        if user_principal_name:
            filter_expr = f"userPrincipalName eq '{user_principal_name}'"
        else:
            filter_expr = f"mail eq '{mail}'"

        try:
            result = await client.get(
                "/users",
                params={
                    "$filter": filter_expr,
                    "$select": "id,displayName,userPrincipalName,mail,accountEnabled",
                    "$top": "1",
                },
            )
            users = result.get("value", [])
            if users:
                return dump_json_capped({"exists": True, "user": users[0]})
            return dump_json_capped({"exists": False, "user": None})
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
            str, Field(description="Initial password; must meet tenant complexity policy.")
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
    ) -> str:
        """Create a new Entra ID user account.

        usage_location is required because it must be set before any license
        can be assigned to the user.
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

        try:
            result = await client.post("/users", body)
            return dump_json_capped(result)
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)
    )
    async def graph_reset_password(
        user_id: Annotated[
            str, Field(description="Target user's id (GUID) or userPrincipalName.")
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
        fully provisioned.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        try:
            result = await client.get(
                f"/users/{user_id}",
                params={
                    "$select": (
                        "id,displayName,userPrincipalName,mail,accountEnabled,"
                        "usageLocation,jobTitle,department,mobilePhone,businessPhones,"
                        "officeLocation,provisionedPlans,assignedLicenses"
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
            Field(description="Set False to disable the account, True to re-enable."),
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
    ) -> str:
        """Update attributes on an existing Entra ID user.

        Only the fields you provide are changed. Setting account_enabled=False
        blocks new sign-ins immediately, but sessions issued before the change
        stay valid until they expire or are revoked with graph_revoke_sessions.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        body: dict = {}
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
        """Set an Entra ID user's manager."""
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

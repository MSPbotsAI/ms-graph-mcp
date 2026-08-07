import json
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from ..api_client import DEFAULT_BASE_URL, GraphClient, GraphError

_NO_TOKEN = "Error: No Graph access token. Send the X-Ms-Graph-Token header."


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool()
    async def graph_check_user_exists(
        user_principal_name: str | None = None,
        mail: str | None = None,
    ) -> str:
        """Check whether an Entra ID user exists by UPN or mail address.

        Exactly one of user_principal_name or mail must be provided.
        Required Graph scope: User.Read.All or Directory.Read.All.

        Returns {"exists": true/false, "user": object | null}.

        Args:
            user_principal_name: The UPN to search for (e.g. alice@contoso.com).
            mail: The primary SMTP address to search for.
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN
        if not user_principal_name and not mail:
            return "Error: Provide either user_principal_name or mail."

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
                return json.dumps({"exists": True, "user": users[0]}, indent=2)
            return json.dumps({"exists": False, "user": None}, indent=2)
        except GraphError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def graph_create_user(
        display_name: str,
        user_principal_name: str,
        mail_nickname: str,
        password: str,
        usage_location: str,
        account_enabled: bool = True,
        given_name: str | None = None,
        surname: str | None = None,
        job_title: str | None = None,
        department: str | None = None,
    ) -> str:
        """Create a new Entra ID user.

        Required Graph scope: User.ReadWrite.All.
        usage_location is required here because it must be set before assigning licenses.

        Args:
            display_name: Full display name (e.g. "Alice Smith").
            user_principal_name: Login UPN (e.g. "alice@contoso.com").
            mail_nickname: Mail alias without domain (e.g. "alice").
            password: Initial password — must meet tenant complexity policy.
            usage_location: Two-letter ISO 3166-1 alpha-2 country code (e.g. "US", "AU", "CN").
            account_enabled: Whether the account is active immediately (default True).
            given_name: First name.
            surname: Last name.
            job_title: Job title.
            department: Department name.
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

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
            return json.dumps(result, indent=2)
        except GraphError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def graph_reset_password(
        user_id: str,
        new_password: str,
        force_change: bool = True,
        force_change_with_mfa: bool = False,
    ) -> str:
        """Admin-reset an existing Entra ID user's password.

        Sets a temporary password via PATCH /users/{id}; the user is forced to
        change it at next sign-in (the "walk the user through a password change"
        flow). Graph returns 204 No Content on success.

        Required Graph scope: User.ReadWrite.All. The calling identity must also
        hold a Password Administrator or User Administrator directory role;
        admin/privileged targets may require a higher role such as Privileged
        Authentication Administrator.

        Args:
            user_id: The target user's id (GUID) or userPrincipalName (e.g. alice@contoso.com).
            new_password: Temporary password — must meet the tenant complexity policy.
            force_change: Force a password change at next sign-in (default True).
            force_change_with_mfa: Force change at next sign-in and require MFA (default False).
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

        password_profile: dict = {
            "password": new_password,
            "forceChangePasswordNextSignIn": force_change,
        }
        if force_change_with_mfa:
            password_profile["forceChangePasswordNextSignInWithMfa"] = True

        body = {"passwordProfile": password_profile}

        try:
            await client.patch(f"/users/{user_id}", body)
            return json.dumps(
                {
                    "status": "success",
                    "user_id": user_id,
                    "message": "Password reset; user must change it at next sign-in.",
                },
                indent=2,
            )
        except GraphError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def graph_get_user(user_id: str) -> str:
        """Read an Entra ID user's full profile, including manager and licenses.

        Use this both to inspect an existing "model" employee whose access profile
        will be mirrored, and to verify a newly created user is fully provisioned
        (account enabled, licenses assigned, mailbox/Exchange plan provisioned).

        Required Graph scope: User.Read.All or Directory.Read.All.

        Args:
            user_id: The target user's id (GUID) or userPrincipalName (e.g. alice@contoso.com).
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

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
            return json.dumps(result, indent=2)
        except GraphError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def graph_update_user(
        user_id: str,
        account_enabled: bool | None = None,
        usage_location: str | None = None,
        mobile_phone: str | None = None,
        business_phones: list[str] | None = None,
        job_title: str | None = None,
        department: str | None = None,
        office_location: str | None = None,
    ) -> str:
        """Update attributes on an existing Entra ID user.

        Only the fields you provide are patched; omitted fields are left unchanged.
        Useful for filling in post-creation details such as the company cellphone
        number (mobile_phone) or a usage_location that was not set at creation.

        Set account_enabled=False to disable a user's account (e.g. as part of
        offboarding); this blocks new sign-ins immediately, but any sessions
        issued before the change stay valid until they expire or are separately
        revoked with graph_revoke_sessions.

        Required Graph scope: User.ReadWrite.All.

        Args:
            user_id: The target user's id (GUID) or userPrincipalName.
            account_enabled: Set False to disable the account, True to re-enable.
            usage_location: Two-letter ISO 3166-1 alpha-2 country code (e.g. "US").
            mobile_phone: Mobile / cellphone number.
            business_phones: List of business phone numbers.
            job_title: Job title.
            department: Department name.
            office_location: Office location.
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

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
            return "Error: Provide at least one attribute to update."

        try:
            await client.patch(f"/users/{user_id}", body)
            return json.dumps(
                {"status": "success", "user_id": user_id, "updated": list(body.keys())},
                indent=2,
            )
        except GraphError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def graph_assign_manager(user_id: str, manager_id: str) -> str:
        """Set an Entra ID user's manager.

        Required Graph scope: User.ReadWrite.All (and User.Read.All to resolve the
        manager). Graph returns 204 No Content on success.

        Args:
            user_id: The target user's id (GUID) or userPrincipalName.
            manager_id: The manager's user id (GUID) or userPrincipalName.
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

        body = {"@odata.id": f"{DEFAULT_BASE_URL}/users/{manager_id}"}

        try:
            await client.put(f"/users/{user_id}/manager/$ref", body)
            return json.dumps(
                {"status": "success", "user_id": user_id, "manager_id": manager_id},
                indent=2,
            )
        except GraphError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def graph_list_auth_methods(user_id: str) -> str:
        """List a user's registered Entra ID authentication (MFA) methods.

        Use this to verify a user has completed MFA registration. Note that
        third-party MFA such as Duo is a separate system and is not reflected here.

        Required Graph scope: UserAuthenticationMethod.Read.All.

        Args:
            user_id: The target user's id (GUID) or userPrincipalName.
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

        try:
            result = await client.get(f"/users/{user_id}/authentication/methods")
            methods = result.get("value", []) if isinstance(result, dict) else []
            return json.dumps(
                {"user_id": user_id, "count": len(methods), "methods": methods},
                indent=2,
            )
        except GraphError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def graph_revoke_sessions(user_id: str) -> str:
        """Revoke all of a user's refresh tokens and session cookies,
        forcing re-authentication on every device and app.

        This invalidates sessions issued before the call; it is not
        instantaneous for every client (access tokens already issued stay
        valid until they expire, typically within ~1 hour) — pair with
        graph_update_user(account_enabled=False) for immediate offboarding
        so no new sign-in is possible while old tokens age out.

        Required Graph scope: User.ReadWrite.All.

        Args:
            user_id: Object ID or UPN of the user whose sessions to revoke.
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

        try:
            await client.post(f"/users/{user_id}/revokeSignInSessions")
            return json.dumps({"user_id": user_id, "status": "sessions_revoked"}, indent=2)
        except GraphError as e:
            return f"Error: {e}"

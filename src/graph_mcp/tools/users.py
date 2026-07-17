import json
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from ..api_client import GraphClient, GraphError

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

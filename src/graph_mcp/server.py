import contextvars
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .api_client import GraphClient
from .config import Settings

# Per-request token isolation via contextvars.
# GatewayTokenMiddleware sets this before the MCP handler runs.
# Python asyncio copies context per task, so concurrent SSE connections are isolated.
_gateway_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "graph_gateway_token", default=None
)


def get_client_from_context(settings: Settings) -> GraphClient | None:
    """Resolve the active GraphClient for the current request context."""
    token = _gateway_token_var.get()
    if not token:
        return None
    return GraphClient(token, settings.graph_base_url)


class GatewayTokenMiddleware:
    """ASGI middleware.

    Reads X-Ms-Graph-Token from request headers and stores it in the contextvar.
    Returns 401 if the header is missing on /mcp requests.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = request.headers.get("x-ms-graph-token")
        if not token:
            response = JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": "This server requires the X-Ms-Graph-Token header containing a valid Azure access token",
                    "required_headers": ["X-Ms-Graph-Token"],
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        ctx_token = _gateway_token_var.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _gateway_token_var.reset(ctx_token)


def create_mcp_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server instance and register all Graph tools."""
    # DNS-rebinding protection is a browser-oriented safeguard that rejects
    # non-localhost Host headers with 421. Disable it so the server works
    # correctly behind a reverse proxy or docker network.
    mcp = FastMCP(
        name="graph-mcp",
        instructions=(
            "Microsoft Graph is Microsoft's unified API for Entra ID (Azure AD) and "
            "Microsoft 365 tenant data. This server exposes 4 tool domains modeling "
            "the identity lifecycle in a tenant: users (create/read/update accounts, "
            "reset passwords, list MFA methods, assign managers, revoke sessions), "
            "groups (search/list groups and manage a user's memberships, which drive "
            "access to Microsoft 365 resources), licenses (check tenant SKU stock and "
            "assign/remove SKUs on a user — usage_location must be set on the user "
            "first), and mail (send email as the signed-in user via delegated "
            "Mail.Send). Typical onboarding: graph_check_user_exists -> "
            "graph_create_user -> graph_assign_groups -> graph_check_license_stock -> "
            "graph_assign_license -> graph_send_mail. Typical offboarding: "
            "graph_update_user(account_enabled=false) -> graph_revoke_sessions -> "
            "graph_assign_license(remove_sku_ids=...) -> graph_remove_group_member. "
            "All tools act on the caller's own tenant via a bearer access token "
            "supplied per-request; there is no cross-tenant access."
        ),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    client_factory: Callable[[], GraphClient | None] = lambda: get_client_from_context(settings)

    from .tools import groups, licenses, mail, users

    users.register(mcp, client_factory)
    groups.register(mcp, client_factory)
    licenses.register(mcp, client_factory)
    mail.register(mcp, client_factory)

    return mcp

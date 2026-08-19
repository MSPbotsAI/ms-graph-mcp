from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import GraphClient, GraphError
from ._common import NO_TOKEN


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
    )
    async def graph_send_mail(
        to_recipients: Annotated[
            list[str],
            Field(description='List of To addresses, e.g. ["bob@contoso.com", "carol@contoso.com"].'),
        ],
        subject: Annotated[str, Field(description="Email subject line.")],
        body: Annotated[str, Field(description="Email body content.")],
        sender_id: Annotated[
            str | None,
            Field(
                description="Mailbox to send from (object ID or UPN). Omit to send as the token's own signed-in user — the normal case, since delegated Mail.Send only authorizes sending as that user. A different sender additionally needs Mail.Send.Shared and send-as rights."
            ),
        ] = None,
        body_content_type: Annotated[str, Field(description='"Text" or "HTML".')] = "Text",
        cc_recipients: Annotated[
            list[str] | None, Field(description="List of CC addresses.")
        ] = None,
        bcc_recipients: Annotated[
            list[str] | None, Field(description="List of BCC addresses.")
        ] = None,
        save_to_sent_items: Annotated[
            bool, Field(description="Whether to save a copy in Sent Items.")
        ] = True,
    ) -> str:
        """Send an email as an Entra ID user via Microsoft Graph."""
        client = client_factory()
        if client is None:
            return NO_TOKEN

        def _addr_list(addresses: list[str]) -> list[dict]:
            return [{"emailAddress": {"address": addr}} for addr in addresses]

        message: dict = {
            "subject": subject,
            "body": {"contentType": body_content_type, "content": body},
            "toRecipients": _addr_list(to_recipients),
        }
        if cc_recipients:
            message["ccRecipients"] = _addr_list(cc_recipients)
        if bcc_recipients:
            message["bccRecipients"] = _addr_list(bcc_recipients)

        payload = {
            "message": message,
            "saveToSentItems": save_to_sent_items,
        }

        # No sender_id -> /me — the only sender authorized under a delegated token.
        path = f"/users/{sender_id}/sendMail" if sender_id else "/me/sendMail"

        try:
            await client.post(path, payload)
            return dump_json_capped({"status": "sent"})
        except GraphError as e:
            return e.to_envelope()

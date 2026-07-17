from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from ..api_client import GraphClient, GraphError

_NO_TOKEN = "Error: No Graph access token. Send the X-Ms-Graph-Token header."


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool()
    async def graph_send_mail(
        sender_id: str,
        to_recipients: list[str],
        subject: str,
        body: str,
        body_content_type: str = "Text",
        cc_recipients: list[str] | None = None,
        bcc_recipients: list[str] | None = None,
        save_to_sent_items: bool = True,
    ) -> str:
        """Send an email on behalf of an Entra ID user via Microsoft Graph.

        Required Graph scope: Mail.Send.

        Args:
            sender_id: Object ID or UPN of the mailbox to send from (e.g. "alice@contoso.com").
            to_recipients: List of To addresses (e.g. ["bob@contoso.com", "carol@contoso.com"]).
            subject: Email subject line.
            body: Email body content.
            body_content_type: "Text" (default) or "HTML".
            cc_recipients: Optional list of CC addresses.
            bcc_recipients: Optional list of BCC addresses.
            save_to_sent_items: Whether to save a copy in Sent Items (default True).
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

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

        try:
            await client.post(f"/users/{sender_id}/sendMail", payload)
            return "Mail sent successfully."
        except GraphError as e:
            return f"Error: {e}"

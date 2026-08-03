from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from ..api_client import GraphClient, GraphError

_NO_TOKEN = "Error: No Graph access token. Send the X-Ms-Graph-Token header."


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool()
    async def graph_send_mail(
        to_recipients: list[str],
        subject: str,
        body: str,
        sender_id: str | None = None,
        body_content_type: str = "Text",
        cc_recipients: list[str] | None = None,
        bcc_recipients: list[str] | None = None,
        save_to_sent_items: bool = True,
    ) -> str:
        """Send an email on behalf of an Entra ID user via Microsoft Graph.

        Required Graph scope: Mail.Send.

        Args:
            to_recipients: List of To addresses (e.g. ["bob@contoso.com", "carol@contoso.com"]).
            subject: Email subject line.
            body: Email body content.
            sender_id: Optional mailbox to send from (object ID or UPN, e.g.
                "alice@contoso.com"). Omit it to send as the signed-in user the
                current token belongs to — that is the normal case, because the
                delegated Mail.Send scope only authorizes sending as that user.
                Sending as a different mailbox also needs Mail.Send.Shared plus
                send-as rights on it.
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

        # 没给 sender_id 就走 /me——委派令牌下这是唯一被授权的发件人
        path = f"/users/{sender_id}/sendMail" if sender_id else "/me/sendMail"

        try:
            await client.post(path, payload)
            return "Mail sent successfully."
        except GraphError as e:
            return f"Error: {e}"

"""Mail tools (Microsoft Graph message API).

Scope note: three tools cover one business flow — "what landed in this
mailbox, what is it about, and does it need a reply": list/search messages,
open one in full, send one. Moving, flagging, deleting, replying in place,
folder management and attachment download are deliberately not exposed —
the read side exists to let an agent triage a mailbox and draft follow-ups,
not to act as a mail client.

Delegated vs app-only, and why it matters more here than anywhere else in
this server: delegated Mail.Read authorizes the SIGNED-IN user's own mailbox
and nothing else. Reading somebody else's mailbox under a delegated token
needs Mail.Read.Shared AND that mailbox actually delegated/shared to the
signed-in user in Exchange — the scope alone is not enough. Tenant-wide
access to every mailbox is not reachable delegated at all; that is what
ms-graph-app's application Mail.Read is for, and it should normally be
narrowed to specific mailboxes with an Exchange application access policy.
So under the delegated grant, omit user_id and you read your own mail; pass
someone else's and expect `unauthorized` unless the sharing is in place.
"""

import re
from collections.abc import Callable
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import GraphClient, GraphError
from ._common import NO_TOKEN, odata_quote

# Messages carry a bodyPreview each, so a page costs far more characters than
# a directory listing; 100 is the ceiling and the 20,000-char cap in _json.py
# still trims below it on chatty mailboxes.
_MAX_MESSAGES = 100
# A single message body can be hundreds of KB of quoted HTML thread history.
# dump_json_capped cannot rescue that on its own — a one-message dict has no
# list field to trim, so it would fall back to discarding the whole result and
# returning a "too large" notice. Truncating the body here keeps the useful
# head of the message instead.
_MAX_BODY_CHARS = 15_000

# Metadata only. Never bodyPreview's big sibling `body` — that is what
# graph_get_message is for.
_MESSAGE_FIELDS = (
    "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,"
    "isRead,isDraft,hasAttachments,importance,flag,webLink,bodyPreview"
)

_USER_ID_DESC = (
    "Mailbox to read (id (GUID) or userPrincipalName). Omit to use the token's own "
    "signed-in user — the normal case under the delegated grant, and the only mailbox "
    "it can read without extra sharing. An app-only token has no signed-in user, so it "
    "must always pass this."
)

_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


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
        body_content_type: Annotated[
            Literal["Text", "HTML"], Field(description='"Text" or "HTML".')
        ] = "Text",
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
        """Send an email as an Entra ID user via Microsoft Graph.

        Irreversible once sent — no unsend. Confirm the exact recipients,
        subject, and body with the user before calling, especially when
        sender_id sends as someone other than the caller.
        """
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

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_list_messages(
        user_id: Annotated[str | None, Field(description=_USER_ID_DESC)] = None,
        folder: Annotated[
            str | None,
            Field(
                description='Mail folder to read: a well-known name ("inbox", "sentitems", '
                '"drafts", "archive", "junkemail", "deleteditems") or a folder id. Pass null to '
                "search every folder, which is what following a whole thread needs."
            ),
        ] = "inbox",
        search: Annotated[
            str | None,
            Field(
                description='Full-text search over the mailbox, e.g. "renewal quote" or '
                '"from:alice@contoso.com subject:invoice". Cannot be combined with the '
                "from_address / conversation_id / received_* / unread_only / has_attachments "
                "filters, and results come back by relevance rather than newest-first."
            ),
        ] = None,
        from_address: Annotated[
            str | None, Field(description="Keep only messages from this exact sender address.")
        ] = None,
        conversation_id: Annotated[
            str | None,
            Field(
                description="Keep only messages in one thread. Take it from a message's "
                "conversationId and pair it with folder=null to see the whole exchange, "
                "including replies that were sent rather than received."
            ),
        ] = None,
        received_after: Annotated[
            str | None,
            Field(description="Only messages received at or after this UTC time, e.g. 2026-09-01T00:00:00Z."),
        ] = None,
        received_before: Annotated[
            str | None,
            Field(description="Only messages received at or before this UTC time, e.g. 2026-09-08T00:00:00Z."),
        ] = None,
        unread_only: Annotated[
            bool, Field(description="Keep only unread messages.")
        ] = False,
        has_attachments: Annotated[
            bool | None, Field(description="Keep only messages with (True) or without (False) attachments.")
        ] = None,
        limit: Annotated[
            int,
            Field(description=f"Max messages to return (1-{_MAX_MESSAGES}).", ge=1, le=_MAX_MESSAGES),
        ] = 25,
    ) -> str:
        """List or search a mailbox, newest first, without message bodies.

        The triage entry point: returns sender, subject, received time,
        read state and a short bodyPreview per message, plus the id and
        conversationId needed to open one with graph_get_message or to pull
        the rest of its thread. One page only — narrow the filters rather
        than raising limit.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        filter_args = {
            "from_address": from_address,
            "conversation_id": conversation_id,
            "received_after": received_after,
            "received_before": received_before,
            "unread_only": unread_only or None,
            "has_attachments": has_attachments,
        }
        if search:
            conflicting = sorted(k for k, v in filter_args.items() if v is not None)
            if conflicting:
                return error_envelope(
                    "invalid_argument",
                    "Graph rejects $search combined with any other filter on messages. "
                    f"Drop {', '.join(conflicting)}, or fold the condition into the search "
                    'text itself (e.g. "from:alice@contoso.com renewal").',
                    False,
                )

        for name, value in (("received_after", received_after), ("received_before", received_before)):
            if value is not None and not _ISO_DATETIME.match(value):
                return error_envelope(
                    "invalid_argument",
                    f"{name} must be an ISO 8601 UTC datetime like 2026-09-01T00:00:00Z, got {value!r}.",
                    False,
                )

        base = f"/users/{user_id}" if user_id else "/me"
        path = f"{base}/mailFolders/{folder}/messages" if folder else f"{base}/messages"
        params: dict = {"$select": _MESSAGE_FIELDS, "$top": max(1, min(limit, _MAX_MESSAGES))}

        if search:
            # Graph wants the search term wrapped in double quotes inside the
            # query string; an embedded quote would close it early. $orderby is
            # not accepted alongside $search — results come back by relevance.
            params["$search"] = '"{}"'.format(search.replace('"', " ").strip())
        else:
            clauses: list[str] = []
            if from_address:
                clauses.append(f"from/emailAddress/address eq '{odata_quote(from_address)}'")
            if conversation_id:
                clauses.append(f"conversationId eq '{odata_quote(conversation_id)}'")
            if received_after:
                clauses.append(f"receivedDateTime ge {received_after}")
            if received_before:
                clauses.append(f"receivedDateTime le {received_before}")
            if unread_only:
                clauses.append("isRead eq false")
            if has_attachments is not None:
                clauses.append(f"hasAttachments eq {str(has_attachments).lower()}")
            if clauses:
                params["$filter"] = " and ".join(clauses)
            params["$orderby"] = "receivedDateTime desc"

        try:
            result = await client.get(path, params=params)
        except GraphError as e:
            return e.to_envelope()

        messages = result.get("value", []) if isinstance(result, dict) else []
        has_more = bool(isinstance(result, dict) and result.get("@odata.nextLink"))
        return dump_json_capped(
            {
                "mailbox": user_id or "me",
                "folder": folder or "all",
                "count": len(messages),
                "has_more": has_more,
                "messages": messages,
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_get_message(
        message_id: Annotated[
            str,
            Field(
                description="Message id from graph_list_messages — never guess it, list first."
            ),
        ],
        user_id: Annotated[str | None, Field(description=_USER_ID_DESC)] = None,
        body_format: Annotated[
            Literal["text", "html"],
            Field(
                description='"text" asks Exchange to flatten the body to plain text, which is '
                "several times cheaper to read and is almost always what you want; "
                '"html" keeps the original markup.'
            ),
        ] = "text",
        include_attachments: Annotated[
            bool,
            Field(
                description="Also list attachment names, types and sizes (one extra call). "
                "File contents are never returned."
            ),
        ] = False,
    ) -> str:
        """Read one message in full, including its body.

        Use after graph_list_messages has narrowed things down — a long
        thread's body is truncated here, and pulling bodies one at a time is
        what keeps a mailbox readable. The reply chain is usually quoted
        inside the body; sibling messages come from listing the
        conversationId instead.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        base = f"/users/{user_id}" if user_id else "/me"
        try:
            result = await client.get(
                f"{base}/messages/{message_id}",
                params={"$select": f"{_MESSAGE_FIELDS},body,replyTo"},
                # Exchange converts the stored body for us; asking for text here
                # is what avoids paying for a wall of HTML we would only strip.
                extra_headers={"Prefer": f'outlook.body-content-type="{body_format}"'},
            )
        except GraphError as e:
            return e.to_envelope()

        message = result if isinstance(result, dict) else {}
        body = message.get("body") or {}
        content = body.get("content") or ""
        if len(content) > _MAX_BODY_CHARS:
            message["body"] = {
                "contentType": body.get("contentType"),
                "content": content[:_MAX_BODY_CHARS],
                "truncated": True,
                "original_length": len(content),
            }

        payload: dict = {"mailbox": user_id or "me", "message": message}

        if include_attachments:
            try:
                # $select matters: without it Graph inlines contentBytes and a
                # single PDF turns the result into megabytes of base64.
                attached = await client.get(
                    f"{base}/messages/{message_id}/attachments",
                    params={"$select": "id,name,contentType,size,isInline"},
                )
                payload["attachments"] = (
                    attached.get("value", []) if isinstance(attached, dict) else []
                )
            except GraphError as e:
                return e.to_envelope()

        return dump_json_capped(payload)

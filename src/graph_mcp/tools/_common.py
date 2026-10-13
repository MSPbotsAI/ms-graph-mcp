from urllib.parse import parse_qs, urlsplit

from .._json import error_envelope
from ..api_client import GraphClient

NO_TOKEN = error_envelope(
    "not_configured", "No Graph access token. Send the X-Ms-Graph-Token header.", False
)


def odata_quote(value: str) -> str:
    """Escape a value for use inside a single-quoted OData string literal.

    OData escapes a quote by doubling it, so an apostrophe in real data —
    o'brien@contoso.com, a group named "Bob's Team" — otherwise closes the literal
    early and Graph rejects the whole $filter with a 400. Callers still supply the
    surrounding quotes: f"mail eq '{odata_quote(mail)}'".
    """
    return value.replace("'", "''")


def skiptoken(next_link: str | None) -> str | None:
    """Pull $skiptoken out of an @odata.nextLink. Only the token goes back to
    the caller — never the full URL — so a page_token can't point the client
    (and its bearer token) at another host. Shared by groups.py and users.py
    (PRD-20393) since graph_list_user_groups/graph_list_groups/graph_list_users
    all now follow graph_list_group_members's single-page-per-call contract.
    """
    if not next_link:
        return None
    values = parse_qs(urlsplit(next_link).query).get("$skiptoken")
    return values[0] if values else None


async def verified_domains(client: GraphClient) -> set[str]:
    """Verified domain names for this tenant (PRD-20393), used to reject a
    user_principal_name change whose domain Graph would not accept for sign-in.

    Deliberately a separate minimal query (just id + isVerified) rather than
    reusing graph_list_domains itself: that tool returns the full domain
    object shape (isDefault/isAdminManaged/authenticationType) for a human to
    browse, and changing its return shape or default filtering to serve this
    helper would risk a regression on an already-covered, unrelated tool for
    no benefit to this check.
    """
    result = await client.get("/domains", params={"$select": "id,isVerified"})
    rows = result.get("value", []) if isinstance(result, dict) else []
    return {d["id"] for d in rows if d.get("isVerified") and d.get("id")}

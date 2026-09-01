from .._json import error_envelope

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

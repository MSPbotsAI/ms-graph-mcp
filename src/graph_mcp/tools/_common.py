from .._json import error_envelope

NO_TOKEN = error_envelope(
    "not_configured", "No Graph access token. Send the X-Ms-Graph-Token header.", False
)

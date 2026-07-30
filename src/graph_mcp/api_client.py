from typing import Any

import httpx

DEFAULT_BASE_URL = "https://graph.microsoft.com/v1.0"


class GraphError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Graph API error {status_code}: {message}")


class GraphClient:
    """Async httpx client wrapping the Microsoft Graph REST API."""

    def __init__(self, access_token: str, base_url: str = DEFAULT_BASE_URL):
        self._token = access_token
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _clean_params(self, params: dict | None) -> dict:
        if not params:
            return {}
        return {k: v for k, v in params.items() if v is not None}

    async def get(self, path: str, params: dict | None = None) -> Any:
        # An absolute URL (e.g. an @odata.nextLink page cursor) is used as-is;
        # a relative path is resolved against the base URL.
        url = path if path.startswith("http") else f"{self._base_url}{path}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers=self._headers(),
                params=self._clean_params(params),
            )
            self._raise_for_status(resp)
            return resp.json() if resp.status_code != 204 else None

    async def post(self, path: str, body: Any = None) -> Any:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}{path}",
                headers=self._headers(),
                json=body,
            )
            self._raise_for_status(resp)
            # POST /users → 201 with body; POST /groups/.../members/$ref → 204 no body
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()

    async def patch(self, path: str, body: Any = None) -> Any:
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{self._base_url}{path}",
                headers=self._headers(),
                json=body,
            )
            self._raise_for_status(resp)
            return resp.json() if resp.status_code != 204 else None

    async def put(self, path: str, body: Any = None) -> Any:
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{self._base_url}{path}",
                headers=self._headers(),
                json=body,
            )
            self._raise_for_status(resp)
            # PUT /users/{id}/manager/$ref → 204 no body
            return resp.json() if resp.status_code != 204 else None

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            try:
                detail = resp.json()
                # Graph error shape: {"error": {"code": "...", "message": "..."}}
                msg = detail.get("error", {}).get("message", str(detail))
            except Exception:
                msg = resp.text
            raise GraphError(resp.status_code, msg)

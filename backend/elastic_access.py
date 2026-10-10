"""Private native-Kibana sign-in handoff after gateway password validation.

This isolated service neither opens indices nor reads app databases. It exchanges
the existing PM login for a real scoped Kibana session, without exposing the
strong native password to the browser or a public credentials endpoint.
"""

import base64
import hmac
import os
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response


def create_app(
    username: str,
    native_password: str,
    gateway_password: str,
    kibana_url: str = "http://seller-persona-kibana:5601",
    transport=None,
) -> FastAPI:
    """Build a separate private service with injectable HTTP transport for checks."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health() -> dict:
        """Expose readiness without native credentials or personal data."""
        return {
            "status": "ok"
            if native_password and gateway_password
            else "not_configured",
            "service": "elastic-access-handoff",
            "version": "1.0.0",
        }

    @app.get("/login")
    async def login(
        request: Request, next: str = "/s/sellergenome/app/dashboards"
    ) -> Response:
        """Authenticate before making any native request and restrict redirect scope."""
        if not native_password or not gateway_password:
            raise HTTPException(503, "Elastic access is not configured.")
        try:
            scheme, encoded = request.headers.get("authorization", "").split(
                " ", 1
            )
            if scheme.lower() != "basic":
                raise ValueError("Invalid scheme")
            user, password = (
                base64.b64decode(encoded, validate=True).decode().split(":", 1)
            )
        except (ValueError, UnicodeError):
            raise HTTPException(
                401, "Use the configured PM gateway login."
            ) from None
        if not hmac.compare_digest(
            user.encode(), username.encode()
        ) or not hmac.compare_digest(
            password.encode(), gateway_password.encode()
        ):
            raise HTTPException(401, "Use the configured PM gateway login.")
        parsed = urlsplit(next)
        if (
            len(next) > 2000
            or parsed.scheme
            or parsed.netloc
            or not parsed.path.startswith("/s/sellergenome/")
            or any(char in next for char in ("\r", "\n", "\\"))
        ):
            raise HTTPException(422, "Select a SellerGenome Space path.")
        try:
            async with httpx.AsyncClient(
                base_url=kibana_url, transport=transport, timeout=30
            ) as client:
                upstream = await client.post(
                    "/internal/security/login",
                    headers={
                        "kbn-xsrf": "sellergenome-login-handoff",
                        "x-forwarded-proto": "https",
                    },
                    json={
                        "providerType": "basic",
                        "providerName": "basic",
                        "currentURL": "https://kibana.lehana.in" + next,
                        "params": {
                            "username": username,
                            "password": native_password,
                        },
                    },
                )
                upstream.raise_for_status()
        except httpx.HTTPError:
            raise HTTPException(
                503,
                "Kibana sign-in is temporarily unavailable. Retry shortly.",
            ) from None
        cookies = upstream.headers.get_list("set-cookie")
        if not any(cookie.startswith("sid=") for cookie in cookies):
            raise HTTPException(503, "Kibana did not establish a session.")
        result = Response(
            status_code=302,
            headers={"Location": next, "Cache-Control": "no-store"},
        )
        for cookie in cookies:
            result.raw_headers.append(
                (b"set-cookie", cookie.encode("latin-1"))
            )
        return result

    return app


app = create_app(
    os.getenv("ELASTIC_PM_USERNAME", "sellergenome_pm"),
    os.getenv("ELASTIC_PM_PASSWORD", ""),
    os.getenv("PM_PASSWORD", ""),
)

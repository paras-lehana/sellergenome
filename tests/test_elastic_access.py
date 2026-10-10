"""Native session handoff must authenticate first and never leak its password."""

import base64
import json

import httpx
from fastapi.testclient import TestClient

from backend.elastic_access import create_app


def authorization(password="owner-password"):
    """Use synthetic credentials only, outside provider/native secret files."""
    return {
        "Authorization": "Basic "
        + base64.b64encode(("sellergenome_pm:" + password).encode()).decode()
    }


def test_invalid_access_and_redirects_make_zero_native_calls():
    """Neither bad access nor an arbitrary redirect can trigger sign-in egress."""

    def deny(request):
        raise AssertionError("Unauthorized native egress")

    app = create_app(
        "sellergenome_pm",
        "native-test-secret",
        "owner-password",
        transport=httpx.MockTransport(deny),
    )
    with TestClient(app) as client:
        assert client.get("/login", follow_redirects=False).status_code == 401
        assert (
            client.get(
                "/login",
                headers=authorization("wrong"),
                follow_redirects=False,
            ).status_code
            == 401
        )
        for target in (
            "https://evil.example/",
            "//evil.example/",
            "/s/unrelated/app/dashboards",
            "/s/sellergenome/\\evil",
        ):
            assert (
                client.get(
                    "/login",
                    params={"next": target},
                    headers=authorization(),
                    follow_redirects=False,
                ).status_code
                == 422
            )


def test_native_session_is_forwarded_without_secret_in_response():
    """Only the bounded native endpoint sees its credential; cookie flags survive."""
    calls = []

    def native(request):
        calls.append(request)
        assert request.url.path == "/internal/security/login"
        assert (
            json.loads(request.content)["params"]["password"]
            == "native-test-secret"
        )
        return httpx.Response(
            200,
            json={"location": "/s/sellergenome/app/dashboards"},
            headers={
                "Set-Cookie": "sid=synthetic-cookie; Secure; HttpOnly; SameSite=Strict; Path=/"
            },
        )

    with TestClient(
        create_app(
            "sellergenome_pm",
            "native-test-secret",
            "owner-password",
            transport=httpx.MockTransport(native),
        )
    ) as client:
        result = client.get(
            "/login", headers=authorization(), follow_redirects=False
        )
        assert result.status_code == 302 and len(calls) == 1
        assert result.headers["location"] == "/s/sellergenome/app/dashboards"
        assert (
            "Secure; HttpOnly; SameSite=Strict" in result.headers["set-cookie"]
        )
        assert result.headers["cache-control"] == "no-store"
        assert "native-test-secret" not in result.text + str(result.headers)


def test_native_failure_remains_failure_and_withholds_error_body():
    """Provider errors cannot become a login success or disclose native details."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(503, text="private-native-detail")
    )
    with TestClient(
        create_app(
            "sellergenome_pm",
            "native-test-secret",
            "owner-password",
            transport=transport,
        )
    ) as client:
        result = client.get(
            "/login", headers=authorization(), follow_redirects=False
        )
        assert result.status_code == 503
        assert "private-native-detail" not in result.text
        assert "set-cookie" not in result.headers

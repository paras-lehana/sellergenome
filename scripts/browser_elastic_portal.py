"""Verify published Elastic counts and real Kibana dashboards/Discover in browsers.

Private passwords never appear in command values, logs or screenshots. Screenshots
contain aggregate charts or explicitly selected non-identifying source metadata.
"""

import argparse
import hashlib
import json
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

from scripts.configure_research_access import read_env

USER = "sellergenome_pm"


def api_checks(domain: str, password: str) -> dict:
    """Public auth challenges and scoped actual readback precede visual claims."""
    elastic, kibana = "https://elastic." + domain, "https://kibana." + domain
    assert httpx.get(elastic + "/", timeout=30).status_code == 401
    assert httpx.get(kibana + "/", timeout=30).status_code == 401
    assert (
        httpx.get(
            elastic + "/", auth=(USER, "invalid-test-password"), timeout=30
        ).status_code
        == 401
    )
    with httpx.Client(
        base_url=elastic, auth=(USER, password), timeout=60
    ) as client:
        response = client.get("/sellergenome-raw-family-*/_count")
        response.raise_for_status()
        count = response.json()["count"]
        assert count == 908856
        fields = client.get("/sellergenome-persona-fields/_count")
        fields.raise_for_status()
        assert fields.json()["count"] >= 70
        facts = client.get("/sellergenome-analytics-facts/_count")
        facts.raise_for_status()
        assert facts.json()["count"] == 101
        assert client.get("/_security/user").status_code == 403
        assert (
            client.post("/logs/_search", json={"size": 0}).status_code == 403
        )
        assert (
            client.post(
                "/sellergenome-raw-family-bot-calls/_update/permission-probe",
                json={"doc": {"test": True}},
            ).status_code
            == 403
        )
    with httpx.Client(
        base_url=kibana, auth=(USER, password), timeout=60
    ) as client:
        spaces = client.get("/api/spaces/space")
        spaces.raise_for_status()
        assert "sellergenome" in [space["id"] for space in spaces.json()]
        # Global read-only Streams readiness must not grant default dashboards.
        assert client.get(
            "/api/saved_objects/dashboard/sg-corpus-atlas"
        ).status_code in (403, 404)
        client.get("/s/sellergenome/api/streams/_status").raise_for_status()
        for identifier in ("sg-pm-corpus-atlas", "sg-pm-persona-evidence"):
            client.get(
                "/s/sellergenome/api/saved_objects/dashboard/" + identifier
            ).raise_for_status()
    return {
        "domain": domain,
        "status": "passed",
        "raw_rows": count,
        "persona_fields": fields.json()["count"],
        "facts": 101,
        "anonymous_denied": True,
        "data_write_denied": True,
        "security_admin_denied": True,
        "other_space_dashboards_denied": True,
    }


def browser_case(
    playwright,
    domain: str,
    engine: str,
    width: int,
    password: str,
    output: Path,
) -> dict:
    """Render real panels without logging raw documents or credentials."""
    browser = getattr(playwright, engine).launch(headless=True)
    context = browser.new_context(
        viewport={"width": width, "height": 1000 if width > 600 else 844},
        reduced_motion="reduce",
        is_mobile=width < 600,
        has_touch=width < 600,
        http_credentials={"username": USER, "password": password},
    )
    errors = []
    page = context.new_page()
    page.on(
        "pageerror",
        lambda error: errors.append(
            str(error).replace(password, "[REDACTED]")
        ),
    )
    panels = []
    base = "https://kibana." + domain + "/s/sellergenome"
    try:
        for identifier, expected in (
            ("sg-pm-corpus-atlas", 6),
            ("sg-pm-persona-evidence", 4),
            ("sg-v4-operations", 10),
        ):
            page.goto(
                base + "/app/dashboards#/view/" + identifier,
                wait_until="networkidle",
                timeout=90000,
            )
            page.wait_for_selector(
                '[data-test-subj="dashboardPanel"]', timeout=60000
            )
            expect(
                page.locator('[data-test-subj="dashboardPanel"]')
            ).to_have_count(expected)
            page.wait_for_function(
                "expected => document.querySelectorAll('canvas').length + document.querySelectorAll('.vega-view svg').length >= expected",
                arg=expected,
                timeout=60000,
            )
            assert not page.get_by_text(
                "Error loading data", exact=False
            ).count()
            assert not page.get_by_text("Forbidden", exact=True).count()
            page.screenshot(
                path=str(
                    output / f"{domain}-{engine}-{width}-{identifier}.png"
                ),
                full_page=True,
            )
            panels.append(
                {"id": identifier, "panels": expected, "status": "rendered"}
            )
        view = (
            "sg-pm-view-"
            + hashlib.sha256(b"family-bot_calls").hexdigest()[:12]
        )
        # No _source/raw/ID/quote columns: browser evidence stays safe to share.
        discover = (
            base
            + "/app/discover#/?_a=(columns:!(family,source_file,outcome,summary_available,meeting_conflict),index:'"
            + view
            + "')"
        )
        page.goto(discover, wait_until="networkidle", timeout=90000)
        page.wait_for_selector(
            '[data-test-subj="discoverDocTable"]', timeout=60000
        )
        expect(
            page.locator('[data-test-subj="discoverDocTable"]')
        ).to_contain_text("bot_calls")
        page.screenshot(
            path=str(output / f"{domain}-{engine}-{width}-discover.png"),
            full_page=True,
        )
        cookies = context.cookies()
        sessions = [cookie for cookie in cookies if cookie["name"] == "sid"]
        # A forwarded native Authorization header is stateless; a sid is optional.
        # Secure/Strict is required if Kibana issues a browser session cookie.
        assert all(
            cookie["secure"]
            and cookie["httpOnly"]
            and cookie["sameSite"] == "Strict"
            for cookie in sessions
        )
        # Stock Kibana's virtualized grid emits this browser layout diagnostic in
        # WebKit even when rows/charts render. Retain it separately; never ignore
        # application/fetch exceptions or replace the functional assertions.
        layout_warnings = [
            error
            for error in errors
            if error
            in (
                "ResizeObserver loop completed with undelivered notifications.",
                "ResizeObserver loop limit exceeded",
            )
        ]
        fatal = [error for error in errors if error not in layout_warnings]
        assert not fatal, fatal
        return {
            "domain": domain,
            "engine": engine,
            "width": width,
            "status": "passed",
            "dashboards": panels,
            "discover": "actual metadata rows",
            "authentication": "HTTP Basic gateway; scoped native account",
            "session_cookie": "Secure HttpOnly Strict"
            if sessions
            else "stateless_no_cookie",
            "page_errors": fatal,
            "vendor_layout_warnings": layout_warnings,
        }
    finally:
        context.close()
        browser.close()


def main() -> None:
    """Write sanitized receipts even when a browser check fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--domains", nargs="+", default=["lehana.in", "aidhunik.com"]
    )
    parser.add_argument("--engines", nargs="+", default=["chromium", "webkit"])
    parser.add_argument("--widths", type=int, nargs="+", default=[1440, 390])
    parser.add_argument(
        "--qa-env",
        type=Path,
        default=Path("/opt/secrets/env_files/seller-persona-v3-qa.env"),
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    password = read_env(args.qa_env)["PM_PASSWORD"]
    receipts = {"api": [], "browser": []}
    try:
        for domain in args.domains:
            receipts["api"].append(api_checks(domain, password))
        with sync_playwright() as playwright:
            for domain in args.domains:
                for engine in args.engines:
                    for width in args.widths:
                        row = browser_case(
                            playwright,
                            domain,
                            engine,
                            width,
                            password,
                            args.out,
                        )
                        receipts["browser"].append(row)
                        (args.out / "results.json").write_text(
                            json.dumps(receipts, indent=2)
                        )
                        print(json.dumps(row), flush=True)
    except Exception as error:
        receipts["status"] = "failed"
        receipts["safe_error"] = (
            type(error).__name__ + ": " + str(error)
        ).replace(password, "[REDACTED]")
        (args.out / "results.json").write_text(json.dumps(receipts, indent=2))
        print(
            json.dumps(
                {"status": "failed", "safe_error": receipts["safe_error"]}
            )
        )
        raise SystemExit(1) from None
    receipts["status"] = "passed"
    (args.out / "results.json").write_text(json.dumps(receipts, indent=2))
    print(
        json.dumps(
            {
                "status": "passed",
                "browser_cases": len(receipts["browser"]),
                "api_cases": len(receipts["api"]),
            }
        )
    )


if __name__ == "__main__":
    main()

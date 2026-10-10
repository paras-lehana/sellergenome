"""Real public analysis/workshop flows and optional private Kibana rendering proof.

Uses Playwright and private password files. Receipts contain counts/check outcomes,
never passwords, private seller identities or raw source values. No provider calls.
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from scripts.configure_research_access import read_env


def public_case(playwright, url, engine, width, output, password):
    """Exercise exact paths, controls, privacy, login and existing workshop demos."""
    browser = getattr(playwright, engine).launch(headless=True)
    context = browser.new_context(
        viewport={"width": width, "height": 900 if width > 600 else 844},
        reduced_motion="reduce",
        is_mobile=width < 600,
        has_touch=width < 600,
    )
    page = context.new_page()
    errors, calls = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: calls.append(request.url))
    result = {
        "url": url,
        "engine": engine,
        "width": width,
        "checks": [],
        "provider_calls": 0,
    }
    try:
        page.goto(url + "/analysis", wait_until="networkidle")
        expect(
            page.get_by_role(
                "heading",
                name="Understand the evidence. Design the conversation.",
            )
        ).to_be_visible()
        expect(page.locator("#persona-result .persona-field")).to_have_count(
            10
        )
        assert page.evaluate(
            "document.documentElement.scrollWidth<=innerWidth+1"
        )
        assert page.locator(".chapter").count() == 10
        page.screenshot(
            path=str(
                output / f"{engine}-{width}-{url.split('//')[1]}-analysis.png"
            ),
            full_page=False,
        )
        result["checks"].append(
            "Ten sourced chapters; actual ten-field ES readback; no page overflow"
        )
        page.locator("#persona-strength").select_option("strong")
        with page.expect_response(
            lambda response: "/deconstruction?strength=strong" in response.url
        ) as filtered:
            page.locator("#persona-query").click()
        selected = filtered.value.json()
        assert all(item["strength"] == "strong" for item in selected["items"])
        if selected["total"] == 0:
            expect(page.locator("#persona-result")).to_contain_text(
                "No fields match"
            )
        page.locator("#persona-strength").select_option("")
        page.locator("#persona-example").select_option("demo-dev")
        page.locator("#persona-query").click()
        expect(page.locator("#persona-result .persona-field")).to_have_count(
            10
        )
        page.locator('[data-version-metric="rate"]').click()
        expect(page.locator('[data-version-metric="rate"]')).to_have_attribute(
            "aria-pressed", "true"
        )
        page.locator('[data-period="2026-09"]').click()
        with page.expect_response(
            lambda response: "/api/analysis/live?" in response.url
        ) as response:
            page.locator("#live-query").click()
        actual = response.value.json()
        assert (
            actual["engine"] == "elasticsearch"
            and actual["denominator"] == 69447
        )
        assert sum(item["count"] for item in actual["items"]) == 69447
        expect(page.locator("#live-result")).to_contain_text(
            "ACTUAL ELASTICSEARCH"
        )
        details = page.locator(".source-detail").first
        details.locator("summary").click()
        expect(details.locator("div")).to_contain_text("disposition_label")
        exported = context.request.get(url + "/api/analysis/export")
        assert exported.status == 200
        assert exported.json()["total_rows"] == 908856
        assert (
            context.request.get(
                url + "/api/personas/private-test/deconstruction"
            ).status
            == 401
        )
        result["checks"].append(
            "Strength/model filters, real September69447-call query, source details/export and private denial"
        )
        page.goto(url + "/walkthrough", wait_until="networkidle")
        expect(
            page.get_by_role(
                "heading", name="Understand the idea. Put it to the test."
            )
        ).to_be_visible()
        manifest = context.request.get(url + "/api/workshop/manifest").json()
        scenarios = [
            scenario
            for section in manifest["sections"]
            for scenario in section["scenarios"]
        ]
        for scenario in scenarios:
            page.goto(
                url + "/walkthrough#workshop/" + scenario["id"],
                wait_until="networkidle",
            )
            button = page.locator('[data-action="workshop-demo"]')
            expect(button).to_be_enabled()
            before = len([call for call in calls if "/api/" in call])
            button.click()
            expect(page.locator("#demo-result")).not_to_contain_text(
                "Try Demo"
            )
            assert len([call for call in calls if "/api/" in call]) == before
            assert page.locator('[data-action="workshop-live"]').is_disabled()
        result["checks"].append(
            f"Exact /walkthrough path and all{len(scenarios)} bundled demos; zero Demo API requests"
        )
        page.goto(url + "/walkthrough#settings", wait_until="networkidle")
        page.locator("#login-password").fill(password)
        page.locator("#login-form button").click()
        expect(page.locator("#access-badge")).to_contain_text("Administrator")
        assert context.request.get(url + "/api/admin/config").status == 200
        assert context.request.get(url + "/api/feedback").status == 200
        session_cookie = [
            cookie
            for cookie in context.cookies()
            if cookie["name"] in {"persona_v3_access", "persona_v4_access"}
        ][0]
        assert (
            session_cookie["secure"] == url.startswith("https://")
            and session_cookie["httpOnly"]
            and session_cookie["sameSite"] == "Strict"
        )
        result["checks"].append(
            "Actual password UI, full admin/feedback access and secure HttpOnly Strict cookie"
        )
        page.goto(url + "/analysis", wait_until="networkidle")
        page.locator("#live-query").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#live-result")).to_contain_text("2,69,455")
        page.locator("#persona-query").focus()
        page.keyboard.press("Tab")
        page.keyboard.press("Shift+Tab")
        assert page.locator("#persona-query").evaluate(
            "el=>el===document.activeElement"
        )
        assert (
            page.locator("#persona-query").evaluate(
                "el=>getComputedStyle(el).outlineStyle"
            )
            != "none"
        )
        assert context.request.post(url + "/api/auth/logout").status == 200
        assert context.request.get(url + "/api/feedback").status == 401
        assert context.request.get(url + "/analysis/unknown").status == 404
        result["checks"].append(
            "Keyboard query/focus, logout revocation and unknownpath404"
        )
        assert not errors, errors
        result["status"] = "passed"
        result["page_errors"] = errors
        return result
    finally:
        context.close()
        browser.close()


def kibana_case(playwright, url, output, password):
    """Render both imported dashboards and verify actual successful ES results."""
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    results = []
    try:
        page.goto(url + "/app/dashboards", wait_until="networkidle")
        page.wait_for_selector(
            '[data-test-subj="loginUsername"]', timeout=60000
        )
        if page.locator('[data-test-subj="loginUsername"]').count():
            page.locator('[data-test-subj="loginUsername"]').fill("elastic")
            page.locator('[data-test-subj="loginPassword"]').fill(password)
            page.locator('[data-test-subj="loginSubmit"]').click()
            page.wait_for_url("**/app/**", timeout=60000)
            page.wait_for_selector(
                '[data-test-subj="loginUsername"]',
                state="hidden",
                timeout=60000,
            )
        for identifier, title in [
            ("sg-corpus-atlas", "corpus and data quality"),
            ("sg-persona-evidence", "persona deconstruction"),
        ]:
            page.goto(
                url + "/app/dashboards#/view/" + identifier,
                wait_until="networkidle",
            )
            page.wait_for_selector(
                '[data-test-subj="dashboardPanel"]', timeout=90000
            )
            page.wait_for_timeout(6000)
            assert (
                page.locator('[data-test-subj="dashboardPanel"]').count() >= 4
            )
            assert (
                page.locator("canvas").count()
                + page.locator(".vega-view svg").count()
                >= 4
            )
            text = page.locator("body").inner_text()
            assert (
                "Error loading" not in text
                and "Invalid specification" not in text
                and "Failed to fetch" not in text
            )
            page.screenshot(
                path=str(output / (identifier + ".png")), full_page=True
            )
            results.append(
                {
                    "dashboard": identifier,
                    "panels": page.locator(
                        '[data-test-subj="dashboardPanel"]'
                    ).count(),
                    "title_visible": title in text.lower(),
                    "status": "passed",
                }
            )
        assert not errors, errors
        return {
            "status": "passed",
            "dashboards": results,
            "page_errors": errors,
            "source": "Actual imported Kibana visualizations",
        }
    finally:
        context.close()
        browser.close()


def main():
    """Run selected public matrices or the independent private dashboard check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urls", nargs="*", default=[])
    parser.add_argument(
        "--engine", choices=["chromium", "webkit", "both"], default="both"
    )
    parser.add_argument("--widths", nargs="+", type=int, default=[1440, 390])
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/opt/secrets/env_files/seller-persona-v3-qa.env"),
    )
    parser.add_argument("--kibana")
    parser.add_argument(
        "--infra-env",
        type=Path,
        default=Path("/opt/secrets/env_files/seller-persona-infra.env"),
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        if args.kibana:
            results.append(
                kibana_case(
                    playwright,
                    args.kibana,
                    args.out,
                    read_env(args.infra_env)["ELASTIC_PASSWORD"],
                )
            )
        if args.urls:
            password = read_env(args.env_file)["PM_PASSWORD"]
            for url in args.urls:
                for engine in (
                    ["chromium", "webkit"]
                    if args.engine == "both"
                    else [args.engine]
                ):
                    for width in args.widths:
                        results.append(
                            public_case(
                                playwright,
                                url.rstrip("/"),
                                engine,
                                width,
                                args.out,
                                password,
                            )
                        )
                        print(json.dumps(results[-1]), flush=True)
                        (args.out / "results.json").write_text(
                            json.dumps(results, indent=2)
                        )
    (args.out / "results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps({"cases": len(results), "status": "passed"}))


if __name__ == "__main__":
    main()

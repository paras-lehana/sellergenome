#!/usr/bin/env python3
"""Exercise real public user flows on both mirrors and both required viewports."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def run(base: str, width: int, height: int, output: Path, engine: str) -> dict:
    """Use DOM interactions, not HTTP health alone, to prove the rehearsal."""
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, engine)
        browser = browser_type.launch(headless=True)
        context = browser.new_context(
            viewport={"width": width, "height": height},
            reduced_motion="reduce",
            is_mobile=width < 600,
            has_touch=width < 600,
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/", wait_until="networkidle")
        version = page.locator('meta[name="app-version"]').get_attribute(
            "content"
        )
        modules = page.evaluate(r"""() => performance.getEntriesByType('resource')
            .map(item => new URL(item.name)).filter(url =>
            /\/(core|lab|sync|loading-tips)\.js$/.test(url.pathname))
            .map(url => url.searchParams.get('v'))""")
        assert len(modules) == 4 and all(value == version for value in modules)
        expect(page.locator("h1")).to_be_visible()
        assert page.locator("body").inner_text().find("couldn't open") == -1
        routes = [
            "overview",
            "sellers",
            "evidence",
            "data",
            "speech",
            "integrations",
            "settings",
            "roadmap",
            "lab",
        ]
        for route in routes:
            page.goto(base + "/#" + route, wait_until="networkidle")
            page.locator("h1").wait_for()
            expect(page.locator(".error-state")).to_have_count(0)
            page.wait_for_function("!document.querySelector('.loading-view')")
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth + 1"
            ), route + " overflows"
            assert (
                "[object Object]" not in page.locator("body").inner_text()
            ), route + " has unrendered objects"
        page.goto(base + "/#sellers", wait_until="networkidle")
        page.get_by_label("Search sellers").fill("Asha")
        page.locator("#seller-search").get_by_role(
            "button", name="Search", exact=True
        ).click()
        expect(page.locator(".seller-card")).to_have_count(1)
        page.locator(".seller-card").click()
        page.locator('[data-action="generate-persona"]').click()
        expect(page.locator("#seller-inspector")).to_contain_text(
            "Method: rules", timeout=30000
        )
        page.get_by_role("tab", name="Evidence").click()
        expect(page.locator(".evidence-card")).to_have_count(5)
        expect(page.locator("#seller-inspector")).to_contain_text("Stale")
        page.locator('[data-action="open-lab"]').click()
        page.get_by_role("button", name="Start rehearsal").click()
        page.locator("#turn-text").wait_for()
        sequence = [
            ("I am frustrated and annoyed", "frustration"),
            ("I do not understand this", "confusion"),
            ("That sounds useful, tell me more", "interest"),
            ("I am in a hurry, keep it short", "rush"),
            ("Please speak English", "language_switch"),
        ]
        for text, signal in sequence:
            page.get_by_label("Your seller response").fill(text)
            with page.expect_response(
                lambda response: "/turns" in response.url
                and response.request.method == "POST",
                timeout=30000,
            ) as response:
                page.get_by_role("button", name="Send turn").click()
            result = response.value.json()
            assert response.value.status == 200, result
            assert result["signal"] == signal, result
            assert result["switch"], result
            page.locator("#turn-text").wait_for()
            expect(page.locator("#conversation")).to_contain_text(text)
        expect(
            page.get_by_role("heading", name="Switch log (5)")
        ).to_be_visible()
        page.screenshot(
            path=str(
                output
                / f"{engine}-{width}-{base.split('//')[1].split('/')[0]}-lab.png"
            ),
            full_page=True,
        )
        page.get_by_role("button", name="Finish & evaluate").click()
        expect(page.locator(".outcome")).to_be_visible()
        expect(page.locator(".outcome")).to_contain_text(
            "Hold", ignore_case=True
        )
        page.goto(base + "/#evidence", wait_until="networkidle")
        page.get_by_label("Evidence query").fill("Hindi")
        page.get_by_role("button", name="Search evidence", exact=True).click()
        expect(page.locator(".evidence-card").first).to_be_visible()
        page.goto(base + "/#settings", wait_until="networkidle")
        expect(page.locator("body")).not_to_contain_text(
            "test-operator-long-secret"
        )
        page.goto(base + "/", wait_until="networkidle")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement !== document.body")
        page.keyboard.press("Tab")
        if width < 600:
            page.locator("#open-menu").click()
            expect(page.locator("#nav-dialog")).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.locator("#nav-dialog")).not_to_be_visible()
        page.screenshot(
            path=str(
                output
                / f"{engine}-{width}-{base.split('//')[1].split('/')[0]}-overview.png"
            ),
            full_page=True,
        )
        assert not errors, errors
        context.close()
        browser.close()
    return {
        "base": base,
        "viewport": f"{width}x{height}",
        "engine": engine,
        "status": "passed",
        "routes": routes,
        "signals": [signal for _, signal in sequence],
        "console_errors": errors,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--urls",
        nargs="+",
        default=[
            "https://portfolio.aidhunik.com/hackathon/seller-persona",
            "https://portfolio.lehana.in/hackathon/seller-persona",
        ],
    )
    parser.add_argument(
        "--out", default="/root/data/qa-artifacts/seller-persona-browser"
    )
    parser.add_argument(
        "--engine", default="chromium", choices=["chromium", "webkit"]
    )
    args = parser.parse_args()
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for url in args.urls:
        for width, height in [(1440, 900), (390, 844)]:
            result = run(url.rstrip("/"), width, height, output, args.engine)
            results.append(result)
            print(json.dumps(result), flush=True)
    (output / f"{args.engine}-results.json").write_text(
        json.dumps(results, indent=2)
    )

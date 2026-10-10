#!/usr/bin/env python3
"""Verify operator authorization and actual model inference through the public UI.

This explicit smoke test makes two paid/provider-backed model requests. Keys are
read locally and never printed or included in screenshots or output artifacts.
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def run(base: str, env_file: Path, output: Path) -> dict:
    """Prove UI inference, indexing, private listing and conversation operation."""
    values = dict(
        line.split("=", 1)
        for line in env_file.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1440, "height": 900}, reduced_motion="reduce"
        )
        page.goto(base + "/#settings", wait_until="networkidle")
        page.locator("#operator-key").fill(values["OPERATOR_API_KEY"])
        page.locator('#operator-form button[type="submit"]').click()
        expect(page.locator("#access-badge")).to_contain_text(
            "Operator key supplied"
        )
        page.goto(base + "/#sellers/demo-kabir", wait_until="networkidle")
        page.locator("#use-llm").check()
        with page.expect_response(
            lambda response: "/persona" in response.url
            and response.request.method == "POST",
            timeout=90000,
        ) as created:
            page.locator('[data-action="generate-persona"]').click()
        assert created.value.status == 200, created.value.status
        persona = created.value.json()
        assert persona["method"] == "rules+llm" and persona["indexed"] is True
        expect(page.locator("#seller-inspector")).to_contain_text("rules+llm")
        page.locator('[data-action="open-lab"]').click()
        page.locator('[name="use_llm"]').check()
        page.get_by_role("button", name="Start rehearsal").click()
        page.locator("#turn-text").fill(
            "Please explain the difference between an enquiry and a buylead."
        )
        with page.expect_response(
            lambda response: "/turns" in response.url
            and response.request.method == "POST",
            timeout=90000,
        ) as replied:
            page.get_by_role("button", name="Send turn").click()
        assert replied.value.status == 200
        reply = replied.value.json()
        assert reply["method"] == "llm" and reply["reply"]
        expect(page.locator("#conversation")).to_contain_text(reply["reply"])
        page.screenshot(
            path=str(output / "operator-llm-rehearsal.png"), full_page=True
        )
        page.get_by_role("button", name="Finish & evaluate").click()
        expect(page.locator(".outcome")).to_be_visible()
        page.goto(base + "/#sellers", wait_until="networkidle")
        page.locator("#seller-dataset").select_option("private")
        with page.expect_response(
            lambda response: "/api/sellers?" in response.url
            and "private" in response.url
        ) as private:
            page.locator('#seller-search button[type="submit"]').click()
        assert private.value.status == 200
        assert private.value.json()["total"] == 149363
        # A separate anonymous browser cannot see this operator's data or session.
        anonymous = browser.new_context()
        denied = anonymous.request.get(base + "/api/sellers?dataset=private")
        assert denied.status == 401
        anonymous.close()
        browser.close()
    return {
        "status": "passed",
        "persona_method": persona["method"],
        "persona_model": persona["model"],
        "indexed": persona["indexed"],
        "reply_method": reply["method"],
        "reply_latency_ms": reply["latency_ms"],
        "private_sellers": 149363,
        "anonymous_private_status": denied.status,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="https://portfolio.aidhunik.com/hackathon/seller-persona",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/opt/secrets/env_files/seller-persona.env"),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/root/data/qa-artifacts/seller-persona-browser"),
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result = run(args.url.rstrip("/"), args.env_file, args.out)
    (args.out / "operator-result.json").write_text(
        json.dumps(result, indent=2)
    )
    print(json.dumps(result))

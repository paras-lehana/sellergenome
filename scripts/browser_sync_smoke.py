#!/usr/bin/env python3
"""Verify protected refresh controls on both mirrors without upstream requests.

Select an explicitly unconfigured source. Queue failures are genuine, not mocked;
enabled test schedules are paused again before returning. Keys/private seller
IDs never enter the saved receipt or console output.
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def run(
    base: str, width: int, height: int, engine: str, key: str, output: Path
) -> dict:
    """Use actual form controls for queue, retry, cancel and schedule changes."""
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        context = browser.new_context(
            viewport={"width": width, "height": height},
            reduced_motion="reduce",
            is_mobile=width < 600,
            has_touch=width < 600,
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/#settings", wait_until="networkidle")
        page.locator("#operator-key").fill(key)
        page.locator('#operator-form button[type="submit"]').click()
        expect(page.locator("#access-badge")).to_contain_text(
            "Operator key supplied"
        )
        page.goto(base + "/#sellers", wait_until="networkidle")
        page.locator("#seller-dataset").select_option("private")
        with page.expect_response(
            lambda response: "/api/sellers?" in response.url
            and "private" in response.url
        ) as listed:
            page.locator('#seller-search button[type="submit"]').click()
        assert listed.value.status == 200
        seller_id = listed.value.json()["items"][0]["id"]
        page.goto(base + "/#integrations", wait_until="networkidle")
        expect(page.locator("#source-refresh-form")).to_be_visible()
        items = context.request.get(base + "/api/integrations").json()["items"]
        # Parallel engines use separate pairs; active-pair deduplication is
        # intentional application behavior, not a retry failure.
        preferred = "enquiries" if engine == "webkit" else "buyleads"
        items.sort(key=lambda item: item["id"] != preferred)
        source = next(
            (
                item["id"]
                for item in items
                if item["id"] in {"buyleads", "enquiries", "redash"}
                and not item["configured"]
            ),
            None,
        )
        assert source, (
            "Failure-path smoke requires an explicitly unconfigured source."
        )
        page.locator("#refresh-source").select_option(source)
        with page.expect_response(
            lambda response: response.url.endswith("/jobs")
            and response.request.method == "POST"
        ) as queued:
            page.get_by_role(
                "button", name="Queue refresh now", exact=True
            ).click()
        assert queued.value.status == 202
        job_id = queued.value.json()["job"]["id"]
        row = page.locator('[data-job-id="' + job_id + '"]')
        expect(row.locator(".badge")).to_have_text("Failed", timeout=20000)
        expect(row).to_contain_text("Not Configured")
        assert (
            context.request.get(base + "/api/sync-jobs/" + job_id).status
            == 401
        )

        cancelled = False
        for _ in range(3):
            with page.expect_response(
                lambda response: response.url.endswith("/retry")
                and response.request.method == "POST"
            ) as retried:
                row.get_by_role("button", name="Retry job", exact=True).click()
            assert retried.value.status == 202
            retry_id = retried.value.json()["job"]["id"]
            if retried.value.json()["created"]:
                assert retried.value.json()["job"]["parent_id"] == job_id
            row = page.locator('[data-job-id="' + retry_id + '"]')
            expect(row).to_be_visible(timeout=10000)
            cancel = row.get_by_role("button", name="Cancel job", exact=True)
            if cancel.is_visible():
                with page.expect_response(
                    lambda response: response.url.endswith("/cancel")
                    and response.request.method == "POST"
                ) as stopped:
                    cancel.click()
                if stopped.value.status == 200:
                    expect(row.locator(".badge")).to_have_text(
                        "Cancelled", timeout=10000
                    )
                    cancelled = True
                    break
            expect(row.locator(".badge")).to_have_text("Failed", timeout=20000)
            job_id = retry_id
        assert cancelled, "Could not observe an uncancelled queue window."

        with page.expect_response(
            lambda response: response.url.endswith("/sync-schedules")
            and response.request.method == "POST"
        ) as scheduled:
            page.get_by_role(
                "button", name="Save refresh schedule", exact=True
            ).click()
        assert scheduled.value.status == 200
        schedule_id = scheduled.value.json()["id"]
        schedule = page.locator('[data-schedule-id="' + schedule_id + '"]')
        expect(schedule.locator(".badge")).to_have_text("Paused")
        schedule.get_by_role(
            "button", name="Enable schedule", exact=True
        ).click()
        expect(
            schedule.get_by_role("button", name="Pause schedule", exact=True)
        ).to_be_visible()
        schedule.get_by_role(
            "button", name="Pause schedule", exact=True
        ).click()
        expect(schedule.locator(".badge")).to_have_text("Paused")
        schedule.get_by_role(
            "button", name="Edit interval", exact=True
        ).click()
        expect(page.locator("#refresh-interval")).to_be_focused()
        page.locator("#refresh-interval").fill("90")
        with page.expect_response(
            lambda response: response.url.endswith("/sync-schedules")
            and response.request.method == "POST"
        ) as edited:
            page.get_by_role(
                "button", name="Save refresh schedule", exact=True
            ).click()
        assert edited.value.json()["interval_seconds"] == 5400
        assert edited.value.json()["enabled"] is False
        assert page.evaluate(
            "document.documentElement.scrollWidth <= innerWidth + 1"
        )
        privacy_style = page.add_style_tag(
            content=".sync-job .small.muted, #refresh-selected { visibility:hidden !important; } .topbar { position:relative !important; }"
        )
        page.evaluate("document.querySelector('#toasts').replaceChildren()")
        page.locator("#source-refresh").screenshot(
            path=str(output / f"{engine}-{width}-operator-refresh.png"),
        )
        privacy_style.evaluate("node => node.remove()")

        # A fresh context verifies server authorization independently of UI visibility.
        anonymous = browser.new_context()
        assert anonymous.request.get(base + "/api/sync-jobs").status == 401
        assert (
            anonymous.request.get(base + "/api/sync-schedules").status == 401
        )
        anonymous.close()
        page.goto(base + "/#settings", wait_until="networkidle")
        page.get_by_role("button", name="Clear key", exact=True).click()
        page.goto(base + "/#integrations", wait_until="networkidle")
        expect(page.locator("#source-refresh-form")).to_have_count(0)
        assert seller_id not in page.locator("body").inner_text()
        page.screenshot(
            path=str(output / f"{engine}-{width}-public-integrations.png"),
            full_page=True,
        )
        assert errors == [], errors
        browser.close()
    return {
        "url": base,
        "engine": engine,
        "viewport": [width, height],
        "status": "passed",
        "source": source,
        "queue_failure": "not_configured",
        "cancelled": cancelled,
        "schedule": "paused_90_minutes",
        "anonymous_history": 401,
        "page_errors": errors,
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
        "--engine", choices=["chromium", "webkit"], default="chromium"
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/opt/secrets/env_files/seller-persona.env"),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/root/data/qa-artifacts/seller-persona-1.1.0/sync"),
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    values = dict(
        line.split("=", 1)
        for line in args.env_file.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    receipts = []
    for url in args.urls:
        folder = args.out / url.split("/")[2]
        folder.mkdir(exist_ok=True)
        for width, height in [(1440, 900), (390, 844)]:
            receipt = run(
                url,
                width,
                height,
                args.engine,
                values["OPERATOR_API_KEY"],
                folder,
            )
            receipts.append(receipt)
            print(json.dumps(receipt), flush=True)
    (args.out / f"{args.engine}-results.json").write_text(
        json.dumps(receipts, indent=2)
    )

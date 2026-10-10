"""Real PM analysis flows; receipts exclude private evidence and passwords."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

from scripts.configure_research_access import read_env


def case(playwright, url, engine, width, output, password):
    """Explain, compare, evaluate and privately inspect using actual DOM controls."""
    browser = getattr(playwright, engine).launch()
    context = browser.new_context(
        viewport={"width": width, "height": 844 if width == 390 else 900},
        reduced_motion="reduce",
        has_touch=width == 390,
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    key = f"{url.split('//')[1].replace(':', '-')}-{engine}-{width}"
    result = {
        "url": url,
        "engine": engine,
        "width": width,
        "checks": [],
        "status": "failed",
    }
    try:
        page.goto(url + "/analysis", wait_until="networkidle")
        study = context.request.get(url + "/api/analysis/pm-study").json()
        expect(page.locator(".pm-help")).to_have_count(7)
        assert page.locator(".pm-help[open]").count() == 0
        result["checks"].append("All seven explanations collapsed by default")
        for path in [
            "/api/analysis/review/calls",
            "/api/analysis/review/calls/review-" + "a" * 24,
        ]:
            assert context.request.get(url + path).status == 401
        result["checks"].append("Anonymous real-call list and detail denied")
        summary = page.locator("#summary-meaning > summary")
        summary.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#summary-meaning")).to_have_attribute("open", "")
        expect(
            page.locator("#summary-meaning .definition-grid article")
        ).to_have_count(7)
        expect(page.locator("#summary-meaning")).to_contain_text("2,58,736")
        page.locator("#summary-meaning").scroll_into_view_if_needed()
        page.screenshot(path=str(output / (key + "-summary.png")))
        summary.click()
        page.locator("#matrix-meaning > summary").click()
        expect(page.locator("#matrix-meaning")).to_contain_text("23.7%")
        expect(page.locator("#matrix-meaning")).to_contain_text("1,936")
        page.locator("#matrix-meaning > summary").click()
        result["checks"].append(
            "Keyboard explanations, seven rules and real cell calculation"
        )
        page.locator("#sample-personas > summary").click()
        expect(page.locator(".pm-persona")).to_have_count(10)
        expect(page.locator("#sample-personas")).to_contain_text("4,511")
        expect(page.locator("#sample-personas")).to_contain_text("2,207")
        page.locator(".pm-persona > summary").first.click()
        expect(page.locator(".pm-persona").first).to_contain_text(
            "Try this approach"
        )
        page.locator(".persona-response-comparison > summary").first.click()
        expect(
            page.locator(".persona-response-comparison").first
        ).to_contain_text("Observed responses within this persona")
        assert page.locator(".pm-persona").evaluate_all(
            "(els)=>els.every(e=>e.getBoundingClientRect().right <= innerWidth + 1)"
        )
        page.locator("#sample-personas").scroll_into_view_if_needed()
        page.screenshot(path=str(output / (key + "-personas.png")))
        result["checks"].append(
            "Ten sample persona cohorts with calls, sellers, outcomes, strategy hypotheses"
        )
        page.locator("#general-diagnostics > summary").click()
        expect(page.locator("#diagnostic-results")).to_contain_text("78,365")
        expect(page.locator("#diagnostic-results")).to_contain_text(
            "596 / 677"
        )
        page.locator("#diagnostic-scope").select_option("sample_general")
        expect(page.locator("#diagnostic-results")).to_contain_text("1,540")
        expect(page.locator("#diagnostic-results")).to_contain_text(
            "136 / 156"
        )
        expect(page.locator("#diagnostic-results")).to_contain_text(
            "Successful rebuttal percentage: not established"
        )
        page.locator(".pm-response-details > details > summary").first.click()
        page.locator("#general-diagnostics").scroll_into_view_if_needed()
        page.screenshot(path=str(output / (key + "-general.png")))
        result["checks"].append(
            "Full/sample denominators switch, observed response breakdown opens"
        )
        page.locator("#diagnostic-evaluator > summary").click()
        page.locator("#evaluator-demo").click()
        expect(page.locator("#evaluator-result")).to_contain_text(
            "SYNTHETIC DEMO"
        )
        expect(page.locator("#evaluator-result")).to_contain_text("callback")
        page.locator("#evaluator-scenario").select_option("unknown")
        page.locator("#evaluator-demo").click()
        expect(page.locator("#evaluator-result")).to_contain_text(
            "insufficient evidence"
        )
        context.set_offline(True)
        page.locator("#evaluator-demo").click()
        expect(page.locator("#evaluator-result .error-note")).to_be_visible()
        context.set_offline(False)
        page.locator("#evaluator-demo").click()
        expect(page.locator("#evaluator-result")).to_contain_text(
            "insufficient evidence"
        )
        result["checks"].append(
            "Real synthetic evaluator: response, unknown, offline failure and retry"
        )
        assert (
            context.request.post(
                url + "/api/auth/login", data={"password": password}
            ).status
            == 200
        )
        if width == 390:
            assert page.locator(".pm-help select:visible").evaluate_all(
                "els => els.every(e => parseFloat(getComputedStyle(e).fontSize) >= 16)"
            )
            assert page.locator(".pm-help td:visible").evaluate_all(
                "els => els.every(e => parseFloat(getComputedStyle(e).fontSize) >= 14)"
            )
        page.locator("#review-load").click()
        expect(page.locator(".pm-call")).to_have_count(12)
        page.locator(".pm-call").first.click()
        expect(page.locator("#review-case")).to_contain_text(
            "LOCAL HISTORICAL EVIDENCE"
        )
        expect(page.locator("#review-case")).to_contain_text("SHA256")
        page.locator("[data-rerun-case]").click()
        expect(page.locator("#review-case")).to_contain_text("Recomputed now")
        expect(page.locator("#review-case")).to_contain_text(study["version"])
        page.locator("#review-scope").select_option("sample_all")
        page.locator("#review-persona").select_option("timing")
        page.locator("#review-outcome").select_option("meeting")
        page.locator("#review-load").click()
        expect(page.locator(".pm-call")).to_have_count(12)
        assert all(
            "Meeting Fixed" in t
            for t in page.locator(".pm-call").all_text_contents()
        )
        page.locator("#review-outcome").select_option("not_meeting")
        page.locator("#review-load").click()
        expect(page.locator(".pm-call")).to_have_count(12)
        assert all(
            "Meeting Fixed" not in t
            for t in page.locator(".pm-call").all_text_contents()
        )
        page.locator("#review-next").click()
        expect(page.locator("#review-list")).to_contain_text("page 2")
        result["checks"].append(
            "Authenticated evidence, rerun, persona and meeting/non-meeting filters, pagination"
        )
        assert context.request.post(url + "/api/auth/logout").status == 200
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(page.locator(".pm-call")).to_have_count(0)
        expect(page.locator("#review-case")).to_be_empty()
        result["checks"].append(
            "Logout clears private evidence; no browser secret storage"
        )
        assert page.evaluate("Object.keys(localStorage).length") == 0
        assert page.evaluate(
            "document.documentElement.scrollWidth <= innerWidth+1"
        )
        assert not errors, errors
        result["checks"].append(
            "No horizontal page overflow or unhandled browser errors"
        )
        result["status"] = "passed"
    except Exception as error:
        result["error"] = str(error)[:1200]
        # Error screenshot can include private calls; keep only in private QA dir.
        page.screenshot(path=str(output / (key + "-failure.png")))
    finally:
        context.close()
        browser.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urls", nargs="+", required=True)
    parser.add_argument(
        "--engine", choices=["chromium", "webkit", "both"], default="both"
    )
    parser.add_argument("--widths", nargs="+", type=int, default=[1440, 390])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/opt/secrets/env_files/seller-persona-v3-qa.env"),
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    password = read_env(args.env_file)["PM_PASSWORD"]
    with sync_playwright() as playwright:
        for url in args.urls:
            for engine in (
                ["chromium", "webkit"]
                if args.engine == "both"
                else [args.engine]
            ):
                for width in args.widths:
                    receipt = case(
                        playwright, url, engine, width, args.out, password
                    )
                    results.append(receipt)
                    (args.out / "results.json").write_text(
                        json.dumps(results, indent=2)
                    )
                    print(json.dumps(receipt), flush=True)
    if any(item["status"] != "passed" for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

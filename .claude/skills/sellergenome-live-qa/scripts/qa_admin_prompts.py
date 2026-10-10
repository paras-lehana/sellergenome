#!/usr/bin/env python3
"""Live QA of 5.9.2–5.9.3 Admin on a public mirror: APIs → API health (Test every API) and Admin → Prompts (list, where
used, a refused broken edit, save → edited → reset), plus Admin search finding a prompt.

Usage: qa_admin_prompts.py BASE OUT_DIR ENGINE WxH [SELLER]
  SELLER  seller for the health test (default syn-91000101; real-… calls live APIs — keep those screenshots out of Git)
Prints one JSON line; exit 1 if a check failed. Signed in with the server-side operator key (never printed).
The prompt edited is persona.locality, and it is reset to default before the script ends.
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE, OUT, ENGINE, SIZE = sys.argv[1:5]
SELLER = sys.argv[5] if len(sys.argv) > 5 else "syn-91000101"
W, H = (int(x) for x in SIZE.split("x"))
KEY = next(l.split("=", 1)[1].strip().strip("'\"") for l in open("/srv/sellergenome-v4/seller-persona-v4.env") if l.startswith("OPERATOR_API_KEY="))
TAG = f"{BASE.split('//')[-1].split('.')[1]}-admin-{ENGINE}-{W}"


def wait_for(page, fn, timeout_ms=60000, step=250):
    for _ in range(timeout_ms // step):
        if fn():
            return True
        page.wait_for_timeout(step)
    return False


row: dict = {"tag": TAG}
problems: list[str] = []
with sync_playwright() as p:
    browser = getattr(p, ENGINE).launch()
    ctx = browser.new_context(viewport={"width": W, "height": H}, extra_http_headers={"x-operator-key": KEY})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"{e}"))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "422" not in m.text else None)

    # 1. API health.
    page.goto(BASE + "/admin#apis", wait_until="domcontentloaded")
    wait_for(page, lambda: page.locator("#health-card").count() > 0, 20000)
    page.locator("#health-seller").fill(SELLER)
    page.locator("#health-form button").click()
    wait_for(page, lambda: page.locator(".health-table tbody tr").count() > 0, 90000)
    rows = page.locator(".health-table tbody tr")
    row["health_rows"] = rows.count()
    row["health_chip"] = page.locator("#health-card .card-head .chip").inner_text() if page.locator("#health-card .card-head .chip").count() else None
    row["health_errors"] = page.locator(".health-table .o-missing").count()
    page.locator("#health-card").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/{TAG}-1-health.png")
    if row["health_rows"] < 8 or not row["health_chip"]:
        problems.append("API health table incomplete")

    # 2. Prompts.
    page.locator('.admin-nav [data-sec="prompts"]').click()
    wait_for(page, lambda: page.locator(".prompt-card").count() > 20, 15000)
    row["prompt_cards"] = page.locator(".prompt-card").count()
    row["prompt_groups"] = page.locator("#admin-body h2.group-title").all_inner_texts()
    card = page.locator('[data-prompt-card="persona.locality"]')
    card.locator("summary").click()
    meta = card.locator(".prompt-meta").inner_text()
    row["meta_has_used_and_effect"] = "Used" in meta and "Takes effect" in meta and "Model call" in meta
    page.screenshot(path=f"{OUT}/{TAG}-2-prompts.png")
    # refused edit: the aggregator without FACTORS_HERE
    agg = page.locator('[data-prompt-card="persona.aggregator"]')
    agg.locator("summary").click()
    agg.locator("textarea").fill("Build the persona.")
    agg.locator("button.btn-primary").click()
    wait_for(page, lambda: "FACTORS_HERE" in (agg.locator("[data-prompt-err]").inner_text() or ""), 10000)
    row["refused_reason"] = agg.locator("[data-prompt-err]").inner_text()[:100]
    agg.locator("[data-prompt-err]").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/{TAG}-3-refused.png")
    if "FACTORS_HERE" not in row["refused_reason"]:
        problems.append("broken edit was not refused with a reason")
    # save → edited → reset
    card = page.locator('[data-prompt-card="persona.locality"]')
    if not card.evaluate("e => e.open"):
        card.locator("summary").click()
    original = card.locator("textarea").input_value()
    card.locator("textarea").fill(original + " Keep the note short.")
    card.locator("button.btn-primary").click()
    wait_for(page, lambda: page.locator('[data-prompt-card="persona.locality"] .chip.warn').count() > 0, 10000)
    row["edited_chip"] = page.locator('[data-prompt-card="persona.locality"] .chip.warn').count() > 0
    page.locator('[data-prompt-card="persona.locality"] [data-prompt-reset]').click()
    wait_for(page, lambda: page.locator('[data-prompt-card="persona.locality"] .chip.warn').count() == 0, 10000)
    row["reset_back"] = page.locator('[data-prompt-card="persona.locality"] textarea').input_value() == original
    if not (row["edited_chip"] and row["reset_back"]):
        problems.append("save/reset round trip")
    row["overflow"] = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")

    # 3. Search finds a prompt by its text.
    page.locator("#admin-q").fill("GST address")
    wait_for(page, lambda: page.locator('[data-open-prompt="persona.locality"]').count() > 0, 5000)
    row["search_finds_prompt"] = page.locator('[data-open-prompt="persona.locality"]').count() > 0
    row["search_open_buttons"] = page.locator("[data-open-prompt]").count()
    if row["search_finds_prompt"]:
        page.locator('[data-open-prompt="persona.locality"]').click()
        wait_for(page, lambda: page.locator('[data-prompt-card="persona.locality"][open]').count() > 0, 5000)
        row["search_opens_prompt"] = page.locator('[data-prompt-card="persona.locality"][open]').count() > 0
    if not (row["search_finds_prompt"] and row.get("search_opens_prompt")):
        problems.append("search did not open the prompt")
    if row["overflow"]:
        problems.append(f"overflow {row['overflow']} px")
    row["errors"] = errors[:5]
    if errors:
        problems.append("console errors")
    row["problems"] = problems
    browser.close()
print(json.dumps(row, ensure_ascii=False))
sys.exit(1 if problems else 0)

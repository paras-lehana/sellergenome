#!/usr/bin/env python3
"""Live QA of the 5.8 persona page on a public mirror: Open persona → the "how it is built" flow (APIs called live,
attributes) → the clean persona page (PM layout) → How this persona was built → Start call; search for a real GLID signed
out (never an invented profile) and signed in (confirmed identity).

Usage: qa_persona.py BASE OUT_DIR ENGINE WxH [REAL_GLID]
  REAL_GLID  an approved real GLID (e.g. the owner's); its persona screenshots show real data — keep them out of Git
Prints one JSON line; exit 1 if a check failed. Signed in with the server-side operator key (never printed).
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE, OUT, ENGINE, SIZE = sys.argv[1:5]
REAL = sys.argv[5] if len(sys.argv) > 5 else ""
W, H = (int(x) for x in SIZE.split("x"))
KEY = next(l.split("=", 1)[1].strip().strip("'\"") for l in open("/srv/sellergenome-v4/seller-persona-v4.env") if l.startswith("OPERATOR_API_KEY="))
TAG = f"{BASE.split('//')[-1].split('.')[1]}-persona-{ENGINE}-{W}"


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
    # 1. Signed out: a real GLID in search.
    if REAL:
        anon = browser.new_context(viewport={"width": W, "height": H}).new_page()
        anon.goto(BASE + "/", wait_until="domcontentloaded")
        if W <= 720 and anon.locator("#search-input").count() and not anon.locator("#search-input").is_visible():
            anon.locator("[data-act='search'], .search-toggle").first.click() if anon.locator("[data-act='search'], .search-toggle").count() else None
        anon.locator("#search-input").fill(REAL)
        wait_for(anon, lambda: anon.locator("#search-results button[data-i]").count() > 0, 15000)
        text = anon.locator("#search-results").inner_text()
        row["anon_search"] = " ".join(text.split())[:160]
        if "Khan" in text or "Real" not in text:
            problems.append("signed-out search for a real GLID shows an invented profile")

    ctx = browser.new_context(viewport={"width": W, "height": H}, extra_http_headers={"x-operator-key": KEY})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"{e} @ {page.url}"))
    page.on("console", lambda m: errors.append(f"{m.text} @ {m.location.get('url', '')}:{m.location.get('lineNumber', '')} on {page.url}") if m.type == "error" else None)

    # 2. Open persona from a simulated tile → the flow.
    page.goto(BASE + "/", wait_until="domcontentloaded")
    wait_for(page, lambda: page.locator("#tiles .tile:not(.real-seller) [data-persona]").count() > 0, 20000)
    page.locator("#tiles .tile:not(.real-seller) [data-persona]").first.click()
    wait_for(page, lambda: "/persona/" in page.url, 20000)
    wait_for(page, lambda: page.locator("#pf-apis .pf-api").count() > 2, 30000)
    row["flow_api_rows_live"] = page.locator("#pf-apis .pf-api").count()
    page.screenshot(path=f"{OUT}/{TAG}-1-flow.png")
    wait_for(page, lambda: page.locator(".persona-head").count() > 0, 90000)
    row["persona_h1"] = page.locator(".persona-head h1").inner_text() if page.locator(".persona-head").count() else None
    row["cards"] = {k: page.locator(sel).count() for k, sel in (("basics", ".pm-basics"), ("engagement", ".pm-eng"), ("buyers", ".pm-buyers"),
                                                                ("plan", ".pm-plan"), ("quality", ".pm-quality"), ("record", ".pm-recordlink"))}
    row["start_button"] = page.locator('[data-act="start"]').count() > 0
    row["persona_overflow"] = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    page.screenshot(path=f"{OUT}/{TAG}-2-persona.png", full_page=False)
    if not row["persona_h1"] or row["cards"]["quality"] or not all(row["cards"][k] for k in ("basics", "engagement", "buyers", "plan", "record")):
        problems.append("persona page layout")
    if not row["start_button"]:
        problems.append("no Start call on the persona page")

    # 3. How this persona was built.
    page.locator('[data-act="flow"]').click()
    wait_for(page, lambda: page.locator(".pf-attr tr").count() > 5, 20000)
    row["flow_attributes"] = page.locator(".pf-attr tr").count()
    row["flow_apis"] = page.locator("#pf-apis .pf-api").count()
    page.locator(".pf-attr").first.scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/{TAG}-3-built.png")
    page.locator("#pf-apis .pf-api").first.click()
    wait_for(page, lambda: page.locator("#inspector[aria-hidden='false']").count() > 0, 10000)
    row["inspector"] = page.locator("#inspector[aria-hidden='false']").count() > 0
    page.keyboard.press("Escape")
    if not (row["flow_attributes"] and row["flow_apis"] and row["inspector"]):
        problems.append("How this persona was built is incomplete")

    # 4. Back to the persona and Start call.
    page.locator('[data-act="persona"]').click()
    wait_for(page, lambda: page.locator('[data-act="start"]').count() > 0, 10000)
    page.locator('[data-act="start"]').click()
    wait_for(page, lambda: "/call/" in page.url, 15000)
    wait_for(page, lambda: page.locator("#barge").count() == 0 and page.locator("#transcript .bubble").count() > 0, 60000)
    if W <= 1180 and page.locator('[data-pane="call"]').count():
        page.locator('[data-pane="call"]').click()
    first = page.locator("#transcript .bubble").first
    row["call_first_turn_bot"] = first.count() > 0 and "bot" in (first.get_attribute("class") or "")
    row["call_talk_note"] = page.locator(".talk-note").count() > 0
    page.screenshot(path=f"{OUT}/{TAG}-4-call.png")
    if not (row["call_first_turn_bot"] and row["call_talk_note"]):
        problems.append("Start call did not open a call from scratch")

    # 5. The real seller (signed in): search identity and persona header.
    if REAL:
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.locator("#search-input").fill(REAL)
        wait_for(page, lambda: page.locator("#search-results button[data-i]").count() > 0, 15000)
        text = page.locator("#search-results").inner_text()
        row["op_search_identity_confirmed"] = "Private seller" not in text.split("\n")[0]
        page.keyboard.press("Escape")
        run = page.request.post(BASE + "/api/v5/runs", data={"seller_id": f"real-{REAL}", "origin": "persona"}).json()["run_id"]
        page.goto(f"{BASE}/persona/{run}", wait_until="domcontentloaded")
        wait_for(page, lambda: page.locator(".persona-head").count() > 0, 120000)
        h1 = page.locator(".persona-head h1").inner_text() if page.locator(".persona-head").count() else ""
        row["real_persona_placeholder"] = "Private seller" in h1 or "Khan" in h1
        page.screenshot(path=f"{OUT}/{TAG}-5-real-persona.png")
        if row["real_persona_placeholder"] or not row["op_search_identity_confirmed"]:
            problems.append("the real seller shows a placeholder or invented identity")
    if row.get("persona_overflow"):
        problems.append(f"persona overflow {row['persona_overflow']} px")
    row["errors"] = errors[:5]
    if errors:
        problems.append("console errors")
    row["problems"] = problems
    browser.close()
print(json.dumps(row, ensure_ascii=False))
sys.exit(1 if problems else 0)

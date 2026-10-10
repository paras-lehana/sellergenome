#!/usr/bin/env python3
"""Live QA of the 5.7 buyer side on a public mirror: buyer-desk tile buttons, Talk as Buyer (VANI opens, you are the
buyer) on the owner's requirement flow, the lead card, the VANI adapted banner, a text turn with the VANI agent on Sarvam,
the Admin VANI card and the /sarvam VANI card.

Usage: qa_buyer.py BASE OUT_DIR ENGINE WxH [--no-sarvam]
  BASE    https://sellergenome.lehana.in | https://sellergenome.aidhunik.com
  ENGINE  chromium | webkit        WxH  1440x900 | 390x844
  --no-sarvam  skip the Sarvam text turn (it spends one Sarvam chat turn)
Signed in with the server-side operator key (never printed). Buyer calls are synthetic. Prints one JSON line; exit 1 on a
failed check.
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE, OUT, ENGINE, SIZE = sys.argv[1:5]
SARVAM = "--no-sarvam" not in sys.argv
W, H = (int(x) for x in SIZE.split("x"))
KEY = next(l.split("=", 1)[1].strip().strip("'\"") for l in open("/srv/sellergenome-v4/seller-persona-v4.env") if l.startswith("OPERATOR_API_KEY="))
TAG = f"{BASE.split('//')[-1].split('.')[1]}-buyer-{ENGINE}-{W}"


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
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    # 1. Buyer desk tiles.
    page.goto(BASE + "/buyer", wait_until="domcontentloaded")
    wait_for(page, lambda: page.locator("#tiles .tile [data-talk]").count() > 0, 20000)
    tile = page.locator("#tiles .tile").filter(has=page.locator("[data-talk]")).first
    bb, tb = tile.locator("[data-barge]").bounding_box(), tile.locator("[data-talk]").bounding_box()
    row["tile_buttons"] = bool(bb and tb and (bb["x"] < tb["x"] or bb["y"] < tb["y"]))
    row["desk_overflow"] = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    page.screenshot(path=f"{OUT}/{TAG}-1-desk.png")
    if not row["tile_buttons"]:
        problems.append("Barge in is not left of Talk as Buyer")

    # 2. Talk as Buyer.
    tile.locator("[data-talk]").click()
    wait_for(page, lambda: "/buyer/call/" in page.url, 20000)
    wait_for(page, lambda: page.locator("#b-transcript .bubble").count() > 0, 20000)
    if W <= 1180 and page.locator('[data-pane="talk"]').count():
        page.locator('[data-pane="talk"]').click()
    first = page.locator("#b-transcript .bubble").first.inner_text()
    row["opening"] = first[:200]
    row["talk_note"] = page.locator(".talk-note").count() > 0
    if not ("VANI" in first and row["talk_note"]):
        problems.append("Talk as Buyer did not open with VANI's line")

    def say(text):
        bots = page.locator("#b-transcript .bubble.bot").count()
        page.locator("#b-input").fill(text)
        page.locator("#b-input").press("Enter")
        wait_for(page, lambda: page.locator("#b-transcript .bubble.bot").count() > bots, 60000)
        page.wait_for_timeout(400)
        return page.locator("#b-transcript .bubble.bot").last.inner_text()

    replies = [say("Haan ji, yahi chahiye."), say("Please speak English, I need fifty pieces.")]
    row["replies"] = [r.split("\n")[1 if r.startswith("VANI") else 0][:160] for r in replies]
    row["said_quantity"] = any("quantity" in r.lower() for r in replies)
    row["banner"] = page.locator(".switch-banner").inner_text()[:160] if page.locator(".switch-banner").count() else None
    page.screenshot(path=f"{OUT}/{TAG}-2-talk.png")
    if row["said_quantity"]:
        problems.append("VANI said 'quantity'")
    if not row["banner"]:
        problems.append("no VANI adapted banner after asking for English")
    if W <= 1180 and page.locator('[data-pane="lead"]').count():
        page.locator('[data-pane="lead"]').click()
        page.wait_for_timeout(300)
    lead = page.locator("#b-lead table").inner_text()
    row["lead_card"] = " | ".join(x.strip() for x in lead.split("\n") if x.strip())[:300]
    page.screenshot(path=f"{OUT}/{TAG}-3-lead.png")
    if "Number of pieces" not in lead:
        problems.append("the requirement lead card is missing")
    if W <= 1180 and page.locator('[data-pane="talk"]').count():
        page.locator('[data-pane="talk"]').click()

    # 3. VANI on Sarvam card (+ one text turn).
    card = page.locator(".agent-live")
    row["sarvam_card"] = card.count() > 0 and "Sarvam" in card.inner_text()
    row["call_button_enabled"] = card.locator('[data-act="va-call"]').is_enabled() if card.count() else False
    if SARVAM and card.count():
        card.locator("#va-input").fill("Haan ji, yahi chahiye.")
        card.locator("#va-input").press("Enter")
        wait_for(page, lambda: page.locator("#va-lines .bubble.bot").count() > 0, 60000)
        row["sarvam_reply"] = page.locator("#va-lines .bubble.bot").last.inner_text()[:200] if page.locator("#va-lines .bubble.bot").count() else None
        if not row["sarvam_reply"]:
            problems.append("no reply from VANI on Sarvam")
    card.scroll_into_view_if_needed() if card.count() else None
    page.screenshot(path=f"{OUT}/{TAG}-4-sarvam.png")
    row["call_overflow"] = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if not row["sarvam_card"]:
        problems.append("VANI on Sarvam card missing")

    # 4. Admin and /sarvam cards.
    page.goto(BASE + "/admin#sarvamvoice", wait_until="domcontentloaded")
    wait_for(page, lambda: page.locator("#sv-buyer-status dl").count() > 0, 30000)
    row["admin_card"] = page.locator("#sv-buyer-status").inner_text()[:200]
    page.locator("#sv-buyer").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/{TAG}-5-admin.png")
    page.goto(BASE + "/sarvam", wait_until="domcontentloaded")
    wait_for(page, lambda: page.get_by_text("VANI · buyer-requirement agent on Sarvam").count() > 0, 30000)
    row["sarvam_page_card"] = page.get_by_text("VANI · buyer-requirement agent on Sarvam").count() > 0
    if "VANI---" not in row["admin_card"] or not row["sarvam_page_card"]:
        problems.append("Admin or /sarvam VANI card missing")
    for k in ("desk_overflow", "call_overflow"):
        if row.get(k):
            problems.append(f"{k} {row[k]} px")
    row["errors"] = errors[:5]
    if errors:
        problems.append("console errors")
    row["problems"] = problems
    browser.close()
print(json.dumps(row, ensure_ascii=False))
sys.exit(1 if problems else 0)

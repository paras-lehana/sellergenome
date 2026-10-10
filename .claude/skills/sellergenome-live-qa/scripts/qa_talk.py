#!/usr/bin/env python3
"""Live QA of the 5.6 flows on a public mirror: the floor's tile buttons, Talk as Seller (call from scratch, bot opens,
you answer as the seller), the persona-switch banner, Admin search, and the anonymous floor (no real sellers).

Usage: qa_talk.py BASE OUT_DIR ENGINE WxH TARGET
  BASE    https://sellergenome.lehana.in | https://sellergenome.aidhunik.com
  ENGINE  chromium | webkit        WxH  1440x900 | 390x844
  TARGET  sim     → Talk as Seller on the first simulated tile (synthetic seller; screenshots may be shared)
          pinned  → Talk as Seller on the pinned real-seller tile (Admin → floor.pinned_real_sellers; screenshots
                    show real seller data — keep them in /root/data/qa-artifacts, never in Git)
Prints one JSON line; exit code 1 if a check failed. Signed in with the server-side operator key (never printed).
"""
import json
import re
import sys

from playwright.sync_api import sync_playwright

BASE, OUT, ENGINE, SIZE, TARGET = sys.argv[1:6]
W, H = (int(x) for x in SIZE.split("x"))
KEY = next(l.split("=", 1)[1].strip().strip("'\"") for l in open("/srv/sellergenome-v4/seller-persona-v4.env") if l.startswith("OPERATOR_API_KEY="))
HOST = BASE.split("//")[-1].split(".")[1]
TAG = f"{HOST}-talk-{ENGINE}-{W}-{TARGET}"
REPLY = "Haan boliye, par jaldi boliye, time nahi hai"


def wait_for(page, fn, timeout_ms=60000, step=250):
    """Poll from Python (the CSP has no unsafe-eval, so wait_for_function cannot run)."""
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

    # 1. Floor (signed in): button order, pinned real-seller tile, overflow.
    page.goto(BASE + "/", wait_until="domcontentloaded")
    wait_for(page, lambda: page.locator("#tiles .tile").count() > 0, 20000)
    page.wait_for_timeout(800)
    first = page.locator("#tiles .tile").first
    row["first_tile_real_seller"] = "real-seller" in (first.get_attribute("class") or "")
    sim = page.locator("#tiles .tile:not(.real-seller)").first
    barge, talk = sim.locator("[data-barge]"), sim.locator("[data-talk]")
    bb, tb = (barge.bounding_box() if barge.count() else None), (talk.bounding_box() if talk.count() else None)
    row["sim_tile_buttons"] = {"barge": bool(bb), "talk": bool(tb), "barge_left_of_talk": bool(bb and tb and (bb["x"] < tb["x"] or bb["y"] < tb["y"]))}
    real = page.locator("#tiles .tile.real-seller")
    row["real_tile"] = {"count": real.count(), "has_barge": real.first.locator("[data-barge]").count() if real.count() else None,
                        "has_wave": real.first.locator("canvas").count() if real.count() else None,
                        "chip": real.first.locator(".chip").first.inner_text() if real.count() else None}
    row["floor_overflow"] = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    page.screenshot(path=f"{OUT}/{TAG}-1-floor.png")
    if not row["sim_tile_buttons"]["barge_left_of_talk"]:
        problems.append("Barge in is not left of Talk as Seller")
    if TARGET == "pinned" and not row["real_tile"]["count"]:
        problems.append("no pinned real-seller tile for the operator")

    # 2. Talk as Seller → call from scratch with the bot's opening.
    button = (real.first if TARGET == "pinned" else sim).locator("[data-talk]")
    button.click()
    wait_for(page, lambda: "/call/" in page.url, 20000)
    wait_for(page, lambda: page.locator("#call-head .ident h1").count() > 0, 40000)
    wait_for(page, lambda: page.locator("#barge").count() == 0, 120000)
    page.wait_for_timeout(1500)
    if W <= 1180 and page.locator('[data-pane="call"]').count():
        page.locator('[data-pane="call"]').click()
        page.wait_for_timeout(300)
    bubbles = page.locator("#transcript .bubble")
    row["talk_note"] = page.locator("#p-call .talk-note").count() > 0
    row["first_turn"] = {"who": "bot" if bubbles.count() and "bot" in (bubbles.first.get_attribute("class") or "") else None,
                         "words": len(bubbles.first.inner_text().split()) if bubbles.count() else 0}
    row["auto_pressed"] = page.locator('[data-act="auto"]').get_attribute("aria-pressed") if page.locator('[data-act="auto"]').count() else None
    page.locator("#transcript").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/{TAG}-2-talk-opening.png")
    if not (row["talk_note"] and row["first_turn"]["who"] == "bot" and row["auto_pressed"] == "false"):
        problems.append("Talk as Seller did not start with the bot's opening and Auto off")

    # 3. Answer as a rushed seller → short reply + visible persona switch.
    bots_before = page.locator("#transcript .bubble.bot").count()
    page.locator("#say-input").fill(REPLY)
    page.locator("#say-input").press("Enter")
    wait_for(page, lambda: page.locator("#transcript .bubble.bot").count() > bots_before, 60000)
    page.wait_for_timeout(600)
    reply = page.locator("#transcript .bubble.bot").last.inner_text()
    reply_text = re.split(r"\n", reply.replace("VANI", "", 1).strip())[0]
    row["bot_reply_words"] = len(reply_text.split())
    row["switch_banner"] = page.locator("#p-call .switch-banner").count() > 0
    row["switch_lines"] = page.locator("#transcript .switchline").count()
    if row["switch_banner"]:
        row["switch_banner_text"] = page.locator("#p-call .switch-banner").inner_text()[:160]
        page.locator("#p-call .switch-banner").scroll_into_view_if_needed()
    row["call_overflow"] = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    page.screenshot(path=f"{OUT}/{TAG}-3-switch.png")
    page.locator("#transcript").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/{TAG}-4-transcript.png")
    if not (row["switch_banner"] and row["switch_lines"]):
        problems.append("no visible persona switch after a rushed reply")
    if row["bot_reply_words"] > 24:
        problems.append(f"bot reply too long ({row['bot_reply_words']} words)")

    # 4. Admin search.
    page.goto(BASE + "/admin", wait_until="domcontentloaded")
    wait_for(page, lambda: page.locator("#admin-q").count() > 0 and page.locator("#admin-body .card").count() > 0, 30000)
    found = {}
    for q in ("pinned", "gateway", "cooldown"):
        page.locator("#admin-q").fill(q)
        page.wait_for_timeout(500)
        found[q] = {"count": page.locator("#admin-q-count").inner_text(), "groups": page.locator("#admin-body .search-group").count(),
                    "fields": page.locator("#admin-body [name]").count()}
        if q == "pinned":
            page.screenshot(path=f"{OUT}/{TAG}-5-admin-search.png")
    row["admin_search"] = found
    row["admin_overflow"] = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    page.locator("#admin-q").press("Escape")
    if not all(v["groups"] for v in found.values()):
        problems.append("Admin search found nothing for one of pinned/gateway/cooldown")

    # 5. Anonymous floor: no real seller.
    anon = browser.new_context(viewport={"width": W, "height": H}).new_page()
    anon.goto(BASE + "/", wait_until="domcontentloaded")
    wait_for(anon, lambda: anon.locator("#tiles .tile").count() > 0, 20000)
    row["anon_real_tiles"] = anon.locator("#tiles .tile.real-seller").count()
    row["anon_tile_buttons"] = [anon.locator("#tiles .tile").first.locator(s).count() for s in ("[data-barge]", "[data-talk]")]
    anon.screenshot(path=f"{OUT}/{TAG}-6-anon-floor.png")
    if row["anon_real_tiles"]:
        problems.append("a real seller is visible to an anonymous visitor")
    for k in ("floor_overflow", "call_overflow", "admin_overflow"):
        if row.get(k):
            problems.append(f"{k} {row[k]} px")
    row["errors"] = errors[:5]
    if errors:
        problems.append("console errors")
    row["problems"] = problems
    browser.close()
print(json.dumps(row, ensure_ascii=False))
sys.exit(1 if problems else 0)

#!/usr/bin/env python3
"""Live QA of one call workspace on a public mirror, signed in with the server-side operator key.

Starts a run for SELLER_ID, waits for the barge-in to finish, then checks and screenshots: the architecture node
for the persona, the compact plan's "Why call", the teleprompter's first line, the Quality panel, the full
pre-call brief (header, products, source chips), a never-mention scan of the brief text, page overflow and console
errors. With --admin also opens Admin → Persona generator.

Usage: qa_call.py BASE OUT_DIR ENGINE WxH SELLER_ID [--admin]
  BASE      https://sellergenome.lehana.in | https://sellergenome.aidhunik.com
  ENGINE    chromium | webkit          WxH  1440x900 | 390x844
  SELLER_ID syn-91000105 (hero) | real-5678901 (approved GLID, signed-in only)
Prints one JSON line per page; exit code 1 if anything failed.
Requires Python Playwright (host has it); never run against private sellers anonymously.
"""
import json
import re
import sys

from playwright.sync_api import sync_playwright

BASE, OUT, ENGINE, SIZE, SID = sys.argv[1:6]
ADMIN = "--admin" in sys.argv
W, H = (int(x) for x in SIZE.split("x"))
KEY = next(l.split("=", 1)[1].strip().strip("'\"") for l in open("/srv/sellergenome-v4/seller-persona-v4.env") if l.startswith("OPERATOR_API_KEY="))
HOST = BASE.split("//")[-1].split(".")[1]   # lehana | aidhunik
TAG = f"{HOST}-call-{ENGINE}-{W}-{SID}"
NEVER = [("phone", r"(?<!\d)[6-9]\d{9}(?!\d)"), ("email", r"[\w.+-]+@[\w-]+\.[\w.]+"),
         ("exact amount", r"(?:₹|\bRs\.?|\bINR)\s*\d[\d,]*(?!\s*(?:-|–|to)\s*\d)"), ("refund/legal/fraud", r"\brefund|\bfraud|chargeback|legal (?:notice|case)"),
         ("first copy", r"first\s*copy"), ("he/she", r"\b(?:he|she)\b")]
# Turnover bands ("₹40 lakh – 1.5 crore", "40 L - 1.5 Cr", "Upto ₹40 lakh") are allowed (prompt + guide); exact amounts are not.
BAND = re.compile(r"(?:upto|up to|approx\.?|about|around)?\s*(?:₹|\bRs\.?|\bINR)?\s*\d[\d.,]*\s*(?:L|lakh|lac|cr|crore)?\s*(?:-|–|to)\s*(?:₹\s*)?\d[\d.,]*\s*(?:L|lakh|lac|cr|crore)\b|(?:upto|up to)\s*(?:₹|Rs\.?)\s*\d[\d.,]*\s*(?:L|lakh|lac|cr|crore)\b", re.I)
failed = False
with sync_playwright() as p:
    b = getattr(p, ENGINE).launch()
    ctx = b.new_context(viewport={"width": W, "height": H}, extra_http_headers={"x-operator-key": KEY})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(BASE + "/", wait_until="domcontentloaded")
    run = page.request.post(BASE + "/api/v5/runs", data={"seller_id": SID, "origin": "search"}).json()["run_id"]
    page.goto(f"{BASE}/call/{run}", wait_until="domcontentloaded")
    page.wait_for_selector("#call-head .ident h1", timeout=30000)
    for _ in range(360):  # the CSP forbids unsafe-eval, so poll from Python instead of wait_for_function
        if not page.locator("#barge").count():
            break
        page.wait_for_timeout(250)
    page.wait_for_timeout(2500)
    node = page.locator('#graph [data-node="sarvam.aggregate"]')
    node = node.get_attribute("aria-label") if node.count() else None
    small = W <= 1180
    if small:
        page.locator('[data-pane="persona"]').click(); page.wait_for_timeout(300)
    q = page.locator("#p-persona .pm-quality")
    quality = q.inner_text()[:300] if q.count() else None
    if q.count():
        q.scroll_into_view_if_needed(); page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/{TAG}-1-persona.png")
    if small:
        page.locator('[data-pane="call"]').click(); page.wait_for_timeout(300)
    why = page.locator("#p-call .pm-trigger")
    why = why.first.inner_text()[:200] if why.count() else None
    say = page.locator("#p-call .prompter")
    say = say.first.inner_text()[:260] if say.count() else None
    if page.locator("#p-call .pm-plan").count():
        page.locator("#p-call .pm-plan").first.scroll_into_view_if_needed(); page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/{TAG}-2-callpane.png")
    page.locator('#call-head [data-act="brief"]').click(); page.wait_for_timeout(900)
    text = page.locator("#brief-view").inner_text()
    head = page.locator("#brief-view .pm-full-head").inner_text()[:240]
    page.screenshot(path=f"{OUT}/{TAG}-3-brief.png")
    scan = BAND.sub(" ", text)
    hits = [label for label, rx in NEVER if re.search(rx, scan, re.I)]
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    row = {"tag": TAG, "node": node, "head": head, "why": why, "say": say, "quality": quality, "never_mention_hits": hits,
           "products": [t.strip() for t in page.locator("#brief-view .pm-kv .tagchip").all_inner_texts()],
           "chips": page.locator("#brief-view .ev-chip").count(), "overflow": over, "errors": errors[:5]}
    failed |= bool(hits or over > 0 or errors or "" in row["products"])
    print(json.dumps(row, ensure_ascii=False))
    if ADMIN:
        page.goto(BASE + "/admin#personagen", wait_until="domcontentloaded")
        page.wait_for_selector("#pg-out", timeout=20000); page.wait_for_timeout(800)
        page.screenshot(path=f"{OUT}/{HOST}-admin-{ENGINE}-{W}-personagen.png")
        eng = page.locator('select[name="persona.engine"]')
        arow = {"tag": f"{HOST}-admin-{ENGINE}-{W}", "engine": eng.input_value() if eng.count() else None,
                "last_eval": page.locator("#pg-out").inner_text()[:240], "overflow": page.evaluate("document.documentElement.scrollWidth - window.innerWidth"),
                "errors": errors[:5]}
        failed |= arow["overflow"] > 0
        print(json.dumps(arow, ensure_ascii=False))
    b.close()
sys.exit(1 if failed else 0)

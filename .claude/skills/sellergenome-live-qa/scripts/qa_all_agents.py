#!/usr/bin/env python3
"""Live QA of 5.10.2–5.10.3: every Sarvam workspace agent in the Agent dropdown; a real seller called through another
agent (Talk as Seller → choose AGENT → Call … live voice → "listening"); Engagement with IndiaMART insights open in place.

Usage: qa_all_agents.py BASE OUT_DIR ENGINE WxH SELLER_ID AGENT_NAME_PART [FAKE_MIC_WAV]
  e.g. qa_all_agents.py https://sellergenome.lehana.in out chromium 1440x900 real-23456789 Manjula mic.wav
Screenshots of a real seller show real data — keep them in qa-artifacts only. Prints one JSON line; exit 1 on a failure.
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE, OUT, ENGINE, SIZE, SELLER, AGENT = sys.argv[1:7]
WAV = sys.argv[7] if len(sys.argv) > 7 else ""
W, H = (int(x) for x in SIZE.split("x"))
KEY = next(l.split("=", 1)[1].strip().strip("'\"") for l in open("/srv/sellergenome-v4/seller-persona-v4.env") if l.startswith("OPERATOR_API_KEY="))
TAG = f"{BASE.split('//')[-1].split('.')[1]}-agents-{ENGINE}-{W}"


def wait_for(page, fn, timeout_ms=60000, step=200):
    for _ in range(timeout_ms // step):
        if fn():
            return True
        page.wait_for_timeout(step)
    return False


row: dict = {"tag": TAG, "seller": SELLER}
problems: list[str] = []
with sync_playwright() as p:
    args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", f"--use-file-for-fake-audio-capture={WAV}",
            "--autoplay-policy=no-user-gesture-required"] if WAV and ENGINE == "chromium" else []
    browser = getattr(p, ENGINE).launch(args=args)
    ctx = browser.new_context(viewport={"width": W, "height": H}, extra_http_headers={"x-operator-key": KEY},
                              permissions=["microphone"] if ENGINE == "chromium" else [])
    ctx.add_init_script("try { localStorage.removeItem('sg-engines'); localStorage.removeItem('sg-agent-app') } catch (e) {}")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"{e}"))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    run = page.request.post(BASE + "/api/v5/runs", data={"seller_id": SELLER, "origin": "talk"}).json()["run_id"]
    page.goto(f"{BASE}/call/{run}", wait_until="domcontentloaded")
    wait_for(page, lambda: page.locator("#call-head [data-agent-select]").count() > 0 and "ON CALL" in page.locator("#call-head .onair").inner_text(), 150000)
    opts = page.locator("#call-head [data-agent-select] option").all_inner_texts()
    row["agents_listed"] = len(opts)
    row["agent_names"] = opts
    if len(opts) < 3:
        problems.append("agent dropdown lists fewer than 3 agents")
    value = next((o for o in page.locator("#call-head [data-agent-select] option").evaluate_all("els => els.map(e => [e.value, e.textContent])") if AGENT.lower() in o[1].lower()), None)
    if not value:
        problems.append(f"no agent named like {AGENT}")
    else:
        page.locator("#call-head [data-agent-select]").select_option(value[0])
        wait_for(page, lambda: page.locator(".payal-live").count() > 0, 10000)
        row["agent_id_shown"] = page.locator("#call-head .agent-id").inner_text()
        row["panel_title"] = page.locator(".payal-live h3").inner_text() if page.locator(".payal-live").count() else None
        if value[0] not in row["agent_id_shown"] or AGENT.lower() not in (row["panel_title"] or "").lower():
            problems.append("chosen agent not shown")
        if WAV and ENGINE == "chromium":
            page.locator('[data-act="payal"]').click()
            wait_for(page, lambda: page.locator("#pl-state").inner_text() not in ("idle", "connecting"), 25000)
            row["call_state"] = page.locator("#pl-state").inner_text()
            page.wait_for_timeout(6000)
            row["lines"] = page.locator("#pl-lines .bubble").count()
            page.screenshot(path=f"{OUT}/{TAG}-1-call.png")
            if page.locator('[data-act="payal"]').count() and "End call" in page.locator('[data-act="payal"]').inner_text():
                page.locator('[data-act="payal"]').click()
            if row["call_state"] in ("idle", "connecting", "error", "ended"):
                problems.append(f"{AGENT} call did not connect ({row['call_state']})")
    # Engagement with IndiaMART: open an insight.
    page.goto(f"{BASE}/persona/{run}", wait_until="domcontentloaded")
    wait_for(page, lambda: page.locator(".pm-eng details.pm-ins").count() > 0, 60000)
    row["insights"] = page.locator(".pm-eng details.pm-ins:not(.gap)").count()
    if row["insights"]:
        page.locator(".pm-eng details.pm-ins:not(.gap) summary").first.click()
        wait_for(page, lambda: page.locator(".pm-eng details.pm-ins[open] .pm-ins-full").count() > 0, 5000)
        row["insight_open_text"] = page.locator(".pm-eng details.pm-ins[open] .pm-ins-full").inner_text()[:200]
        row["insight_records"] = page.locator(".pm-eng details.pm-ins[open] .pm-ins-recs li").count()
        page.locator(".pm-eng details.pm-ins[open]").scroll_into_view_if_needed()
        page.screenshot(path=f"{OUT}/{TAG}-2-insight.png")
    if not row.get("insight_open_text"):
        problems.append("insight did not open")
    row["overflow"] = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if row["overflow"]:
        problems.append(f"overflow {row['overflow']} px")
    row["errors"] = errors[:5]
    if errors:
        problems.append("console errors")
    row["problems"] = problems
    browser.close()
print(json.dumps(row, ensure_ascii=False))
sys.exit(1 if problems else 0)

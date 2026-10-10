#!/usr/bin/env python3
"""Live QA of 5.10.0: the running bar while Talk as Seller / Open persona / Talk as Buyer load, and the on-screen agent
picker (floor, call header with a mid-call switch, buyer desk, buyer call).

Usage: qa_agent_loading.py BASE OUT_DIR ENGINE WxH [FAKE_MIC_WAV]
  FAKE_MIC_WAV  Chromium only: a WAV played as the microphone, to test switching agents while Talk live is on
Prints one JSON line; exit 1 if a check failed. Signed in with the server-side operator key (never printed).
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE, OUT, ENGINE, SIZE = sys.argv[1:5]
WAV = sys.argv[5] if len(sys.argv) > 5 else ""
W, H = (int(x) for x in SIZE.split("x"))
KEY = next(l.split("=", 1)[1].strip().strip("'\"") for l in open("/srv/sellergenome-v4/seller-persona-v4.env") if l.startswith("OPERATOR_API_KEY="))
TAG = f"{BASE.split('//')[-1].split('.')[1]}-agent-{ENGINE}-{W}"


def wait_for(page, fn, timeout_ms=60000, step=150):
    for _ in range(timeout_ms // step):
        if fn():
            return True
        page.wait_for_timeout(step)
    return False


row: dict = {"tag": TAG}
problems: list[str] = []
bar_on = lambda p: p.locator("#top-progress.on").count() > 0  # noqa: E731
with sync_playwright() as p:
    args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", f"--use-file-for-fake-audio-capture={WAV}",
            "--autoplay-policy=no-user-gesture-required"] if WAV and ENGINE == "chromium" else []
    browser = getattr(p, ENGINE).launch(args=args)
    ctx = browser.new_context(viewport={"width": W, "height": H}, extra_http_headers={"x-operator-key": KEY},
                              permissions=["microphone"] if ENGINE == "chromium" else [])
    ctx.add_init_script("try { localStorage.removeItem('sg-engines') } catch (e) {}")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"{e}"))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    # 1. Floor: agent picker, then Talk as Seller with the running bar.
    page.goto(BASE + "/", wait_until="domcontentloaded")
    wait_for(page, lambda: page.locator("#floor-agent .agent-bar").count() > 0, 20000)
    row["floor_agent"] = page.locator("#floor-agent .agent-id").inner_text() if page.locator("#floor-agent .agent-id").count() else None
    page.locator('#floor-agent [data-agent="sarvam_agent"]').click()
    wait_for(page, lambda: "Payal---" in page.locator("#floor-agent .agent-id").inner_text(), 5000)
    row["floor_agent_sarvam"] = page.locator("#floor-agent .agent-id").inner_text()
    page.screenshot(path=f"{OUT}/{TAG}-1-floor-agent.png")
    page.locator('#floor-agent [data-agent="builtin"]').click()
    if not (row["floor_agent"] and "Payal---" in row["floor_agent_sarvam"]):
        problems.append("floor agent picker")
    wait_for(page, lambda: page.locator("#tiles .tile:not(.real-seller) [data-talk]").count() > 0, 20000)
    page.locator("#tiles .tile:not(.real-seller) [data-talk]").first.click()
    row["bar_on_talk"] = wait_for(page, lambda: bar_on(page), 3000, 50)
    row["bar_label"] = page.locator(".top-progress-label").inner_text() if row["bar_on_talk"] else None
    page.screenshot(path=f"{OUT}/{TAG}-2-loading.png")
    wait_for(page, lambda: "/call/" in page.url, 20000)
    wait_for(page, lambda: page.locator("#call-head .onair").count() > 0 and "ON CALL" in page.locator("#call-head .onair").inner_text(), 120000)
    wait_for(page, lambda: not bar_on(page), 5000)
    row["bar_off_after_call"] = not bar_on(page)
    if not (row["bar_on_talk"] and row["bar_off_after_call"]):
        problems.append("running bar on Talk as Seller")

    # 2. Call header: agent picker and a mid-call switch.
    wait_for(page, lambda: page.locator("#call-head .agent-bar").count() > 0, 15000)
    row["call_started"] = wait_for(page, lambda: page.locator("#transcript .bubble").count() > 0, 30000)   # the opening line
    row["call_agent"] = page.locator("#call-head .agent-id").inner_text() if page.locator("#call-head .agent-id").count() else None
    if WAV and ENGINE == "chromium":
        page.wait_for_timeout(3000)
        page.locator('[data-act="mic"]').click()
        wait_for(page, lambda: "Stop listening" in page.locator('[data-act="mic"]').inner_text(), 10000)
    page.locator('#call-head [data-agent="sarvam_agent"]').click()
    wait_for(page, lambda: page.locator(".payal-live").count() > 0, 10000)
    row["payal_panel_after_switch"] = page.locator(".payal-live").count() > 0
    row["call_agent_after"] = page.locator("#call-head .agent-id").inner_text()
    if WAV and ENGINE == "chromium":
        wait_for(page, lambda: page.locator("#pl-state").count() and page.locator("#pl-state").inner_text() not in ("idle", "connecting"), 20000)
        row["payal_state"] = page.locator("#pl-state").inner_text() if page.locator("#pl-state").count() else None
        row["mic_stopped"] = "Stop listening" not in page.locator('[data-act="mic"]').inner_text()
    page.screenshot(path=f"{OUT}/{TAG}-3-call-switch.png")
    if page.locator('[data-act="payal"]').count() and "End call" in page.locator('[data-act="payal"]').inner_text():
        page.locator('[data-act="payal"]').click()
        page.wait_for_timeout(1500)
    page.locator('#call-head [data-agent="builtin"]').click()
    page.wait_for_timeout(800)
    row["back_to_builtin"] = page.locator(".payal-live").count() == 0
    if not (row["call_agent"] and row["payal_panel_after_switch"] and "Payal---" in row["call_agent_after"] and row["back_to_builtin"]):
        problems.append("call agent switch")
    row["call_overflow"] = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")

    # 3. Open persona: bar during the flow, gone on the persona page.
    page.goto(BASE + "/", wait_until="domcontentloaded")
    wait_for(page, lambda: page.locator("#tiles .tile:not(.real-seller) [data-persona]").count() > 0, 20000)
    page.locator("#tiles .tile:not(.real-seller) [data-persona]").first.click()
    row["bar_on_persona"] = wait_for(page, lambda: bar_on(page), 3000, 50)
    wait_for(page, lambda: page.locator(".persona-head").count() > 0, 120000)
    wait_for(page, lambda: not bar_on(page), 5000)
    row["bar_off_persona"] = not bar_on(page)
    if not (row["bar_on_persona"] and row["bar_off_persona"]):
        problems.append("running bar on Open persona")

    # 4. Buyer desk and a buyer call.
    page.goto(BASE + "/buyer", wait_until="domcontentloaded")
    wait_for(page, lambda: page.locator("#floor-agent .agent-bar").count() > 0, 20000)
    row["buyer_agent"] = page.locator("#floor-agent .agent-bar").inner_text().replace("\n", " ")[:120]
    wait_for(page, lambda: page.locator("#tiles [data-talk]").count() > 0, 20000)
    page.locator("#tiles [data-talk]").first.click()
    row["bar_on_buyer"] = wait_for(page, lambda: bar_on(page), 3000, 50)
    wait_for(page, lambda: page.locator(".call-head .agent-bar").count() > 0, 30000)
    row["buyer_call_agent"] = page.locator(".call-head .agent-id").inner_text() if page.locator(".call-head .agent-id").count() else None
    page.locator('.call-head [data-agent="sarvam_agent"]').click()
    page.wait_for_timeout(800)
    row["buyer_call_agent_sarvam"] = page.locator(".call-head .agent-id").inner_text()
    page.screenshot(path=f"{OUT}/{TAG}-4-buyer-call.png")
    if page.locator('[data-act="va-call"]').count() and "End call" in page.locator('[data-act="va-call"]').inner_text():
        page.locator('[data-act="va-call"]').click()
    page.locator('.call-head [data-agent="builtin"]').click()
    if not ("VANI" in row["buyer_agent"] and row["buyer_call_agent"] and "VANI---" in row["buyer_call_agent_sarvam"]):
        problems.append("buyer agent picker")
    row["buyer_overflow"] = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    for k in ("call_overflow", "buyer_overflow"):
        if row[k]:
            problems.append(f"{k} {row[k]} px")
    row["errors"] = [e for e in errors if "favicon" not in e][:5]
    if row["errors"]:
        problems.append("console errors")
    row["problems"] = problems
    browser.close()
print(json.dumps(row, ensure_ascii=False))
sys.exit(1 if problems else 0)

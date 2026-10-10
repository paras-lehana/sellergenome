#!/usr/bin/env python3
"""Live QA of 5.13's Talk as Seller workspace: loading chips (orange → green), the four areas on one screen, Show more,
the side bar from a persona value / a log entry / an architecture box (with outside-click close), a call start that logs
the agent and the Elasticsearch write, and the phone stack.

Usage: qa_workspace.py BASE OUT_DIR ENGINE WxH SELLER [FAKE_MIC_WAV]
  SELLER  syn-… (simulated tile) or real-<glid> (screenshots show real data — keep them in qa-artifacts only)
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE, OUT, ENGINE, SIZE, SELLER = sys.argv[1:6]
WAV = sys.argv[6] if len(sys.argv) > 6 else ""
W, H = (int(x) for x in SIZE.split("x"))
KEY = next(l.split("=", 1)[1].strip().strip("'\"") for l in open("/srv/sellergenome-v4/seller-persona-v4.env") if l.startswith("OPERATOR_API_KEY="))
TAG = f"{BASE.split('//')[-1].split('.')[1]}-ws-{ENGINE}-{W}-{SELLER.split('-')[0]}"
VOICE = bool(WAV) and ENGINE == "chromium"


def wait_for(page, fn, timeout_ms=60000, step=150):
    for _ in range(timeout_ms // step):
        try:
            if fn():
                return True
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(step)
    return False


row: dict = {"tag": TAG}
problems: list[str] = []
with sync_playwright() as p:
    args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", f"--use-file-for-fake-audio-capture={WAV}", "--autoplay-policy=no-user-gesture-required"] if VOICE else []
    browser = getattr(p, ENGINE).launch(args=args)
    ctx = browser.new_context(viewport={"width": W, "height": H}, extra_http_headers={"x-operator-key": KEY}, permissions=["microphone"] if VOICE else [])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"{e}"))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(BASE + "/", wait_until="domcontentloaded")
    run = page.request.post(BASE + "/api/v5/runs", data={"seller_id": SELLER, "origin": "talk"}).json()["run_id"]
    page.goto(f"{BASE}/call/{run}?view=talk", wait_until="domcontentloaded")
    # 1. Loading: breathing API chips under the bar.
    row["loading_chips"] = wait_for(page, lambda: page.locator(".top-progress-apis .tp-api").count() > 2, 15000, 100)
    if row["loading_chips"]:
        row["chips_running"] = page.locator(".top-progress-apis .tp-api.run").count()
        page.screenshot(path=f"{OUT}/{TAG}-0-loading.png")
    wait_for(page, lambda: not page.locator("#w-main").is_disabled(), 150000)
    page.wait_for_timeout(1500)
    # 2. The four areas on one screen.
    row["areas"] = {k: page.locator(sel).count() for k, sel in (("persona", ".ws-left .ws-card"), ("call", ".ws-mid #w-main"), ("log", "#w-log .ws-log-item"), ("arch", "#w-arch .ws-node"))}
    boxes = page.evaluate("""() => ['.ws-left', '.ws-mid', '.ws-right', '.ws-arch'].map(s => { const r = document.querySelector(s).getBoundingClientRect(); return [s, Math.round(r.top), Math.round(r.bottom), Math.round(r.height)]; })""")
    row["one_screen"] = all(b[2] <= H + 2 for b in boxes) if W > 1100 else None
    row["arch_states"] = {st: page.locator(f"#w-arch .ws-node.{st}").count() for st in ("ok", "run", "err", "skip")}
    page.screenshot(path=f"{OUT}/{TAG}-1-ready.png")
    if not all(row["areas"].values()) or row["one_screen"] is False:
        problems.append(f"areas {row['areas']} / one screen {row['one_screen']} {boxes}")
    # 3. Show more.
    more = page.locator(".ws-more").first
    more.click()
    row["show_more"] = page.locator(".ws-card:not(.clamped)").count() > 0
    # 4. Side bar from a persona value (source dot / evidence chip), outside click closes it.
    src = page.locator(".ws-left [data-src]").first
    if src.count():
        src.click()
        row["sidebar_value"] = wait_for(page, lambda: page.locator("#inspector[aria-hidden='false']").count() > 0, 8000)
        page.wait_for_timeout(1500)
        row["sidebar_tab"] = page.locator("#inspector [aria-selected='true']").first.inner_text() if page.locator("#inspector [aria-selected='true']").count() else None
        page.screenshot(path=f"{OUT}/{TAG}-2-sidebar.png")
        if W > 800:
            page.mouse.click(120, H // 2)   # outside the side bar (it is 760 px wide at 1440)
        else:
            page.keyboard.press("Escape")   # on a phone the side bar is full width: Escape or its close button
        row["sidebar_closed_by_outside_click"] = wait_for(page, lambda: page.locator("#inspector[aria-hidden='true']").count() > 0, 5000)
    # 5. Log entry and architecture box open the side bar.
    page.locator("#w-log .ws-log-item button").first.click()
    row["sidebar_log"] = wait_for(page, lambda: page.locator("#inspector[aria-hidden='false']").count() > 0, 8000)
    page.keyboard.press("Escape")
    page.locator("#w-arch .ws-node[data-node]").first.click()
    row["sidebar_arch"] = wait_for(page, lambda: page.locator("#inspector[aria-hidden='false']").count() > 0, 8000)
    page.keyboard.press("Escape")
    if not (row.get("sidebar_value") and row.get("sidebar_closed_by_outside_click") and row["sidebar_log"] and row["sidebar_arch"] and row["show_more"]):
        problems.append("side bar / show more")
    # 6. Start a call: the log gets the agent and the Elasticsearch write.
    n0 = page.locator("#w-log .ws-log-item").count()
    page.locator("#w-main").click()
    wait_for(page, lambda: page.locator("#w-log .ws-log-item", has_text="Call started").count() > 0, 30000)
    wait_for(page, lambda: page.locator("#w-log .ws-log-item", has_text="Saved to Elasticsearch").count() > 0, 15000)
    row["log_call_started"] = page.locator("#w-log .ws-log-item", has_text="Call started").first.inner_text().split("\n")[1][:80] if page.locator("#w-log .ws-log-item", has_text="Call started").count() else None
    row["log_es_saved"] = page.locator("#w-log .ws-log-item", has_text="Saved to Elasticsearch").count() > 0
    row["new_flash"] = page.locator("#w-log .ws-log-item.new").count() > 0
    if VOICE:
        wait_for(page, lambda: page.locator("#w-lines .bubble").count() >= 3, 45000)
        row["lines"] = [b.replace("\n", " ")[:90] for b in page.locator("#w-lines .bubble").all_inner_texts()][:4]
    page.screenshot(path=f"{OUT}/{TAG}-3-call.png")
    row["log_added"] = page.locator("#w-log .ws-log-item").count() - n0
    if "End" in page.locator("#w-main").inner_text():
        page.locator("#w-main").click()
    if not (row["log_call_started"] and row["log_es_saved"]):
        problems.append("call not logged")
    row["overflow"] = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if row["overflow"]:
        problems.append(f"overflow {row['overflow']} px")
    row["errors"] = [e for e in errors if "favicon" not in e and not (not VOICE and ("OverconstrainedError" in e or "WebSocket" in e or "Microphone" in e))][:5]
    if row["errors"]:
        problems.append("console errors")
    row["problems"] = problems
    browser.close()
print(json.dumps(row, ensure_ascii=False))
sys.exit(1 if problems else 0)

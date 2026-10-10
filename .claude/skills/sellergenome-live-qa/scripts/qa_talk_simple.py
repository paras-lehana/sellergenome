#!/usr/bin/env python3
"""Live QA of 5.11.0's simple Talk as Seller page: floor → Talk as Seller → the chosen agent starts by itself (built-in
by default: opening, then a spoken turn), switch to AGENT mid-call → the built-in stops and exactly one Sarvam session
starts; then a REAL seller called with AGENT preselected → its start variables carry the real seller_context. Details
below are collapsed by default.

Usage: qa_talk_simple.py BASE OUT_DIR ENGINE WxH AGENT_APP_ID REAL_SELLER [FAKE_MIC_WAV]
Prints one JSON line; exit 1 if a check failed. Real-seller screenshots show real data — keep them in qa-artifacts only.
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE, OUT, ENGINE, SIZE, APP, REAL = sys.argv[1:7]
WAV = sys.argv[7] if len(sys.argv) > 7 else ""
W, H = (int(x) for x in SIZE.split("x"))
KEY = next(l.split("=", 1)[1].strip().strip("'\"") for l in open("/srv/sellergenome-v4/seller-persona-v4.env") if l.startswith("OPERATOR_API_KEY="))
TAG = f"{BASE.split('//')[-1].split('.')[1]}-talk-{ENGINE}-{W}"
VOICE = bool(WAV) and ENGINE == "chromium"


def wait_for(page, fn, timeout_ms=60000, step=200):
    for _ in range(timeout_ms // step):
        try:
            if fn():
                return True
        except Exception:  # noqa: BLE001 — element re-rendered
            pass
        page.wait_for_timeout(step)
    return False


row: dict = {"tag": TAG}
problems: list[str] = []
with sync_playwright() as p:
    args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", f"--use-file-for-fake-audio-capture={WAV}",
            "--autoplay-policy=no-user-gesture-required"] if VOICE else []
    browser = getattr(p, ENGINE).launch(args=args)
    ctx = browser.new_context(viewport={"width": W, "height": H}, extra_http_headers={"x-operator-key": KEY},
                              permissions=["microphone"] if VOICE else [])
    # Error toasts are page errors the console never sees ("Cannot read properties of null" after a switch, 5.11.1).
    ctx.add_init_script("""document.addEventListener('DOMContentLoaded', () => new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => {
      if (n.classList && n.classList.contains('err')) (window.__errToasts = window.__errToasts || []).push(n.textContent); })))
      .observe(document.body, { childList: true, subtree: true }));""")
    page = ctx.new_page()
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("() => { localStorage.removeItem('sg-engines'); localStorage.removeItem('sg-agent-app'); }")
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"{e}"))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    reqs = {"sarvam_sessions": 0, "listen_tickets": 0}
    page.on("request", lambda r: reqs.__setitem__("sarvam_sessions", reqs["sarvam_sessions"] + 1) if "/sarvam-live/sessions" in r.url and r.method == "POST"
            else reqs.__setitem__("listen_tickets", reqs["listen_tickets"] + 1) if "/listen/ticket" in r.url else None)
    starts: list[dict] = []
    page.on("response", lambda r: starts.append(r.json()) if "/sarvam-live/sessions" in r.url and r.request.method == "POST" and r.ok else None)

    # 1. Floor → Talk as Seller → the simple page; the built-in agent starts by itself.
    page.reload(wait_until="domcontentloaded")
    wait_for(page, lambda: page.locator("#tiles .tile:not(.real-seller) [data-talk]").count() > 0, 20000)
    page.locator("#tiles .tile:not(.real-seller) [data-talk]").first.click()
    wait_for(page, lambda: page.locator(".talk-call").count() > 0, 20000)
    row["simple_page"] = page.locator(".talk-call").count() > 0 and "view=talk" in page.url
    row["sections"] = page.locator("details.talk-sec").count() if wait_for(page, lambda: page.locator("details.talk-sec").count() > 0, 120000) else 0
    row["sections_open"] = page.locator("details.talk-sec[open]").count()
    wait_for(page, lambda: page.locator("#t-lines .bubble.bot").count() > 0, 60000)
    row["opening"] = page.locator("#t-lines .bubble.bot").first.inner_text()[:90] if page.locator("#t-lines .bubble.bot").count() else None
    row["state_after_start"] = page.locator("#t-state").inner_text()
    if VOICE:
        wait_for(page, lambda: page.locator("#t-lines .bubble.seller").count() > 0, 30000)
        wait_for(page, lambda: page.locator("#t-lines .bubble.bot").count() > 1, 30000)
        row["turns"] = [b.split("\n")[0][:60] for b in page.locator("#t-lines .bubble").all_inner_texts()][:4]
    page.screenshot(path=f"{OUT}/{TAG}-1-builtin.png")
    row["sarvam_sessions_before_switch"] = reqs["sarvam_sessions"]
    if not (row["simple_page"] and row["sections"] >= 3 and row["sections_open"] == 0 and row["opening"]):
        problems.append("simple page / auto-start / collapsed sections")
    if row["sarvam_sessions_before_switch"]:
        problems.append("a Sarvam session started while the built-in agent was chosen")

    # 2. Switch to AGENT mid-call → the built-in stops, exactly one Sarvam session.
    tickets_before = reqs["listen_tickets"]
    page.locator("#t-agent [data-agent-select]").select_option(APP)
    wait_for(page, lambda: reqs["sarvam_sessions"] >= 1, 15000)
    wait_for(page, lambda: page.locator("#t-state").inner_text() in ("Listening", "Speaking"), 30000)
    page.wait_for_timeout(5000)
    row["after_switch_state"] = page.locator("#t-state").inner_text()
    row["after_switch_agent"] = page.locator("#t-agent .agent-id").inner_text()[:80]
    row["after_switch_lines"] = [b.split("\n")[0][:80] for b in page.locator("#t-lines .bubble").all_inner_texts()][:3]
    row["sarvam_sessions_after_switch"] = reqs["sarvam_sessions"]
    row["listen_tickets_after_switch"] = reqs["listen_tickets"] - tickets_before
    page.screenshot(path=f"{OUT}/{TAG}-2-switched.png")
    if reqs["sarvam_sessions"] != 1 or row["listen_tickets_after_switch"]:
        problems.append(f"switch opened {reqs['sarvam_sessions']} Sarvam session(s) and {row['listen_tickets_after_switch']} built-in listener(s)")
    if VOICE and row["after_switch_state"] not in ("Listening", "Speaking"):
        problems.append(f"switched agent not live ({row['after_switch_state']})")
    page.locator("#t-main").click()
    wait_for(page, lambda: "Start" in page.locator("#t-main").inner_text(), 10000)
    row["ended"] = page.locator("#t-state").inner_text()

    # 3. A real seller with AGENT preselected: its variables carry the real seller context.
    run = page.request.post(BASE + "/api/v5/runs", data={"seller_id": REAL, "origin": "talk"}).json()["run_id"]
    starts.clear()
    page.goto(f"{BASE}/call/{run}?view=talk", wait_until="domcontentloaded")
    wait_for(page, lambda: not page.locator("#t-main").is_disabled(), 150000)
    page.locator("#t-main").click()
    wait_for(page, lambda: len(starts) > 0, 20000)
    v = ((starts[0] if starts else {}).get("start") or {}).get("agent_variables") or {}
    row["real_vars"] = sorted(v)
    row["real_context_chars"] = len(v.get("seller_context") or "")
    row["real_company_present"] = bool(v.get("company_name")) and bool(v.get("seller_context"))   # ours, not the agent's test defaults
    if VOICE:
        wait_for(page, lambda: page.locator("#t-lines .bubble.bot").count() > 0, 25000)
        row["real_opening"] = page.locator("#t-lines .bubble.bot").first.inner_text()[:100] if page.locator("#t-lines .bubble.bot").count() else None
    page.screenshot(path=f"{OUT}/{TAG}-3-real.png")
    if "End" in page.locator("#t-main").inner_text():
        page.locator("#t-main").click()
    if not (row["real_company_present"] and (row["real_context_chars"] > 50 or "seller_context" not in row["real_vars"])):
        problems.append("real seller context not sent")
    toasts = page.evaluate("window.__errToasts || []")
    row["error_toasts"] = [t for t in toasts if VOICE or not any(x in t for x in ("Microphone", "microphone", "Sarvam did not answer"))][:5]
    if row["error_toasts"]:
        problems.append("error toasts")
    row["overflow"] = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if row["overflow"]:
        problems.append(f"overflow {row['overflow']} px")
    # headless WebKit has no microphone device: the SDK's getUserMedia fails with OverconstrainedError (not a page bug)
    row["errors"] = [e for e in errors if "favicon" not in e and not (not VOICE and ("OverconstrainedError" in e or "WebSocket" in e)) and "ERR_NETWORK_CHANGED" not in e][:5]
    if row["errors"]:
        problems.append("console errors")
    row["problems"] = problems
    browser.close()
print(json.dumps(row, ensure_ascii=False))
sys.exit(1 if problems else 0)

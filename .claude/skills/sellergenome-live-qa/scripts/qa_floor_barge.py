#!/usr/bin/env python3
"""Anonymous (signed-out) path: open the floor, click the first *Barge in*, wait for the preparation to stream,
screenshot persona pane, compact plan, full brief and record drawer; report overflow, source-chip count and
console errors. This is what a judge or PM sees without logging in.

Usage: qa_floor_barge.py BASE OUT_DIR ENGINE WxH      e.g. qa_floor_barge.py https://sellergenome.aidhunik.com /tmp/qa webkit 390x844
"""
import sys, json
from playwright.sync_api import sync_playwright
BASE, OUT, engine, size = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
W, H = (int(x) for x in size.split('x')); tag = f"{BASE.split('//')[-1].split('.')[1]}-floor-{engine}-{W}"
with sync_playwright() as p:
    b = getattr(p, engine).launch(); pg = b.new_page(viewport={'width': W, 'height': H})
    errs = []; pg.on('pageerror', lambda e: errs.append(str(e))); pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
    pg.goto(BASE + '/', wait_until='networkidle'); pg.locator('[data-barge]').first.click(); pg.wait_for_url('**/call/**')
    pg.wait_for_selector('#call-head .ident h1', timeout=20000)
    for _ in range(240):
        if not pg.locator('#barge').count(): break
        pg.wait_for_timeout(250)
    pg.wait_for_timeout(3000)
    if W <= 1180: pg.locator('[data-pane="persona"]').click(); pg.wait_for_timeout(300)
    pg.screenshot(path=f'{OUT}/{tag}-1-persona.png')
    if W <= 1180: pg.locator('[data-pane="call"]').click(); pg.wait_for_timeout(300)
    pg.locator('#p-call .pm-plan').scroll_into_view_if_needed(); pg.screenshot(path=f'{OUT}/{tag}-2-plan.png')
    pg.locator('#call-head [data-act="brief"]').click(); pg.wait_for_timeout(800); pg.screenshot(path=f'{OUT}/{tag}-3-full.png')
    over = pg.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    pg.locator('#brief-view [data-record]').first.click(); pg.wait_for_timeout(600); pg.screenshot(path=f'{OUT}/{tag}-4-record.png')
    chips = pg.locator('#brief-view .ev-chip').count()
    print(json.dumps({'tag': tag, 'overflow': over, 'chips': chips, 'errors': errs[:4]}))
    b.close()
sys.exit(1 if (over > 0 or errs) else 0)

"""Authenticated V4 browser acceptance; synthetic actions and real ES readback.

Passwords are read privately. Screenshots contain only synthetic scenario data.
Provider spending is excluded; separate integration probes record that evidence.
"""
import argparse,json,time,re
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from scripts.configure_research_access import read_env


def case(playwright,base,engine,width,password,out):
    expect.set_options(timeout=15000)  # Public two-proxy round trips; backend budgets remain separate.
    browser=getattr(playwright,engine).launch()
    context=browser.new_context(viewport={'width':width,'height':900 if width>600 else 844},is_mobile=width<600,has_touch=width<600,reduced_motion='reduce')
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    result={'url':base,'engine':engine,'width':width,'checks':[]};prefix=f'{engine}-{width}-{base.split("//")[1].replace(":","_")}'
    def shot(name):page.screenshot(path=str(out/(prefix+'-'+name+'.png')),full_page=False)
    def close():page.get_by_role('button',name='Close inspector').click()
    try:
        page.goto(base+'/',wait_until='networkidle');expect(page.locator('.activity').first).to_be_visible()
        assert context.request.get(base+'/api/operations/runs').status==401
        shot('dashboard')
        page.get_by_role('button',name='Sign in',exact=True).click();page.get_by_label('Password',exact=True).fill(password);page.get_by_role('button',name='Sign in securely').click()
        expect(page.locator('#access')).to_have_text('Sign out')
        page.locator('.activity').first.click();page.locator('#prepare-form button').click();expect(page.locator('.factor').first).to_be_visible(timeout=60000)
        expect(page.locator('[data-act=prompt]')).to_be_visible();shot('workspace')
        run_id=page.url.split('#')[-1]
        run=context.request.get(base+'/api/operations/runs/'+run_id).json()
        assert run['status']=='completed'
        assert all(x['mode']=='sample' for x in run['nodes'] if x.get('mode'))
        result['checks'].append('Authenticated feed click executes one sample preparation; actual trace nodes and factors render')
        if width<600:page.locator('[data-panel-select=architecture]').click()
        page.locator('[data-node]').nth(1).click();page.locator('[data-inspect-tab=Response]').click();expect(page.locator('#node-code')).to_contain_text('glid');shot('inspector');close()
        if width<600:page.locator('[data-panel-select=persona]').click()
        page.locator('.factor').filter(has_text='warmth').first.click();page.locator('#factor-form [name=value]').fill('warm');page.locator('#factor-form [name=author]').fill('Browser QA');page.locator('#factor-form button').click()
        expect(page.locator('#inspector')).not_to_be_visible()
        refreshed=context.request.get(base+'/api/operations/runs/'+run_id).json();assert refreshed['layers']['established']==run['layers']['established']
        result['checks'].append('Factor correction persisted incidentally without rewriting established history')
        suggestion=page.locator('[data-panel=persona] .suggestion');suggestion.locator('summary').click();suggestion.locator('textarea').fill('QA: keep evidence origin and retrieval location visible.')
        context.set_offline(True);suggestion.locator('button').click();expect(page.locator('#notice')).to_contain_text(re.compile('fetch|network|load',re.I));expect(suggestion.locator('button')).to_be_enabled();assert suggestion.locator('textarea').input_value();context.set_offline(False)
        suggestion.locator('button').click();expect(suggestion.locator('[role=status]')).to_have_text('Saved',timeout=15000);result['checks'].append('Offline suggestion retained its draft; server acknowledged the recovered save')
        page.locator('[data-act=buyer]').click();page.locator('#buyer-start [name=language]').select_option('en-IN');page.locator('#buyer-start [name=product]').fill('Packaging machine');page.locator('#buyer-start button').click()
        # Expose consent/tool ordering races without depending on network speed.
        def delayed_turn(route):
            page.wait_for_timeout(400)
            route.continue_()
        page.route('**/api/buyer/conversations/*/turns',delayed_turn)
        for answer in ['yes','10 to 20 boxes','QA Buyer','Pune','Maharashtra']:
            page.locator('#buyer-turn input').fill(answer);page.locator('#buyer-turn button').click();expect(page.locator('#buyer-turn input')).to_have_value('')
        page.locator('[data-buyer-tool=submit_lead]').click();expect(page.locator('#inspector-content')).to_contain_text('sample accepted')
        page.locator('[data-buyer-tool=seller_lookup]').click();expect(page.locator('.teleprompter').last).to_contain_text('connect')
        page.locator('#buyer-turn input').fill('yes');page.locator('#buyer-turn button').click();page.locator('[data-buyer-tool=transfer]').click();expect(page.locator('#inspector-content')).to_contain_text('succeeded');shot('buyer');close()
        result['checks'].append('Buyer confirmation, range capture, test submission, availability, consent and single transfer termination')
        page.locator('[data-act=meeting]').first.click();page.locator('#meeting-form button').click();expect(page.locator('#inspector-content')).to_contain_text('proposed');close()
        page.goto(base+'/meetings',wait_until='networkidle');page.locator('[data-meeting-confirm]').first.click();page.locator('[data-meeting-book]').first.click();expect(page.locator('#inspector-content')).to_contain_text('synthetic_fixture');close();result['checks'].append('Test meeting separates proposal, confirmation and synthetic booking receipt')
        page.goto(base+'/operations#'+run_id,wait_until='networkidle');expect(page.locator('.factor').first).to_be_visible()
        if width<600:page.locator('[data-panel-select=conversation]').click()
        page.locator('[data-act=rehearsal]').click();expect(page.locator('h1')).to_contain_text('Listen. Understand. Adapt.');assert page.locator('#start-session').count()==0
        result['checks'].append('V4 effective persona connects to an owned existing speech rehearsal')
        page.goto(base+'/operations#factors',wait_until='networkidle');expect(page.locator('h1')).to_contain_text('Search the persona');expect(page.locator('.factor-grid .card').first).to_be_visible();shot('search')
        page.goto(base+'/admin',wait_until='networkidle');expect(page.locator('#credential-form')).to_be_visible();assert page.locator('.config-item').count()>200;result['checks'].append('Real Elasticsearch factor search and complete Admin schema/write-only credential form')
        page.goto(base+'/sarvam',wait_until='networkidle');expect(page.locator('.tool-card')).to_have_count(36);page.locator('[data-tool-demo]').first.click();expect(page.locator('#inspector-content')).to_contain_text('provider_called');close()
        page.goto(base+'/#speech',wait_until='networkidle');expect(page.locator('h1')).to_contain_text('Hear the difference');assert '/studio' in page.url;result['checks'].append('36 MCP tools visible; Demo receipt is spend-free; old root bookmark reaches Recording Studio')
        page.goto(base+'/',wait_until='networkidle');page.locator('#theme').click();shot('light');assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
        page.keyboard.press('Tab');assert page.locator(':focus').count()==1
        assert not page.evaluate("Object.keys(localStorage).some(k=>/password|operator.*key|token/i.test(k))")
        page.locator('#access').click();expect(page.locator('#access')).to_have_text('Sign in');assert context.request.get(base+'/api/operations/runs').status==401
        assert not errors,errors
        result.update(status='passed',errors=errors)
    except Exception as e:
        result.update(status='failed',error=str(e)[:1800],errors=errors);shot('failure')
    finally:context.close();browser.close()
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--urls',nargs='+',default=['http://127.0.0.1:18080']);parser.add_argument('--engines',nargs='+',default=['chromium','webkit']);parser.add_argument('--widths',nargs='+',type=int,default=[1440,390]);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--qa-env',type=Path,default=Path('/opt/secrets/env_files/seller-persona-v3-qa.env'));args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True);password=read_env(args.qa_env)['PM_PASSWORD'];results=[]
    with sync_playwright() as p:
        for url in args.urls:
            for engine in args.engines:
                for width in args.widths:
                    result=case(p,url,engine,width,password,args.out);results.append(result);print(json.dumps(result),flush=True)
    (args.out/'results.json').write_text(json.dumps(results,indent=2));raise SystemExit(1 if any(x['status']=='failed' for x in results) else 0)
if __name__=='__main__':main()

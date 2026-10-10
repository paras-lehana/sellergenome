"""Import V4 operational charts alongside existing corpus/PM dashboards."""
import argparse
import json
from pathlib import Path
import httpx
from scripts.install_research_dashboards import vega,dashboard
from scripts.configure_research_access import read_env


def objects(prefix):
    """Use real indexed events and explicit denominators; no fabricated activity."""
    charts=[]
    definitions=[
        ('Source results by adapter','traces','source',{'terms':{'status':['completed','sample_replayed']}},None),
        ('Cache decisions by retrieval location','traces','cache',{'exists':{'field':'cache'}},None),
        ('Preparation latency by mode (mean milliseconds)','traces','mode',{'bool':{'filter':[{'term':{'node':'run'}},{'term':{'status':'completed'}}]}},'duration_ms'),
        ('Incidental influence by factor','factors','factor',None,'influence'),
        ('Effective factor evidence strength','factors','strength',None,None),
        ('Conflicting factor revisions','factors','factor',{'term':{'contradiction':True}},None),
        ('Missing context fields across snapshots','snapshots','metadata.missing_fields',None,None),
        ('Buyer collection states','buyer_states','status',None,None),
        ('Test meeting outcomes','actions','status',None,None),
        ('Run modes: separate denominators','traces','mode',{'bool':{'filter':[{'term':{'node':'run'}},{'term':{'status':'completed'}}]}},None),
    ]
    for title,kind,field,query,metric in definitions:
        group={'terms':{'field':field,'size':30}}
        if metric:group['aggs']={'total':{'avg':{'field':metric}}}
        chart=vega('V4 · '+title,prefix+'-'+kind,group,query=query)
        state=json.loads(chart['attributes']['visState'])
        spec=json.loads(state['params']['spec'])
        spec['encoding']['x']['axis']['title']=field.replace('metadata.','').replace('_',' ').title()
        spec['encoding']['y']['axis']['title']='Mean milliseconds' if metric=='duration_ms' else 'Mean incidental influence (0–1)' if metric=='influence' else 'Document / revision count'
        if field!='mode':
            modes={'terms':{'field':'mode','missing':'unclassified','size':8}}
            if metric:modes['aggs']={'total':{'avg':{'field':metric}}}
            spec['data']['url']['body']['aggs']['groups']['aggs']={'by_mode':modes}
            spec['transform']=[{'flatten':['by_mode.buckets'],'as':['execution']}]
            spec['encoding']['y']['field']='execution.total.value' if metric else 'execution.doc_count'
            spec['encoding']['xOffset']={'field':'execution.key','type':'nominal'}
            spec['encoding']['color']={'field':'execution.key','type':'nominal','title':'Execution mode','legend':{'orient':'top'}}
            spec['mark'].pop('color',None)
        state['params']['spec']=json.dumps(spec)
        chart['attributes']['visState']=json.dumps(state)
        chart['attributes']['description']='Actual V4 projection; colors separate Sample, Local, Live and historical modes. Counts are documents/revisions, not unique sellers or causal outcomes. Panels query the complete retained V4 cohort, independently of the dashboard time picker.'
        charts.append(chart)
    board=dashboard('sg-v4-operations','SellerGenome · V4 operations and persona influence',charts)
    board['attributes'].update(description='Operational evidence from actual V4 aliases. Sample, local history and live modes are distinct. Each panel labels execution modes; panels show the retained cohort, not the global time picker. Factor charts count revisions; missingness counts source snapshots. Empty panels mean no matching execution, not integration failure.',timeFrom='now-30d',timeTo='now')
    views=[{'id':'sg-v4-'+kind,'type':'index-pattern','attributes':{'title':prefix+'-'+kind,'name':'V4 '+kind,'timeFieldName':'at'},'references':[]} for kind in ['snapshots','established','observations','factors','traces','actions','buyer_states']]
    return [*views,*charts,board]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kibana',default='http://127.0.0.1:5602/s/sellergenome')
    parser.add_argument('--prefix',default='sellergenome-v4-preview')
    parser.add_argument('--infra-env',type=Path,default=Path('/opt/secrets/env_files/seller-persona-infra.env'))
    parser.add_argument('--output',type=Path,default=Path('/root/data/qa-artifacts/2026-10-09-sellergenome-v4/kibana-v4.ndjson'))
    args=parser.parse_args();items=objects(args.prefix)
    raw='\n'.join(json.dumps(x) for x in items)+'\n';args.output.write_text(raw)
    private=read_env(args.infra_env)
    with httpx.Client(base_url=args.kibana,auth=('elastic',private['ELASTIC_PASSWORD']),headers={'kbn-xsrf':'v4-import'},timeout=60) as client:
        response=client.post('/api/saved_objects/_import',params={'overwrite':'true'},files={'file':('v4.ndjson',raw,'application/ndjson')})
        response.raise_for_status();result=response.json()
        if not result.get('success') or result.get('successCount')!=len(items):raise RuntimeError('V4 dashboard import incomplete.')
        client.get('/api/saved_objects/dashboard/sg-v4-operations').raise_for_status()
    print(json.dumps({'status':'verified','data_views':7,'charts':10,'dashboards':1,'prefix':args.prefix}))

if __name__=='__main__':main()

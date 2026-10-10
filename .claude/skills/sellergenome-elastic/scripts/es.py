"""Read-only Elasticsearch helper that runs INSIDE the live container (it holds the credentials as env vars;
nothing is printed but results). Pipe it in:

  docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - indices               < es.py
  docker exec -i … python - mapping sellergenome-v5-personas                                 < es.py
  docker exec -i … python - count   sellergenome-v5-persona-evals ['{"term":{"kind":"edge"}}'] < es.py
  docker exec -i … python - search  sellergenome-v5-personas '{"size":3,"query":{"term":{"call_plan_trigger":"open_request"}}}' < es.py
  docker exec -i … python - agg     sellergenome-v5-persona-evals eval_id score               < es.py

Only GET/_search/_count/_mapping/_cat are issued — writes go through the app (elastic.queue) so mappings stay strict.
"""
import json
import os
import sys

import httpx

url = os.environ["ELASTICSEARCH_URL"].rstrip("/")
auth = (os.environ.get("ELASTICSEARCH_USERNAME", ""), os.environ.get("ELASTICSEARCH_PASSWORD", ""))
cmd, args = (sys.argv[1] if len(sys.argv) > 1 else "indices"), sys.argv[2:]
with httpx.Client(verify=False, timeout=20, auth=auth if auth[0] else None) as c:
    if cmd == "indices":
        r = c.get(f"{url}/_cat/indices/{args[0] if args else 'sellergenome-*,seller-persona-*'}?h=index,docs.count,store.size&s=index&format=json")
        for i in r.json():
            print(f"{i['index']:60} {str(i.get('docs.count')):>9} {str(i.get('store.size')):>9}")
        aliases = c.get(f"{url}/_cat/aliases/{args[0] if args else 'sellergenome-*'}?h=alias,index&format=json").json()
        for a in aliases:
            print(f"  alias {a['alias']} → {a['index']}")
    elif cmd == "mapping":
        props = list(c.get(f"{url}/{args[0]}/_mapping").json().values())[0]["mappings"]
        print("dynamic:", props.get("dynamic"))
        for k, v in sorted(props.get("properties", {}).items()):
            print(f"  {k:28} {v.get('type', 'object')}{'' if v.get('enabled', True) else ' (not indexed)'}")
    elif cmd == "count":
        q = {"query": json.loads(args[1])} if len(args) > 1 else {}
        print(c.post(f"{url}/{args[0]}/_count", json=q).json().get("count"))
    elif cmd == "search":
        r = c.post(f"{url}/{args[0]}/_search", json=json.loads(args[1]) if len(args) > 1 else {"size": 5})
        body = r.json()
        print("total", body.get("hits", {}).get("total"))
        for h in body.get("hits", {}).get("hits", []):
            print(json.dumps(h["_source"], ensure_ascii=False, default=str)[:600])
        if body.get("aggregations"):
            print(json.dumps(body["aggregations"], ensure_ascii=False)[:2000])
    elif cmd == "agg":
        index, term, metric = args[0], args[1], (args[2] if len(args) > 2 else None)
        aggs = {"t": {"terms": {"field": term, "size": 25}}}
        if metric:
            aggs["t"]["aggs"] = {"m": {"avg": {"field": metric}}}
        body = c.post(f"{url}/{index}/_search", json={"size": 0, "aggs": aggs}).json()
        for b in body["aggregations"]["t"]["buckets"]:
            print(f"  {str(b['key']):40} {b['doc_count']:>7}" + (f"  avg {metric} {b['m']['value']:.1f}" if metric and b['m']['value'] is not None else ""))
    else:
        sys.exit(f"unknown command {cmd}")

"""Run the persona-generator scenario suite live (Sarvam via llm.lehana.in) and write a JSON report.

    docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - [out.json] [name filters…] < scripts/persona_eval.py

To test an unmerged worktree, run the image with the app container's network and env files and the worktree mounted
read-only at /work (PYTHONPATH=/work), as in AGENTS.md → Known Operational Notes.

Cases: 14 synthetic heroes, the edge scenarios in backend/genome/persona_eval.py, and the approved real sellers whose
live responses are cached in Elasticsearch (read-only; nothing is requested upstream).
"""
import asyncio
import json
import sys
from datetime import date, datetime

from backend.genome import persona_eval as pe
from backend.genome.sarvam import SarvamClient
from backend.genome.settings import GenomeSettings
import backend.genome.elastic as E

REAL = ["5678901", "45678901"]


class NoStore:  # budget and hit log are not needed for an offline evaluation run
    def take_budget(self, *a): return True
    def add_hit(self, *a, **k): pass
    def budget_used(self, *a): return 0


async def real_cases(settings) -> list[dict]:
    cls = next(getattr(E, n) for n in dir(E) if isinstance(getattr(E, n), type) and hasattr(getattr(E, n), "raw_search"))
    es = cls(settings, None)
    out = []
    for glid in REAL:
        results = {}
        for api in ("company.detail", "sts.history", "pns.insights", "enquiry.list", "context.global"):
            body = {"size": 1, "sort": [{"at": "desc"}], "query": {"bool": {"filter": [{"term": {"origin": "live_api"}}, {"term": {"api": api}},
                                                                                      {"term": {"glid": glid}}, {"term": {"status": "ok"}}]}}}
            try:
                r, _ = await es.search("api-responses", body)
            except Exception:  # noqa: BLE001
                continue
            hits = r.get("hits", {}).get("hits", [])
            if hits:
                results[api] = {"status": "ok", "origin": "live_api", "response": hits[0]["_source"]["response"]}
        if not results:
            continue
        cd = ((results.get("company.detail") or {}).get("response") or {}).get("DATA", {}).get("COMPANYDETAIL", {})
        seller = {"glid": glid, "company_name": cd.get("DIR_SEARCH_COMPANY"), "contact_name": cd.get("DIR_SEARCH_CONTACTPERSON"),
                  "city": cd.get("DIR_SEARCH_CITY"), "state": cd.get("DIR_SEARCH_STATE")}
        out.append({"name": f"Real · GLID {glid} (cached live data)", "kind": "real", "seller": seller, "results": results, "lead": {}, "expect": []})
    return out


async def main():
    settings = GenomeSettings()
    today = date(2026, 10, 10)
    cases = pe.scenarios(today) + await real_cases(settings)
    only = [a for a in sys.argv[2:]]
    if only:
        cases = [c for c in cases if any(o.lower() in c["name"].lower() for o in only)]
    sarvam = SarvamClient(settings, NoStore())
    rows = await pe.run(sarvam, cases, today, on_row=lambda r: print(json.dumps({k: r.get(k) for k in ("name", "score", "critical", "warnings", "scenario_ok", "attempts",
                                                                                                            "ms", "expected", "plan", "error")}, ensure_ascii=False), flush=True))
    report = {"at": datetime.now().isoformat(timespec="seconds"), "summary": pe.summary(rows), "rows": rows}
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/persona-eval.json"
    json.dump(report, open(path, "w"), ensure_ascii=False, indent=1, default=str)
    print("SUMMARY", json.dumps(report["summary"], ensure_ascii=False), flush=True)

asyncio.run(main())

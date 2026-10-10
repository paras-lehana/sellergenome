#!/usr/bin/env python3
"""Print an evaluation report (scripts/persona_eval.py output or an Admin eval document) as a compact table.

Usage: summarize.py REPORT.json [PREVIOUS.json]   — with a previous report, prints the per-case score change.
"""
import json
import sys


def load(path: str) -> dict:
    d = json.load(open(path))
    if "last_eval" in d:  # GET /api/v5/admin/persona-generator → {"last_eval": {...}}
        d = d["last_eval"] or {}
    return {"rows": d.get("rows", []), "summary": d.get("summary") or {}}


cur = load(sys.argv[1])
prev = {r["name"]: r for r in load(sys.argv[2])["rows"]} if len(sys.argv) > 2 else {}
s = cur.get("summary") or {}
print(f"cases {s.get('cases')} · delivered {s.get('mean_score')} · model {s.get('mean_raw_score')} · no critical {s.get('no_critical')}/{s.get('cases')}"
      f" · repaired {s.get('repaired')} · scenario {s.get('scenario_checks')} · plan right {s.get('plan_matches')} · median {(s.get('median_ms') or 0) / 1000:.1f}s")
for r in cur["rows"]:
    if "error" in r and r["error"]:
        print(f"  ERROR  {r['name'][:56]:56} {r['error'][:80]}")
        continue
    delta = f" ({r['score'] - prev[r['name']]['score']:+})" if r["name"] in prev and "score" in prev[r["name"]] else ""
    exp = (r.get("expected") or [None])[0]
    flag = "" if (r.get("plan") or None) == exp else f"  plan {r.get('plan')} ≠ {exp}"
    fails = [c["check"] if isinstance(c, dict) else c for c in r.get("failed") or []]
    sc = [c["check"] for c in r.get("scenario_checks") or [] if not c["ok"]]
    print(f"  {r['score']:>3}{delta:6} raw {str(r.get('raw_score')):>4} a{r.get('attempts')} {r['name'][:56]:56}{flag}"
          + (f"  ✗ {'; '.join(fails)[:110]}" if fails else "") + (f"  scenario ✗ {sc}" if sc else ""))
for title, key in (("top failures (delivered)", "top_failures"), ("top failures (model's own reply)", "top_raw_failures")):
    if s.get(key):
        print(f"{title}: " + " · ".join(f"{c} ({n})" for c, n in s[key]))

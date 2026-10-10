"""Scenario suite for the persona generator (5.5) — "how good is it" in numbers.

Each scenario is a seller plus API results (production-shaped mocks, edited to create the situation the prompt's rules
talk about) and expectations specific to it. ``run`` generates a persona per scenario through Sarvam, applies the general
checks (``persona_generator.evaluate``) and the scenario's own, and returns one row per scenario. Used by Admin → Persona
generator and ``scripts/persona_eval.py``. Real sellers are added from cached live responses when available.
"""

from __future__ import annotations

import asyncio
import copy
import re
import time
from datetime import date, timedelta
from typing import Any, Callable

from backend.genome import mock_upstream, persona_generator as pg, roster

LANG = {"hi-IN": "Hindi", "ta-IN": "Tamil", "te-IN": "Telugu", "en-IN": "English", "mr-IN": "Marathi", "gu-IN": "Gujarati", "bn-IN": "Bengali"}


def mock_results(p: dict) -> dict:
    return {"company.detail": {"status": "ok", "origin": "mock", "response": mock_upstream.company_detail(p)},
            "context.global": {"status": "ok", "origin": "mock", "response": mock_upstream.global_context(p)},
            "sts.history": {"status": "ok", "origin": "mock", "response": mock_upstream.sts_history(p)},
            "pns.insights": {"status": "ok", "origin": "mock", "response": mock_upstream.pns_insights(p)},
            "enquiry.list": {"status": "ok", "origin": "mock", "response": mock_upstream.enquiry_list(p)}}


def lead_of(p: dict, **extra) -> dict:
    lead = {"data_type": p.get("data_type"), "language": LANG.get(p.get("language"), "Hindi"),
            "last_call_summary": (p.get("last_call") or {}).get("summary"), "last_meeting_date": p.get("last_meeting_date")}
    lead.update(extra)
    return {k: v for k, v in lead.items() if v not in (None, "")}


def _gc(results: dict) -> dict:
    return results["context.global"]["response"]["data"]


def _row(day: date, **kw) -> dict:
    base = {"call_answered_date": f"{day.isoformat()} 11:20:00", "call_answered_summary": "", "call_answered_transcript": "", "call_sts_update": "",
            "meet_sts_update": "", "meet_sucessful_date": "", "video_meet_sts_update": "", "video_verified_date": ""}
    base.update(kw)
    return base


# ---- edge scenarios: (name, base hero, edit(profile, results, lead, today), expectations) --------------------------------

def _new_free(p, r, lead, today):
    gc = _gc(r)
    gc["kycdetails"].update(customer_type="Free", registered_since_with_indiamart="0 Y 3 M", gst_turnover="NA", payment_invoice_date="NA",
                            payment_receipt_date="", last_call_details=[], approved_product_count="2")
    gc["connectdetails"] = {"connect_7D": [], "connect_30D": [], "last_call_summary": ""}
    gc["ticketsdetails"] = {"wip_tickets": [], "current": {"tickets": None}, "last_7_days": {"tickets": None}, "last_8_to_90_days": {"tickets": None}}
    gc["activitydetails"].update(enq_last_30_days=1, enq_last_90_days=1, pns_received_30_days=0, pns_received_90_days=0,
                                 last_week={"bl_active_days": 1, "bl_cons": 0}, last_4_week={"bl_active_days": 2, "bl_cons": 1, "pns_call_picked": 0, "pns_call_received": 0},
                                 last_month={}, last_3_month={})
    r["sts.history"]["response"] = []
    r["pns.insights"]["response"] = {"Code": 200, "data": []}
    lead.clear()
    lead.update(language="Hindi")


def _follow_up(p, r, lead, today):
    gc = _gc(r)
    gc["connectdetails"]["connect_7D"] = [_row(today - timedelta(days=2), call_answered_summary="Seller asked for a call back on "
                                                 f"{(today + timedelta(days=1)).strftime('%d %b')} to discuss the catalogue update.",
                                                 call_sts_update=f"Call back on {(today + timedelta(days=1)).isoformat()} — follow up on catalogue",
                                                 call_answered_transcript="Executive: namaste ji. Customer: haan ji, parso call kar lena, abhi bahar hoon.")]
    gc["ticketsdetails"]["wip_tickets"] = []
    gc["ticketsdetails"]["current"] = {"tickets": None}
    lead.pop("data_type", None)


def _pnsm(p, r, lead, today):
    gc = _gc(r)
    gc["ticketsdetails"]["wip_tickets"] = []
    gc["ticketsdetails"]["current"] = {"tickets": None}
    gc["connectdetails"]["connect_30D"] = [x for x in gc["connectdetails"]["connect_30D"] if not x.get("meet_sucessful_date")]
    lead["data_type"] = "PNSM"
    lead.pop("last_call_summary", None)


def _company_contact(p, r, lead, today):
    p["contact_name"] = p["company_name"]
    _gc(r)["kycdetails"].update(customer_name=p["company_name"], first_name="")
    lead["data_type"] = "PIM"


def _first_copy(p, r, lead, today):
    enq = r["enquiry.list"]["response"]["response"]["enquiries"]
    proto = copy.deepcopy(enq[0]) if enq else {"enquiryISQ": [], "mobileVerified": 1, "emailVerified": 0, "addressVerified": 0, "gstVerified": 0,
                                               "buyerCity": "", "buyerState": "", "countryFlag": "in_flag_s.png", "queryId": 1}
    for i, title in enumerate(["Rolex Watch First Copy", "Nike Shoes 1st Copy"]):
        e = copy.deepcopy(proto)
        e.update(enquiryTitle=title, postedDate=f"{(today - timedelta(days=i + 1)).isoformat()}T10:00:00.000000", buyerName=f"Test Buyer {i}",
                 buyerMemberSince="2025-01-01T00:00:00", queryId=900000 + i, enquiryISQ=[{"DESC": "Quality", "RESPONSE": "First Copy"}])
        enq.insert(0, e)


def _conflict(p, r, lead, today):
    _gc(r)["activitydetails"]["enq_last_30_days"] = 40


def _large(p, r, lead, today):
    _gc(r)["kycdetails"].update(gst_turnover="100 - 500 Cr", company_type="Private Limited Company")


def _unanswered(p, r, lead, today):
    gc = _gc(r)
    gc["connectdetails"] = {"connect_7D": [_row(today - timedelta(days=d), call_sts_update="Call not answered") for d in (1, 3)],
                            "connect_30D": [_row(today - timedelta(days=d), call_sts_update="Not reachable") for d in (1, 3, 8, 12)],
                            "last_call_summary": ""}


def _tamil(p, r, lead, today):
    lead["language"] = "Tamil"
    gc = _gc(r)
    gc["connectdetails"]["connect_7D"] = [_row(today - timedelta(days=3), call_answered_summary="Seller discussed buy lead usage in Tamil.",
                                               call_answered_transcript="Executive: vanakkam. Customer: சரி சொல்லுங்க, leads எப்படி use பண்றது?")]


def _meeting_pending(p, r, lead, today):
    gc = _gc(r)
    meet = today - timedelta(days=6)
    gc["connectdetails"]["connect_30D"] = [_row(meet, meet_sucessful_date=meet.isoformat(),
                                                meet_sts_update="01 Meeting Agenda: Catalogue review 02 Decision Maker Name & No: [withheld] "
                                                                "03 Major Products/Categories: valves 04 Meeting Summary & Work Done: reviewed listings "
                                                                "05 ROI/Business Generated in last 6 Months: NA 06 Work to be Done Post Meeting: need to follow up "
                                                                "on product photos and prices 07 Comments/Pending work: photos pending")]
    gc["connectdetails"]["connect_7D"] = []
    gc["ticketsdetails"]["wip_tickets"] = []
    gc["ticketsdetails"]["current"] = {"tickets": None}
    lead.pop("data_type", None)
    lead.pop("last_call_summary", None)


def _renewal(p, r, lead, today):
    lead["data_type"] = "OLPR"
    gc = _gc(r)
    gc["ticketsdetails"]["wip_tickets"] = []
    gc["ticketsdetails"]["current"] = {"tickets": None}


def _plan_type(out: dict) -> str | None:
    return ((out.get("call_plan") or {}).get("trigger") or {}).get("type") if isinstance(out, dict) else None


def _opening(out: dict) -> str:
    return str((((out or {}).get("call_plan") or {}).get("opening") or {}).get("line") or "")


EDGE: list[tuple[str, str, Callable, list[tuple[str, Callable[[dict], bool]]]]] = [
    ("New free seller, little data", "syn-91000101", _new_free, [
        ("No call plan", lambda o: o.get("call_plan") is None), ("Segment Free", lambda o: o.get("segment") == "Free"),
        ("Few insights (≤ 6)", lambda o: len((o.get("engagement_with_indiamart") or {}).get("insights") or []) <= 6),
        ("Mostly default dials (≥ 3 marked G)", lambda o: sum(1 for d in ((o.get("persona") or {}).get("dials") or {}).values() if d.get("confidence") == "G") >= 3)]),
    ("Follow-up due (callback tomorrow)", "syn-91000102", _follow_up, [("Trigger follow_up_due", lambda o: _plan_type(o) == "follow_up_due")]),
    ("Unknown hot-lead type PNSM", "syn-91000106", _pnsm, [("No hot_lead trigger", lambda o: _plan_type(o) != "hot_lead")]),
    ("Contact name is the company name", "syn-91000114", _company_contact, [
        ("Opening says aap, no 'ji' name", lambda o: "ji" not in _opening(o).lower().split() or "aap" in _opening(o).lower())]),
    ("First Copy enquiries in the list", "syn-91000107", _first_copy, [("No counterfeit mention", lambda o: "first copy" not in str(o).lower())]),
    ("Enquiry counts disagree (40 vs list)", "syn-91000108", _conflict, [("A data doubt is raised", lambda o: bool((o.get("engagement_with_indiamart") or {}).get("data_doubts")))]),
    ("Large business (100–500 Cr)", "syn-91000109", _large, [("Formality ≥ 60", lambda o: (((o.get("persona") or {}).get("dials") or {}).get("formality") or {}).get("pos", 0) >= 60)]),
    ("Run of unanswered calls", "syn-91000110", _unanswered, [
        ("Reachability insight", lambda o: any(i.get("group") == "Reachability" for i in (o.get("engagement_with_indiamart") or {}).get("insights") or [])),
        ("Brisk pace (≥ 50)", lambda o: (((o.get("persona") or {}).get("dials") or {}).get("pace") or {}).get("pos", 0) >= 50)]),
    ("Tamil-speaking seller", "syn-91000111", _tamil, [
        ("Opening left for a native speaker", lambda o: o.get("call_plan") is None or "native speaker" in _opening(o).lower()),
        ("Voice language Tamil", lambda o: "tamil" in str(((o.get("persona") or {}).get("voice") or {}).get("language")).lower())]),
    ("Meeting with pending work", "syn-91000112", _meeting_pending, [("Trigger unresolved_last_interaction", lambda o: _plan_type(o) == "unresolved_last_interaction")]),
    ("Renewal activity (OLPR)", "syn-91000113", _renewal, [("Trigger hot_lead", lambda o: _plan_type(o) == "hot_lead"),
                                                          ("No sale assumed (no demo/meeting push)", lambda o: not re.search(r"demo|meeting", _opening(o), re.I))]),
]


def scenarios(today: date) -> list[dict]:
    out = []
    for glid, hero in sorted(roster.heroes().items()):
        p = copy.deepcopy(hero)
        out.append({"name": f"Hero · {hero['company_name']} ({hero.get('archetype')})", "kind": "hero", "seller": p, "results": mock_results(p),
                    "lead": lead_of(p), "expect": []})
    for name, base, edit, expect in EDGE:
        p = copy.deepcopy(roster.profile(base))
        results, lead = mock_results(p), lead_of(p)
        edit(p, results, lead, today)
        out.append({"name": f"Edge · {name}", "kind": "edge", "seller": p, "results": results, "lead": lead, "expect": expect})
    return out


async def run(sarvam, cases: list[dict], today: date, *, pause: float = 2.5, on_row: Callable[[dict], Any] | None = None) -> list[dict]:
    """Generate + evaluate each case sequentially (the LLM gateway allows 30 requests a minute per caller)."""
    rows = []
    for case in cases:
        started = time.monotonic()
        data, refs = pg.build_input(case["seller"], case["results"], today, case.get("lead"))
        try:
            gen = await pg.generate(sarvam, data, refs, today, scope="operator")
            out, ev = gen["persona"], gen["evaluation"]
            own = [{"check": label, "ok": bool(fn(out if isinstance(out, dict) else {})), "critical": False, "detail": ""} for label, fn in case["expect"]]
            row = {"name": case["name"], "kind": case["kind"], "glid": case["seller"].get("glid"), "score": ev["score"], "critical": ev["critical"],
                   "raw_score": ev.get("raw_score"), "raw_critical": ev.get("raw_critical"), "raw_failed": ev.get("raw_failed", []),
                   "warnings": ev["warnings"], "passed": ev["passed"], "total": ev["total"], "scenario_checks": own,
                   "scenario_ok": sum(c["ok"] for c in own), "attempts": len(gen["attempts"]), "ms": gen["ms"],
                   "expected": [t["type"] for t in gen["expected_triggers"]], "plan": _plan_type(out) if isinstance(out, dict) else None,
                   "failed": [c for c in ev["checks"] if not c["ok"]], "corrected": ev.get("corrected", []), "persona": out,
                   "input_chars": len(str(data))}
        except Exception as error:  # noqa: BLE001 — one failed case never stops the suite
            row = {"name": case["name"], "kind": case["kind"], "glid": case["seller"].get("glid"), "error": f"{type(error).__name__}: {str(error)[:200]}",
                   "ms": round((time.monotonic() - started) * 1000)}
        rows.append(row)
        if on_row:
            on_row(row)
        await asyncio.sleep(pause)
    return rows


def summary(rows: list[dict]) -> dict:
    ok = [r for r in rows if "error" not in r]
    sc = [r["score"] for r in ok]
    checks = sum(len(r["scenario_checks"]) for r in ok)
    raw = [r["raw_score"] for r in ok if r.get("raw_score") is not None]
    return {"cases": len(rows), "errors": len(rows) - len(ok), "mean_score": round(sum(sc) / len(sc), 1) if sc else None,
            "mean_raw_score": round(sum(raw) / len(raw), 1) if raw else None,
            "no_critical": sum(1 for r in ok if r["critical"] == 0), "raw_no_critical": sum(1 for r in ok if r.get("raw_critical") == 0),
            "repaired": sum(1 for r in ok if r["attempts"] > 1),
            "scenario_checks": f"{sum(r['scenario_ok'] for r in ok)}/{checks}",
            "plan_matches": sum(1 for r in ok if (r["plan"] or None) == (r["expected"][0] if r["expected"] else None)),
            "median_ms": sorted(r["ms"] for r in ok)[len(ok) // 2] if ok else None,
            "top_failures": _top_failures(ok), "top_raw_failures": _top_failures(ok, "raw_failed")}


def _top_failures(rows: list[dict], key: str = "failed") -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for r in rows:
        for c in r.get(key) or []:
            name = c["check"] if isinstance(c, dict) else c
            counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:10]

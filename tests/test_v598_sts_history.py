"""5.9.8 — executive STS entries (NSDMERP, MERPC2C) are people's calls, meetings and follow-ups, not system notices."""
from datetime import date
from urllib.parse import quote

from backend.genome import parsers, persona_generator as pg


def blob(*entries: str) -> list[dict]:
    return [{"data": [quote("<br/><br/>".join(entries))], "status": "200"}]


ENTRIES = (
    "08 OCT 2026 07:08:28 PM, Updated by Ravi Test&nbsp; through NSDMERP<br/>&nbsp;&nbsp; Mail To: x@example.com<br/>&nbsp;&nbsp; "
    "Comments: Follow up - General - customer ask to call back later payment follow up\r\n  TBRO ADDED : 13-Oct-2026 08:45<br />",
    "08 OCT 2026 06:28:41 PM, Updated by Ravi Test (100001)&nbsp; through NSDMERP<br/>&nbsp;&nbsp; Comments: Sales Appointment - 08-Oct-2026 | "
    "Self | Followup | Ravi Test - 100001 | Warm 1. Met With Name and designation - Partner - Owner 2. BAH Presentation - Yes<br />",
    "08 OCT 2026 06:25:31 PM, Updated by Ravi Test&nbsp; through MERPC2C<br/>&nbsp;&nbsp; Comments: Called through Inhouse C2C for NSDMERPC2C "
    "Call was made from 9000000000 to 98*****801 Call Type : Outgoing Call Status : Answered Call Duration : 92 seconds<br />",
    "08 OCT 2026 06:23:28 PM, Updated by Ravi Test&nbsp; through MERPC2C<br/>&nbsp;&nbsp; Comments: Called through Inhouse C2C for NSDMERPC2C "
    "Call was made from 9000000000 to 98*****801 Call Type : Outgoing Call Status : NotAnswered<br />",
    "05 OCT 2026 10:00:00 AM, Updated by Online Payment&nbsp; through Online Payment<br/>&nbsp;&nbsp; Comments: Online payment attempted for packages<br />",
)


def test_executive_entries_become_meeting_calls_and_a_follow_up():
    entries = parsers.sts_entries(blob(*ENTRIES))
    assert [e["automated"] for e in entries] == [False, False, False, False, True]
    assert [e["kind"] for e in entries] == ["follow_up", "meeting", "call", "call", "payment"]
    data, _ = pg.build_input({"glid": "1", "seller_id": "real-1", "company_name": "Test", "city": "", "state": ""},
                             {"sts.history": {"status": "ok", "response": blob(*ENTRIES)}}, date(2026, 10, 10), {})
    types = [(r["type"], r["outcome"]) for r in data["records"]]
    assert ("Meeting", "Meeting update") in types and ("Executive call", "Answered") in types and ("Executive call", "Not answered") in types
    assert ("Call note", "Follow-up set") in types and ("System notice", "payment") in types
    text = " ".join(r["text"] for r in data["records"])
    assert "Ravi Test" not in text and "100001" not in text and "98*****801" not in text and "9000000000" not in text
    assert pg.expected_triggers(data, date(2026, 10, 10))[0]["type"] == "follow_up_due"   # TBRO 13-Oct-2026 is within 3 days


def test_webERP_and_processes_unchanged():
    assert not parsers.automated("Kapil Sharma", "WebERP") and parsers.automated("Weberp Auto Process", "WebERP")
    assert parsers.automated("Online Payment", "Online Payment") and parsers.automated("X", "SP_UPDATE_STS_HIST")


def test_an_invented_figure_is_critical_and_never_shown():
    data = {"lead": {"glid": 1}, "records": [{"id": "r1", "date": "2026-10-08", "type": "Meeting", "outcome": "Meeting update", "text": "met"}],
            "facts": [{"id": "f1", "label": "products listed on the storefront", "value": 78}]}
    out = {"engagement_with_indiamart": {"insights": [
        {"id": "i1", "group": "Relationship", "text": "Met on 8 Oct 2026.", "confidence": "H", "evidence": [{"record_id": "r1"}]},
        {"id": "i2", "group": "Platform use", "text": "Active on 28 of the last 28 days.", "confidence": "M", "evidence": []}]},
           "record": [{"id": "r1", "date": "8 Oct 2026", "type": "Meeting", "outcome": {"label": "Meeting update", "tone": "neu"}, "text": "met"}]}
    assert any(num == "28" for _, num in pg.invented_numbers(out, data))
    changes = pg.polish(out, data, {"record_ids": {"r1"}, "fact_ids": {"f1"}}, date(2026, 10, 10))
    assert [i["id"] for i in out["engagement_with_indiamart"]["insights"]] == ["i1"] and any("Dropped 1" in c for c in changes)


def test_a_cut_off_persona_is_retried_once_with_more_room():
    import asyncio
    from backend.genome.sarvam import SarvamError
    calls = []

    class Fake:
        async def chat_json(self, system, user, *, scope, max_tokens, temperature, run_id=None, label=""):
            calls.append((label, max_tokens))
            if label == "persona-generator":
                raise SarvamError("Sarvam chat failed: JSONDecodeError (truncated at max_tokens)")
            return {"data": {}, "ms": 1, "model": "m"}

    asyncio.run(pg.generate(Fake(), {"lead": {"glid": 1}, "records": [], "facts": []}, {}, date(2026, 10, 10), scope="operator"))
    assert calls[0] == ("persona-generator", 7000) and calls[1] == ("persona-generator-retry", 7000)

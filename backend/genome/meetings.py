"""Meeting desk (owner point 7; decision D11).

willingness → online / in person → address candidates (profile, GST, dataset, what the seller said)
→ confirm → time in IST → a real meeting link (Jitsi, no account needed), an ``.ics`` file and a
prefilled Google Calendar link → handoff note for the human executive.
No customer invitations are sent; the Google Calendar API path (OAuth) remains for later.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

IST = timezone(timedelta(hours=5, minutes=30))


def plan(settings, run: dict, call: dict | None, mode: str, address_kind: str | None, address_text: str | None,
         start_iso: str, note: str = "") -> dict:
    """Build a confirmed test meeting with working links. ``start_iso`` is local IST, e.g. 2026-10-11T16:00."""
    seller = run["seller"]
    try:
        start = datetime.fromisoformat(start_iso)
    except ValueError as error:
        raise ValueError("Pick a valid date and time.") from error
    if start.tzinfo is None:
        start = start.replace(tzinfo=IST)
    if start < datetime.now(IST) - timedelta(minutes=5):
        raise ValueError("The meeting time is in the past.")
    duration = int(settings.get("meetings.duration_minutes", 30))
    end = start + timedelta(minutes=duration)
    addresses = (run.get("contract") or {}).get("addresses", [])
    chosen = None
    if mode == "in_person":
        chosen = next((a for a in addresses if a["kind"] == address_kind), None) if address_kind else None
        text = (address_text or (chosen or {}).get("text") or "").strip()
        if not text:
            raise ValueError("Choose or type an address for an in-person meeting.")
        location = text
    else:
        location = "Online"
    meeting_id = "mtg-" + secrets.token_hex(5)
    room = hashlib.sha256(f"{run['id']}{meeting_id}".encode()).hexdigest()[:10]
    link = settings.get("meetings.meeting_link_base", "https://meet.jit.si/IndiaMART-SellerGenome-") + room if mode == "online" else None
    title = f"IndiaMART meeting · {seller.get('company_name')}"
    persona = run.get("persona") or {}
    handoff = [
        f"Seller: {seller.get('company_name')} ({seller.get('city')}), GLID {seller.get('glid')}",
        f"Why now: {seller.get('trigger')}",
        f"Persona: {persona.get('summary', '')[:300]}",
        f"Primary objection: {((persona.get('factors') or {}).get('primary_objection') or {}).get('value', 'unknown')}",
        f"Confirm first: {'; '.join(((run.get('teleprompter') or {}).get('confirm_first') or [])[:2]) or 'nothing flagged'}",
    ]
    if call and call.get("outcome"):
        handoff.append(f"Call outcome: {call['outcome'].get('label')} — {call['outcome'].get('detail', '')}")
    if note:
        handoff.append(f"Note: {note[:300]}")
    description = "\n".join(handoff) + (f"\nJoin: {link}" if link else "") + "\n(Test booking from SellerGenome — no invitation sent.)"
    fmt = "%Y%m%dT%H%M%SZ"
    gcal = "https://calendar.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE", "text": title,
        "dates": f"{start.astimezone(timezone.utc).strftime(fmt)}/{end.astimezone(timezone.utc).strftime(fmt)}",
        "details": description, "location": link or location, "ctz": "Asia/Kolkata"})
    meeting = {
        "id": meeting_id, "run_id": run["id"], "call_id": (call or {}).get("id"), "seller_id": run["seller_id"],
        "status": "seller_confirmed", "test_only": True, "mode": mode, "title": title,
        "start": start.isoformat(), "end": end.isoformat(), "start_label": start.strftime("%a %d %b, %I:%M %p IST"),
        "location": location, "address_source": (chosen or {}).get("label") if chosen else ("typed" if mode == "in_person" else None),
        "address_origin": (chosen or {}).get("origin"), "meeting_link": link, "google_calendar_link": gcal,
        "ics_path": f"/api/v5/meetings/{meeting_id}.ics", "handoff": handoff, "description": description,
        "map_link": ("https://www.openstreetmap.org/search?query=" + quote(location)) if mode == "in_person" else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "states": ["proposed", "seller_confirmed", "calendar_file_ready"],
        "not_done": "Not sent to any calendar or person; meeting held and sale completed are never inferred.",
    }
    return meeting


def ics(meeting: dict) -> str:
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    fmt = "%Y%m%dT%H%M%SZ"
    start = datetime.fromisoformat(meeting["start"]).astimezone(timezone.utc).strftime(fmt)
    end = datetime.fromisoformat(meeting["end"]).astimezone(timezone.utc).strftime(fmt)
    stamp = datetime.now(timezone.utc).strftime(fmt)
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SellerGenome//Command Center//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "BEGIN:VEVENT", f"UID:{meeting['id']}@sellergenome.lehana.in", f"DTSTAMP:{stamp}", f"DTSTART:{start}", f"DTEND:{end}",
        f"SUMMARY:{esc(meeting['title'])}", f"LOCATION:{esc(meeting.get('meeting_link') or meeting['location'])}",
        f"DESCRIPTION:{esc(meeting['description'])}",
        *([f"URL:{meeting['meeting_link']}"] if meeting.get("meeting_link") else []),
        "STATUS:TENTATIVE", "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"

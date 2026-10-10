---
name: sellergenome-meeting-desk
description: Work on SellerGenome's meeting booking — the call workspace meeting desk (online vs in-person, address candidates from profile/GST/dataset/what the seller said, IST time, a working Jitsi link, an .ics download and a prefilled Google Calendar link, handoff note), Payal's book_meeting tool endpoint, and the pending Google Calendar API path. Use when booking or rescheduling behaviour changes, a meeting link/.ics/calendar link is wrong, the bot or Payal must "fix a meeting", or someone asks to send real invitations (not allowed without the owner).
---

# Meeting desk (decision D11)

The bot's only job is to book a meeting for a human executive. SellerGenome books **test** meetings with working
links and never sends customer invitations.

## Flow (`backend/genome/meetings.py`, UI in `frontend/cc/call.js`)

willingness → online / in person → address (in person: `address_kind` = `profile`, `gst` or `dataset` from the
contract's address candidates — the docs' `addressGlusr`/`addressGst` names are not accepted — or `address_text`
typed from what the seller said; neither → 422) → confirm → time in IST (hard-coded in `meetings.py`; the
`meetings.timezone` setting is not read; past times refused) → `plan()` returns a meeting with:
- an online room `meetings.meeting_link_base` + id (Jitsi, no account), duration `meetings.duration_minutes` (30);
- `GET /api/v5/meetings/{id}.ics` (calendar file; DTSTART/DTEND stored in UTC);
- a prefilled Google Calendar link;
- a handoff note for the executive.

API: `POST /api/v5/meetings {run_id, call_id?, mode, address_kind?, address_text?, start, note?}` → stored in SQLite
kind `meetings`. Payal books through her `book_meeting` tool → `POST /api/v5/sarvam-agent/meetings` (tool key) →
same Jitsi base, reply template `{{confirmation}}` — but that endpoint creates **no** meetings record, .ics or
calendar link; it only logs the call (`sarvam_voice.record_call("meeting")`) (skill `sellergenome-sarvam-voice-agent`).

## Rules

- "Meeting fixed" is a claim until a human holds it; the floor and docs must not call it a sale.
- No invitations to sellers, no production lead writes, no outbound dialling. The Google Calendar API path needs OAuth
  and the owner's go-ahead (pending); until then the prefilled link is the calendar path.
- Addresses are private: only show them to operators for private sellers; never send them to an LLM beyond what the
  locality step already uses.
- Times are IST in the UI; the .ics stores UTC (calendars convert).

## Test

Browser case 52: choose the GST address, online → Jitsi link opens; `.ics` downloads; Google Calendar link opens
prefilled. Payal: `/sarvam` → "kal 11 baje online" + confirm → the tool panel shows "Meeting booked" (case 70).

"""The live call floor (owner point 3) — a deterministic, clearly-labelled simulation.

Every viewer sees the same floor because each lane's state is a pure function of the wall clock:
lane *i* plays hero scripts in rotation with its own offset and pace. The browser receives each
lane's script and elapsed time and animates locally between resyncs.

Real numbers sit next to simulated ones and are labelled: prep runs today, median prep time and
Elastic cache hit rate come from actual V5 runs; connected voice-bot calls (plug-in) appear as REAL.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from backend.genome import roster

ROOT = Path(__file__).resolve().parents[2]
IST = timezone(timedelta(hours=5, minutes=30))
EPOCH = 1791500000  # fixed reference so lane positions are stable across restarts


@lru_cache(maxsize=1)
def scripts() -> dict:
    return json.loads((ROOT / "data" / "genome" / "floor_scripts.json").read_text())


def _h(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)


def _lane(i: int, now: float, keys: list[str], spt: float, tail: float) -> tuple[str, float, int]:
    """Which script a lane plays now, how far in, and the cycle number."""
    offset = (_h(f"lane{i}") % 97) * 1.7
    length = lambda k: len(scripts()["seller"][k]) * spt + tail  # noqa: E731
    cycle_len = sum(length(k) for k in keys) / len(keys)
    t = (now - EPOCH + offset)
    cycle = int(t // cycle_len)
    key = keys[(i * 5 + cycle * 3) % len(keys)]
    elapsed = (t % cycle_len) * (length(key) / cycle_len)
    return key, elapsed, cycle


def seller_floor(settings, store, plugin_calls: list[dict] | None = None, operator: bool = False, hints_for=None) -> dict:
    now = time.time()
    spt = float(settings.get("floor.seconds_per_turn", 4.5))
    tail = 30.0
    lanes = int(settings.get("floor.seller_lanes", 12))
    keys = sorted(scripts()["seller"].keys())
    tiles, used = [], set()
    for i in range(lanes):
        key, elapsed, cycle = _lane(i, now, keys, spt, tail)
        if key in used:  # avoid showing the same seller twice
            key = next(k for k in keys if k not in used) if len(used) < len(keys) else key
        used.add(key)
        tiles.append(tile_for(f"syn-{key}", elapsed, spt, lane=i, cycle=cycle))
    for call in plugin_calls or []:
        tiles.insert(0, {**call, "real": True})
    if operator:  # pinned real sellers: never shown to anonymous visitors
        pinned = [str(g).strip() for g in settings.get("floor.pinned_real_sellers", []) or [] if str(g).strip().isdigit()][:6]
        for glid in reversed(pinned):
            tiles.insert(0, real_tile(f"real-{glid}", store, hints_for(glid) if hints_for else None, tile_id=f"pinned-real-{glid}"))
    return {"tiles": tiles, "queue": exploration_queue(settings, now, used), "kpis": kpis(settings, store, now, len(tiles)),
            "ticker": ticker(now, keys, spt), "server_time": now, "simulation": True,
            "note": "Simulated calling floor. Barging in runs real context preparation."}


def confirmed_identity(store, seller_id: str) -> dict:
    """Identity a live run confirmed for a real seller (company record), newest first — never the placeholder."""
    for run in store.list("runs", 200) if store else []:
        if run.get("seller_id") != seller_id:
            continue
        fields = (run.get("contract") or {}).get("fields") or {}
        got = {k: (fields.get(k) or {}).get("value") for k in ("company_name", "contact_name", "city")
               if (fields.get(k) or {}).get("availability") == "present" and (fields.get(k) or {}).get("origin") in ("live_api", "elastic_cache", "local_db")}
        if got.get("company_name"):
            return {**got, "confirmed_at": run.get("finished_at") or run.get("created_at"), "run_id": run.get("id")}
    return {}


def real_tile(seller_id: str, store, hints: dict | None, tile_id: str | None = None, found: bool = False) -> dict:
    """A real seller's tile: no simulated call, no generated persona strip (owner, 2026-10-10: "dont make things anywhere")
    — only what a live run confirmed. "Talk as Seller" starts a real preparation and a call from scratch."""
    profile = roster.profile(seller_id, hints)
    seller = roster.summary(profile)
    ident = confirmed_identity(store, seller_id)
    seller.update({k: v for k, v in ident.items() if k in ("company_name", "contact_name", "city") and v})
    seller["identity"] = "confirmed" if ident else "pending"
    seller["archetype"] = None
    seller["category"] = (hints or {}).get("category") or None
    return {
        "id": tile_id or f"found-{seller_id}", "seller": seller, "speaker": None, "turns": [], "elapsed_s": 0.0, "turn_seconds": 4.5,
        "on_air_s": 0.0, "stage": "ready", "signal": None, "switch": None, "language": profile["language"], "genome": [],
        "found": found, "real": False, "real_seller": True, "confirmed_at": ident.get("confirmed_at"),
        "note": ("Identity from the live company record" if ident else "Identity pending — the live company record confirms it during a run")
                + ". No call is simulated for a real seller.",
    }


def tile_for(seller_id: str, elapsed: float, spt: float, lane: int | None = None, cycle: int = 0, found: bool = False,
             hints: dict | None = None, store=None) -> dict:
    if seller_id.startswith("real-"):
        return real_tile(seller_id, store, hints, found=found)
    profile = roster.profile(seller_id, hints)
    turns = scripts()["seller"].get(profile["glid"]) or generic_script(profile)
    shown = min(len(turns), int(elapsed // spt) + 1)
    stage = next((t.get("stage") for t in reversed(turns[:shown]) if t.get("stage")), "greeting")
    signal = next((t.get("signal") for t in reversed(turns[:shown]) if t.get("signal")), None)
    switch = next((t.get("switch") for t in reversed(turns[:shown]) if t.get("switch")), None)
    return {
        "id": f"lane{lane}-{cycle}" if lane is not None else f"found-{seller_id}",
        "seller": roster.summary(profile), "speaker": profile.get("speaker"),
        "turns": turns, "elapsed_s": round(elapsed, 1), "turn_seconds": spt,
        "on_air_s": round(elapsed + 8 + (_h(seller_id) % 20), 1),
        "stage": stage, "signal": signal, "switch": switch, "language": profile["language"],
        "genome": genome_strip(profile), "found": found, "real": False,
    }


def generic_script(profile: dict) -> list[dict]:
    name = (profile.get("contact_name") or "").split()[0] if profile.get("contact_name") else ""
    activity = (profile.get("trigger") or {}).get("activity", "were active on IndiaMART").lower()
    if profile["language"] == "en-IN":
        return [{"who": "bot", "text": f"Hello{' ' + name if name else ''}, this is Payal, IndiaMART's virtual assistant. I noticed you {activity}. Do you have two minutes?", "stage": "greeting"},
                {"who": "seller", "text": "Yes, go ahead.", "signal": "interest"}]
    return [{"who": "bot", "text": f"Namaste{' ' + name if name else ''} ji, main IndiaMART ki virtual assistant Payal bol rahi hoon. Kya do minute baat ho sakti hai?", "stage": "greeting"},
            {"who": "seller", "text": "Haan boliye.", "signal": "interest"}]


def genome_strip(profile: dict) -> list[dict]:
    """A seller's persona as a short 'genome' barcode for tiles (archetype, language, tier, engagement…)."""
    c = profile.get("counts", {})
    picked = (c.get("pns_picked_30d") or 0) / max(1, c.get("pns_received_30d") or 1)
    return [
        {"k": "archetype", "v": profile.get("archetype")},
        {"k": "language", "v": profile.get("language")},
        {"k": "tier", "v": "paid" if (profile.get("custtypeweight") or 2000) < 1000 else "free"},
        {"k": "pickup", "v": round(picked, 2)},
        {"k": "enquiries", "v": c.get("enquiries_received_30d")},
        {"k": "catalog_gaps", "v": sum(1 for p in profile.get("products", []) if not p.get("price"))},
        {"k": "doubts", "v": len(profile.get("doubts", []))},
    ]


def exploration_queue(settings, now: float, busy: set) -> list[dict]:
    """Sellers inside the ~15-minute exploration window, counting down (accelerated clock)."""
    mult = float(settings.get("floor.clock_multiplier", 15))
    window = float(settings.get("floor.exploration_window_minutes", 15))
    heroes = [h for g, h in sorted(roster.heroes().items()) if g not in busy] or list(roster.heroes().values())
    out = []
    for i, hero in enumerate(heroes[:5]):
        period = window * 60 / mult  # real seconds for a full window
        phase = ((now - EPOCH) + i * period / 5) % period
        remaining_sim_min = window - phase * mult / 60
        out.append({"seller": roster.summary(hero), "activity": hero["trigger"]["activity"],
                    "remaining_minutes": round(remaining_sim_min, 1), "window_minutes": window, "multiplier": mult})
    return sorted(out, key=lambda x: x["remaining_minutes"])


def kpis(settings, store, now: float, on_air: int) -> dict:
    day_start = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    working = max(0, min(12 * 3600, now - (day_start + 9 * 3600)))
    calls_today = int(working / 3600 * 340)
    runs = [r for r in store.list("runs", 200) if r.get("status") in ("completed", "degraded")]
    durations = [r["duration_ms"] for r in runs if r.get("duration_ms")]
    hits = store.hits(limit=1000)
    api_hits = [h for h in hits if h.get("origin") in ("mock", "live_api", "elastic_cache", "local_db")]
    cache_hits = sum(1 for h in api_hits if h.get("origin") == "elastic_cache")
    return {
        "simulated": {"on_air": on_air, "calls_today": calls_today, "meetings_today": int(calls_today * 0.115),
                      "switches_last_hour": int(340 * 0.42)},
        "real": {"prep_runs_today": store.count("runs", since=day_start),
                 "median_prep_ms": round(statistics.median(durations)) if durations else None,
                 "cache_hit_rate": round(cache_hits / len(api_hits), 3) if api_hits else None,
                 "api_hits_logged": len(api_hits)},
    }


def ticker(now: float, keys: list[str], spt: float) -> list[dict]:
    """Recent floor events (deterministic)."""
    out = []
    for k in range(8):
        t = now - k * 7.3
        key = keys[int(t // 7.3) % len(keys)]
        hero = roster.heroes()[key]
        kind = ["activity", "queued", "dialling", "connected", "switch", "meeting"][int(t // 7.3) % 6]
        text = {
            "activity": f"{hero['company_name']} · {hero['trigger']['activity']}",
            "queued": f"{hero['company_name']} entered the exploration window",
            "dialling": f"Dialling {hero['contact_name']} ({hero['city']})",
            "connected": f"Connected · {hero['company_name']} · {hero['language']}",
            "switch": f"Persona switch on {hero['company_name']} call",
            "meeting": f"Meeting proposed · {hero['company_name']}",
        }[kind]
        out.append({"at": datetime.fromtimestamp(t, IST).strftime("%H:%M:%S"), "kind": kind, "text": text, "seller_id": f"syn-{key}"})
    return out


def buyer_floor(settings, live: list[dict] | None = None) -> dict:
    now = time.time()
    spt = float(settings.get("floor.seconds_per_turn", 4.5))
    calls = scripts()["buyer"]
    lanes = int(settings.get("floor.buyer_lanes", 8))
    tiles = []
    for i in range(min(lanes, len(calls))):
        call = calls[(i + int((now - EPOCH) // 120)) % len(calls)]
        length = len(call["turns"]) * spt + 25
        elapsed = ((now - EPOCH) + (_h(call["id"]) % 60)) % length
        shown = min(len(call["turns"]), int(elapsed // spt) + 1)
        captured: dict = {}
        for t in call["turns"][:shown]:
            captured.update(t.get("capture") or {})
        tiles.append({"id": f"buyer-{call['id']}", "call": {k: v for k, v in call.items() if k != "turns"}, "turns": call["turns"],
                      "elapsed_s": round(elapsed, 1), "turn_seconds": spt, "captured": captured,
                      "on_air_s": round(elapsed + 6 + (_h(call["id"]) % 15), 1),  # the tile clock (was missing: "ON AIR NaN:NaN")
                      "signal": next((t.get("signal") for t in reversed(call["turns"][:shown]) if t.get("signal")), None)})
    tiles = list(live or []) + tiles  # live calls to the VANI buyer agent on Sarvam (REAL CALL) come first
    return {"tiles": tiles, "server_time": now, "simulation": True,
            "note": "Simulated missed buyer calls answered by Buyer-side VANI. Barging in runs the real VANI prompt on Sarvam."}

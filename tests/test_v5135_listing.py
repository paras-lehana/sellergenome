"""5.13.5 — the competitor listing through the gateway: the confirmed answer shape, the `cityflname` city filter, the
all-India fallback, and the talking points built from it. All names and numbers here are made up."""
import asyncio

from backend.genome import agent_context as ac, orchestrator, parsers
from backend.genome.registry import DEFAULTS


def row(name, glid, city="Mumbai", **extra):
    return {"COMPANY": name, "gl_id": glid, "city": city, "member_since": "14 yrs", "wt_avg": "4.3", "total_ratings_cnt": "246",
            "Trustseal_url": "https://example.test/ts", "glusr_paid_url": "https://example.test/paid", "prd_price": "90",
            "glusr_comp_phone": "0000000000", "glusr_gst_no": "GSTTEST", "full_address": "Somewhere", **extra}


LISTING = {"cityid": 70624, "mcatname": "Shirting Fabrics", "out_total_unq_count": "27522", "out_total_count": "51140",
           "data": [row("Alpha Mills", "11"), row("Beta Fabrics", "22", wt_avg="4.6", total_ratings_cnt="36"),
                    row("Own Co", "99"), row("Gamma Textiles", "33", Trustseal_url=""), row("Delta Weaves", "44")]}


def test_registry_filters_the_city_with_cityflname():
    api = next(a for a in DEFAULTS if a.id == "market.listing")
    assert api.params["cityflname"] == "{city_slug}" and "city" not in api.params
    assert parsers.city_slug("Navi Mumbai") == "navi-mumbai"


def test_listing_rows_keep_public_facts_only_and_drop_the_seller():
    rows = parsers.listing_sellers(LISTING, "99")
    assert [r["company"] for r in rows] == ["Alpha Mills", "Beta Fabrics", "Gamma Textiles", "Delta Weaves"]
    assert rows[0] == {"company": "Alpha Mills", "city": "Mumbai", "glid": "11", "plan": "paid", "trustseal": True,
                       "rating": "4.3 stars from 246 ratings", "years": "14 years on IndiaMART", "price_shown": True}
    assert not rows[2]["trustseal"]
    flat = str(rows)
    assert "0000000000" not in flat and "GSTTEST" not in flat and "Somewhere" not in flat   # phones, GST, addresses never kept


def test_listing_market_city_rank_and_counts():
    m = parsers.listing_market(LISTING, "99")
    assert m["city"] == "Mumbai" and m["shown"] == 5 and m["own_rank"] == 3 and m["paid"] == 5 and m["trustseal"] == 4
    assert m["sellers_india"] == 27522
    assert parsers.listing_market({**LISTING, "cityid": 0}, "12345")["city"] == "" and parsers.listing_market({"x": 1}) == {}


def test_talking_points_name_competitors_and_where_the_seller_stands():
    run = {"seller": {"glid": "12345", "city": "Mumbai", "synthetic": False}, "contract": {"fields": {}}, "persona": {},
           "results": {"market.listing": {"status": "ok", "response": LISTING}}}
    f = ac.call_facts(run)
    assert f["competitors"].startswith("Alpha Mills (Mumbai; TrustSEAL; 4.3 stars from 246 ratings; 14 years on IndiaMART)")
    assert f["competitors"].endswith("and 2 more in the first 5 search results")
    assert f["market_snapshot"].startswith("The first 5 IndiaMART search results for Shirting Fabrics in Mumbai are all paid sellers"
                                           " with a price shown; this seller is not among them; the Mumbai search lists 27,522 sellers.")
    india = {**run, "results": {"market.listing": {"status": "ok", "response": {**LISTING, "cityid": 0}}}}
    assert "Shirting Fabrics across India" in ac.call_facts(india)["market_snapshot"] and "27,522 sellers list it across India" in ac.call_facts(india)["market_snapshot"]
    own = {**run, "seller": {**run["seller"], "glid": "99"}}
    assert "this seller is at position 3" in ac.call_facts(own)["market_snapshot"]


def test_unknown_city_falls_back_to_all_india():
    calls, notes = [], []

    async def hit(api, values):
        calls.append(values["city_slug"])
        return {"status": "error"} if values["city_slug"] else {"status": "ok"}
    got = asyncio.run(orchestrator.listing_with_fallback(hit, notes.append, "8591", "bhiwandi"))
    assert calls == ["bhiwandi", ""] and got["status"] == "ok" and "all of India" in notes[0]
    calls.clear()
    asyncio.run(orchestrator.listing_with_fallback(hit, notes.append, "8591", ""))
    assert calls == [""]                                            # no city: one call, no fallback

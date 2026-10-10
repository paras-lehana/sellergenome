"""Persona index in Elasticsearch (owner points 8 and 11).

- ``index_persona``   one persona doc + one doc per factor (typed slots) + registry updates
- ``suggest`` / ``apply``  PM suggestions and applied edits; an applied edit is a new factor revision
                           with ``origin: operator`` and keeps the previous value in history
- ``search`` / ``facets`` / ``registry``  the persona-index explorer (/genome)
SQLite keeps the latest state so the UI works even when Elasticsearch is down; Elasticsearch is
the searchable, chartable copy.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.genome.aggregator import KEYWORD_FACTORS, STANDARD_FACTORS


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _typed(name: str, value: Any, declared: str | None = None) -> dict:
    """Place a value into exactly one typed slot."""
    slots = {"value_keyword": None, "value_number": None, "value_bool": None, "value_text": None}
    if isinstance(value, bool):
        slots["value_bool"], kind = value, "bool"
    elif isinstance(value, (int, float)):
        slots["value_number"], kind = float(value), "number"
    elif isinstance(value, list):
        text = "; ".join(v if isinstance(v, str) else " → ".join(str(x) for x in v.values()) if isinstance(v, dict) else str(v) for v in value)
        slots["value_text"], kind = text[:2000], "text"
    elif declared == "text" or (isinstance(value, str) and (len(value) > 60 or name not in KEYWORD_FACTORS and " " in value and declared != "keyword")):
        slots["value_text"], kind = str(value)[:2000], "text"
        if len(str(value)) <= 120:
            slots["value_keyword"] = str(value).lower()
    else:
        slots["value_keyword"], kind = str(value).lower()[:256], "keyword"
    return {**slots, "value_type": kind}


class PersonaIndex:
    def __init__(self, elastic, store) -> None:
        self.elastic, self.store = elastic, store

    def factor_docs(self, seller: dict, persona: dict, revision: int, run_id: str | None) -> list[dict]:
        at = utc_now()
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="milliseconds")
        common = {"seller_id": seller["seller_id"], "glid": seller["glid"], "language": persona["factors"].get("language", {}).get("value"),
                  "archetype": persona.get("archetype"), "city": seller.get("city"), "revision": revision, "latest": True,
                  "synthetic": bool(seller.get("synthetic")), "at": at, "expires_at": expires}
        docs = []
        for name, f in persona["factors"].items():
            docs.append({**common, "id": f"{seller['seller_id']}:{name}", "factor": name, "novel": False,
                         **_typed(name, f["value"]), "score": f.get("score", 0), "strength": f.get("strength", "weak"),
                         "origin": f.get("origin", persona.get("origin", "llm")), "author": f.get("author", "sarvam"),
                         "reason": f.get("reason", ""), "sources": f.get("sources", [])})
        for n in persona.get("novel_factors", []):
            docs.append({**common, "id": f"{seller['seller_id']}:{n['name']}", "factor": n["name"], "novel": True,
                         **_typed(n["name"], n["value"], n.get("value_type")), "score": n.get("score", 0), "strength": "weak",
                         "origin": "llm", "author": "sarvam", "reason": n.get("reason", ""), "sources": n.get("sources", [])})
        for t in persona.get("tags", []):
            docs.append({**common, "id": f"{seller['seller_id']}:tag:{t['tag']}", "factor": "tag", "novel": False,
                         **_typed("tag", t["tag"], "keyword"), "score": t.get("score", 0), "strength": "weak", "origin": persona.get("origin", "llm"),
                         "author": "sarvam", "reason": t.get("reason", ""), "sources": t.get("sources", [])})
        return docs

    @staticmethod
    def generator_fields(persona: dict) -> dict:
        """Searchable fields of a persona made by the persona generator (5.5); empty for the aggregator."""
        gen = persona.get("generated") or {}
        out = gen.get("persona") if isinstance(gen.get("persona"), dict) else None
        if not out:
            return {"engine": persona.get("engine") or "aggregator"}
        dials = (out.get("persona") or {}).get("dials") or {}

        def pos(name: str) -> int | None:
            v = (dials.get(name) or {}).get("pos")
            return int(v) if isinstance(v, (int, float)) else None
        return {"engine": persona.get("engine"), "schema_version": str(out.get("schema_version") or ""), "segment": out.get("segment"),
                "call_plan_trigger": ((out.get("call_plan") or {}).get("trigger") or {}).get("type") or "none",
                "quality_score": (gen.get("evaluation") or {}).get("score"), "quality_critical": (gen.get("evaluation") or {}).get("critical"),
                "dial_pace": pos("pace"), "dial_mix": pos("mix"), "dial_formality": pos("formality"), "dial_warmth": pos("warmth"), "generated": out}

    def index_persona(self, seller: dict, persona: dict, run_id: str | None, enabled: bool) -> dict:
        """Store latest locally; queue ES writes when indexing is enabled. Returns doc counts and ids."""
        previous = self.store.get("personas", seller["seller_id"])
        revision = (previous or {}).get("revision", 0) + 1
        docs = self.factor_docs(seller, persona, revision, run_id)
        record = {"seller_id": seller["seller_id"], "glid": seller["glid"], "revision": revision, "persona": persona,
                  "at": utc_now(), "run_id": run_id, "synthetic": bool(seller.get("synthetic")), "city": seller.get("city"),
                  "company": seller.get("company_name")}
        self.store.put("personas", seller["seller_id"], record)
        for d in docs:
            self.store.put("factors", d["id"], d)
        queued = 0
        if enabled and self.elastic.enabled:
            self.elastic.queue("personas", f"{seller['seller_id']}:r{revision}", {
                "id": f"{seller['seller_id']}:r{revision}", "seller_id": seller["seller_id"], "glid": seller["glid"],
                "revision": revision, "latest": True, "summary": persona.get("summary", ""), "archetype": persona.get("archetype"),
                "language": persona["factors"].get("language", {}).get("value"), "city": seller.get("city"),
                "tags": [t["tag"] for t in persona.get("tags", [])], "completeness": persona.get("completeness", 0),
                "model": persona.get("model"), "origin": persona.get("origin"), "at": record["at"],
                "synthetic": bool(seller.get("synthetic")), "document": persona, **self.generator_fields(persona),
            }, run_id=run_id, node="persona.index")
            for d in docs:
                self.elastic.queue("persona-factors", d["id"], d, run_id=run_id, node="persona.index")
                queued += 1
            for n in persona.get("novel_factors", []):
                reg = self.store.get("registry", n["name"]) or {"factor": n["name"], "count": 0, "first_seen": record["at"],
                                                                "status": "proposed", "value_type": n.get("value_type", "keyword")}
                reg.update(count=reg["count"] + 1, last_seen=record["at"], example=str(n["value"])[:200])
                self.store.put("registry", n["name"], reg)
                self.elastic.queue("factor-registry", n["name"], reg, run_id=run_id, node="persona.index")
        return {"revision": revision, "documents": len(docs) + 1, "queued": queued,
                "index": self.elastic.alias("persona-factors"), "sample": docs[0] if docs else None}

    # --- suggestions and edits -------------------------------------------------------------------

    def suggest(self, seller_id: str, factor: str, to_value: Any, reason: str, author: str) -> dict:
        current = self.store.get("factors", f"{seller_id}:{factor}")
        sid = "sug-" + hashlib.sha256(f"{seller_id}{factor}{to_value}{utc_now()}".encode()).hexdigest()[:16]
        doc = {"id": sid, "seller_id": seller_id, "factor": factor, "status": "open", "author": author[:60],
               "from_value": str(_display(current))[:256], "to_value": str(to_value)[:256], "reason": reason[:500], "at": utc_now()}
        self.store.put("suggestions", sid, doc)
        if self.elastic.enabled:
            self.elastic.queue("suggestions", sid, doc, node="persona.suggest")
        return doc

    def apply(self, seller_id: str, factor: str, value: Any, reason: str, author: str, suggestion_id: str | None = None) -> dict:
        current = self.store.get("factors", f"{seller_id}:{factor}")
        if not current:
            raise KeyError("Unknown factor for this seller.")
        history = (current.get("history") or []) + [{k: current.get(k) for k in ("value_keyword", "value_number", "value_bool", "value_text", "score", "origin", "author", "revision", "at")}]
        updated = {**current, **_typed(factor, value), "origin": "operator", "author": author[:60], "strength": "strong",
                   "score": max(current.get("score", 0), 80), "reason": reason[:300] or "Applied by PM", "revision": current.get("revision", 1) + 1,
                   "at": utc_now(), "history": history[-10:]}
        self.store.put("factors", updated["id"], updated)
        record = self.store.get("personas", seller_id)
        if record and factor in record["persona"]["factors"]:
            record["persona"]["factors"][factor].update(value=value, origin="operator", author=author[:60], strength="strong", reason=updated["reason"])
            self.store.put("personas", seller_id, record)
        if self.elastic.enabled:
            body = {k: v for k, v in updated.items() if k != "history"}
            self.elastic.queue("persona-factors", updated["id"], body, node="persona.apply")
        if suggestion_id:
            s = self.store.get("suggestions", suggestion_id)
            if s:
                s.update(status="applied")
                self.store.put("suggestions", suggestion_id, s)
                if self.elastic.enabled:
                    self.elastic.queue("suggestions", suggestion_id, s, node="persona.suggest")
        return updated

    # --- explorer --------------------------------------------------------------------------------

    def search_query(self, factor: str | None, value: str | None, scope_synthetic: bool | None, text: str | None = None) -> dict:
        filters: list[dict] = [{"term": {"latest": True}}]
        if factor:
            filters.append({"term": {"factor": factor}})
        if value:
            filters.append({"bool": {"should": [{"term": {"value_keyword": value.lower()}}, {"match": {"value_text": value}}], "minimum_should_match": 1}})
        if scope_synthetic is not None:
            filters.append({"term": {"synthetic": scope_synthetic}})
        must = [{"multi_match": {"query": text, "fields": ["value_text", "reason"]}}] if text else []
        return {"size": 50, "track_total_hits": True, "query": {"bool": {"filter": filters, "must": must}},
                "sort": [{"score": "desc"}, {"at": "desc"}],
                "_source": ["seller_id", "glid", "factor", "value_keyword", "value_number", "value_text", "score", "strength", "origin", "city", "language", "archetype", "novel", "reason"]}

    def facets_query(self, scope_synthetic: bool | None) -> dict:
        filters: list[dict] = [{"term": {"latest": True}}]
        if scope_synthetic is not None:
            filters.append({"term": {"synthetic": scope_synthetic}})
        return {"size": 0, "query": {"bool": {"filter": filters}}, "aggs": {
            "factors": {"terms": {"field": "factor", "size": 40}, "aggs": {"values": {"terms": {"field": "value_keyword", "size": 8}}}},
            "novel": {"filter": {"term": {"novel": True}}, "aggs": {"names": {"terms": {"field": "factor", "size": 20}}}},
            "origins": {"terms": {"field": "origin", "size": 10}},
        }}

    def local_search(self, factor: str | None, value: str | None, scope_synthetic: bool | None) -> list[dict]:
        out = []
        for d in self.store.list("factors", 5000):
            if not d.get("latest"):
                continue
            if scope_synthetic is not None and bool(d.get("synthetic")) != scope_synthetic:
                continue
            if factor and d.get("factor") != factor:
                continue
            if value and value.lower() not in str(_display(d)).lower():
                continue
            out.append(d)
        return sorted(out, key=lambda d: -float(d.get("score") or 0))[:50]


def _display(doc: dict | None) -> Any:
    if not doc:
        return None
    for k in ("value_text", "value_keyword", "value_number", "value_bool"):
        if doc.get(k) is not None:
            return doc[k]
    return None

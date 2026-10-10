"""Preparation runs — what happens when you barge into a call (owner points 3, 4, 10).

A run fans out to every source for one seller (company + context first), reads the Elastic cache
before each API, persists responses asynchronously, assembles the production contract, asks
Sarvam for the persona, indexes the persona and composes the teleprompter. Every step is an event
streamed to the browser (SSE), and every API hit keeps its full request/response for the inspector.

Event shape:
    {seq, t_ms, node, kind, status, origin, label, detail, ms, ref}
    kind: start | cache | request | response | skip | error | fact | llm | index | persist | done
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Callable

from backend.genome import aggregator, contract, parsers, persona_generator, prompts, roster, teleprompter
from backend.genome.sarvam import BudgetExceeded, SarvamError

# Phase 1 — identity and context, in parallel. company.alias → company.detail and userCtx → getContext are chains.
SELLER_SOURCES_FIRST = ["company.alias", "context.global", "context.insights", "dataset.seller", "leads.purchased",
                        "enquiry.list", "pns.insights", "sts.history"]
# Phase 2 — history and enrichment; leads.detail ×N and lms.messages ×N fan out from phase-1 answers.
SELLER_SOURCES_SECOND = ["redash.hotlead", "redash.pns", "dataset.calls", "packages.catalog"]
LOCALITY_PROMPT = ("Extract the locality (neighbourhood/industrial area), city, state and pincode a field executive should visit. "
                   "Prefer the GST address when addresses disagree. Return JSON {locality, city, state, pincode, landmark, confidence 0-1, note}.")
MAPPING_POINTERS = ("/mapping_id", "/data/mapping_id", "/data/kycdetails/mapping_id", "/Data/mapping_id",
                    "/context_uid", "/data/context_uid", "/contextUID", "/data/contextUID", "/data/contextUid",
                    "/uid", "/data/uid", "/Data/context_uid", "/Data/uid")
UID_KEY_HINTS = ("mapping_id", "context_uid", "contextuid", "uid")


def mapping_id(result: dict | None) -> str:
    """generateContextUID → the mapping_id getContext takes (5.10.1; replaces userCtx). The field name is unconfirmed,
    so the documented-looking pointers come first, then any top-level or ``data`` key that names a UID."""
    if not result or result.get("status") != "ok":
        return ""
    response = result.get("response")
    for pointer in MAPPING_POINTERS:
        value = contract.pointer_get(response, pointer)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            return str(value).strip()
    for scope in (response, (response or {}).get("data") if isinstance(response, dict) else None,
                  (response or {}).get("Data") if isinstance(response, dict) else None):
        for key, value in (scope.items() if isinstance(scope, dict) else ()):
            if any(h in key.lower().replace("-", "_") for h in UID_KEY_HINTS) and isinstance(value, (str, int)) \
                    and not isinstance(value, bool) and str(value).strip():
                return str(value).strip()
    return ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


async def listing_with_fallback(hit, note, mcat: str, slug: str) -> dict:
    """The competitor listing for the seller's city; when the listing does not know the city (it answers an error, e.g.
    "bhiwandi" is "bhiwandi-maharashtra" there), ask again for all of India so the agents still get competitors."""
    got = await hit("market.listing", {"mcatid": mcat, "city_slug": slug})
    if slug and got.get("status") == "error":
        note(f"No listing for city '{slug}' — asking for all of India instead.")
        got = await hit("market.listing", {"mcatid": mcat, "city_slug": ""})
    return got


class Orchestrator:
    def __init__(self, settings, store, elastic, executor, sarvam, persona_index, private_hints: Callable[[str], dict] | None = None) -> None:
        self.settings, self.store, self.elastic = settings, store, elastic
        self.executor, self.sarvam, self.persona_index = executor, sarvam, persona_index
        self.private_hints = private_hints  # glid -> {"city","category",...} from the local dataset (best effort)
        self._waiters: dict[str, list[asyncio.Event]] = {}
        self._tasks: set[asyncio.Task] = set()
        elastic.on_flushed = self._on_flushed

    # --- events ---------------------------------------------------------------------------------

    def emit(self, run: dict, node: str, kind: str, status: str, label: str, detail: str = "", origin: str | None = None,
             ms: float | None = None, ref: str | None = None, extra: dict | None = None) -> dict:
        event = {"t_ms": round((time.time() - run["_started_epoch"]) * 1000, 1), "node": node, "kind": kind, "status": status,
                 "origin": origin, "label": label, "detail": detail[:400], "ms": ms, "ref": ref, "at": utc_now(), **(extra or {})}
        event = self.store.add_event(run["id"], event)
        for waiter in self._waiters.get(run["id"], []):
            waiter.set()
        if self.elastic.enabled and self.settings.get("elastic.persist_responses", True):
            self.elastic.queue("events", f"{run['id']}:{event['seq']}", {
                "run_id": run["id"], "seq": event["seq"], "node": node, "kind": kind, "origin": origin, "status": status,
                "ms": ms, "at": event["at"], "seller_id": run["seller_id"], "flow": run["flow"], "label": label})
        return event

    def _on_flushed(self, run_id: str | None, node: str | None, ok: bool, ms: float, idx: str) -> None:
        if not run_id or not node or not node.startswith(("persist:", "persona.index")):
            return
        run = self.store.get("runs", run_id)
        if not run:
            return
        key = f"_flushed:{node}"
        if run.get(key):
            return
        run[key] = True
        self.store.put("runs", run_id, run)
        api = node.split(":", 1)[1] if ":" in node else node
        event = self.store.add_event(run_id, {
            "t_ms": round((time.time() - run["_started_epoch"]) * 1000, 1), "node": "elastic.persist" if node.startswith("persist:") else "persona.index",
            "kind": "persist", "status": "ok" if ok else "error", "origin": "elastic",
            "label": f"Elasticsearch write · {api}" if node.startswith("persist:") else "Persona documents written",
            "detail": f"PUT {idx}/_doc/… {'acknowledged' if ok else 'failed — will retry'}", "ms": round(ms, 1), "ref": api, "at": utc_now()})
        for waiter in self._waiters.get(run_id, []):
            waiter.set()

    async def wait(self, run_id: str, timeout: float) -> None:
        event = asyncio.Event()
        self._waiters.setdefault(run_id, []).append(event)
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._waiters[run_id].remove(event)

    # --- runs -----------------------------------------------------------------------------------

    def create(self, seller_id: str, *, flow: str = "seller", origin: str = "floor", operator: bool = False,
               call_id: str | None = None) -> dict:
        seller = self._seller(seller_id)
        run_id = "run-" + secrets.token_hex(6)
        run = {"id": run_id, "seller_id": seller["seller_id"], "glid": seller["glid"], "flow": flow, "origin": origin,
               "operator": operator, "status": "running", "created_at": utc_now(), "call_id": call_id,
               "seller": roster.summary(seller), "results": {}, "nodes": {}, "persona": None, "contract": None,
               "teleprompter": None, "_started_epoch": time.time()}
        self.store.put("runs", run_id, run)
        task = asyncio.create_task(self._execute(run_id, seller))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    def _seller(self, seller_id: str) -> dict:
        hints = None
        if seller_id.startswith("real-") and self.private_hints:
            try:
                hints = self.private_hints(roster.glid_of(seller_id))
            except Exception:
                hints = None
        return roster.profile(seller_id, hints)

    async def _execute(self, run_id: str, seller: dict) -> None:
        run = self.store.get("runs", run_id)
        operator = run["operator"]
        if seller.get("synthetic"):
            self.emit(run, "trigger", "start", "ok", "Platform activity → hot lead",
                      f"{(seller.get('trigger') or {}).get('activity', 'Seller searched')} · GLID {seller['glid']} (simulated floor)", origin="internal")
        else:
            self.emit(run, "trigger", "start", "ok", "Operator selected a private seller",
                      f"GLID {seller['glid']} · no live activity feed is connected, so the latest STS entry stands in", origin="internal")
        self.emit(run, "orchestrator", "start", "ok", "Barging in — preparing context",
                  f"Fan-out to {len(SELLER_SOURCES_FIRST) + len(SELLER_SOURCES_SECOND) + 4} sources (+ buy-lead and chat fan-out), "
                  f"company + context first{self._gateway_note(operator)}", origin="internal")
        results: dict[str, dict] = {}
        semaphore = asyncio.Semaphore(int(self.settings.get("runs.max_concurrent_sources", 4)))

        async def hit(api_id: str, values: dict | None = None, key: str | None = None) -> dict:
            async with semaphore:
                api = self.executor.registry.get(api_id)
                node = key or api_id
                self.emit(run, node, "start", "running", api.label, self._request_line(api, seller, values), origin=None, ref=node)
                result = await self.executor.call(api_id, seller, values, run_id=run_id, operator=operator)
            results[node] = result
            if result.get("cache") is not None:
                c = result["cache"]
                self.emit(run, node, "cache", "hit" if c["hit"] else "miss", f"Elastic cache {'HIT' if c['hit'] else 'miss'} · {api.label}",
                          f"POST {c['index']}/_search · cache_key {c['key'][:12]}…", origin="elastic_cache" if c["hit"] else "elastic",
                          ms=c["ms"], ref=node, extra={"es_query": c["query"]})
            if result["status"] == "skipped":
                self.emit(run, node, "skip", "skipped", api.label, result["note"], origin="off", ref=node)
            elif result["status"] == "error":
                self.emit(run, node, "error", "error", api.label, f"{result.get('error')}: {result['note']}", origin=result.get("origin"), ms=result["ms"], ref=node)
            elif result["origin"] != "elastic_cache":
                size = len(json.dumps(result["response"], ensure_ascii=False, default=str)) if result["response"] is not None else 0
                self.emit(run, node, "response", "ok", api.label, f"{result['http_status']} · {size:,} bytes"
                          + (f" · {result['note']}" if result["note"] else ""), origin=result["origin"], ms=result["ms"], ref=node)
            return result

        try:
            # Phase 1: identity + context in parallel (company alias first because detail depends on it).
            async def company_chain() -> None:
                alias = await hit("company.alias")
                alias_value = (contract.pointer_get(alias.get("response"), "/URL_DETAIL/FREESHOWROOM_ALIAS") if alias["status"] == "ok" else None) or ""
                if not alias_value:  # the detail call needs the alias; an empty one only returns an empty page
                    record = contract.pointer_get(alias.get("response"), "/DATA/COMPANYDETAIL/DIR_SEARCH_COMPANY") if alias["status"] == "ok" else None
                    if record:
                        # Free sellers have no showroom alias, but the glusrid call already returns the full company record:
                        # reuse it as company.detail (labelled), so identity and persona use real data instead of "pending".
                        results["company.detail"] = {**alias, "reused_from": "company.alias",
                                                     "note": "Same live record as company.alias — the seller has no showroom alias, so no second call was made."}
                        self.emit(run, "company.detail", "response", "ok", self.executor.registry.get("company.detail").label,
                                  "No showroom alias (free seller) — the company.alias response already holds the company record; reused.",
                                  origin=alias.get("origin") or "live_api", ms=0.0, ref="company.detail")
                        return
                    self.emit(run, "company.detail", "skip", "skipped", self.executor.registry.get("company.detail").label,
                              f"No showroom alias from company.alias ({alias.get('status')}) — details not requested.", origin="off", ref="company.detail")
                    return
                await hit("company.detail", {"alias": alias_value})

            async def context_chain() -> None:
                ctx = await hit("context.global")
                mapping = mapping_id(ctx)
                await hit("context.getcontext", {"mapping_id": mapping} if mapping else {"source_uid": seller["glid"]})
            phase_one = [company_chain(), context_chain()] + [hit(a) for a in SELLER_SOURCES_FIRST if a not in ("company.alias", "context.global")]
            await asyncio.wait_for(asyncio.gather(*phase_one), timeout=self.settings.get("runs.timeout_seconds", 25))
            # Initial facts — usable before slower enrichment finishes.
            assembled = contract.assemble(seller, results, lead_id=run_id)
            if not seller.get("synthetic"):
                self._confirm_identity(run, seller, assembled)
            facts = {k: assembled["fields"][k]["value"] for k in ("company_name", "city", "state", "products", "custtypeweight") if k in assembled["fields"]}
            self.emit(run, "facts", "fact", "ok", "Initial facts ready",
                      f"{facts.get('company_name')} · {facts.get('city')} · {assembled['classification']} seller · "
                      f"{assembled['completeness']['present']}/{assembled['completeness']['total']} fields", origin="internal")
            # Phase 2: Redash, dataset calls, catalogue — and the buy-lead → chat fan-out.
            async def leads_and_chats() -> None:
                offers = parsers.lead_ids((results.get("leads.purchased") or {}).get("response"))[: int(self.settings.get("upstream.max_leads", 3))]
                fan = asyncio.Semaphore(int(self.settings.get("runs.buyer_concurrency", 3)))

                async def one(api_id: str, values: dict, key: str) -> None:
                    async with fan:
                        await hit(api_id, values, key=key)
                if not offers and (results.get("leads.purchased") or {}).get("status") == "ok":
                    self.emit(run, "leads.detail", "skip", "skipped", self.executor.registry.get("leads.detail").label,
                              "No purchased buy leads in the live response — no details to fetch.", origin="off", ref="leads.detail")
                await asyncio.gather(*[one("leads.detail", {"offerid": o}, f"leads.detail:{o}") for o in offers])
                contacts, why = self._lms_contacts(seller, results)
                if not contacts:
                    self.emit(run, "lms.messages", "skip", "skipped", self.executor.registry.get("lms.messages").label, why, origin="off", ref="lms.messages")
                    return
                await asyncio.gather(*[one("lms.messages", {"contact_glid": c}, f"lms.messages:{c}") for c in contacts])
            async def market_listing() -> None:
                """Competitors: sellers listing in the seller's main category (company API mcat) in the seller's city."""
                if seller.get("synthetic"):
                    return
                mcat = parsers.main_mcat((results.get("company.detail") or {}).get("response"))
                if not mcat:
                    self.emit(run, "market.listing", "skip", "skipped", self.executor.registry.get("market.listing").label,
                              "No category id in the company API answer — competitors come from Global Context instead.", origin="off", ref="market.listing")
                    return
                city = (assembled["fields"].get("city") or {}).get("value") or seller.get("city")
                label = self.executor.registry.get("market.listing").label
                await listing_with_fallback(hit, lambda detail: self.emit(run, "market.listing", "fallback", "running", label, detail,
                                                                          origin=None, ref="market.listing"),
                                            mcat, parsers.city_slug(city))
            await asyncio.wait_for(asyncio.gather(leads_and_chats(), market_listing(), *[hit(a) for a in SELLER_SOURCES_SECOND]),
                                   timeout=self.settings.get("runs.timeout_seconds", 25))
            assembled = contract.assemble(seller, results, lead_id=run_id)
            evidence = contract.evidence_items(assembled)
            # Phase 3: the persona — the owner's Persona Generator prompt (default) or the 5.x aggregation (Admin → Persona policy).
            if self.settings.get("persona.engine", "generator") == "generator":
                persona = await self._generate(run, seller, assembled, evidence, results)
            else:
                persona = await self._aggregate(run, seller, assembled, evidence, results)
            # Phase 4: locality (production spends ~18 s here) — only when we have addresses and budget.
            await self._locality(run, seller, assembled, results)
            # Phase 5: persona index + teleprompter.
            indexed = self.persona_index.index_persona(seller, persona, run_id, self.settings.get("elastic.index_personas", True))
            self.emit(run, "persona.index", "index", "queued" if indexed["queued"] else "ok", "Persona index",
                      f"{indexed['documents']} documents → {indexed['index']} (revision {indexed['revision']})",
                      origin="elastic", ref="persona.index", extra={"sample_doc": indexed["sample"]})
            tele = teleprompter.compose(seller, assembled, persona, self.settings)
            self.emit(run, "teleprompter", "done", "ok", "Teleprompter ready", tele["opening"][:160], origin="internal")
            self.emit(run, "voice.plugin", "skip", "waiting", "Voice bot (plug-in)",
                      "Context available at /api/v5/voice/sessions/{id}/context — no external bot connected; the built-in rehearsal is ready.",
                      origin="off")
            run = {**self.store.get("runs", run_id), "_started_epoch": run["_started_epoch"]}
            run.update(status="completed", contract=assembled, evidence=evidence, persona=persona, teleprompter=tele,
                       results=results, finished_at=utc_now(), persona_revision=indexed["revision"],
                       duration_ms=round((time.time() - run["_started_epoch"]) * 1000))
            self.emit(run, "run", "done", "ok", "Context ready — you are on the call",
                      f"{run['duration_ms']} ms total · persona from {persona['origin']}", origin="internal", ms=run["duration_ms"])
        except asyncio.TimeoutError:
            run = self.store.get("runs", run_id) or run
            run.update(status="degraded", results=results, error="Some sources timed out", finished_at=utc_now())
            self.emit(run, "run", "error", "error", "Some sources timed out", "Facts already published stay valid.", origin="internal")
        except Exception as error:  # surface, never hide
            run = self.store.get("runs", run_id) or run
            run.update(status="failed", results=results, error=f"{type(error).__name__}: {str(error)[:200]}", finished_at=utc_now())
            self.emit(run, "run", "error", "error", "Preparation failed", run["error"], origin="internal")
        finally:
            self.store.put("runs", run_id, run)

    def _request_line(self, api, seller: dict, values: dict | None) -> str:
        if api.kind == "dataset":
            return f"POST {api.url.replace('elasticsearch://', '')} · seller_id={seller['glid']}"
        if not api.url:
            return f"{api.method} (endpoint not supplied)"
        req = self.executor._describe(api, {"glid": seller["glid"], "empid": str(self.settings.get("upstream.employee_id", "")), **(values or {})}, None)
        return f"{api.method} {req['url'][:160]}" + (" · via gateway" if req.get("gateway") and not seller.get("synthetic") else "")

    def _confirm_identity(self, run: dict, seller: dict, assembled: dict) -> None:
        """Replace a private seller's pending identity with what live APIs (or the dataset) actually returned."""
        trusted = ("live_api", "elastic_cache", "local_db", "supplied_export")
        confirmed = {}
        for key in ("company_name", "contact_name", "city", "state", "business_nature"):
            f = assembled["fields"].get(key) or {}
            if f.get("availability") == "present" and f.get("origin") in trusted:
                confirmed[key] = f["value"]
        latest = next((h for h in assembled.get("history", []) if h.get("api") == "sts.history"), None)
        if latest:
            seller["trigger"] = {"activity": f"{latest['kind'].replace('STS · ', '').title()} on {str(latest['at'])[:10]}",
                                 "activity_id": None, "source": "sts.history", "detail": latest.get("summary", "")[:160]}
        if confirmed.get("state") and not confirmed.get("city"):
            confirmed["city"] = ""
        seller.update(confirmed)
        if confirmed.get("state"):
            seller["language"] = roster.language_for_state(confirmed["state"], seller.get("language", "hi-IN"))
        seller["identity"] = "confirmed" if confirmed.get("company_name") else "pending"
        stored = self.store.get("runs", run["id"]) or {}
        stored["seller"] = roster.summary(seller)
        self.store.put("runs", run["id"], stored)
        run["seller"] = stored["seller"]
        if confirmed:
            self.emit(run, "identity", "fact", "ok", "Identity confirmed from live data",
                      f"{seller.get('company_name')} · {seller.get('city') or '?'}, {seller.get('state') or '?'}"
                      + (f" · latest activity: {seller['trigger']['activity']}" if latest else "")
                      + f" · language guess {seller['language']} from state (confirm on the call)", origin="live_api", ref="company.detail")
        else:
            self.emit(run, "identity", "skip", "skipped", "Identity not confirmed",
                      "No live or dataset source returned the company — the header keeps 'Private seller'.", origin="off")

    def _gateway_note(self, operator: bool) -> str:
        from backend.genome.registry import gateway_base
        base = gateway_base(self.settings)
        if not base:
            return ""
        return f" · IndiaMART APIs via gateway {base}" if operator else " · IndiaMART APIs via the configured gateway"

    def _lms_contacts(self, seller: dict, results: dict) -> tuple[list[str], str]:
        """Who to read chats with: buyers from PNS and purchased leads, then configured test contacts.

        Live chat reads need BOTH GLIDs approved (V3 sources contract); synthetic sellers use their mock buyer.
        With upstream.glid_access = prod (D51) any numeric buyer GLID passes and the test contacts are skipped.
        """
        limit = int(self.settings.get("upstream.max_lms_contacts", 2))
        if seller.get("synthetic"):
            buyers = [b["glid"] for b in seller.get("buyers", [])][:1]
            return buyers, "Synthetic seller without buyers"
        glid = str(seller["glid"])
        candidates: list[str] = []
        pns = parsers.pns_calls((results.get("pns.insights") or {}).get("response"), glid)
        candidates += [c["counterpart_glid"] for c in pns if c["counterpart_glid"]]
        for key, r in results.items():
            if key.startswith("leads.detail:") and r.get("status") == "ok":
                lead = parsers.lead(r.get("response")) or {}
                if lead.get("buyer_glid"):
                    candidates.append(lead["buyer_glid"])
        if self.settings.get("upstream.glid_access", "dev") != "prod":
            candidates += [str(c) for c in self.settings.get("upstream.lms_test_contacts", []) or []]
        seen, out = set(), []
        for c in candidates:
            if c and c != glid and c not in seen and self.executor.approved(c):
                seen.add(c)
                out.append(c)
        return out[:limit], (f"None of {len(set(candidates))} related contacts is an approved test GLID — chat reads need both GLIDs approved."
                             if candidates else "No related contact found in PNS calls or buy leads.")

    async def _aggregate(self, run: dict, seller: dict, assembled: dict, evidence: list[dict], results: dict) -> dict:
        scope = "operator" if run["operator"] else "public"
        speakers = self.settings.get("persona.speakers", [])
        languages = self.settings.get("persona.languages", [])
        system, user = aggregator.build_prompt(seller, evidence, speakers, languages)
        digest = hashlib.sha256((system + user + self.settings.get("llm.model", "")).encode()).hexdigest()[:24]
        cached = self.store.get("aggregations", digest)
        max_age = float(self.settings.get("llm.aggregation_cache_hours", 24)) * 3600
        self.emit(run, "sarvam.aggregate", "start", "running", "Sarvam · persona aggregation",
                  f"{self.settings.get('llm.model')} · {len(evidence)} evidence items · reasoning {self.settings.get('llm.reasoning_effort')}",
                  origin="llm", ref="sarvam.aggregate")
        if cached and time.time() - cached["at_epoch"] < max_age:
            persona = cached["persona"]
            self.emit(run, "sarvam.aggregate", "cache", "hit", "Sarvam result reused",
                      f"Same evidence hash {digest[:10]}… aggregated {int((time.time() - cached['at_epoch']) // 60)} min ago — no new spend",
                      origin="elastic_cache", ms=0.0, ref="sarvam.aggregate")
            results["sarvam.aggregate"] = {"request": cached.get("request"), "response": cached.get("raw"), "ms": cached.get("ms"),
                                           "origin": "elastic_cache", "status": "ok", "label": "Sarvam · persona aggregation",
                                           "note": "Same evidence aggregated recently — reused, no new spend"}
            persona["completeness"] = assembled["completeness"]["present"] / max(1, assembled["completeness"]["total"])
            return persona
        try:
            reply = await self.sarvam.chat_json(system, user, scope=scope, run_id=run["id"], label="aggregate")
            persona = aggregator.validate(reply["data"], evidence, self.settings, seller)
            persona.update(origin="llm", model=reply["model"], ms=reply["ms"])
            self.store.put("aggregations", digest, {"persona": persona, "at_epoch": time.time(), "request": reply["request"],
                                                    "raw": reply["data"], "ms": reply["ms"]})
            self.emit(run, "sarvam.aggregate", "llm", "ok", "Sarvam · persona aggregation",
                      f"{len(persona['factors'])} factors · {len(persona['tags'])} tags · {len(persona['novel_factors'])} novel factors",
                      origin="llm", ms=reply["ms"], ref="sarvam.aggregate")
            results["sarvam.aggregate"] = {"request": reply["request"], "response": reply["data"], "ms": reply["ms"],
                                           "origin": "llm", "status": "ok", "label": "Sarvam · persona aggregation", "usage": reply.get("usage")}
        except (SarvamError, BudgetExceeded, ValueError) as error:
            persona = aggregator.rules_persona(seller, assembled, evidence, self.settings)
            persona.update(origin="rules", model="rules-v5", ms=0)
            results["sarvam.aggregate"] = {"request": {"model": self.settings.get("llm.model"), "system_prompt": system, "user": user[:6000]},
                                           "response": {"error": f"{type(error).__name__}: {str(error)[:200]}"}, "ms": 0,
                                           "origin": "default", "status": "error", "label": "Sarvam · persona aggregation"}
            self.emit(run, "sarvam.aggregate", "error", "fallback", "Sarvam unavailable → rules persona",
                      f"{type(error).__name__}: {str(error)[:160]}. Persona marked origin=rules.", origin="default", ref="sarvam.aggregate")
        persona["completeness"] = assembled["completeness"]["present"] / max(1, assembled["completeness"]["total"])
        return persona

    async def _generate(self, run: dict, seller: dict, assembled: dict, evidence: list[dict], results: dict) -> dict:
        """Persona Generator v0.3: minimised API input → Sarvam → automatic checks (+1 repair) → overlay on the rules persona."""
        scope = "operator" if run["operator"] else "public"
        fields = assembled.get("fields") or {}
        val = lambda k: (fields.get(k) or {}).get("value") if (fields.get(k) or {}).get("availability") == "present" else None  # noqa: E731
        lead = {"data_type": val("data_type"), "last_call_summary": val("last_call_summary"), "last_meeting_date": val("last_meeting_date")}
        if seller.get("synthetic"):
            lead["language"] = {"hi-IN": "Hindi", "en-IN": "English", "ta-IN": "Tamil", "te-IN": "Telugu", "mr-IN": "Marathi", "gu-IN": "Gujarati",
                                "bn-IN": "Bengali", "kn-IN": "Kannada", "ml-IN": "Malayalam", "pa-IN": "Punjabi", "od-IN": "Odia"}.get(seller.get("language"), "Hindi")
        today = datetime.now(parsers.IST).date()
        data, refs = persona_generator.build_input(seller, results, today, lead)
        base = aggregator.rules_persona(seller, assembled, evidence, self.settings)
        base.update(origin="rules", model="rules-v5", ms=0)
        system, _guide = persona_generator.prompt_parts()
        # The app version is part of the key: a release that changes the checks or the code fixes never reuses an old result.
        digest = hashlib.sha256((system + json.dumps(data, sort_keys=True, default=str) + self.settings.get("llm.model", "")
                                 + str(self.settings.get("app.version", ""))).encode()).hexdigest()[:24]
        cached = self.store.get("generations", digest)
        max_age = float(self.settings.get("llm.aggregation_cache_hours", 24)) * 3600
        label = "Sarvam · persona generator v0.3"
        self.emit(run, "sarvam.aggregate", "start", "running", label,
                  f"{len(data['records'])} records · {len(data['facts'])} facts · blocks {', '.join(k for k in data if k not in ('records', 'facts'))}",
                  origin="llm", ref="sarvam.aggregate")
        try:
            if cached and time.time() - cached["at_epoch"] < max_age:
                result = cached["result"]
                self.emit(run, "sarvam.aggregate", "cache", "hit", "Persona reused", f"Same input hash {digest[:10]}… — no new spend",
                          origin="elastic_cache", ms=0.0, ref="sarvam.aggregate")
            else:
                result = await persona_generator.generate(self.sarvam, data, refs, today, scope=scope, run_id=run["id"])
                self.store.put("generations", digest, {"result": result, "at_epoch": time.time()})
                ev = result["evaluation"]
                self.emit(run, "sarvam.aggregate", "llm", "ok", label,
                          f"quality {ev['score']}/100 · {ev['passed']}/{ev['total']} checks · {ev['critical']} critical · "
                          f"{len(result['attempts'])} attempt(s) · call plan: {((result['persona'].get('call_plan') or {}).get('trigger') or {}).get('type') or 'none'}",
                          origin="llm", ms=result["ms"], ref="sarvam.aggregate")
            persona = persona_generator.to_persona(result, base, self.settings)
            results["sarvam.persona_generator"] = {"request": result.get("request"), "response": result.get("persona"), "ms": result.get("ms"),
                                                   "origin": "llm", "status": "ok", "label": label, "evaluation": result.get("evaluation"),
                                                   "input": data}
        except (SarvamError, BudgetExceeded, ValueError) as error:
            persona = base
            results["sarvam.persona_generator"] = {"request": {"model": self.settings.get("llm.model")}, "response": {"error": f"{type(error).__name__}: {str(error)[:200]}"},
                                                   "ms": 0, "origin": "default", "status": "error", "label": label, "input": data}
            self.emit(run, "sarvam.aggregate", "error", "fallback", "Sarvam unavailable → rules persona",
                      f"{type(error).__name__}: {str(error)[:160]}. Persona marked origin=rules.", origin="default", ref="sarvam.aggregate")
        results["sarvam.aggregate"] = results["sarvam.persona_generator"]  # the graph's "Sarvam · persona" node and its inspector
        persona["completeness"] = assembled["completeness"]["present"] / max(1, assembled["completeness"]["total"])
        return persona

    async def _locality(self, run: dict, seller: dict, assembled: dict, results: dict) -> None:
        addresses = [a for a in assembled.get("addresses", []) if a.get("text")]
        if len(addresses) < 1:
            return
        scope = "operator" if run["operator"] else "public"
        key = hashlib.sha256(json.dumps([a["text"] for a in addresses]).encode()).hexdigest()[:20]
        cached = self.store.get("localities", key)
        self.emit(run, "sarvam.locality", "start", "running", "Sarvam · locality from addresses",
                  f"{len(addresses)} address candidates (production ExtractLocalityV1 took ~18.1 s)", origin="llm", ref="sarvam.locality")
        if cached:
            result, ms = cached["result"], 0.0
            self.emit(run, "sarvam.locality", "cache", "hit", "Locality reused", f"{result.get('locality')} · {result.get('city')}", origin="elastic_cache", ms=0.0, ref="sarvam.locality")
        else:
            try:
                reply = await self.sarvam.chat_json(
                    prompts.text("persona.locality"),
                    json.dumps([{"kind": a["kind"], "text": a["text"]} for a in addresses], ensure_ascii=False),
                    scope=scope, max_tokens=300, run_id=run["id"], label="locality")
                result, ms = reply["data"], reply["ms"]
                self.store.put("localities", key, {"result": result})
                self.emit(run, "sarvam.locality", "llm", "ok", "Sarvam · locality",
                          f"{result.get('locality')} · {result.get('city')} — {ms:.0f} ms vs ~18,121 ms in production", origin="llm", ms=ms, ref="sarvam.locality")
            except (SarvamError, BudgetExceeded) as error:
                self.emit(run, "sarvam.locality", "error", "skipped", "Locality not extracted", str(error)[:160], origin="off", ref="sarvam.locality")
                return
        loc = result.get("locality")
        if loc:
            f = assembled["fields"]["lead_locality_1"]
            if f["availability"] != "present":
                assembled["fields"]["lead_locality_1"] = contract.envelope("lead_locality_1", loc, "llm", "sarvam.locality", "/locality",
                                                                            note="Extracted by Sarvam from address candidates")
        assembled["locality"] = result
        results["sarvam.locality"] = {"response": result, "ms": ms, "origin": "llm", "status": "ok", "label": "Sarvam · locality",
                                      "request": {"model": self.settings.get("llm.model"), "addresses": [a["text"] for a in addresses]}}

    def public_run(self, run: dict, operator: bool) -> dict:
        """What the browser gets; PII redacted for anonymous viewers of private sellers (never happens for synthetic)."""
        out = {k: v for k, v in run.items() if not k.startswith("_")}
        out["results"] = {k: {kk: vv for kk, vv in v.items() if kk not in ("response", "request")} | {"has_response": v.get("response") is not None}
                          for k, v in (run.get("results") or {}).items()}
        return out

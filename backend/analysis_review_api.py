"""Aggregate PM study and authenticated, bounded historical evidence review."""

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from backend.analysis import report
from backend.call_diagnostics import PERSONAS, RULE_BY_ID, demo, evaluate


@contextmanager
def review_db(data_dir: Path, study_id: str):
    """Open only the fixed offline sidecar; do not create empty DBs or accept paths."""
    path = data_dir / "analysis-review.sqlite3"
    if not path.exists():
        raise HTTPException(
            503,
            "Private call-review data has not been installed. Aggregate findings remain available.",
        )
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(
            "SELECT value FROM metadata WHERE key='study_id'"
        ).fetchone()
        if not row or row[0] != study_id:
            raise HTTPException(
                503,
                "The call-review build does not match this report. Rebuild or restore the paired study artifacts.",
            )
        yield db
    finally:
        db.close()


def analysis_review_router(root: Path, get_config, operator) -> APIRouter:
    """Reuse existing PM sessions; reading history never enables upstream requests."""
    router = APIRouter(prefix="/api/analysis")

    def study():
        return report(root / "data/pm-analysis.json")

    def private_response(payload):
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/pm-study")
    def pm_study():
        """Only source definitions and aggregate counts are publicly readable."""
        return study()

    @router.get("/evaluator/demo")
    def evaluator_demo(
        scenario: Literal["timing", "early_end", "unknown"] = "timing",
    ):
        """No paid model, recording, seller lookup or fixture-to-real write."""
        return demo(scenario)

    @router.get("/review/calls")
    def review_calls(
        request: Request,
        scope: Literal[
            "all_general", "sample_general", "sample_all"
        ] = "sample_general",
        persona: str | None = None,
        reason: str | None = None,
        outcome: Literal["all", "meeting", "not_meeting"] = "all",
        page: int = Query(1, ge=1, le=10000),
    ):
        """Page selected historical calls with fixed filters and no public raw data."""
        operator(request)
        if persona is not None and persona not in PERSONAS:
            raise HTTPException(422, "Select a listed persona cohort.")
        if reason is not None and reason not in {*RULE_BY_ID, "unknown"}:
            raise HTTPException(422, "Select a listed primary review reason.")
        clauses, args = [], []
        if scope != "all_general":
            clauses.append("c.sample=1")
        if scope != "sample_all":
            clauses.append("c.label='General (talked)'")
        if persona:
            clauses.append(
                "EXISTS(SELECT 1 FROM memberships m WHERE m.case_id=c.id AND m.persona=?)"
            )
            args.append(persona)
        if reason:
            clauses.append("c.primary_reason=?")
            args.append(reason)
        if outcome != "all":
            clauses.append(
                "c.label "
                + ("=" if outcome == "meeting" else "!=")
                + " 'Meeting Fixed'"
            )
        where = " AND ".join(clauses) or "1=1"
        with review_db(get_config().data_dir, study()["study_id"]) as db:
            total = db.execute(
                "SELECT count(*) FROM cases c WHERE " + where, args
            ).fetchone()[0]
            rows = db.execute(
                "SELECT c.id,c.label,c.primary_reason,c.payload FROM cases c WHERE "
                + where
                + " ORDER BY c.id LIMIT 12 OFFSET ?",
                [*args, (page - 1) * 12],
            ).fetchall()
        items = []
        for row in rows:
            payload = json.loads(row["payload"])
            items.append(
                {
                    "id": row["id"],
                    "seller_glid": payload["seller_glid"],
                    "label": row["label"],
                    "primary_reason": row["primary_reason"],
                    "observed_at": payload["observed_at"],
                    "sample_member": payload["sample_member"],
                    "usable_turns": len(payload["turns"]),
                }
            )
        return private_response(
            {
                "scope": scope,
                "total": total,
                "page": page,
                "page_size": 12,
                "items": items,
            }
        )

    def load_case(identifier):
        if not re.fullmatch(r"review-[a-f0-9]{24}", identifier):
            raise HTTPException(404, "Historical review case not found.")
        with review_db(get_config().data_dir, study()["study_id"]) as db:
            row = db.execute(
                "SELECT payload FROM cases WHERE id=?", (identifier,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Historical review case not found.")
        return json.loads(row[0])

    @router.get("/review/calls/{identifier}")
    def review_case(identifier: str, request: Request):
        """Return authenticated summary/turn citations, never a signed audio URL."""
        operator(request)
        return private_response(load_case(identifier))

    @router.post("/review/calls/{identifier}/evaluate")
    def evaluate_case(identifier: str, request: Request):
        """Rerun the actual evaluator on the immutable local evidence, without writes."""
        operator(request)
        payload = load_case(identifier)
        prior = payload["evaluation"]
        payload["evaluation"] = evaluate(
            payload["summary"],
            payload["turns"],
            label=prior["source_label"],
            flag=prior["meeting_flag"],
        )
        payload["execution"] = {
            "mode": "local_historical",
            "provider_calls": 0,
            "recomputed": True,
            "source_modified": False,
        }
        return private_response(payload)

    return router

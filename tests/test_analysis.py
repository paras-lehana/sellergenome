"""Aggregate contracts and persona-field explanations must preserve privacy."""

import json

import httpx
import pytest
from fastapi import HTTPException

from backend.analysis import live_aggregate, persona_fields
from backend.deconstruction import deconstruct


@pytest.fixture
def anyio_backend():
    """Use the application's asyncio backend without a separate pytest plugin."""
    return "asyncio"


class AggregateSearch:
    """Capture exact outbound ES queries without any external network access."""

    def __init__(self, handler):
        self.handler = handler

    def client(self):
        return httpx.AsyncClient(
            base_url="http://elasticsearch",
            transport=httpx.MockTransport(self.handler),
        )


@pytest.mark.anyio
async def test_live_query_contains_no_raw_hits_and_preserves_denominator():
    """Public chart queries must request aggregates only, with a bounded taxonomy."""

    def handle(request):
        body = json.loads(request.content)
        assert body["size"] == 0
        assert body["query"] == {"term": {"month": "2026-09"}}
        assert body["aggs"]["groups"]["terms"]["size"] == 12
        return httpx.Response(
            200,
            json={
                "took": 2,
                "hits": {"total": {"value": 100}},
                "aggregations": {
                    "groups": {
                        "buckets": [{"key": "Meeting Fixed", "doc_count": 12}]
                    }
                },
            },
        )

    result = await live_aggregate(
        AggregateSearch(handle), "outcomes", "2026-09"
    )
    assert result["denominator"] == 100 and result["items"][0]["count"] == 12
    assert result["engine"] == "elasticsearch"


@pytest.mark.anyio
async def test_arbitrary_aggregate_contracts_make_zero_requests():
    """A caller cannot turn the chart service into an arbitrary ES query proxy."""

    def deny(request):
        raise AssertionError("Unexpected egress")

    for view, period in [("seller_id", "all"), ("outcomes", "2026-99")]:
        with pytest.raises(HTTPException) as error:
            await live_aggregate(AggregateSearch(deny), view, period)
        assert error.value.status_code == 422


@pytest.mark.anyio
async def test_persona_field_query_is_seller_and_mode_scoped():
    """Filtering for strong evidence cannot remove the private/demo boundary."""

    def handle(request):
        body = json.loads(request.content)
        assert body["query"]["bool"]["filter"] == [
            {"term": {"seller_id": "demo-asha"}},
            {"term": {"source_mode": "demo"}},
            {"term": {"strength": "strong"}},
        ]
        return httpx.Response(
            200, json={"took": 1, "hits": {"total": {"value": 0}, "hits": []}}
        )

    result = await persona_fields(
        AggregateSearch(handle), "demo-asha", "demo", "strong"
    )
    assert result["total"] == 0 and result["items"] == []


def test_deconstruction_retains_unknown_strength_and_original_evidence():
    """Legacy evidence is not promoted to strong just because it has confidence."""
    persona = {
        "revision": 3,
        "fields": {
            "language": {
                "value": "hi-IN",
                "confidence": 0.9,
                "evidence_ids": ["missing"],
                "reason": "Confirm preference.",
            }
        },
    }
    docs = deconstruct(
        persona, {"id": "private-test", "source_mode": "private"}, []
    )
    assert docs[0]["strength"] == "legacy_unclassified"
    assert docs[0]["citations"][0]["freshness"] == "unknown"
    assert docs[0]["source_mode"] == "private"
    assert docs[0]["persona_revision"] == 3


@pytest.mark.anyio
async def test_unavailable_live_query_never_returns_snapshot_as_live():
    """The dated report remains usable, but a failed live request stays failed."""

    def handle(request):
        return httpx.Response(503)

    with pytest.raises(HTTPException) as error:
        await live_aggregate(AggregateSearch(handle), "outcomes", "all")
    assert error.value.status_code == 503

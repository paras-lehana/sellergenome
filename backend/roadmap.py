"""Visible delivery map, aligned with the real PS04 judging rubric."""


def roadmap() -> dict:
    """Describe executable capabilities separately from remaining work."""
    items = [
        (
            "persona",
            "Evidence-backed personas",
            "implemented",
            "Generate one persona per seller, with field-level reasons, confidence, dates and source IDs.",
            "Inspect generated spec and cited evidence.",
        ),
        (
            "index",
            "Persona intelligence in Elasticsearch",
            "implemented",
            "Search original evidence and index typed AI-derived fields alongside provenance.",
            "Verify mapped nested fields, bulk ingest and filtered retrieval.",
        ),
        (
            "live",
            "Live conversation adaptation",
            "implemented",
            "Five mandatory signals plus stop intent; persistent before/after switch log.",
            "Submit your own turns and observe a genuine state change.",
        ),
        (
            "voice",
            "Sarvam voice rehearsal",
            "implemented",
            "Real bilingual voice examples, microphone transcription and response playback with quota fallback.",
            "Operator access and valid Sarvam credits; verify two languages.",
        ),
        (
            "llm",
            "Grounded model inference",
            "configuration_needed",
            "Authenticated gateway, validated fields and known evidence citations.",
            "Operator generates AI persona and inspects its indexed model provenance.",
        ),
        (
            "datasets",
            "Dataset and recording catalog",
            "implemented",
            "All supplied CSVs profiled; import and recording cohorts preserve data quality caveats.",
            "Re-run profiler; compare family counts and join/duplicate reports.",
        ),
        (
            "sources",
            "Connected seller context",
            "configuration_needed",
            "Bounded read-only connectors for context, company, STS, leads, enquiry, messages, PNS and saved Redash queries.",
            "Supply reachable source endpoints and scoped credentials; verify response mapping.",
        ),
        (
            "outcomes",
            "Actionable call outcomes",
            "implemented",
            "Proceed, hold or drop with a transparent readiness rubric.",
            "Finish calls with interest, uncertainty and stop intent.",
        ),
        (
            "diarization",
            "Speaker diarization jobs",
            "implemented",
            "Durable Sarvam batch jobs, timed speaker clusters, correction and explicit role review before private evidence ingestion.",
            "Review a consented multilingual gold sample and report diarization error.",
        ),
        (
            "duplex",
            "Full-duplex voice transport",
            "implemented",
            "WebSocket streaming STT/TTS, VAD endpointing, browser echo-control requests and speech-start barge-in. No telephone calls or WebRTC claim.",
            "Measure real end-of-turn to first audio p50/p95 and interruptions.",
        ),
        (
            "cowork",
            "Sarvam Cowork handoff",
            "planned",
            "Expose a scoped MCP tool surface or approved agent deployment after hackathon account capabilities are confirmed.",
            "Demonstrate a read-only authenticated tool call in the supplied account.",
        ),
        (
            "refresh_jobs",
            "Durable source refresh and index recovery",
            "implemented",
            "Operator job queue, safe receipts, retries/cancellation, opt-in refresh schedules and a persistent indexing outbox. Live source success requires configured upstream access.",
            "Queue a selected private seller, inspect failures and retries; save a paused schedule and verify index backlog recovery.",
        ),
        (
            "incremental",
            "Production change streams",
            "planned",
            "Production-scale CDC, source cursor checkpoints, deletion propagation and externally managed event streams beyond the built bounded refresh scheduler.",
            "Replay an update without stale data overwriting a newer observation.",
        ),
        (
            "evaluation",
            "Measured persona effectiveness",
            "planned",
            "Seller-disjoint, time-separated evaluation; human voice review; prospective conversion experiment.",
            "Report uncertainty; do not use balanced sample outcomes as population uplift.",
        ),
    ]
    return {
        "items": [
            {
                "id": i,
                "title": title,
                "status": status,
                "description": description,
                "acceptance": acceptance,
            }
            for i, title, status, description, acceptance in items
        ],
        "judging": [
            {"label": "Seller fit", "weight": 25},
            {"label": "Timely live switches", "weight": 25},
            {"label": "Distinct personas", "weight": 20},
            {"label": "Natural voice in 2+ languages", "weight": 15},
            {"label": "Past-call evidence", "weight": 15},
        ],
        "demo_script": [
            "Open Asha, Kabir and Meera; compare delivery choices and their evidence.",
            "Generate an AI-assisted persona with operator access and inspect the method/model/index result.",
            "Start a rehearsal. Say 'I do not understand', then 'I am in a hurry', then request Hindi.",
            "Play each reply through Sarvam; show the switch log and source timestamps.",
            "Finish the session and show the next action and readiness rubric.",
            "Open Data Studio to explain the full research corpus and honest integration gaps.",
        ],
    }

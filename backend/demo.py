"""Synthetic, clearly labeled sellers for safe public hackathon rehearsal."""

from datetime import datetime, timedelta, timezone


def demo_sellers() -> list[dict]:
    """Build contrasting scenarios without copying a supplied seller identity."""
    now = datetime.now(timezone.utc)
    rows = [
        (
            "demo-asha",
            "Asha",
            "Asha Industrial Supply",
            "Jaipur",
            "Industrial pumps",
            "hi-IN",
            "price_sensitive",
            "paid",
            "Asked for a clear explanation of value before discussing an upgrade.",
            "Please explain the benefit in Hindi. Keep it practical.",
            0.9,
        ),
        (
            "demo-kabir",
            "Kabir",
            "Kabir Packaging Works",
            "Pune",
            "Packaging materials",
            "en-IN",
            "rushed",
            "paid",
            "Requested a short call after a busy dispatch shift.",
            "I only have a minute. What is the next step?",
            1.15,
        ),
        (
            "demo-meera",
            "Meera",
            "Meera Textile Studio",
            "Chennai",
            "Cotton textiles",
            "ta-IN",
            "curious",
            "free",
            "Asked how relevant enquiries are matched to the business.",
            "Can you show me how it works? I prefer Tamil.",
            0.95,
        ),
        (
            "demo-ravi",
            "Ravi",
            "Ravi Machine Tools",
            "Delhi",
            "Machine tools",
            "hi-IN",
            "frustrated",
            "paid",
            "Expressed frustration with repeat questions; an unresolved concern needs acknowledgment.",
            "I have already explained this. Please resolve my concern first.",
            0.85,
        ),
        (
            "demo-noor",
            "Noor",
            "Noor Homewares",
            "Lucknow",
            "Home furnishings",
            "en-IN",
            "confused",
            "free",
            "Requested a simpler explanation of enquiries and buyleads.",
            "I am not sure I understand the difference.",
            0.85,
        ),
        (
            "demo-dev",
            "Dev",
            "Dev Solar Components",
            "Ahmedabad",
            "Solar equipment",
            "en-IN",
            "interested",
            "paid",
            "Expressed interest in learning one concrete next step.",
            "That sounds useful. What should we do next?",
            1.0,
        ),
    ]
    sellers = []
    for (
        identifier,
        name,
        company,
        city,
        category,
        language,
        tag,
        segment,
        note,
        quote,
        pace,
    ) in rows:
        evidence = []
        for index, (kind, text, age, ttl) in enumerate(
            [
                (
                    "profile",
                    f"Synthetic business profile: {category}; explicit language preference {language}.",
                    48,
                    720,
                ),
                ("call", note, 3, 2160),
                ("conversation", quote, 0.25, 2),
                (
                    "activity",
                    "Demonstration only: seller reviewed an enquiry earlier in this scenario.",
                    4,
                    24,
                ),
                (
                    "activity",
                    "Older demonstration activity retained for audit; do not treat as current intent.",
                    96,
                    24,
                ),
            ]
        ):
            evidence.append(
                {
                    "id": f"{identifier}-ev-{index + 1}",
                    "seller_id": identifier,
                    "kind": kind,
                    "text": text,
                    "observed_at": (now - timedelta(hours=age)).isoformat(),
                    "ingested_at": now.isoformat(),
                    "ttl_hours": ttl,
                    "confidence": 1.0,
                    "source_ref": "Synthetic rehearsal fixture",
                    "source_mode": "demo",
                }
            )
        sellers.append(
            {
                "id": identifier,
                "name": name,
                "company": company,
                "city": city,
                "category": category,
                "language": language,
                "segment": segment,
                "persona_tag": tag,
                "source_mode": "demo",
                "updated_at": now.isoformat(),
                "metrics": {
                    "preferred_pace": pace,
                    "profile_completeness": 100,
                },
                "evidence": evidence,
            }
        )
    return sellers

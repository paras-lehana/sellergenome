"""Synthetic, explicitly dated source responses; never copied private records."""

import copy

SAMPLE_DATE = "2026-09-30 12:00:00"
SAMPLES = {
    "redash_hotlead": [
        {
            "data_hotlead_disposition_dtlid": "sample-disposition-1",
            "fk_click_to_call_id": "sample-call-1",
            "fk_glusr_usr_id": "sample",
            "call_start_time": SAMPLE_DATE,
            "last_updated_on": SAMPLE_DATE,
            "lead_call_duration": 98,
            "lead_call_summary": "The seller asked for a concise explanation of relevant enquiries, preferred Hindi, raised a pricing concern, and agreed to discuss a possible meeting after checking availability. This is synthetic illustrative evidence.",
            "lead_shl_trigger": 1,
        }
    ],
    "redash_recordings": [
        {
            "call_recordings_detail_id": "sample-recording-1",
            "fk_click_to_call_id": "sample-call-1",
            "fk_glusr_usr_id": "sample",
            "call_start_time": SAMPLE_DATE,
            "updated_on": SAMPLE_DATE,
            "status": "available",
        }
    ],
    "redash_executive": [{"fk_glusr_usr_id": "sample", "call_count": 3}],
    "redash_pns": [
        {
            "pns_call_record_id": "sample-pns-1",
            "pns_call_receiver_glusr_id": "sample",
            "pns_call_record_date": SAMPLE_DATE,
            "pns_call_status": "answered",
            "pns_call_patch_duration": 47,
            "pns_call_ring_duration": 8,
            "pns_call_ref_prime_mcat": "synthetic-category",
        }
    ],
    "context": [
        {
            "updated_at": SAMPLE_DATE,
            "preferred_language": "hi-IN",
            "kycdetails": {
                "custtype_weight": 500,
                "business_type": "manufacturer",
                "turnover_band": "not_provided",
            },
            "activitydetails": {
                "activity_count": 8,
                "last_activity": SAMPLE_DATE,
            },
        }
    ],
    "company": [
        {
            "updated_at": SAMPLE_DATE,
            "category": "Industrial packaging",
            "business_type": "manufacturer",
            "product_count": 12,
            "turnover_band": "not_provided",
        }
    ],
    "chat": [
        {
            "message_id": "sample-message-1",
            "msg_date": SAMPLE_DATE,
            "msg_query_type": "enquiry",
            "msg_ref_type": "product",
            "msg_call_status": "answered",
            "msg_call_duration": 24,
            "message_text": "Could you explain the packaging specification in one short step? Synthetic seller/buyer message; speaker role requires verification.",
        }
    ],
    "pns": [
        {
            "file_id": "sample-pns-insight-1",
            "call_timestamp": SAMPLE_DATE,
            "extracted_data": {
                "metadata": {
                    "primary_language": "Hindi",
                    "buyer_intent": {"intent_level": "interested"},
                    "buyer_persona": {"persona_category": "retailer"},
                    "call_outcome": {
                        "category": "clarification",
                        "conclusion_notes": "The buyer requested availability; the seller explained the product specification. These are synthetic participants.",
                    },
                },
                "lead_tag": {"deal_readiness": "needs_information"},
                "next_steps": {
                    "callback": True,
                    "seller_next_steps": "Clarify the requested specification",
                    "buyer_next_steps": "Confirm quantity",
                },
            },
        }
    ],
    "sts": [
        {
            "id": "sample-history-1",
            "observed_at": SAMPLE_DATE,
            "status": "follow_up",
            "summary": "Synthetic seller requested a later conversation after resolving an existing query.",
        }
    ],
    "buyleads": [
        {
            "lead_id": "sample-lead-1",
            "created_at": SAMPLE_DATE,
            "category": "Industrial packaging",
            "status": "available",
            "summary": "Synthetic buyer requirement; not evidence of the seller's current intent.",
        }
    ],
    "enquiries": [
        {
            "id": "sample-enquiry-1",
            "observed_at": SAMPLE_DATE,
            "category": "Industrial packaging",
            "enquiries_received": 4,
            "status": "received",
        }
    ],
}


def sample_records(source: str) -> list[dict]:
    """Return isolated copies so a caller cannot mutate another replay."""
    return copy.deepcopy(SAMPLES[source])

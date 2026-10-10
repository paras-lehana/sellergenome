"""Typed extension boundary for as-yet-unprovided company API contracts.

Endpoint contracts are reviewed server files, not arbitrary browser URLs. Missing
contracts produce a precise diagnostic; related identities are checked at egress.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from backend.admin_config import secret_value
from backend.source_policy import require_approved
from backend.context_contract import pointer_get

CONTRACTS=Path(__file__).resolve().parents[1]/'config/source-contracts.json'
SECRETS={'relationships':'RELATIONSHIPS_API_KEY','buyer_details':'BUYER_DETAILS_API_KEY','product_context':'PRODUCT_API_KEY','package_catalog':'PACKAGE_API_KEY'}


class ContextAdapter:
    def __init__(self, config, transport=None, contracts=None):
        self.config,self.transport=config,transport
        self.contracts=contracts if contracts is not None else json.loads(CONTRACTS.read_text())['contracts']

    async def fetch(self, source, seller_id, buyer_id=None):
        seller=require_approved(seller_id)
        buyer=require_approved(buyer_id) if buyer_id else None
        contract=self.contracts.get(source)
        if not contract:
            raise ValueError('not_configured: verified '+source+' endpoint contract is required; no request sent')
        endpoint=contract['endpoint']
        parsed=urlsplit(endpoint)
        if parsed.scheme!='https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Reviewed extension endpoints require fixed HTTPS URLs without embedded credentials.')
        params={contract['seller_parameter']:seller}
        if buyer:
            params[contract['buyer_parameter']]=buyer
        key=secret_value(SECRETS[source])
        if contract.get('credential_required') and not key:
            raise ValueError('credential_not_configured: '+source)
        headers={contract.get('credential_header','Authorization'):key} if key else {}
        require_approved(seller_id)
        if buyer_id: require_approved(buyer_id)
        async with httpx.AsyncClient(timeout=15,transport=self.transport,follow_redirects=False) as client:
            response=await client.get(endpoint,params=params,headers=headers)
            response.raise_for_status()
            if len(response.content)>2_000_000:
                raise ValueError('Source response exceeds the bounded payload size.')
            payload=response.json()
        identity=pointer_get(payload,contract['response_identity_pointer'])
        if str(identity)!=str(buyer or seller):
            raise ValueError('Returned identity does not match the requested subject.')
        return {'payload':payload,'exchange':{'method':'GET','endpoint':endpoint,'parameters':params,'headers':{'credential':'[server-held]'},'status':response.status_code}}


def related_buyers(records, limit=10):
    """Read explicit buyer identifiers only; do not guess from generic GLIDs."""
    found=[]
    for record in records:
        for key in ('buyer_glid','fk_buyer_glusr_usr_id','buyer_id'):
            value=record.get(key)
            if value is not None and str(value) not in found:
                found.append(str(value))
    return found[:limit]

"""Call one Sarvam Voice Agents MCP tool with SellerGenome's stored OAuth session. Runs INSIDE the live container
(the token file must stay owned by uid 1000), prints the JSON result. Read-mostly by design.

  docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - TOOL 'JSON_ARGS' < mcp_call.py
  e.g.  … python - tools                                         < mcp_call.py   # list tool names
        … python - agents '{"operation":"list"}'                 < mcp_call.py
        … python - knowledge_base '{"operation":"list"}'         < mcp_call.py
        … python - analytics '{"operation":"…"}'                 < mcp_call.py

Refuses telephony/deployment/campaign tools (never allowed) and destructive KB deletes (owner approval first).
Configuration writes (configure_*, create_*) should go through Admin → Build, which records ids and versions.
"""
import asyncio
import json
import sys

from backend.genome.sarvam_voice import VoiceAuth, VoiceMCP

NEVER = {"telephony", "configure_deployment", "configure_connection", "configure_endpoint", "verify_phone_number", "place_test_call",
         "campaigns", "configure_campaign", "stream_cohort_users", "upload_cohort"}
OWNER_ONLY = {"delete_kb", "delete_kb_file"}
WRITES = ("configure_", "create_", "upload_", "kb_file_upload_")


async def main() -> None:
    tool = sys.argv[1] if len(sys.argv) > 1 else "tools"
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    if tool in NEVER:
        sys.exit(f"{tool}: not allowed for SellerGenome (no telephony, deployments or campaigns).")
    if tool in OWNER_ONLY:
        sys.exit(f"{tool}: destructive — needs the owner's explicit OK; do it in the Sarvam dashboard or Admin.")
    if tool.startswith(WRITES):
        print(f"warning: {tool} writes configuration; prefer Admin → Sarvam voice agent → Build", file=sys.stderr)
    log: list = []
    async with VoiceMCP(VoiceAuth(), log) as m:
        if tool == "tools":
            print("\n".join(sorted(t["name"] for t in await m.tools())))
            return
        result = await m.call(tool, args, quiet=True)
        print(json.dumps(result, ensure_ascii=False, indent=1, default=str)[:20000])


asyncio.run(main())

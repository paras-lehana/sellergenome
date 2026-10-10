# Sarvam Voice Agents Code Tools

Upload adapters only after OAuth verifies organization01a110b3-f038-7adb-b627-6863a3785c40 and the intended workspace. Model API-key readiness does not establish Voice Agents access. The pinned SDK is isolated from the portal.

`client.py` supplies context loading, additional evidence, incidental observations, catalog lookup, test meeting proposals, buyer tools and final outcome persistence against the same validated backend. Workspace secret references: SELLERGENOME_URL and SELLERGENOME_SERVICE_KEY. Runtime variables: run_id, subject_id, voice_session_id and optional buyer_conversation_id. Never put credentials or full private context in dispatch metadata.

OnStart must fetch the explicit run_id, expose bounded rendered persona instructions and confirm readiness. OnEnd persists the final outcome to the voice-session endpoint. Hook class names must be exactly OnStart and OnEnd; see SDK wrappers in hooks.py. Provider deployment requires a verified workspace and remains separate from these locally tested contracts. Nothing here activates outbound dialing.

Build the single uploadable file with `python -m integrations.sarvam_code_tools.build_bundle --output /private/path/tools.py`. It combines client helpers, seven typed tools and both hooks without repository-relative imports. Import-check it in the pinned SDK before uploading. Set the additional workspace secret `SELLERGENOME_ORGANIZATION_ID` to the approved organization after independently verifying OAuth workspace identity. This local fence is not remote proof of workspace ownership. Evidence and persona tools additionally restrict their subject/snapshots to the prepared run.

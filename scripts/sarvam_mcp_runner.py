"""Isolated pinned MCP client. Input arrives via stdin; key only via env."""
import asyncio
import json
import sys
import os
from fastmcp import Client
from sarvam_mcp.server import build_server


async def main():
    request=json.loads(sys.stdin.read())
    async with Client(build_server()) as client:
        try:
            result=await client.call_tool(request['name'],request['arguments'])
        except Exception as error:
            message=str(error).replace(os.getenv('SARVAM_API_KEY','') or '[absent-key]','[REDACTED]')
            print(json.dumps({'is_error':True,'content':[{'type':'text','text':json.dumps({'error_type':type(error).__name__,'error':message[:600]})}]}))
            return
        # Text/resources only; never dump base64 audio into application traces.
        blocks=[]
        for item in result.content:
            value=item.model_dump(mode='json')
            if value.get('type')=='text':
                blocks.append({'type':'text','text':value.get('text','')[:20000]})
            elif value.get('type')=='resource':
                blocks.append({'type':'resource','uri':value.get('resource',{}).get('uri')})
        print(json.dumps({'content':blocks,'is_error':getattr(result,'is_error',False)}))

asyncio.run(main())

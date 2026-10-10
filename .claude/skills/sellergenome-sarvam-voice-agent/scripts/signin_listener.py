#!/usr/bin/env python3
"""Reconnect the Sarvam Voice MCP sign-in from the owner's browser (5.13.7 incident, 2026-10-10).

Sarvam redirects the browser to http://127.0.0.1:53682/callback?code=…. Run this on the rig, and VS Code forwards
port 53682 to the owner's machine (Ports panel → Forward a Port → 53682), so the callback lands here.
1. Admin → Sarvam voice agent → *Get sign-in link* (or POST /api/v5/admin/sarvam-voice/connect), then send the
   owner the link.
2. The owner signs in. Sarvam may show "Sign in to approve" with a code: approve it in the window that opens.
3. This listener completes the sign-in inside the app container. The app writes the token file with the right owner.
   The page then shows "connected", and the listener exits.

It is threaded on purpose: VS Code's forwarder opens an idle connection first, which blocked a single-threaded server.
Usage: python3 signin_listener.py [container]          (default container: sellergenome-v4)
"""
import http.server
import json
import subprocess
import sys
import threading
from html import escape

CONTAINER = sys.argv[1] if len(sys.argv) > 1 else "sellergenome-v4"
COMPLETE = ("import asyncio, json, sys\n"
            "from backend.genome.sarvam_voice import VoiceAuth\n"
            "print(json.dumps(asyncio.run(VoiceAuth().complete(sys.stdin.read()))))\n")


def complete(address: str) -> tuple[bool, str]:
    r = subprocess.run(["docker", "exec", "-i", "-e", "PYTHONPATH=/app", "-w", "/app", CONTAINER, "python", "-c", COMPLETE],
                       input=address, capture_output=True, text=True, timeout=120)
    try:
        status = json.loads(r.stdout.strip().splitlines()[-1])
        return bool(status.get("connected")), f"connected · token valid {status.get('expires_in_hours')} h"
    except (ValueError, IndexError):
        return False, (r.stderr.strip().splitlines() or ["no answer"])[-1][:300]


class Handler(http.server.BaseHTTPRequestHandler):
    timeout = 15   # an idle pre-connection must not hold a thread forever

    def do_GET(self):  # noqa: N802
        if not self.path.startswith("/callback"):
            return self.reply("<p>SellerGenome sign-in listener is running. Finish the Sarvam sign-in to land here.</p>")
        ok, detail = complete("http://127.0.0.1:53682" + self.path)
        self.reply(("<h2>SellerGenome is connected to Sarvam Voice Agents ✓</h2><p>You can close this tab.</p>" if ok
                    else f"<h2>Not connected yet</h2><p>{escape(detail)}</p><p>Open the sign-in link again and approve.</p>"))
        print("connected" if ok else f"failed: {detail}", flush=True)
        if ok:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def reply(self, body: str) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<!doctype html><meta charset=utf-8><body style='font-family:sans-serif;padding:24px'>{body}</body>".encode())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer.daemon_threads = True
    http.server.ThreadingHTTPServer(("127.0.0.1", 53682), Handler).serve_forever()

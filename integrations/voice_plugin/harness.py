"""Simulate a voice-bot call against SellerGenome so it shows on the floor as a REAL CALL tile.

    SELLERGENOME_URL=https://sellergenome.lehana.in VOICE_PLUGIN_KEY=... python harness.py --glid 91000103
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from client import GenomeVoiceClient  # noqa: E402

SCRIPT = [
    ("bot", "Hello, this is Payal, IndiaMART's virtual assistant. You just bought a lead top-up — do you have two minutes?"),
    ("seller", "I'm on the shop floor, make it quick."),
    ("bot", "One line: you used 18 of 20 leads in 4 days. A monthly quota could save top-ups. Call at 6 pm?"),
    ("seller", "Hindi mein boliye please."),
    ("bot", "Ji bilkul. Shaam 6 baje executive aapko call karenge — theek rahega?"),
    ("seller", "Haan theek hai, 6 baje."),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glid", default="91000103")
    ap.add_argument("--delay", type=float, default=2.5)
    args = ap.parse_args()
    bot = GenomeVoiceClient()
    session = bot.register(args.glid, provider="harness")
    print("session", session["id"], "run", session["run_id"])
    ctx = bot.context()
    print("voice", ctx["voice"], "| opening:", (ctx["teleprompter"] or {}).get("opening"))
    started = time.time()
    for speaker, text in SCRIPT:
        ack = bot.events({"type": "transcript", "speaker": speaker, "text": text, "t_ms": int((time.time() - started) * 1000)})
        if bot.changed(ack):
            ctx = bot.context()
            print(f"context revision {ctx['context_revision']}: language now {ctx['voice']['language']}")
        print(f"{speaker:>6}: {text}")
        time.sleep(args.delay)
    bot.events({"type": "outcome", "label": "meeting_fixed", "details": "Executive call at 6 pm"},
               {"type": "ended", "reason": "completed", "duration_ms": int((time.time() - started) * 1000)})
    print("ended")


if __name__ == "__main__":
    main()

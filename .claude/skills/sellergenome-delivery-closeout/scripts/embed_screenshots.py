#!/usr/bin/env python3
"""Embed screenshots into the ONE delivery document as compressed JPEG data URIs (≤640 px wide, ~20–40 KB each),
so the owner can forward a single file. Uses ffmpeg (no PIL on the host).

Usage: embed_screenshots.py DOC.md PLACEHOLDER SHOT.png "caption" [SHOT2.png "caption2" …]
  Replaces the PLACEHOLDER text (e.g. {{SCREENSHOTS}}) with captioned images.
  CROP=900 env keeps only the top N pixels of tall captures (default 900; use 1500 for long tables).
Keep the whole document under ~1 MB (6–12 images). Never embed screenshots that show secrets or private sellers.
"""
import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path

doc, placeholder, pairs = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
if len(pairs) % 2:
    sys.exit("give SHOT caption pairs")
crop = int(os.environ.get("CROP", "900"))
parts = []
with tempfile.TemporaryDirectory() as tmp:
    for i in range(0, len(pairs), 2):
        png, caption = pairs[i], pairs[i + 1]
        jpg = Path(tmp) / f"{i}.jpg"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", png, "-vf", f"crop=iw:min(ih\\,{crop}):0:0,scale='min(640,iw)':-2",
                        "-q:v", "9", str(jpg)], check=True)
        data = base64.b64encode(jpg.read_bytes()).decode()
        parts.append(f"*{caption}*\n\n![{Path(png).stem}](data:image/jpeg;base64,{data})\n")
        print(f"{png}: {jpg.stat().st_size // 1024} KB")
text = doc.read_text()
if placeholder not in text:
    sys.exit(f"{placeholder} not found in {doc}")
doc.write_text(text.replace(placeholder, "\n".join(parts), 1))
print(f"{doc}: {doc.stat().st_size // 1024} KB")

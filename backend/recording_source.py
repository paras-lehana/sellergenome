"""Fixed dataset recording lookup; never accepts a caller-supplied URL."""

import csv
import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from backend.config import ROOT
from backend.provider_errors import ProviderUnavailable


class RecordingSource:
    def __init__(self, config):
        self.config = config
        self.locations = None

    def locate(self, identifier):
        if self.locations is None:
            self.locations = {}
            dataset = Path(os.getenv("DATASET_DIR", str(ROOT / "dataset")))
            for path in sorted(dataset.rglob("*recording_sample*.csv")):
                with path.open(encoding="utf-8-sig", newline="") as file:
                    for row in csv.DictReader(file):
                        key = (
                            "recording-"
                            + hashlib.sha256(
                                row["attempt_id"].encode()
                            ).hexdigest()[:24]
                        )
                        self.locations[key] = row.get("call_recording_url", "")
        url = self.locations.get(identifier, "")
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname
            not in self.config.values["voice"]["recording_hosts"]
            or parsed.port not in (None, 443)
            or parsed.username
        ):
            raise HTTPException(
                404,
                "Recording source is unavailable or outside the configured host allowlist.",
            )
        return url

    async def fetch(self, identifier):
        url = self.locate(identifier)
        maximum = (
            self.config.values["voice"]["batch_max_upload_mb"] * 1024 * 1024
        )
        try:
            async with httpx.AsyncClient(
                timeout=30, follow_redirects=False
            ) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise ProviderUnavailable(
                            "Original recording is unavailable from its source. No audio was substituted.",
                            "recording_source",
                            response.status_code,
                        )
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > maximum:
                            raise HTTPException(
                                413,
                                "Source recording exceeds the upload limit.",
                            )
                    if not content:
                        raise ProviderUnavailable(
                            "Original recording source returned no audio.",
                            "recording_source",
                        )
                    # Provided source is MP3; accept WAV by magic, never HTML/error documents.
                    if content[:4] == b"RIFF":
                        extension = "wav"
                    elif content[:3] == b"ID3" or (
                        content[0] == 255 and content[1] & 224 == 224
                    ):
                        extension = "mp3"
                    else:
                        raise ProviderUnavailable(
                            "Original recording format was not recognized.",
                            "recording_source",
                        )
                    return bytes(content), extension
        except httpx.HTTPError as error:
            raise ProviderUnavailable(
                "Original recording source could not be reached.",
                "recording_source",
            ) from error

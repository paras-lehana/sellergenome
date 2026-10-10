"""Durable, private batch diarization and human-reviewed transcript ingestion."""

import asyncio
import json
import os
import secrets
import time
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from backend.persona import utcnow
from backend.provider_errors import ProviderUnavailable
from backend.admin_config import find_secret_version

AUDIO_TYPES = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/vnd.wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/webm": "webm",
    "video/webm": "webm",
    "audio/ogg": "ogg",
}
TERMINAL = {"completed", "failed", "cancelled", "approved"}


def storage_url(value):
    """Only signed URLs returned by our authenticated provider, never browser URLs."""
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ProviderUnavailable(
            "Provider returned an unsupported storage URL.", "sarvam"
        )
    if not (
        host == "api.sarvam.ai"
        or host.endswith(".blob.core.windows.net")
        or host == "storage.googleapis.com"
        or host.endswith(".storage.googleapis.com")
    ):
        raise ProviderUnavailable(
            "Provider storage host is outside the allowlist.", "sarvam"
        )
    return value


def normalize_segments(result):
    """Validate provider timings/clusters, never guess identities or fabricate segments."""
    entries = result.get("diarized_transcript", {}).get("entries", [])
    if not isinstance(entries, list) or len(entries) > 2000:
        raise ProviderUnavailable(
            "Diarization returned an invalid segment list.", "sarvam"
        )
    segments = []
    for entry in entries:
        try:
            start, end = (
                float(entry["start_time_seconds"]),
                float(entry["end_time_seconds"]),
            )
            text, speaker = entry["transcript"], str(entry["speaker_id"])
            if not (
                0 <= start < end <= 7200
                and isinstance(text, str)
                and 0 < len(text) <= 6000
                and len(speaker) <= 40
            ):
                raise ValueError()
            segments.append(
                {"start": start, "end": end, "speaker": speaker, "text": text}
            )
        except (KeyError, ValueError, TypeError) as error:
            raise ProviderUnavailable(
                "Diarization returned invalid timing or text.", "sarvam"
            ) from error
    if not segments:
        raise ProviderUnavailable(
            "No diarized speech was returned; review the input audio.",
            "sarvam",
        )
    return segments


class SpeechService:
    def __init__(self, config, store, providers):
        self.config, self.store, self.pool = config, store, providers.sarvam
        self.directory = config.data_dir / "speech"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)
        with store.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS speech_jobs(id TEXT PRIMARY KEY,owner TEXT NOT NULL,payload TEXT NOT NULL)"
            )
        self.task = None
        self.wake = asyncio.Event()

    def save(self, job):
        with self.store.connect() as db:
            # Cancellation fences all later saves, including completion after a slow fetch.
            row = db.execute(
                "SELECT payload FROM speech_jobs WHERE id=?", (job["id"],)
            ).fetchone()
            if (
                row
                and json.loads(row[0])["status"] == "cancelled"
                and job["status"] != "cancelled"
            ):
                return
            db.execute(
                "INSERT OR REPLACE INTO speech_jobs VALUES(?,?,?)",
                (job["id"], job["owner"], json.dumps(job)),
            )

    def get(self, identifier, owner):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM speech_jobs WHERE id=? AND owner=?",
                (identifier, owner),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Speech job not found in this browser.")
        return json.loads(row[0])

    def list(self, owner=None):
        with self.store.connect() as db:
            if owner is None:
                rows = db.execute("SELECT payload FROM speech_jobs").fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM speech_jobs WHERE owner=? ORDER BY rowid DESC LIMIT 30",
                    (owner,),
                ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def summary(self):
        counts = {}
        for job in self.list():
            counts[job["status"]] = counts.get(job["status"], 0) + 1
        return {
            "counts": counts,
            "worker": "running"
            if self.task and not self.task.done()
            else "disabled"
            if not self.config.values["jobs"]["enabled"]
            else "stopped",
        }

    @staticmethod
    def public(job):
        return {
            key: value
            for key, value in job.items()
            if key
            not in {
                "owner",
                "remote_id",
                "fingerprint",
                "provider_result",
                "credential_version",
            }
        }

    def create(
        self,
        content,
        extension,
        owner,
        language,
        speakers,
        seller_id,
        source_at,
        synthetic=False,
    ):
        active = sum(job["status"] not in TERMINAL for job in self.list())
        if active >= self.config.values["voice"]["batch_max_jobs"]:
            raise HTTPException(
                429, "Speech queue is full. Finish or cancel existing work."
            )
        identifier = secrets.token_hex(16)
        path = self.directory / (identifier + "." + extension)
        with path.open("xb") as file:
            os.chmod(path, 0o600)
            file.write(content)
        job = {
            "id": identifier,
            "owner": owner,
            "status": "queued",
            "created_at": utcnow(),
            "created_epoch": time.time(),
            "extension": extension,
            "bytes": len(content),
            "language": language,
            "num_speakers": speakers,
            "seller_id": seller_id,
            "source_at": source_at,
            "synthetic": synthetic,
            "segments": [],
            "review_required": True,
            "model": self.config.values["voice"]["stt_model"],
            "provider": "sarvam",
            "poll_failures": 0,
        }
        self.save(job)
        self.wake.set()
        return job

    async def start(self):
        if self.config.values["jobs"]["enabled"]:
            self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def run(self):
        while True:
            for job in self.list():
                if job["status"] in TERMINAL:
                    continue
                try:
                    await self.step(job)
                except ProviderUnavailable as error:
                    # Poll existing IDs on transient network/server errors; never recreate a paid job.
                    job["poll_failures"] += 1
                    if (
                        job.get("remote_id")
                        and error.code in (None, 429, 500, 502, 503, 504)
                        and job["poll_failures"] < 4
                    ):
                        job["warning"] = str(error)
                    else:
                        job.update(
                            status="failed",
                            error=str(error),
                            finished_at=utcnow(),
                        )
                    self.save(job)
                except (KeyError, ValueError, TypeError, httpx.HTTPError):
                    job.update(
                        status="failed",
                        error="Speech processing failed validation or storage transfer. No transcript was accepted.",
                        finished_at=utcnow(),
                    )
                    self.save(job)
            self.wake.clear()
            try:
                await asyncio.wait_for(
                    self.wake.wait(),
                    self.config.values["voice"]["batch_poll_seconds"],
                )
            except TimeoutError:
                pass

    async def transfer(self, method, url, **kwargs):
        async with httpx.AsyncClient(
            timeout=90, follow_redirects=False
        ) as client:
            if method == "GET":
                async with client.stream("GET", storage_url(url)) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 10 * 1024 * 1024:
                            raise ValueError("Transcript output too large")
                    return json.loads(content)
            response = await client.request(method, storage_url(url), **kwargs)
            response.raise_for_status()

    async def normalize(self, job):
        """Bounded local demuxing: force the declared format and prohibit network protocols."""
        voice = self.config.values["voice"]
        original = self.directory / (job["id"] + "." + job["extension"])
        destination = self.directory / (job["id"] + ".normalized.wav")
        if destination.is_file() and job.get("normalized"):
            return destination
        demuxer = {
            "wav": "wav",
            "mp3": "mp3",
            "m4a": "mov",
            "webm": "matroska",
            "ogg": "ogg",
        }[job["extension"]]

        async def command(*args):
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                async with asyncio.timeout(voice["normalize_timeout_seconds"]):
                    stdout, _ = await process.communicate()
            except BaseException:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                raise
            if process.returncode:
                raise ProviderUnavailable(
                    "Audio decoding failed. Upload a valid supported recording; no provider charge was started.",
                    "audio",
                    422,
                )
            return stdout

        try:
            metadata = json.loads(
                await command(
                    "ffprobe",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-f",
                    demuxer,
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(original),
                )
            )
            duration = float(metadata["format"]["duration"])
            if not 0 < duration <= voice["batch_max_audio_seconds"]:
                raise ProviderUnavailable(
                    "Audio exceeds the configured duration limit or has no valid duration.",
                    "audio",
                    422,
                )
            destination.touch(mode=0o600, exist_ok=True)
            await command(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-f",
                demuxer,
                "-i",
                str(original),
                "-map",
                "0:a:0",
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(destination),
            )
            os.chmod(destination, 0o600)
            job.update(
                normalized=True,
                normalized_format="mono PCM16 WAV at 16 kHz",
                duration_seconds=round(duration, 3),
            )
            self.save(job)
            return destination
        except (OSError, TimeoutError, ValueError, KeyError) as error:
            raise ProviderUnavailable(
                "Audio normalization failed or timed out. No provider job was started.",
                "audio",
                422,
            ) from error

    async def step(self, job):
        voice = self.config.values["voice"]
        if time.time() - job["created_epoch"] > voice["batch_timeout_seconds"]:
            job.update(
                status="failed",
                error="Speech job exceeded the configured processing deadline.",
                finished_at=utcnow(),
            )
            self.save(job)
            return
        if not job.get("remote_id"):
            await self.normalize(job)
            if self.get(job["id"], job["owner"])["status"] == "cancelled":
                return
        filename = (
            "audio.wav"
            if job.get("normalized")
            else "audio." + job["extension"]
        )
        if not job.get("remote_id"):
            body, pin = await self.pool.request(
                "POST",
                "/speech-to-text/job/v1",
                return_pin=True,
                json={
                    "job_parameters": {
                        "model": voice["stt_model"],
                        "language_code": job["language"],
                        "mode": "transcribe",
                        "with_timestamps": True,
                        "with_diarization": True,
                        "num_speakers": job["num_speakers"],
                        "keyterms": voice["keyterms"],
                    }
                },
            )
            pin = (
                pin
                if isinstance(pin, dict)
                else {
                    "slot": pin,
                    "version": None,
                    "fingerprint": self.pool.fingerprint(pin),
                }
            )
            job.update(
                remote_id=body["job_id"],
                credential_slot=pin["slot"],
                credential_version=pin["version"],
                fingerprint=pin["fingerprint"],
                status="uploading",
            )
            self.save(
                job
            )  # Save remote ID before upload so restarts don't create another job.
        if not job.get("credential_version"):
            name = (
                "SARVAM_API_KEY"
                if job["credential_slot"] == "primary"
                else "SARVAM_BACKUP_API_KEY"
            )
            recovered = find_secret_version(name, job["fingerprint"])
            if recovered:
                job["credential_version"] = recovered
                self.save(job)
        if job["fingerprint"] != self.pool.fingerprint(
            job["credential_slot"], job.get("credential_version")
        ):
            raise ProviderUnavailable(
                "This job belongs to a rotated account key. Restore its key to recover it.",
                "sarvam",
                401,
            )
        if self.get(job["id"], job["owner"])["status"] == "cancelled":
            return
        path = "/speech-to-text/job/v1"
        slot = job["credential_slot"]
        if job["status"] == "uploading":
            previous, _ = await self.pool.request(
                "GET",
                path + "/" + job["remote_id"] + "/status",
                slot=slot,
                key_version=job.get("credential_version"),
            )
            if previous.get("job_state") in {
                "Running",
                "Completed",
                "PartiallyCompleted",
            }:
                job["status"] = "running"
        if job["status"] == "uploading":
            body, _ = await self.pool.request(
                "POST",
                path + "/upload-files",
                slot=slot,
                key_version=job.get("credential_version"),
                json={"job_id": job["remote_id"], "files": [filename]},
            )
            url = body["upload_urls"][filename]["file_url"]
            suffix = (
                ".normalized.wav"
                if job.get("normalized")
                else "." + job["extension"]
            )
            content = (self.directory / (job["id"] + suffix)).read_bytes()
            await self.transfer(
                "PUT",
                url,
                content=content,
                headers={
                    "x-ms-blob-type": "BlockBlob",
                    "Content-Type": "application/octet-stream",
                },
            )
            if self.get(job["id"], job["owner"])["status"] == "cancelled":
                return
            # Start is retriable after a restart; if already started, status is authoritative.
            await self.pool.request(
                "POST",
                path + "/" + job["remote_id"] + "/start",
                slot=slot,
                key_version=job.get("credential_version"),
            )
            job["status"] = "running"
            self.save(job)
        body, _ = await self.pool.request(
            "GET",
            path + "/" + job["remote_id"] + "/status",
            slot=slot,
            key_version=job.get("credential_version"),
        )
        job["remote_state"] = body.get("job_state", "Unknown")
        if job["remote_state"] == "Failed":
            raise ProviderUnavailable(
                "Sarvam batch transcription failed. No transcript was ingested.",
                "sarvam",
                422,
            )
        if job["remote_state"] not in {"Completed", "PartiallyCompleted"}:
            self.save(job)
            return
        details = body.get("job_details", [])
        outputs = [
            item["file_name"]
            for detail in details
            for item in detail.get("outputs", [])
            if item.get("file_name", "").endswith(".json")
        ]
        if not outputs:
            raise ProviderUnavailable(
                "Completed job returned no transcript file.", "sarvam", 422
            )
        download, _ = await self.pool.request(
            "POST",
            path + "/download-files",
            slot=slot,
            key_version=job.get("credential_version"),
            json={"job_id": job["remote_id"], "files": outputs},
        )
        result = await self.transfer(
            "GET", download["download_urls"][outputs[0]]["file_url"]
        )
        segments = normalize_segments(result)
        job.update(
            status="completed",
            segments=segments,
            provider_result=result,
            detected_language=result.get("language_code"),
            speaker_count=len({s["speaker"] for s in segments}),
            finished_at=utcnow(),
            warning=None,
        )
        self.save(job)
        self.store.put(
            "metadata",
            "provider-sarvam",
            {
                "status": "verified",
                "at": utcnow(),
                "message": "Real batch transcription with timed speaker clusters succeeded. Human review is required before evidence ingestion.",
            },
        )
        if (
            job.get("recording_id")
            and self.get(job["id"], job["owner"])["status"] == "completed"
        ):
            recording = self.store.get("recordings", job["recording_id"])
            if recording:
                recording.update(
                    diarization_status="completed",
                    transcript_status="sarvam_pending_review",
                    review_status="not_reviewed",
                )
                self.store.put(
                    "recordings",
                    recording["id"],
                    recording,
                    seller_id=recording["seller_id"],
                )

    def cancel(self, job):
        if job["status"] in {"approved", "cancelled"}:
            return job
        job.update(
            status="cancelled",
            finished_at=utcnow(),
            error=None,
            note="Local processing stopped; provider-side cancellation is not available in this contract.",
        )
        self.save(job)
        return job

    def approve(self, job, segments, roles):
        """Atomic reviewed evidence + baseline invalidation + index intent, idempotent."""
        if job["status"] == "approved":
            return job
        if job["status"] != "completed" or not job.get("seller_id"):
            raise HTTPException(
                409, "Complete a linked seller transcript before approval."
            )
        if job.get("synthetic"):
            raise HTTPException(
                409,
                "Synthetic evaluation audio cannot become real seller evidence.",
            )
        provider_speakers = {segment["speaker"] for segment in job["segments"]}
        if set(roles) != provider_speakers or set(roles.values()) - {
            "seller",
            "agent",
            "unknown",
        }:
            raise HTTPException(
                422, "Assign a role to every provider speaker cluster."
            )
        if {segment["speaker"] for segment in segments} - provider_speakers:
            raise HTTPException(
                422,
                "Reviewed segments must use the provider's speaker clusters.",
            )
        seller_text = "\n".join(
            segment["text"]
            for segment in segments
            if roles[segment["speaker"]] == "seller"
        )
        if not seller_text.strip() or len(seller_text) > 6000:
            raise HTTPException(
                422,
                "Reviewed seller speech must be non-empty and at most 6000 characters.",
            )
        identifier = "speech-" + job["id"]
        event = {
            "id": identifier,
            "seller_id": job["seller_id"],
            "kind": "historical_call",
            "ttl_hours": self.config.values["freshness"]["historical_hours"],
            "text": seller_text,
            "observed_at": job["source_at"],
            "ingested_at": utcnow(),
            "source": "reviewed_sarvam_diarization",
            "source_mode": "private",
            "actor_role": "seller",
            "human_reviewed": True,
            "strength": "strong",
            "provenance": {
                "job_id": job["id"],
                "model": job["model"],
                "speaker_roles": roles,
                "human_reviewed": True,
                "timing_level": "chunk",
                "corrected_segments": [
                    segment
                    for segment in segments
                    if roles[segment["speaker"]] == "seller"
                ],
            },
            "confidence": 0.8,
        }
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM speech_jobs WHERE id=?", (job["id"],)
            ).fetchone()
            current = json.loads(row[0])
            if current["status"] == "approved":
                return current
            if current["status"] != "completed":
                raise HTTPException(409, "Speech job changed before approval.")
            seller_row = db.execute(
                "SELECT payload FROM sellers WHERE id=?", (job["seller_id"],)
            ).fetchone()
            if (
                not seller_row
                or json.loads(seller_row[0]).get("source_mode") == "demo"
            ):
                raise HTTPException(
                    409,
                    "Reviewed real audio must be linked to an existing private seller.",
                )
            seller = json.loads(seller_row[0])
            if seller.get("metrics", {}).get("do_not_call") is True:
                raise HTTPException(
                    409,
                    "Contact suppression prevents persona evidence approval.",
                )
            self.store.put_document(
                db, "evidence", identifier, event, seller_id=seller["id"]
            )
            persona_row = db.execute(
                "SELECT payload FROM personas WHERE id=?", (seller["id"],)
            ).fetchone()
            if persona_row:
                persona = json.loads(persona_row[0])
                persona.update(
                    invalidated_at=utcnow(),
                    invalidation_reason="Reviewed speech evidence added",
                )
                self.store.put_document(db, "personas", seller["id"], persona)
            job.update(
                status="approved",
                reviewed_segments=segments,
                roles=roles,
                approved_at=utcnow(),
                evidence_id=identifier,
                review_required=False,
            )
            if job.get("recording_id"):
                recording_row = db.execute(
                    "SELECT payload FROM recordings WHERE id=?",
                    (job["recording_id"],),
                ).fetchone()
                if recording_row:
                    recording = json.loads(recording_row[0])
                    recording.update(
                        review_status="reviewed",
                        transcript_status="reviewed_sarvam",
                        reviewed_evidence_id=identifier,
                    )
                    self.store.put_document(
                        db,
                        "recordings",
                        recording["id"],
                        recording,
                        seller_id=recording["seller_id"],
                    )
            db.execute(
                "UPDATE speech_jobs SET payload=? WHERE id=?",
                (json.dumps(job), job["id"]),
            )
        return job

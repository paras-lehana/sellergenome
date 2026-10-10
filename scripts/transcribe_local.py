#!/usr/bin/env python3
"""Transcribe a local audio file with optional faster-whisper, without uploads.

Install separately: pip install -r requirements-asr.txt
No model is downloaded unless --allow-model-download is explicitly selected.
Output contains private machine text; keep it outside git/public directories.
VAD and quality flags reduce review effort, not the possibility of hallucination.
"""

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def finite(value: Any) -> float | None:
    """Keep unavailable/non-finite model scores explicit and valid in JSON."""
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def segment_document(
    segment: Any, previous_text: str, args: argparse.Namespace
) -> dict:
    """Attach heuristic review reasons without treating them as correctness proof."""
    text = str(segment.text).strip()
    avg_logprob = finite(getattr(segment, "avg_logprob", None))
    no_speech = finite(getattr(segment, "no_speech_prob", None))
    compression = finite(getattr(segment, "compression_ratio", None))
    flags = []
    if avg_logprob is not None and avg_logprob < args.logprob_threshold:
        flags.append("low_average_log_probability")
    if no_speech is not None and no_speech > args.no_speech_threshold:
        flags.append("high_no_speech_probability")
    if compression is not None and compression > args.compression_threshold:
        flags.append("possible_repetition_or_compression_anomaly")
    if text and previous_text.casefold() == text.casefold():
        flags.append("repeated_adjacent_segment")
    if re.search(r"\d", text):
        flags.append("numbers_require_audio_verification")
    words = []
    for word in getattr(segment, "words", None) or []:
        probability = finite(getattr(word, "probability", None))
        words.append(
            {
                "start": finite(word.start),
                "end": finite(word.end),
                "word": word.word,
                "probability": probability,
            }
        )
        if (
            probability is not None
            and probability < args.word_probability_threshold
        ):
            if "low_word_probability" not in flags:
                flags.append("low_word_probability")
    start, end = finite(segment.start), finite(segment.end)
    if start is None or end is None or start < 0 or end < start:
        flags.append("invalid_segment_timing")
    return {
        "id": segment.id,
        "start": start,
        "end": end,
        "text": text,
        "words": words,
        "avg_logprob": avg_logprob,
        "no_speech_prob": no_speech,
        "compression_ratio": compression,
        "review_flags": flags,
        "speaker": None,
    }


def file_hash(path: Path) -> str:
    """Record the source fingerprint without copying source paths into metadata."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_private_json(output: Path, result: dict) -> None:
    """Publish a complete transcript atomically with owner-only permissions."""
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".transcript-", dir=output.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                result, handle, ensure_ascii=False, indent=2, allow_nan=False
            )
            handle.write("\n")
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parser() -> argparse.ArgumentParser:
    """Expose runtime controls without importing or downloading ML dependencies."""
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "audio",
        type=Path,
        help="Existing local audio file; URLs are not accepted.",
    )
    result.add_argument(
        "--output",
        type=Path,
        help="Private JSON output; default: AUDIO.transcript.json",
    )
    result.add_argument(
        "--model",
        default="large-v3",
        help="Cached model name or local CTranslate2 model directory.",
    )
    result.add_argument(
        "--allow-model-download",
        action="store_true",
        help="Permit initial model weight download; audio stays local.",
    )
    result.add_argument(
        "--device", choices=("cpu", "cuda", "auto"), default="cpu"
    )
    result.add_argument(
        "--compute-type",
        default="int8",
        choices=("int8", "float32", "float16", "int8_float16"),
    )
    result.add_argument(
        "--language",
        default=None,
        help="Whisper language code, e.g. hi/en/ta; omit for detection.",
    )
    result.add_argument("--cpu-threads", type=int, default=4)
    result.add_argument("--beam-size", type=int, default=5)
    result.add_argument("--max-input-mb", type=int, default=250)
    result.add_argument("--max-duration-seconds", type=int, default=7200)
    result.add_argument("--vad-silence-ms", type=int, default=500)
    result.add_argument(
        "--hallucination-silence-seconds", type=float, default=2.0
    )
    result.add_argument("--logprob-threshold", type=float, default=-1.0)
    result.add_argument("--no-speech-threshold", type=float, default=0.6)
    result.add_argument("--compression-threshold", type=float, default=2.4)
    result.add_argument(
        "--word-probability-threshold", type=float, default=0.5
    )
    result.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace an existing transcript.",
    )
    return result


def main() -> None:
    """Validate local paths before optional dependency import or inference."""
    cli = parser()
    args = cli.parse_args()
    if not args.audio.is_file():
        cli.error(
            "audio must be an existing local regular file; no URL fetching is supported"
        )
    if (
        min(
            args.max_input_mb,
            args.max_duration_seconds,
            args.cpu_threads,
            args.beam_size,
            args.vad_silence_ms,
        )
        <= 0
    ):
        cli.error("resource and decoding limits must be positive")
    if (
        not 0 <= args.no_speech_threshold <= 1
        or not 0 <= args.word_probability_threshold <= 1
    ):
        cli.error("probability thresholds must be between zero and one")
    if (
        not all(
            math.isfinite(value)
            for value in (
                args.hallucination_silence_seconds,
                args.logprob_threshold,
                args.compression_threshold,
            )
        )
        or args.hallucination_silence_seconds <= 0
        or args.compression_threshold <= 0
    ):
        cli.error(
            "quality thresholds must be finite, with positive silence/compression limits"
        )
    if args.audio.stat().st_size > args.max_input_mb * 1024 * 1024:
        cli.error("local input exceeds the configured size limit")
    output = args.output or args.audio.with_suffix(".transcript.json")
    if output.resolve() == args.audio.resolve():
        cli.error("output cannot replace the source audio")
    if output.exists() and not args.overwrite:
        cli.error(
            "output exists; choose a new output or explicitly pass --overwrite"
        )
    # Import lazily: --help and validation require neither model weights nor ML wheels.
    try:
        import av
        from faster_whisper import WhisperModel
    except ImportError:
        cli.error(
            "optional ASR dependencies missing; install requirements-asr.txt in a separate environment"
        )
    try:
        # Reject obviously oversized durations before loading expensive model weights.
        with av.open(str(args.audio)) as container:
            if container.duration is not None:
                seconds = container.duration / av.time_base
                if seconds > args.max_duration_seconds:
                    cli.error(
                        "audio duration exceeds the configured duration limit"
                    )
        started = time.monotonic()
        model = WhisperModel(
            args.model,
            device=args.device,
            compute_type=args.compute_type,
            cpu_threads=args.cpu_threads,
            local_files_only=not args.allow_model_download,
        )
        segments, info = model.transcribe(
            str(args.audio),
            language=args.language,
            task="transcribe",
            beam_size=args.beam_size,
            temperature=0,
            condition_on_previous_text=False,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": args.vad_silence_ms},
            hallucination_silence_threshold=args.hallucination_silence_seconds,
            log_prob_threshold=args.logprob_threshold,
            no_speech_threshold=args.no_speech_threshold,
            compression_ratio_threshold=args.compression_threshold,
        )
        if info.duration > args.max_duration_seconds:
            cli.error("decoded audio exceeds the configured duration limit")
        documents, previous = [], ""
        for segment in segments:
            document = segment_document(segment, previous, args)
            documents.append(document)
            previous = document["text"]
        duration = round(time.monotonic() - started, 3)
    except (RuntimeError, ValueError, OSError) as exc:
        # Avoid echoing transcript text or local model paths from dependency errors.
        cli.exit(
            1,
            f"Transcription failed ({type(exc).__name__}); check local model availability, audio codec and device compatibility.\n",
        )
    result = {
        "schema_version": "1.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": file_hash(args.audio),
        "source_mode": "private",
        "method": "faster-whisper-local",
        "model": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
        "language": info.language,
        "language_probability": finite(info.language_probability),
        "audio_duration_seconds": finite(info.duration),
        "duration_after_vad_seconds": finite(
            getattr(info, "duration_after_vad", None)
        ),
        "processing_seconds": duration,
        "segments": documents,
        "text": " ".join(item["text"] for item in documents),
        "human_review_required": True,
        "diarization_performed": False,
        "quality": {
            "flagged_segments": sum(
                bool(item["review_flags"]) for item in documents
            ),
            "empty_transcript": not any(item["text"] for item in documents),
            "limits": "Scores are heuristic; an unflagged transcript can still contain errors or hallucinations.",
            "review_status": "unreviewed_machine_output",
        },
        "settings": {
            "vad_filter": True,
            "word_timestamps": True,
            "condition_on_previous_text": False,
            "beam_size": args.beam_size,
            "vad_silence_ms": args.vad_silence_ms,
            "hallucination_silence_seconds": args.hallucination_silence_seconds,
            "logprob_threshold": args.logprob_threshold,
            "no_speech_threshold": args.no_speech_threshold,
            "compression_threshold": args.compression_threshold,
            "word_probability_threshold": args.word_probability_threshold,
        },
    }
    write_private_json(output, result)
    # Console emits metadata only. Transcript content belongs in the private file.
    print(
        json.dumps(
            {
                "status": "machine_transcript_written",
                "output": str(output),
                "segments": len(documents),
                "review_required": True,
                "flagged_segments": result["quality"]["flagged_segments"],
            }
        )
    )


if __name__ == "__main__":
    main()

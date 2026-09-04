#!/usr/bin/env python3
"""Run isolated faster-whisper A/B transcriptions without touching the queue."""
import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_profiles import artifact_path, profiles_for


def default_output_dir(audio):
    """Keep the full source name so meeting.wav and meeting.mp3 cannot collide."""
    return audio.parent / f"{audio.name}-whisper-ab"


def profile_artifacts(output_dir, audio, profile):
    """Name sidecars the way the pipeline does: <source-name>.<profile>.<ext>."""
    base = output_dir / audio.name
    return tuple(
        artifact_path(base, profile, suffix, comparison=True)
        for suffix in (".json", ".txt")
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--profile", choices=("fast", "accurate", "both"), default="both")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="en")
    parser.add_argument("--no-vad", action="store_true")
    args = parser.parse_args()

    audio = args.audio.expanduser().resolve()
    if not audio.is_file():
        parser.error(f"audio file does not exist: {audio}")
    output_dir = (args.output_dir or default_output_dir(audio)).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    from faster_whisper import WhisperModel

    results = []
    for profile in profiles_for(args.profile):
        print(f"Loading {profile.label} ({profile.model_id})...", flush=True)
        model = WhisperModel(profile.model_id, device=args.device,
                             compute_type=args.compute_type,
                             cpu_threads=os.cpu_count() or 8)
        started = time.time()
        kwargs = {"language": args.language or None, "beam_size": 5}
        if not args.no_vad:
            kwargs.update(vad_filter=True,
                          vad_parameters={"min_silence_duration_ms": 1200})
        segment_iter, info = model.transcribe(str(audio), **kwargs)
        segments = [
            {"start": segment.start, "end": segment.end, "text": segment.text}
            for segment in segment_iter
        ]
        elapsed = time.time() - started
        x_realtime = info.duration / elapsed if elapsed else None
        json_path, txt_path = profile_artifacts(output_dir, audio, profile)
        json_path.write_text(json.dumps({
            "audio": str(audio),
            "profile": profile.key,
            "model": profile.model_id,
            "duration_seconds": info.duration,
            "elapsed_seconds": elapsed,
            "x_realtime": x_realtime,
            "segments": segments,
        }, indent=2), encoding="utf-8")
        txt_path.write_text(
            " ".join(segment["text"].strip() for segment in segments),
            encoding="utf-8",
        )
        result = {
            "profile": profile.key,
            "model": profile.model_id,
            "duration_seconds": info.duration,
            "elapsed_seconds": elapsed,
            "x_realtime": x_realtime,
            "segment_count": len(segments),
        }
        results.append(result)
        print(f"{profile.key}: {elapsed:.1f}s, {x_realtime:.2f}x realtime",
              flush=True)
        del model
        gc.collect()

    manifest = output_dir / "comparison.json"
    manifest.write_text(json.dumps({"audio": str(audio), "results": results}, indent=2),
                        encoding="utf-8")
    print(f"Results: {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
from model_profiles import profiles_for


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
    output_dir = (args.output_dir or audio.parent / f"{audio.stem}-whisper-ab").expanduser()
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
        stem = output_dir / f"{audio.stem}.{profile.key}"
        stem.with_suffix(stem.suffix + ".json").write_text(json.dumps({
            "audio": str(audio),
            "profile": profile.key,
            "model": profile.model_id,
            "duration_seconds": info.duration,
            "elapsed_seconds": elapsed,
            "x_realtime": x_realtime,
            "segments": segments,
        }, indent=2), encoding="utf-8")
        stem.with_suffix(stem.suffix + ".txt").write_text(
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

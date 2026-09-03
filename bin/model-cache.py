#!/usr/bin/env python3
"""Manage the optional faster-whisper model disk caches on demand."""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_profiles import cache_path_for, directory_size, hub_cache_root, profiles_for


def status(selection):
    root = hub_cache_root()
    for profile in profiles_for(selection):
        path = cache_path_for(profile, root)
        size = directory_size(path) if path.exists() else 0
        print(f"{profile.key:8} {profile.model_id:20} {'cached' if path.exists() else 'not cached'} {size / 1024**3:.2f} GiB")


def download(selection, device, compute_type):
    from faster_whisper import WhisperModel
    for profile in profiles_for(selection):
        print(f"Downloading/loading {profile.label} ({profile.model_id})…", flush=True)
        WhisperModel(profile.model_id, device=device, compute_type=compute_type)
        print(f"Ready: {profile.model_id}")


def remove(selection):
    root = hub_cache_root()
    for profile in profiles_for(selection):
        path = cache_path_for(profile, root)
        if path.exists():
            shutil.rmtree(path)
            print(f"Removed {path}")
        else:
            print(f"Not cached: {profile.model_id}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "download", "remove"))
    parser.add_argument("profile", choices=("fast", "accurate", "both"), nargs="?", default="both")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    args = parser.parse_args()
    if args.action == "status":
        status(args.profile)
    elif args.action == "download":
        download(args.profile, args.device, args.compute_type)
    else:
        remove(args.profile)


if __name__ == "__main__":
    main()

"""Supported faster-whisper model profiles and their Hugging Face cache paths."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelProfile:
    key: str
    model_id: str
    cache_repo: str
    label: str
    pros: tuple[str, ...]
    cons: tuple[str, ...]


FAST = ModelProfile(
    key="fast",
    model_id="distil-large-v3",
    cache_repo="Systran/faster-distil-whisper-large-v3",
    label="Fast — Distil Large v3",
    pros=("Fastest supported option", "Lower disk, RAM, and CPU cost"),
    cons=("May be less reliable on difficult, overlapping, or distant speech",),
)
ACCURATE = ModelProfile(
    key="accurate",
    model_id="large-v3",
    cache_repo="Systran/faster-whisper-large-v3",
    label="Accurate — Whisper Large v3",
    pros=("Best supported accuracy-oriented option", "More robust on difficult audio"),
    cons=("Substantially slower on CPU", "Uses about 2.9 GiB of cache"),
)
PROFILES = {profile.key: profile for profile in (FAST, ACCURATE)}


def profiles_for(selection):
    """Resolve fast, accurate, or both into ordered model profiles."""
    normalized = selection.strip().lower()
    if normalized == "both":
        return (FAST, ACCURATE)
    try:
        return (PROFILES[normalized],)
    except KeyError as exc:
        raise ValueError("model profile must be fast, accurate, or both") from exc


def profiles_for_config(config):
    """Resolve the profile setting while preserving legacy custom model IDs."""
    selection = config.get("WHISPER_MODEL_PROFILE", "").strip()
    if selection:
        return profiles_for(selection)
    model_id = config.get("WHISPER_MODEL", FAST.model_id).strip() or FAST.model_id
    for profile in PROFILES.values():
        if profile.model_id == model_id:
            return (profile,)
    return (ModelProfile(
        key="custom",
        model_id=model_id,
        cache_repo=f"Systran/faster-whisper-{model_id}",
        label=f"Custom — {model_id}",
        pros=("Preserves the legacy WHISPER_MODEL setting",),
        cons=("Performance and accuracy depend on the selected model",),
    ),)


def artifact_path(audio, profile, suffix, comparison=False):
    """Name artifacts without losing the source extension or profile."""
    audio = Path(audio)
    if comparison:
        return audio.with_name(f"{audio.name}.{profile.key}{suffix}")
    return audio.with_name(f"{audio.name}{suffix}")


def artifacts_complete(audio, profile, comparison=False):
    """Return whether sidecars and the final completion marker exist."""
    return all(
        artifact_path(audio, profile, suffix, comparison).is_file()
        for suffix in (".json", ".txt", ".complete.json")
    )


def directory_size(path):
    """Measure unique file data without counting Hugging Face symlinks twice."""
    total = 0
    seen = set()
    for item in Path(path).rglob("*"):
        if not item.is_file():
            continue
        stat = item.stat()
        identity = (stat.st_dev, stat.st_ino)
        if identity not in seen:
            seen.add(identity)
            total += stat.st_size
    return total


def hub_cache_root(environ=None, home=None):
    """Resolve the Hugging Face Hub cache using its environment precedence."""
    environ = os.environ if environ is None else environ
    if environ.get("HF_HUB_CACHE"):
        return Path(environ["HF_HUB_CACHE"]).expanduser()
    if environ.get("HF_HOME"):
        return Path(environ["HF_HOME"]).expanduser() / "hub"
    if environ.get("XDG_CACHE_HOME"):
        return Path(environ["XDG_CACHE_HOME"]).expanduser() / "huggingface" / "hub"
    home = Path.home() if home is None else Path(home)
    return home / ".cache" / "huggingface" / "hub"


def cache_path_for(profile, cache_root=None):
    """Return the exact Hugging Face cache directory for a profile."""
    if cache_root is None:
        cache_root = hub_cache_root()
    return Path(cache_root) / f"models--{profile.cache_repo.replace('/', '--')}"

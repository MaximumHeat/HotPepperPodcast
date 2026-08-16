"""Voice catalog and model-directory primitives."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Voice:
    id: str
    model_path: Path
    language: str | None = None
    dataset: str | None = None
    sample_rate: int | None = None
    num_speakers: int | None = None
    speaker_id_map: dict[str, int] | None = None
    license: str | None = None
    license_url: str | None = None


def default_voice_directory() -> Path:
    root = os.environ.get("XDG_DATA_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".local" / "share"
    return base / "hotpepperpodcast" / "voices"


def discover_voices(directory: str | Path) -> list[Voice]:
    directory = Path(directory).expanduser()
    result: list[Voice] = []
    for model_path in sorted(directory.glob("*.onnx")):
        voice_id = model_path.stem
        metadata: dict = {}
        config_path = model_path.with_suffix(model_path.suffix + ".json")
        if config_path.exists():
            try:
                metadata = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
        audio = metadata.get("audio", {})
        language = metadata.get("language", {}).get("code") or metadata.get("language", {}).get("name")
        dataset = metadata.get("dataset")
        return_metadata = metadata.get("license")
        speaker_id_map = metadata.get("speaker_id_map")
        if not isinstance(speaker_id_map, dict):
            speaker_id_map = None
        result.append(
            Voice(
                id=voice_id,
                model_path=model_path,
                language=language,
                dataset=dataset,
                sample_rate=audio.get("sample_rate"),
                num_speakers=metadata.get("num_speakers"),
                speaker_id_map=speaker_id_map,
                license=return_metadata if isinstance(return_metadata, str) else None,
                license_url=metadata.get("license_url"),
            )
        )
    return result


def find_voice(directory: str | Path, voice_id: str) -> Voice | None:
    return next((voice for voice in discover_voices(directory) if voice.id == voice_id), None)


def list_speaker_ids(directory: str | Path, voice_id: str) -> list[str]:
    """Return the sorted speaker ids declared by a multi-speaker voice model."""
    voice = find_voice(directory, voice_id)
    if voice is None or not voice.speaker_id_map:
        return []
    return sorted(voice.speaker_id_map.keys())

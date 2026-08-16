"""Deterministic chapter extraction for rendered HotPepperPodcast episodes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Sequence

from .models import Project


CHAPTERS_VERSION = "1.2.0"
CHAPTERS_FILENAME = "chapters.json"


def _seconds(value: float) -> float:
    """Round timestamps without introducing noisy floating-point tails."""
    return round(max(0.0, float(value)), 3)


def build_chapters(
    project: Project,
    line_indexes: Sequence[int],
    speech_intervals: Sequence[tuple[int, int]],
    sample_rate: int,
    duration: float,
) -> dict[str, Any]:
    """Build Podcasting 2.0 JSON Chapters from rendered line timing.

    Chapter markers remain authored data: only non-empty ``chapter`` values on
    enabled lines are exported. ``speech_intervals`` is produced from the actual
    synthesized WAV segments, so pauses and provider timing are reflected in the
    timestamps rather than estimated from character counts.
    """
    if len(line_indexes) != len(speech_intervals):
        raise ValueError("rendered line indexes and speech intervals must have equal lengths")
    if sample_rate <= 0:
        raise ValueError("chapter sample rate must be positive")

    chapters: list[dict[str, Any]] = []
    for line_index, interval in zip(line_indexes, speech_intervals):
        title = project.script[line_index].chapter
        if not title:
            continue
        start_frame = interval[0]
        if start_frame < 0:
            raise ValueError("chapter start frame cannot be negative")
        chapters.append({
            "startTime": _seconds(start_frame / sample_rate),
            "title": title,
            "toc": True,
        })

    for index, chapter in enumerate(chapters):
        next_start = chapters[index + 1]["startTime"] if index + 1 < len(chapters) else _seconds(duration)
        # End times are useful to players and remain deterministic. A zero-length
        # interval can occur with an unusual provider; keep the JSON valid while
        # preserving the authored order.
        chapter["endTime"] = max(chapter["startTime"], next_start)

    return {
        "version": CHAPTERS_VERSION,
        "title": project.title,
        **({"author": project.author} if project.author else {}),
        "chapters": chapters,
    }


def write_chapters(path: str | Path, chapters: dict[str, Any]) -> Path:
    """Atomically write stable, human-readable chapter JSON."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(chapters, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return destination

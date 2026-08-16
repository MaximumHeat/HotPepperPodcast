#!/usr/bin/env python3
"""Generate the dependency-free HotPepperPodcast starter WAV library."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import wave

SAMPLE_RATE = 22_050
ASSET_LICENSE = "CC0-1.0-or-public-domain-dedication"
ASSET_LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"


def envelope(position: int, total: int, attack: float = 0.04, release: float = 0.18) -> float:
    seconds = position / SAMPLE_RATE
    remaining = (total - position) / SAMPLE_RATE
    return min(1.0, seconds / attack) * min(1.0, remaining / release)


def tone(seconds: float, frequencies: tuple[float, ...], amplitude: float, beat: float = 0.0) -> list[int]:
    total = round(seconds * SAMPLE_RATE)
    samples: list[int] = []
    for index in range(total):
        time = index / SAMPLE_RATE
        value = sum(math.sin(2 * math.pi * frequency * time) for frequency in frequencies) / len(frequencies)
        if beat:
            value *= 0.86 + 0.14 * math.sin(2 * math.pi * beat * time)
        samples.append(round(32767 * amplitude * envelope(index, total) * value))
    return samples


def concat(*parts: list[int], gap: int = 0) -> list[int]:
    result: list[int] = []
    for index, part in enumerate(parts):
        if index and gap:
            result.extend([0] * gap)
        result.extend(part)
    return result


def make_assets() -> dict[str, list[int]]:
    # Short, unobtrusive synthetic cues: no sampled or third-party material.
    intro = concat(
        tone(0.22, (392.0, 523.25), 0.20),
        tone(0.24, (523.25, 659.25), 0.20),
        tone(0.34, (659.25, 783.99), 0.18),
        gap=round(0.025 * SAMPLE_RATE),
    )
    outro = concat(
        tone(0.30, (783.99, 659.25), 0.18),
        tone(0.24, (659.25, 523.25), 0.18),
        tone(0.42, (523.25, 392.0), 0.16),
        gap=round(0.025 * SAMPLE_RATE),
    )
    transition = concat(
        tone(0.12, (523.25, 659.25), 0.16),
        tone(0.16, (659.25, 783.99), 0.17),
        tone(0.24, (783.99, 1046.5), 0.14),
        gap=round(0.02 * SAMPLE_RATE),
    )
    clean_cue = concat(
        tone(0.09, (880.0, 1320.0), 0.12),
        tone(0.16, (1320.0, 1760.0), 0.10),
        gap=round(0.015 * SAMPLE_RATE),
    )
    bed = tone(4.0, (130.81, 164.81, 196.0, 261.63), 0.075, beat=0.35)
    return {
        "intro.wav": intro,
        "outro.wav": outro,
        "transition-sting.wav": transition,
        "clean-cue.wav": clean_cue,
        "subtle-bed.wav": bed,
    }


def write_wav(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in samples))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "examples" / "media")
    args = parser.parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    assets = make_assets()
    records = []
    for filename, samples in assets.items():
        path = output_dir / filename
        write_wav(path, samples)
        records.append({
            "file": filename,
            "format": "WAV PCM signed 16-bit mono",
            "sample_rate": SAMPLE_RATE,
            "frames": len(samples),
            "duration_seconds": round(len(samples) / SAMPLE_RATE, 3),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "license": ASSET_LICENSE,
            "license_url": ASSET_LICENSE_URL,
            "source": "original deterministic synthesis by HotPepperPodcast",
        })
    manifest = {
        "manifest_version": 1,
        "library": "HotPepperPodcast starter media",
        "generated_by": "scripts/generate_starter_media.py",
        "generated_assets_only": True,
        "license": ASSET_LICENSE,
        "license_url": ASSET_LICENSE_URL,
        "notes": "Original synthetic tones; no external recordings, samples, or network downloads.",
        "assets": records,
    }
    (output_dir / "ASSET_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(records)} starter assets in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

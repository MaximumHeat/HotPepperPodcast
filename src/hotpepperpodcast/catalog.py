"""Piper voice catalog metadata and safe installation planning."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Callable
from urllib.parse import urlparse
import urllib.error
import urllib.request

from .cache import CatalogCache, default_cache_path, load_cache, save_cache
from .voices import default_voice_directory

PIPER_MANIFEST_URL = "https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json"
PIPER_RESOLVE_ROOT = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"


class CatalogError(ValueError):
    """Raised when catalog metadata is unsafe or incomplete."""


@dataclass(frozen=True)
class VoiceCatalogEntry:
    id: str
    display_name: str
    language: str
    accent: str
    quality: str
    model_url: str
    config_url: str
    model_card_url: str
    digest: str
    digest_algorithm: str
    license_name: str
    license_url: str
    config_digest: str | None = None
    model_card_digest: str | None = None
    attribution: str = ""
    description: str = ""
    size_bytes: int | None = None
    num_speakers: int = 1

    @property
    def sha256(self) -> str:
        """Compatibility accessor for older local callers."""
        if self.digest_algorithm.lower() != "sha256":
            raise AttributeError("this catalog entry uses a non-SHA-256 digest")
        return self.digest

    def validate(self) -> None:
        if not self.id or "/" in self.id or "\\" in self.id or ".." in self.id:
            raise CatalogError(f"unsafe voice id: {self.id!r}")
        if not self.display_name:
            raise CatalogError(f"voice {self.id!r} has no display name")
        for field_name, value in (
            ("model_url", self.model_url),
            ("config_url", self.config_url),
            ("model_card_url", self.model_card_url),
            ("license_url", self.license_url),
        ):
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise CatalogError(f"voice {self.id!r} has invalid {field_name}")
        expected_length = {"md5": 32, "sha256": 64}.get(self.digest_algorithm.lower())
        if expected_length is None:
            raise CatalogError(f"voice {self.id!r} uses unsupported digest algorithm {self.digest_algorithm!r}")
        if len(self.digest) != expected_length or any(c not in "0123456789abcdefABCDEF" for c in self.digest):
            raise CatalogError(f"voice {self.id!r} must have a valid {self.digest_algorithm.upper()} digest")
        if not self.license_name or not self.license_url:
            raise CatalogError(f"voice {self.id!r} has incomplete license metadata")
        if self.num_speakers < 1:
            raise CatalogError(f"voice {self.id!r} has invalid speaker count")

    @classmethod
    def from_manifest_record(cls, voice_id: str, record: dict) -> "VoiceCatalogEntry":
        try:
            language = record["language"]
            files = record["files"]
            model_path = next(path for path in files if path.endswith(".onnx"))
            config_path = next(path for path in files if path.endswith(".onnx.json"))
            card_path = next(path for path in files if path.endswith("MODEL_CARD"))
        except (KeyError, StopIteration, TypeError) as exc:
            raise CatalogError(f"official catalog entry {voice_id!r} is incomplete") from exc
        model_info = files[model_path]
        config_info = files[config_path]
        card_info = files[card_path]
        entry = cls(
            id=voice_id,
            display_name=f"{language.get('name_english', 'Unknown')} — {record.get('name', voice_id)} ({record.get('quality', 'unknown')})",
            language=str(language.get("code", "")),
            accent=str(language.get("country_english", "")),
            quality=str(record.get("quality", "")),
            model_url=PIPER_RESOLVE_ROOT + model_path,
            config_url=PIPER_RESOLVE_ROOT + config_path,
            model_card_url=PIPER_RESOLVE_ROOT + card_path,
            digest=str(model_info.get("md5_digest", "")),
            digest_algorithm="md5",
            license_name="See MODEL_CARD",
            license_url=PIPER_RESOLVE_ROOT + card_path,
            config_digest=str(config_info.get("md5_digest", "")),
            model_card_digest=str(card_info.get("md5_digest", "")),
            description="Official Piper voice; review MODEL_CARD before installation.",
            size_bytes=model_info.get("size_bytes"),
            num_speakers=int(record.get("num_speakers", 1)),
        )
        entry.validate()
        return entry


def _parse_manifest(raw: object) -> dict[str, VoiceCatalogEntry]:
    if not isinstance(raw, dict):
        raise CatalogError("voice catalog must be a JSON object")
    result: dict[str, VoiceCatalogEntry] = {}
    for voice_id, record in raw.items():
        try:
            result[voice_id] = VoiceCatalogEntry.from_manifest_record(voice_id, record)
        except CatalogError:
            continue
    if not result:
        raise CatalogError("voice catalog contained no usable entries")
    return result


def load_manifest(
    source: str | Path = PIPER_MANIFEST_URL,
    opener: Callable | None = None,
    timeout: float = 20.0,
    cache_path: str | Path | None = None,
    max_cache_age: float = 24 * 60 * 60,
    allow_stale_cache: bool = True,
    notice: Callable[[str], None] | None = None,
) -> dict[str, VoiceCatalogEntry]:
    """Load an official-style manifest, using a clearly bounded local cache.

    Local files are always read directly. Remote manifests use a fresh cache
    when available, then refresh the cache; a stale cache is used only when
    the network request fails and ``allow_stale_cache`` is true.
    """
    is_local_path = isinstance(source, Path) or (isinstance(source, str) and urlparse(source).scheme == "")
    if is_local_path:
        try:
            return _parse_manifest(json.loads(Path(source).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise CatalogError(f"could not load local voice catalog {source}: {exc}") from exc
    cache = load_cache(cache_path) if str(source) == PIPER_MANIFEST_URL else None
    if cache and cache.source == str(source) and cache.is_fresh(max_cache_age):
        if notice:
            notice(f"using catalog cache ({cache.age_seconds / 3600:.1f} hours old)")
        return _parse_manifest(cache.payload)
    try:
        open_url = opener or urllib.request.urlopen
        with open_url(str(source), timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
            parsed = _parse_manifest(raw)
            if str(source) == PIPER_MANIFEST_URL:
                try:
                    save_cache(CatalogCache(raw, str(source), time.time()), cache_path or default_cache_path())
                except OSError:
                    pass
            return parsed
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError, TimeoutError, CatalogError) as exc:
        if cache and cache.source == str(source) and allow_stale_cache:
            if notice:
                notice(f"network refresh failed; using stale catalog cache ({cache.age_seconds / 3600:.1f} hours old)")
            return _parse_manifest(cache.payload)
        raise CatalogError(f"could not load voice catalog {source}: {exc}") from exc


@dataclass(frozen=True)
class InstallPlan:
    voice: VoiceCatalogEntry
    destination: Path
    model_path: Path
    config_path: Path
    model_card_path: Path
    model_partial_path: Path
    config_partial_path: Path
    model_card_partial_path: Path

    @classmethod
    def for_voice(cls, voice: VoiceCatalogEntry, destination: str | Path | None = None) -> "InstallPlan":
        voice.validate()
        root = Path(destination).expanduser() if destination is not None else default_voice_directory()
        if not root.is_absolute():
            raise CatalogError("voice destination must be an absolute path")
        model = root / f"{voice.id}.onnx"
        config = root / f"{voice.id}.onnx.json"
        model_card = root / f"{voice.id}.MODEL_CARD"
        return cls(
            voice, root, model, config, model_card,
            model.with_suffix(model.suffix + ".part"),
            config.with_suffix(config.suffix + ".part"),
            model_card.with_suffix(model_card.suffix + ".part"),
        )

    def validate_destination(self) -> None:
        resolved = self.destination.resolve()
        if resolved == Path("/") or len(resolved.parts) < 3:
            raise CatalogError(f"refusing unsafe voice destination: {resolved}")
        if any(path.parent != self.destination for path in (self.model_path, self.config_path, self.model_card_path)):
            raise CatalogError("voice paths escaped destination")

    @property
    def requires_elevation(self) -> bool:
        candidate = self.destination
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        return not os.access(candidate, os.W_OK | os.X_OK)

    def sudo_hint(self) -> str:
        self.validate_destination()
        staging = Path("/tmp") / f"hotpepperpodcast-{self.voice.id}"
        return (
            "Adding this model requires elevated permissions.\n\n"
            "Open a new shell tab and run:\n\n"
            f"mkdir -p {staging}\n"
            f"curl -fL --continue-at - {self.voice.model_url} -o {staging / (self.voice.id + '.onnx')}\n"
            f"curl -fL --continue-at - {self.voice.config_url} -o {staging / (self.voice.id + '.onnx.json')}\n"
            f"curl -fL --continue-at - {self.voice.model_card_url} -o {staging / (self.voice.id + '.MODEL_CARD')}\n"
            f"sudo install -Dm644 {staging / (self.voice.id + '.onnx')} {self.model_path}\n"
            f"sudo install -Dm644 {staging / (self.voice.id + '.onnx.json')} {self.config_path}\n"
            f"sudo install -Dm644 {staging / (self.voice.id + '.MODEL_CARD')} {self.model_card_path}\n\n"
            "Then return to HotPepperPodcast and choose Verify model."
        )

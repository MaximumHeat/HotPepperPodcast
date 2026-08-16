"""Core, dependency-light data models for HotPepperPodcast projects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = 1


MEDIA_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}
ARTWORK_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def _safe_artwork_filename(value: object, context: str) -> str:
    filename = str(value or "").strip()
    path = Path(filename)
    if (
        not filename
        or "\x00" in filename
        or "/" in filename
        or "\\" in filename
        or path.is_absolute()
        or path.name != filename
        or filename in {".", ".."}
        or path.suffix.lower() not in ARTWORK_EXTENSIONS
    ):
        raise ProjectError(f"{context} must be a local PNG or JPEG filename in the project media directory")
    return filename


def _safe_media_filename(value: object, context: str) -> str:
    filename = str(value or "").strip()
    path = Path(filename)
    if (
        not filename
        or "\x00" in filename
        or "/" in filename
        or "\\" in filename
        or path.is_absolute()
        or path.name != filename
        or filename in {".", ".."}
        or path.suffix.lower() not in MEDIA_EXTENSIONS
    ):
        raise ProjectError(f"{context} must be a local media filename in the project media directory")
    return filename


def _bool_value(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@dataclass(frozen=True)
class AudioCue:
    """A local audio asset anchored to the start of a script line."""

    file: str
    start_line: int
    offset_ms: int = 0
    volume: float = 1.0
    loop: bool = False
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    duck_speech: bool = False
    duck_amount: float = 0.65
    duck_attack_ms: int = 80
    duck_release_ms: int = 220

    @classmethod
    def from_dict(cls, raw: dict[str, Any], lane: str, index: int, script_length: int) -> "AudioCue":
        if not isinstance(raw, dict):
            raise ProjectError(f"{lane} cue {index + 1} must be a mapping")
        filename = _safe_media_filename(raw.get("file"), f"{lane} cue {index + 1} file")
        try:
            start_line = int(raw.get("start_line", 1))
            offset_ms = int(raw.get("offset_ms", 0))
            volume = float(raw.get("volume", 1.0))
            fade_in_ms = int(raw.get("fade_in_ms", 0))
            fade_out_ms = int(raw.get("fade_out_ms", 0))
            duck_speech = _bool_value(raw.get("duck_speech"), False)
            duck_amount = float(raw.get("duck_amount", 0.65))
            duck_attack_ms = int(raw.get("duck_attack_ms", 80))
            duck_release_ms = int(raw.get("duck_release_ms", 220))
        except (TypeError, ValueError) as exc:
            raise ProjectError(f"{lane} cue {index + 1} has invalid timing, volume, or fades") from exc
        if not 1 <= start_line <= script_length:
            raise ProjectError(f"{lane} cue {index + 1} start_line must be between 1 and {script_length}")
        if not 0 <= offset_ms <= 600_000:
            raise ProjectError(f"{lane} cue {index + 1} offset_ms is out of range")
        if not 0.0 <= volume <= 2.0:
            raise ProjectError(f"{lane} cue {index + 1} volume must be between 0.0 and 2.0")
        if not 0 <= fade_in_ms <= 600_000 or not 0 <= fade_out_ms <= 600_000:
            raise ProjectError(f"{lane} cue {index + 1} fade durations must be between 0 and 600000 ms")
        if not 0.0 <= duck_amount <= 1.0:
            raise ProjectError(f"{lane} cue {index + 1} duck_amount must be between 0.0 and 1.0")
        if not 0 <= duck_attack_ms <= 600_000 or not 0 <= duck_release_ms <= 600_000:
            raise ProjectError(f"{lane} cue {index + 1} duck attack/release must be between 0 and 600000 ms")
        loop = _bool_value(raw.get("loop"), lane == "music")
        if lane == "effects" and loop:
            raise ProjectError("effects cues cannot loop; use a music cue for repeating beds")
        return cls(filename, start_line, offset_ms, volume, loop, fade_in_ms, fade_out_ms, duck_speech, duck_amount, duck_attack_ms, duck_release_ms)

    def to_dict(self, lane: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "file": self.file,
            "start_line": self.start_line,
            "offset_ms": self.offset_ms,
            "volume": self.volume,
        }
        if lane == "music" or self.loop:
            result["loop"] = self.loop
        if self.fade_in_ms:
            result["fade_in_ms"] = self.fade_in_ms
        if self.fade_out_ms:
            result["fade_out_ms"] = self.fade_out_ms
        if self.duck_speech:
            result["duck_speech"] = True
            result["duck_amount"] = self.duck_amount
            result["duck_attack_ms"] = self.duck_attack_ms
            result["duck_release_ms"] = self.duck_release_ms
        return result


@dataclass(frozen=True)
class Timeline:
    """Optional local music and effects lanes for a speech timeline."""

    music: tuple[AudioCue, ...] = ()
    effects: tuple[AudioCue, ...] = ()

    @classmethod
    def from_dict(cls, raw: object, script_length: int) -> "Timeline":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ProjectError("project timeline must be a mapping")
        parsed: dict[str, tuple[AudioCue, ...]] = {}
        for lane in ("music", "effects"):
            values = raw.get(lane, [])
            if not isinstance(values, list):
                raise ProjectError(f"timeline {lane} lane must be a list")
            parsed[lane] = tuple(AudioCue.from_dict(item, lane, index, script_length) for index, item in enumerate(values))
        return cls(**parsed)

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        if self.music:
            result["music"] = [cue.to_dict("music") for cue in self.music]
        if self.effects:
            result["effects"] = [cue.to_dict("effects") for cue in self.effects]
        return result


class ProjectError(ValueError):
    """Raised when a project cannot be safely rendered."""


@dataclass(frozen=True)
class PublishMetadata:
    """Optional podcast-directory metadata authored alongside an episode."""

    subtitle: str = ""
    series: str = ""
    season_number: int | None = None
    episode_number: int | None = None
    episode_type: str = "full"
    explicit: bool = False
    language: str = "en"
    keywords: tuple[str, ...] = ()
    website: str = ""
    copyright: str = ""
    category: str = ""

    @classmethod
    def from_dict(cls, raw: object) -> "PublishMetadata":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ProjectError("publish_metadata must be a mapping")
        def text_value(name: str, default: str = "") -> str:
            value = raw.get(name, default)
            return "" if value is None else str(value).strip()
        subtitle = text_value("subtitle")
        series = text_value("series")
        episode_type = text_value("episode_type", "full").lower() or "full"
        language = text_value("language", "en") or "en"
        website = text_value("website")
        copyright_text = text_value("copyright")
        category = text_value("category")
        if len(subtitle) > 255 or len(series) > 255 or len(copyright_text) > 255 or len(category) > 100:
            raise ProjectError("publish metadata text fields must be 255 characters or fewer")
        if episode_type not in {"full", "trailer", "bonus"}:
            raise ProjectError("publish_metadata episode_type must be full, trailer, or bonus")
        if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,4})?", language):
            raise ProjectError("publish_metadata language must be an ISO-style language code")
        if website:
            parsed = urlparse(website)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(character.isspace() for character in website):
                raise ProjectError("publish_metadata website must be an http(s) URL")
        def optional_number(name: str) -> int | None:
            value = raw.get(name)
            if value in (None, ""):
                return None
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ProjectError(f"publish_metadata {name} must be a positive integer") from exc
            if not 1 <= number <= 100_000:
                raise ProjectError(f"publish_metadata {name} must be between 1 and 100000")
            return number
        keywords_raw = raw.get("keywords", []) or []
        if isinstance(keywords_raw, str):
            keywords = tuple(item.strip() for item in keywords_raw.split(",") if item.strip())
        elif isinstance(keywords_raw, list):
            keywords = tuple(str(item).strip() for item in keywords_raw if item is not None and str(item).strip())
        else:
            raise ProjectError("publish_metadata keywords must be a list or comma-separated string")
        if len(keywords) > 20 or any(len(keyword) > 64 for keyword in keywords):
            raise ProjectError("publish_metadata supports up to 20 keywords of 64 characters each")
        if len(set(keyword.casefold() for keyword in keywords)) != len(keywords):
            raise ProjectError("publish_metadata keywords must be unique")
        return cls(
            subtitle=subtitle,
            series=series,
            season_number=optional_number("season_number"),
            episode_number=optional_number("episode_number"),
            episode_type=episode_type,
            explicit=_bool_value(raw.get("explicit"), False),
            language=language,
            keywords=keywords,
            website=website,
            copyright=copyright_text,
            category=category,
        )

    def is_empty(self) -> bool:
        return not any((self.subtitle, self.series, self.season_number, self.episode_number, self.website, self.copyright, self.category, self.keywords)) and not self.explicit and self.episode_type == "full" and self.language == "en"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.subtitle:
            result["subtitle"] = self.subtitle
        if self.series:
            result["series"] = self.series
        if self.season_number is not None:
            result["season_number"] = self.season_number
        if self.episode_number is not None:
            result["episode_number"] = self.episode_number
        if self.episode_type != "full":
            result["episode_type"] = self.episode_type
        if self.explicit:
            result["explicit"] = True
        if self.language != "en":
            result["language"] = self.language
        if self.keywords:
            result["keywords"] = list(self.keywords)
        if self.website:
            result["website"] = self.website
        if self.copyright:
            result["copyright"] = self.copyright
        if self.category:
            result["category"] = self.category
        return result


@dataclass(frozen=True)
class Speaker:
    id: str
    name: str
    backend: str = "piper-direct"
    voice: str = "en_US-lessac-medium"
    piper_speaker: str = ""
    speed: float = 1.0
    pause_after_ms: int = 350
    pronunciation: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], index: int = 0) -> "Speaker":
        if not isinstance(raw, dict):
            raise ProjectError(f"speaker {index + 1} must be a mapping")
        speaker_id = str(raw.get("id", "")).strip()
        name = str(raw.get("name", speaker_id)).strip()
        if not speaker_id:
            raise ProjectError(f"speaker {index + 1} is missing id")
        if not name:
            raise ProjectError(f"speaker {speaker_id!r} is missing name")
        speed = float(raw.get("speed", 1.0))
        if not 0.5 <= speed <= 2.0:
            raise ProjectError(f"speaker {speaker_id!r} speed must be between 0.5 and 2.0")
        pause = int(raw.get("pause_after_ms", 350))
        if pause < 0 or pause > 60_000:
            raise ProjectError(f"speaker {speaker_id!r} pause_after_ms is out of range")
        pronunciation = raw.get("pronunciation", {}) or {}
        if not isinstance(pronunciation, dict):
            raise ProjectError(f"speaker {speaker_id!r} pronunciation must be a mapping")
        return cls(
            id=speaker_id,
            name=name,
            backend=str(raw.get("backend", "piper-direct")),
            voice=str(raw.get("voice", "en_US-lessac-medium")),
            piper_speaker=str(raw.get("piper_speaker", "") or "").strip(),
            speed=speed,
            pause_after_ms=pause,
            pronunciation={str(k): str(v) for k, v in pronunciation.items()},
        )


@dataclass(frozen=True)
class ScriptLine:
    speaker: str
    text: str
    pause_after_ms: int | None = None
    pronunciation: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    chapter: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any], index: int = 0) -> "ScriptLine":
        if not isinstance(raw, dict):
            raise ProjectError(f"script line {index + 1} must be a mapping")
        speaker = str(raw.get("speaker", "")).strip()
        text = str(raw.get("text", "")).strip()
        if not speaker:
            raise ProjectError(f"script line {index + 1} is missing speaker")
        if not text:
            raise ProjectError(f"script line {index + 1} is missing text")
        pause = raw.get("pause_after_ms")
        pause_value = None if pause is None else int(pause)
        if pause_value is not None and not 0 <= pause_value <= 60_000:
            raise ProjectError(f"script line {index + 1} pause_after_ms is out of range")
        pronunciation = raw.get("pronunciation", {}) or {}
        if not isinstance(pronunciation, dict):
            raise ProjectError(f"script line {index + 1} pronunciation must be a mapping")
        return cls(
            speaker=speaker,
            text=text,
            pause_after_ms=pause_value,
            pronunciation={str(k): str(v) for k, v in pronunciation.items()},
            enabled=bool(raw.get("enabled", True)),
            chapter=(str(raw["chapter"]).strip() if raw.get("chapter") else None),
        )


@dataclass(frozen=True)
class Project:
    title: str
    author: str
    speakers: tuple[Speaker, ...]
    script: tuple[ScriptLine, ...]
    output_formats: tuple[str, ...] = ("wav", "mp3")
    timeline: Timeline = field(default_factory=Timeline)
    export_stems: bool = False
    schema_version: int = SCHEMA_VERSION
    source_path: str | None = None
    description: str = ""
    loudness_check: bool = False
    loudness_target_db: float = -16.0
    loudness_tolerance_db: float = 2.0
    loudness_max_peak_db: float = -1.0
    publish_metadata: PublishMetadata = field(default_factory=PublishMetadata)
    artwork: str = ""
    package_export: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source_path: str | None = None) -> "Project":
        if not isinstance(raw, dict):
            raise ProjectError("project document must be a mapping")
        project_raw = raw.get("project", raw)
        if not isinstance(project_raw, dict):
            raise ProjectError("project must be a mapping")
        schema_version = int(project_raw.get("schema_version", raw.get("schema_version", SCHEMA_VERSION)))
        if schema_version != SCHEMA_VERSION:
            raise ProjectError(f"unsupported schema_version {schema_version}; expected {SCHEMA_VERSION}")
        title = str(project_raw.get("title", "Untitled episode")).strip()
        author = str(project_raw.get("author", "")).strip()
        if not title:
            raise ProjectError("project title cannot be empty")
        speakers_raw = raw.get("speakers", [])
        lines_raw = raw.get("script", raw.get("lines", []))
        if not isinstance(speakers_raw, list):
            raise ProjectError("speakers must be a list")
        if not isinstance(lines_raw, list):
            raise ProjectError("script must be a list")
        speakers = tuple(Speaker.from_dict(item, i) for i, item in enumerate(speakers_raw))
        lines = tuple(ScriptLine.from_dict(item, i) for i, item in enumerate(lines_raw))
        speaker_ids = {speaker.id for speaker in speakers}
        if len(speaker_ids) != len(speakers):
            raise ProjectError("speaker ids must be unique")
        missing = sorted({line.speaker for line in lines} - speaker_ids)
        if missing:
            raise ProjectError(f"script references unknown speakers: {', '.join(missing)}")
        timeline = Timeline.from_dict(project_raw.get("timeline", raw.get("timeline")), len(lines))
        export_stems = _bool_value(project_raw.get("export_stems", raw.get("export_stems")), False)
        loudness_check = _bool_value(project_raw.get("loudness_check", raw.get("loudness_check")), False)
        try:
            loudness_target_db = float(project_raw.get("loudness_target_db", raw.get("loudness_target_db", -16.0)))
            loudness_tolerance_db = float(project_raw.get("loudness_tolerance_db", raw.get("loudness_tolerance_db", 2.0)))
            loudness_max_peak_db = float(project_raw.get("loudness_max_peak_db", raw.get("loudness_max_peak_db", -1.0)))
        except (TypeError, ValueError) as exc:
            raise ProjectError("loudness settings must be numeric") from exc
        if not -40.0 <= loudness_target_db <= -3.0:
            raise ProjectError("loudness_target_db must be between -40.0 and -3.0 dBFS")
        if not 0.1 <= loudness_tolerance_db <= 12.0:
            raise ProjectError("loudness_tolerance_db must be between 0.1 and 12.0 dB")
        if not -12.0 <= loudness_max_peak_db <= 0.0:
            raise ProjectError("loudness_max_peak_db must be between -12.0 and 0.0 dBFS")
        publish_metadata = PublishMetadata.from_dict(project_raw.get("publish_metadata", raw.get("publish_metadata")))
        artwork = _safe_artwork_filename(project_raw.get("artwork", raw.get("artwork", "")), "project artwork") if project_raw.get("artwork", raw.get("artwork", "")) else ""
        package_export = _bool_value(project_raw.get("package_export", raw.get("package_export")), False)
        for lane_name, cues in (("music", timeline.music), ("effects", timeline.effects)):
            for cue in cues:
                if not lines[cue.start_line - 1].enabled:
                    raise ProjectError(f"{lane_name} cue {cue.file!r} cannot anchor to a disabled script line")
        formats = tuple(str(item).lower().lstrip(".") for item in project_raw.get("output_formats", ["wav", "mp3"]))
        allowed = {"wav", "mp3", "opus", "ogg", "flac", "m4a"}
        invalid = sorted(set(formats) - allowed)
        if invalid:
            raise ProjectError(f"unsupported output formats: {', '.join(invalid)}")
        if not formats:
            raise ProjectError("at least one output format is required")
        return cls(
            title=title,
            author=author,
            speakers=speakers,
            script=lines,
            output_formats=formats,
            timeline=timeline,
            export_stems=export_stems,
            loudness_check=loudness_check,
            loudness_target_db=loudness_target_db,
            loudness_tolerance_db=loudness_tolerance_db,
            loudness_max_peak_db=loudness_max_peak_db,
            publish_metadata=publish_metadata,
            artwork=artwork,
            package_export=package_export,
            schema_version=schema_version,
            source_path=source_path,
            description=str(project_raw.get("description", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project": {
                "title": self.title,
                "author": self.author,
                "description": self.description,
                "output_formats": list(self.output_formats),
                **({"timeline": self.timeline.to_dict()} if self.timeline.to_dict() else {}),
                **({"export_stems": True} if self.export_stems else {}),
                **({"loudness_check": True} if self.loudness_check else {}),
                **({"loudness_target_db": self.loudness_target_db} if self.loudness_target_db != -16.0 else {}),
                **({"loudness_tolerance_db": self.loudness_tolerance_db} if self.loudness_tolerance_db != 2.0 else {}),
                **({"loudness_max_peak_db": self.loudness_max_peak_db} if self.loudness_max_peak_db != -1.0 else {}),
                **({"publish_metadata": self.publish_metadata.to_dict()} if not self.publish_metadata.is_empty() else {}),
                **({"artwork": self.artwork} if self.artwork else {}),
                **({"package_export": True} if self.package_export else {}),
            },
            "speakers": [
                {
                    "id": s.id,
                    "name": s.name,
                    "backend": s.backend,
                    "voice": s.voice,
                    **({"piper_speaker": s.piper_speaker} if s.piper_speaker else {}),
                    "speed": s.speed,
                    "pause_after_ms": s.pause_after_ms,
                    **({"pronunciation": s.pronunciation} if s.pronunciation else {}),
                }
                for s in self.speakers
            ],
            "script": [
                {
                    "speaker": line.speaker,
                    "text": line.text,
                    **({"pause_after_ms": line.pause_after_ms} if line.pause_after_ms is not None else {}),
                    **({"pronunciation": line.pronunciation} if line.pronunciation else {}),
                    **({"enabled": False} if not line.enabled else {}),
                    **({"chapter": line.chapter} if line.chapter else {}),
                }
                for line in self.script
            ],
        }

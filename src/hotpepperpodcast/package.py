"""Local artwork validation and self-contained podcast package export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import tempfile
import zlib
from xml.etree import ElementTree as ET

from .credits import CREDITS_FILENAME, LICENSE_RECORDS_FILENAME, build_license_records, write_credits, write_license_records
from .models import ARTWORK_EXTENSIONS, Project

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM_NS = "http://www.w3.org/2005/Atom"


class PackageError(RuntimeError):
    """Raised when artwork or package export cannot be completed safely."""


@dataclass(frozen=True)
class ArtworkInfo:
    filename: str
    width: int
    height: int
    media_type: str
    size_bytes: int


@dataclass(frozen=True)
class PackageResult:
    package_dir: Path
    files: tuple[Path, ...]
    artwork: ArtworkInfo


def _media_path(project: Project, filename: str) -> Path:
    if not project.source_path:
        raise PackageError("artwork requires a project loaded from a file")
    source_file = Path(project.source_path).expanduser().resolve()
    media_dir = source_file.parent / "media"
    if media_dir.is_symlink() or not media_dir.is_dir():
        raise PackageError("artwork media directory must be a real directory inside the project")
    resolved_dir = media_dir.resolve()
    raw_target = media_dir / filename
    if raw_target.is_symlink():
        raise PackageError("artwork files cannot be symlinks")
    target = raw_target.resolve()
    if target.parent != resolved_dir or not target.is_file():
        raise PackageError(f"artwork file was not found in the project media directory: {filename}")
    return target


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 9 < len(data):
        while index < len(data) and data[index] != 0xFF:
            index += 1
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            break
        length = int.from_bytes(data[index:index + 2], "big")
        if length < 2 or index + length > len(data):
            break
        if marker in set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0)):
            if length >= 7:
                height = int.from_bytes(data[index + 3:index + 5], "big")
                width = int.from_bytes(data[index + 5:index + 7], "big")
                return width, height
        index += length
    return None


def inspect_artwork(project: Project) -> ArtworkInfo:
    """Validate project-local square podcast artwork without third-party packages."""
    if not project.artwork:
        raise PackageError("package export requires project artwork")
    if Path(project.artwork).suffix.lower() not in ARTWORK_EXTENSIONS:
        raise PackageError("artwork must be a PNG or JPEG file")
    path = _media_path(project, project.artwork)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PackageError(f"cannot read artwork {project.artwork!r}: {exc}") from exc
    if not data or len(data) > 25 * 1024 * 1024:
        raise PackageError("artwork must be non-empty and no larger than 25 MiB")
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        index = 8
        saw_iend = False
        while index + 12 <= len(data):
            chunk_length = int.from_bytes(data[index:index + 4], "big")
            end = index + 12 + chunk_length
            if end > len(data):
                raise PackageError("artwork PNG is truncated")
            chunk_type = data[index + 4:index + 8]
            chunk_data = data[index + 8:index + 8 + chunk_length]
            stored_crc = int.from_bytes(data[index + 8 + chunk_length:end], "big")
            if zlib.crc32(chunk_type + chunk_data) & 0xffffffff != stored_crc:
                raise PackageError("artwork PNG has an invalid CRC")
            index = end
            if chunk_type == b"IEND":
                saw_iend = True
                break
        if not saw_iend:
            raise PackageError("artwork PNG is missing IEND")
        if path.suffix.lower() != ".png":
            raise PackageError("PNG artwork must use a .png extension")
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        media_type = "image/png"
    elif data.startswith(b"\xff\xd8"):
        if path.suffix.lower() not in {".jpg", ".jpeg"}:
            raise PackageError("JPEG artwork must use a .jpg or .jpeg extension")
        dimensions = _jpeg_dimensions(data)
        if dimensions is None:
            raise PackageError("artwork JPEG dimensions could not be read")
        width, height = dimensions
        media_type = "image/jpeg"
    else:
        raise PackageError("artwork must be a valid PNG or JPEG image")
    if not 1400 <= width <= 3000 or not 1400 <= height <= 3000 or width != height:
        raise PackageError("artwork must be square and between 1400x1400 and 3000x3000 pixels")
    return ArtworkInfo(project.artwork, width, height, media_type, len(data))


def _safe_package_name(name: str) -> str:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise PackageError(f"unsafe package filename: {name!r}")
    return name


def _audio_type(path: Path) -> str:
    return {
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".flac": "audio/flac",
        ".aac": "audio/aac",
    }.get(path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")


def _episode_audio(files: tuple[Path, ...]) -> Path:
    candidates = [path for path in files if path.suffix.lower() in {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac"} and "_stem_" not in path.name]
    if not candidates:
        raise PackageError("package export requires at least one rendered audio output")
    priority = {".mp3": 0, ".m4a": 1, ".ogg": 2, ".opus": 3, ".wav": 4, ".flac": 5, ".aac": 6}
    return sorted(candidates, key=lambda path: priority.get(path.suffix.lower(), 99))[0]


def _text(parent: ET.Element, tag: str, value: object) -> ET.Element:
    child = ET.SubElement(parent, tag)
    child.text = str(value)
    return child


PODCAST_NS = "https://podcastindex.org/namespace/1.0"


def _feed(project: Project, audio_name: str, audio_path: Path, artwork_name: str, duration: float, chapters_name: str | None = None) -> bytes:
    ET.register_namespace("itunes", ITUNES_NS)
    ET.register_namespace("atom", ATOM_NS)
    ET.register_namespace("podcast", PODCAST_NS)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    _text(channel, "title", project.title)
    _text(channel, "link", project.publish_metadata.website or ".")
    _text(channel, "description", project.description or project.publish_metadata.subtitle or project.title)
    _text(channel, "language", project.publish_metadata.language.lower())
    _text(channel, "copyright", project.publish_metadata.copyright)
    _text(channel, "pubDate", format_datetime(datetime(2000, 1, 1, tzinfo=timezone.utc), usegmt=True))
    if project.publish_metadata.category:
        category = ET.SubElement(channel, f"{{{ITUNES_NS}}}category")
        category.set("text", project.publish_metadata.category)
    _text(channel, f"{{{ITUNES_NS}}}author", project.author or project.publish_metadata.series or project.title)
    _text(channel, f"{{{ITUNES_NS}}}explicit", "true" if project.publish_metadata.explicit else "false")
    _text(channel, f"{{{ITUNES_NS}}}type", "serial" if project.publish_metadata.season_number else "episodic")
    image = ET.SubElement(channel, f"{{{ITUNES_NS}}}image")
    image.set("href", f"artwork/{artwork_name}")
    atom = ET.SubElement(channel, f"{{{ATOM_NS}}}link")
    atom.set("href", "feed.xml")
    atom.set("rel", "self")
    atom.set("type", "application/rss+xml")
    item = ET.SubElement(channel, "item")
    _text(item, "title", project.title)
    _text(item, "description", project.description or project.publish_metadata.subtitle or project.title)
    _text(item, "pubDate", format_datetime(datetime(2000, 1, 1, tzinfo=timezone.utc), usegmt=True))
    digest = hashlib.sha256(audio_path.read_bytes()).hexdigest()
    guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
    guid.text = f"urn:hotpepperpodcast:{digest}"
    enclosure = ET.SubElement(item, "enclosure", {"url": f"audio/{audio_name}", "length": str(audio_path.stat().st_size), "type": _audio_type(audio_path)})
    del enclosure
    _text(item, f"{{{ITUNES_NS}}}duration", max(1, round(duration)))
    _text(item, f"{{{ITUNES_NS}}}explicit", "true" if project.publish_metadata.explicit else "false")
    _text(item, f"{{{ITUNES_NS}}}episodeType", project.publish_metadata.episode_type)
    if project.publish_metadata.season_number is not None:
        _text(item, f"{{{ITUNES_NS}}}season", project.publish_metadata.season_number)
    if project.publish_metadata.episode_number is not None:
        _text(item, f"{{{ITUNES_NS}}}episode", project.publish_metadata.episode_number)
    _text(channel, f"{{{ITUNES_NS}}}keywords", ", ".join(project.publish_metadata.keywords)) if project.publish_metadata.keywords else None
    item_image = ET.SubElement(item, f"{{{ITUNES_NS}}}image")
    item_image.set("href", f"artwork/{artwork_name}")
    if chapters_name:
        chapters = ET.SubElement(item, f"{{{PODCAST_NS}}}chapters")
        chapters.set("url", chapters_name)
        chapters.set("type", "application/json+chapters")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def export_package(
    project: Project,
    output_dir: Path,
    files: tuple[Path, ...],
    manifest_path: Path,
    package_dir: Path | None = None,
    chapters_path: Path | None = None,
) -> PackageResult:
    """Build an atomic, self-contained directory containing episode feed assets."""
    artwork = inspect_artwork(project)
    audio = _episode_audio(files)
    destination = (package_dir or (output_dir / "package")).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise PackageError("package destination cannot be a symlink")
    if destination.exists() and not destination.is_dir():
        raise PackageError("package destination must be a directory")
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    backup: Path | None = None
    try:
        audio_dir = temporary / "audio"
        artwork_dir = temporary / "artwork"
        audio_dir.mkdir()
        artwork_dir.mkdir()
        package_files: list[Path] = []
        copied_audio: list[str] = []
        for source in files:
            if source.suffix.lower() not in {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac"} or "_stem_" in source.name:
                continue
            name = _safe_package_name(source.name)
            shutil.copy2(source, audio_dir / name)
            copied_audio.append(name)
            package_files.append(audio_dir / name)
        shutil.copy2(_media_path(project, artwork.filename), artwork_dir / artwork.filename)
        package_files.append(artwork_dir / artwork.filename)
        shutil.copy2(manifest_path, temporary / "manifest.json")
        package_files.append(temporary / "manifest.json")
        metadata = output_dir / "publish-metadata.json"
        if metadata.is_file():
            shutil.copy2(metadata, temporary / metadata.name)
            package_files.append(temporary / metadata.name)
        if chapters_path is not None:
            if chapters_path.parent.resolve() != output_dir.resolve() or not chapters_path.is_file():
                raise PackageError("chapter export must be a file in the render output directory")
            shutil.copy2(chapters_path, temporary / chapters_path.name)
            package_files.append(temporary / chapters_path.name)
        source_records = output_dir / LICENSE_RECORDS_FILENAME
        source_credits = output_dir / CREDITS_FILENAME
        if source_records.is_file() and source_credits.is_file():
            shutil.copy2(source_records, temporary / LICENSE_RECORDS_FILENAME)
            shutil.copy2(source_credits, temporary / CREDITS_FILENAME)
        else:
            try:
                license_records = build_license_records(project, artwork_filename=artwork.filename)
            except (OSError, ValueError, TypeError) as exc:
                raise PackageError(f"could not build credits and license records: {exc}") from exc
            write_license_records(temporary / LICENSE_RECORDS_FILENAME, license_records)
            write_credits(temporary / CREDITS_FILENAME, license_records)
        package_files.extend((temporary / LICENSE_RECORDS_FILENAME, temporary / CREDITS_FILENAME))
        duration = json.loads(manifest_path.read_text(encoding="utf-8")).get("duration_seconds", 0)
        feed = _feed(project, audio.name, audio, artwork.filename, float(duration), chapters_path.name if chapters_path else None)
        (temporary / "feed.xml").write_bytes(feed)
        package_files.append(temporary / "feed.xml")
        summary = {
            "package_version": 1,
            "title": project.title,
            "audio": [f"audio/{name}" for name in copied_audio],
            "feed": "feed.xml",
            "artwork": {"path": f"artwork/{artwork.filename}", "width": artwork.width, "height": artwork.height, "media_type": artwork.media_type, "size_bytes": artwork.size_bytes},
            "manifest": "manifest.json",
            "metadata": "publish-metadata.json" if metadata.is_file() else None,
            "chapters": chapters_path.name if chapters_path else None,
            "credits": CREDITS_FILENAME,
            "license_records": LICENSE_RECORDS_FILENAME,
            "offline_urls": True,
        }
        (temporary / "package-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        package_files.append(temporary / "package-summary.json")
        if destination.exists():
            existing_manifest = destination / "manifest.json"
            existing_summary = destination / "package-summary.json"
            if not existing_manifest.is_file() or not existing_summary.is_file():
                raise PackageError("refusing to overwrite an unrecorded package directory")
            backup = destination.with_name(f".{destination.name}-backup-{os.getpid()}")
            if backup.exists():
                shutil.rmtree(backup)
            destination.rename(backup)
        try:
            temporary.rename(destination)
        except Exception:
            if backup is not None and not destination.exists():
                backup.rename(destination)
            raise
        if backup is not None:
            shutil.rmtree(backup)
        return PackageResult(destination, tuple(destination / path.relative_to(temporary) for path in package_files), artwork)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        if backup is not None and backup.exists() and not destination.exists():
            backup.rename(destination)
        elif backup is not None and backup.exists():
            shutil.rmtree(backup)
        raise

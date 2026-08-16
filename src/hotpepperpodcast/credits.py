"""Deterministic credits and license records for local podcast packages."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path
import tempfile
from typing import Any

from .models import Project

CREDITS_FILENAME = "CREDITS.md"
LICENSE_RECORDS_FILENAME = "license-records.json"


def _source_media_dir(project: Project) -> Path:
    if not project.source_path:
        raise ValueError("credits require a project loaded from a file")
    media_dir = Path(project.source_path).expanduser().resolve().parent / "media"
    if media_dir.is_symlink() or not media_dir.is_dir():
        raise ValueError("credits media directory must be a real project-local directory")
    return media_dir


def _asset_path(media_dir: Path, filename: str) -> Path:
    raw = media_dir / filename
    if raw.is_symlink():
        raise ValueError(f"credit asset cannot be a symlink: {filename}")
    target = raw.resolve()
    if target.parent != media_dir.resolve() or not target.is_file():
        raise ValueError(f"credit asset was not found in the project media directory: {filename}")
    return target


def _read_manifest(media_dir: Path, name: str) -> dict[str, Any] | None:
    path = media_dir / name
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _asset_manifest_record(manifest: dict[str, Any] | None, filename: str) -> dict[str, Any] | None:
    if not manifest:
        return None
    assets = manifest.get("assets")
    if isinstance(assets, list):
        for record in assets:
            if isinstance(record, dict) and record.get("file") == filename:
                return record
    if manifest.get("asset") == filename:
        return manifest
    return None


def _asset_record(
    media_dir: Path,
    filename: str,
    kind: str,
    package_path: str,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    path = _asset_path(media_dir, filename)
    data = path.read_bytes()
    upstream_record = _asset_manifest_record(manifest, filename)
    upstream: dict[str, Any] = {}
    if upstream_record:
        # Artwork manifests describe one named asset and starter-media
        # manifests provide per-file records. Never apply a manifest's global
        # license to an unlisted user file.
        if manifest and manifest.get("asset") == filename:
            for name in ("license", "license_url", "source", "format", "dimensions"):
                if name in manifest:
                    upstream[name] = manifest[name]
        upstream.update(upstream_record)
    record: dict[str, Any] = {
        "kind": kind,
        "source_path": f"media/{filename}",
        "package_path": package_path,
        "filename": filename,
        "media_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "license_status": "not-recorded",
        "license": None,
        "license_url": None,
        "source": "User-provided local project media",
    }
    if upstream:
        for name in ("license", "license_url", "source", "format", "dimensions"):
            if name in upstream:
                record[name] = upstream[name]
        if upstream.get("license"):
            record["license_status"] = "recorded"
        record["manifest"] = "ARTWORK_MANIFEST.json" if kind == "artwork" else "ASSET_MANIFEST.json"
        expected = upstream.get("sha256") or (upstream_record or {}).get("sha256")
        if isinstance(expected, str) and expected.lower() != record["sha256"].lower():
            record["license_status"] = "manifest-hash-mismatch"
            record["hash_warning"] = "source manifest SHA-256 does not match the packaged file"
    return record


def build_license_records(project: Project, artwork_filename: str | None = None, effective_engines: dict[str, str] | None = None) -> dict[str, Any]:
    """Build deterministic asset/model license records without network access."""
    media_dir = _source_media_dir(project)
    asset_manifest = _read_manifest(media_dir, "ASSET_MANIFEST.json")
    artwork_manifest = _read_manifest(media_dir, "ARTWORK_MANIFEST.json")
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cue in (*project.timeline.music, *project.timeline.effects):
        if cue.file in seen:
            continue
        seen.add(cue.file)
        assets.append(_asset_record(media_dir, cue.file, "timeline-audio", f"audio/{cue.file}", asset_manifest))
    if artwork_filename:
        assets.append(_asset_record(media_dir, artwork_filename, "artwork", f"artwork/{artwork_filename}", artwork_manifest))

    models: list[dict[str, Any]] = []
    seen_models: set[tuple[str, str]] = set()
    for speaker in project.speakers:
        backend = (effective_engines or {}).get(speaker.id, speaker.backend)
        key = (backend, speaker.voice)
        if key in seen_models:
            continue
        seen_models.add(key)
        is_piper = "piper" in backend.lower()
        model = {
            "kind": "voice-model",
            "backend": backend,
            "voice": speaker.voice,
            "license_status": "review-required",
            "license": "See the installed Piper MODEL_CARD" if is_piper else "Provider/model license metadata is not embedded in the project",
            "license_url": None,
            "source": "Configured local TTS provider; catalog metadata is not embedded in the project",
            "model_card_required": is_piper,
        }
        if is_piper:
            model["verification_command"] = f"hotpepperpodcast voices verify {speaker.voice}"
        models.append(model)

    limitations = [
        "User-provided assets without a local manifest are marked not-recorded.",
        "Provider/model licenses remain review-required because catalog metadata is not embedded in the project.",
    ]
    if any(model.get("model_card_required") for model in models):
        limitations[1] = "Piper model licenses require review of each installed MODEL_CARD; other providers remain review-required until their metadata is supplied."

    return {
        "records_version": 1,
        "project": {
            "title": project.title,
            "author": project.author,
            "source_of_truth": "authored project script",
        },
        "assets": assets,
        "models": models,
        "provenance": {
            "network_access": False,
            "asset_manifests": [name for name, value in (("ASSET_MANIFEST.json", asset_manifest), ("ARTWORK_MANIFEST.json", artwork_manifest)) if value],
            "limitations": limitations,
        },
    }


def _atomic_write(path: str | Path, content: str) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return destination


def write_license_records(path: str | Path, records: dict[str, Any]) -> Path:
    """Write stable machine-readable license records atomically."""
    return _atomic_write(path, json.dumps(records, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def credits_markdown(records: dict[str, Any]) -> str:
    """Render consolidated human-readable credits from license records."""
    project = records["project"]
    lines = [
        "# Credits and License Records",
        "",
        f"**Episode:** {project['title']}",
        f"**Author:** {project['author'] or 'Not specified'}",
        "",
        "This `CREDITS.md` file was generated by HotPepperPodcast from the authored project and local provenance manifests. Review the machine-readable `license-records.json` alongside it.",
        "",
        "## Voice models",
        "",
    ]
    models = records.get("models", [])
    if models:
        lines.extend(["| Backend | Voice | License status | License/reference |", "|---|---|---|---|"])
        for model in models:
            lines.append(f"| `{model['backend']}` | `{model['voice']}` | **{model['license_status']}** | {model['license']} |")
    else:
        lines.append("No voice models were recorded.")
    if any(model.get("model_card_required") for model in models):
        lines.append("Each Piper voice requires review of its installed `MODEL_CARD` before redistribution. Verify an installed voice with the command recorded in `license-records.json`.")
    else:
        lines.append("Provider/model licenses remain review-required because catalog metadata is not embedded in the project.")
    lines.extend(["", "## Episode assets", ""])
    assets = records.get("assets", [])
    if assets:
        lines.extend(["| Package path | SHA-256 | License status | License | Source |", "|---|---|---|---|---|"])
        for asset in assets:
            license_name = asset.get("license") or "Not recorded"
            source = asset.get("source") or "Not recorded"
            lines.append(f"| `{asset['package_path']}` | `{asset['sha256']}` | **{asset['license_status']}** | {license_name} | {source} |")
    else:
        lines.append("No local timeline or artwork assets were recorded.")
    lines.extend([
        "",
        "## Review notes",
        "",
        "- Records are generated locally with no network access.",
        "- A `recorded` asset license comes from a matching project-local asset/artwork manifest.",
        "- `not-recorded` means the file is included with a hash but its license was not supplied; do not redistribute it until reviewed.",
        "- `review-required` model records identify the voice but do not claim a universal Piper license.",
        "",
    ])
    return "\n".join(lines)


def write_credits(path: str | Path, records: dict[str, Any]) -> Path:
    """Write consolidated human-readable credits atomically."""
    return _atomic_write(path, credits_markdown(records))

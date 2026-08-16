"""Safe voice-model download primitives.

The installer never runs sudo, never prompts for passwords, and never activates
an unverified model pair.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .catalog import InstallPlan


class InstallError(RuntimeError):
    """Raised when a model cannot be downloaded or activated."""


class InstallValidationError(InstallError):
    """Raised when downloaded bytes fail validation and must not be resumed."""


def digest_file(path: Path, algorithm: str = "sha256", chunk_size: int = 1024 * 1024) -> str:
    try:
        digest = hashlib.new(algorithm)
    except ValueError as exc:
        raise InstallError(f"unsupported digest algorithm: {algorithm}") from exc
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_digest(path: Path, expected: str, algorithm: str = "sha256") -> None:
    actual = digest_file(path, algorithm)
    if actual.lower() != expected.lower():
        raise InstallValidationError(f"checksum mismatch for {path.name}: expected {expected}, got {actual}")


ProgressCallback = Callable[[str, int, int | None], None]


def _download_resumable(
    url: str,
    partial: Path,
    opener: Callable | None = None,
    timeout: float = 60.0,
    progress: ProgressCallback | None = None,
) -> None:
    partial.parent.mkdir(parents=True, exist_ok=True)
    existing = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    request = urllib.request.Request(url, headers=headers)
    open_url = opener or urllib.request.urlopen
    try:
        response = open_url(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if existing and exc.code == 416:
            return
        raise InstallError(f"download failed for {url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InstallError(f"download failed for {url}: {exc}") from exc
    accepts_range = existing > 0 and getattr(response, "status", None) == 206
    mode = "ab" if accepts_range else "wb"
    offset = existing if accepts_range else 0
    content_length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
    try:
        total = offset + int(content_length) if content_length else None
    except (TypeError, ValueError):
        total = None
    downloaded = offset
    try:
        with response, partial.open(mode) as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(url, downloaded, total)
    except OSError as exc:
        raise InstallError(f"cannot write partial download {partial}: {exc}") from exc


def _verify_optional_digest(path: Path, expected: str | None, algorithm: str, label: str) -> None:
    if expected:
        verify_digest(path, expected, algorithm)
    elif path.stat().st_size == 0:
        raise InstallValidationError(f"downloaded {label} is empty")


def _validate_piper_config(path: Path, voice_id: str) -> None:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallValidationError(f"downloaded config is not valid JSON/Piper metadata for {voice_id}: {exc}") from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("audio"), dict):
        raise InstallValidationError(f"downloaded config is not valid Piper metadata for {voice_id}")


def download_voice(
    plan: InstallPlan,
    accept_license: bool,
    opener: Callable | None = None,
    timeout: float = 60.0,
    progress: ProgressCallback | None = None,
) -> None:
    """Download and atomically activate a catalog voice."""
    plan.validate_destination()
    if not accept_license:
        raise InstallError(f"license acceptance is required before installing {plan.voice.id}")
    try:
        _download_resumable(plan.voice.model_url, plan.model_partial_path, opener, timeout, progress)
        _download_resumable(plan.voice.config_url, plan.config_partial_path, opener, timeout, progress)
        _download_resumable(plan.voice.model_card_url, plan.model_card_partial_path, opener, timeout, progress)
        verify_digest(plan.model_partial_path, plan.voice.digest, plan.voice.digest_algorithm)
        _validate_piper_config(plan.config_partial_path, plan.voice.id)
        _verify_optional_digest(plan.config_partial_path, plan.voice.config_digest, plan.voice.digest_algorithm, "config")
        _verify_optional_digest(plan.model_card_partial_path, plan.voice.model_card_digest, plan.voice.digest_algorithm, "MODEL_CARD")
        plan.destination.mkdir(parents=True, exist_ok=True)
        marker = plan.destination / f".{plan.voice.id}.installing"
        marker.write_text("pair activation in progress\n", encoding="utf-8")
        activated = False
        try:
            os.replace(plan.model_partial_path, plan.model_path)
            os.replace(plan.config_partial_path, plan.config_path)
            os.replace(plan.model_card_partial_path, plan.model_card_path)
            activated = True
        finally:
            if activated:
                marker.unlink(missing_ok=True)
    except InstallValidationError:
        for partial in (plan.model_partial_path, plan.config_partial_path, plan.model_card_partial_path):
            partial.unlink(missing_ok=True)
        raise
    except PermissionError as exc:
        raise InstallError(f"permission denied for {plan.destination}; choose a user-owned directory or use the displayed sudo fallback") from exc
    except (OSError, ValueError) as exc:
        raise InstallError(f"could not activate {plan.voice.id}: {exc}") from exc


def verify_installed_voice(plan: InstallPlan) -> None:
    """Verify an activated model, config, and model-card pair without changing it."""
    plan.validate_destination()
    marker = plan.destination / f".{plan.voice.id}.installing"
    if marker.exists():
        raise InstallValidationError(f"voice {plan.voice.id} has an interrupted installation; remove the marker after inspection and reinstall")
    if not all(path.is_file() for path in (plan.model_path, plan.config_path, plan.model_card_path)):
        raise InstallValidationError(f"voice {plan.voice.id} is incomplete; model, config, and MODEL_CARD are required")
    verify_digest(plan.model_path, plan.voice.digest, plan.voice.digest_algorithm)
    _validate_piper_config(plan.config_path, plan.voice.id)
    _verify_optional_digest(plan.config_path, plan.voice.config_digest, plan.voice.digest_algorithm, "config")
    _verify_optional_digest(plan.model_card_path, plan.voice.model_card_digest, plan.voice.digest_algorithm, "MODEL_CARD")

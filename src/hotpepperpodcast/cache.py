"""Local cache for the official voice catalog.

The cache is user-owned and separate from private project notes. It makes
catalog browsing usable when the network is unavailable without silently
claiming the remote catalog is current.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any

DEFAULT_CATALOG_SOURCE = "https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json"


@dataclass(frozen=True)
class CatalogCache:
    payload: dict[str, Any]
    source: str
    fetched_at: float
    etag: str | None = None
    last_modified: str | None = None

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.fetched_at)

    def is_fresh(self, max_age: float) -> bool:
        return self.age_seconds <= max_age


def default_cache_path() -> Path:
    root = os.environ.get("XDG_CACHE_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".cache"
    return base / "hotpepperpodcast" / "voices.json"


def _cache_document(cache: CatalogCache) -> dict[str, Any]:
    return {
        "cache_version": 1,
        "source": cache.source,
        "fetched_at": cache.fetched_at,
        "etag": cache.etag,
        "last_modified": cache.last_modified,
        "payload": cache.payload,
    }


def load_cache(path: str | Path | None = None) -> CatalogCache | None:
    path = Path(path).expanduser() if path else default_cache_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            return None
        if document.get("cache_version") != 1 or not isinstance(document.get("payload"), dict):
            return None
        return CatalogCache(
            payload=document["payload"],
            source=str(document.get("source", DEFAULT_CATALOG_SOURCE)),
            fetched_at=float(document["fetched_at"]),
            etag=document.get("etag"),
            last_modified=document.get("last_modified"),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
        return None


def save_cache(cache: CatalogCache, path: str | Path | None = None) -> Path:
    path = Path(path).expanduser() if path else default_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(_cache_document(cache), stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except OSError:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return path

import json
import time

from hotpepperpodcast.cache import CatalogCache, default_cache_path, load_cache, save_cache


def test_cache_roundtrip_and_freshness(tmp_path):
    path = tmp_path / "voices.json"
    cache = CatalogCache({"voice": {"files": {}}}, "test-source", time.time())
    assert save_cache(cache, path) == path
    loaded = load_cache(path)
    assert loaded is not None
    assert loaded.source == "test-source"
    assert loaded.payload == cache.payload
    assert loaded.is_fresh(60)


def test_stale_cache_is_not_fresh(tmp_path):
    path = tmp_path / "voices.json"
    save_cache(CatalogCache({}, "source", time.time() - 100), path)
    loaded = load_cache(path)
    assert loaded is not None
    assert not loaded.is_fresh(10)


def test_malformed_cache_is_ignored(tmp_path):
    path = tmp_path / "voices.json"
    path.write_text("[]", encoding="utf-8")
    assert load_cache(path) is None
    path.write_text(json.dumps({"cache_version": 99, "payload": {}}), encoding="utf-8")
    assert load_cache(path) is None


def test_default_cache_respects_xdg(monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/hpp-cache-test")
    assert default_cache_path().as_posix() == "/tmp/hpp-cache-test/hotpepperpodcast/voices.json"

import json
import struct
import wave
from pathlib import Path
import pytest
from hotpepperpodcast.models import Project
from hotpepperpodcast.package import PackageError, export_package, inspect_artwork
from hotpepperpodcast.render import RenderError, _cue_gain, _duck_gain, analyze_wav_loudness, render_project

class FakeProvider:
    def __init__(self, calls): self.calls = calls
    def synthesize(self, text, voice, output_path, speed=1.0):
        self.calls.append((text, voice, speed))
        with wave.open(str(output_path), "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(22050)
            out.writeframes((b"\x01\x00" * 2205))

def project():
    return Project.from_dict({"schema_version": 1, "project": {"title": "Render Test", "output_formats": ["wav"]}, "speakers": [{"id": "host", "name": "Host", "voice": "voice", "pause_after_ms": 100, "pronunciation": {"FFmpeg": "F F mpeg"}}], "script": [{"speaker": "host", "text": "Hello FFmpeg"}, {"speaker": "host", "text": "skip", "enabled": False}, {"speaker": "host", "text": "Again", "pause_after_ms": 0}]})

def _write_media(path: Path, value: int = 1000, frames: int = 2205):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(22050)
        out.writeframes((value.to_bytes(2, "little", signed=True)) * frames)


def test_loudness_analysis_reports_rms_and_peak(tmp_path):
    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(1000)
        out.writeframes((16384).to_bytes(2, "little", signed=True) * 1000)
    result = analyze_wav_loudness(path, target_db=-6, tolerance_db=0.1, max_peak_db=-7)
    assert result["method"] == "sample_rms_proxy"
    assert result["rms_dbfs"] == pytest.approx(-6.021, abs=0.01)
    assert result["peak_dbfs"] == pytest.approx(-6.021, abs=0.01)
    assert result["status"] == "check"


def test_render_manifest_records_effective_engine(tmp_path):
    (tmp_path / "media").mkdir()
    class EngineProvider(FakeProvider):
        engine_id = "espeak-ng"
    rendered_project = Project.from_dict(project().to_dict(), source_path=str(tmp_path / "episode.yaml"))
    result = render_project(rendered_project, lambda backend: EngineProvider([]), tmp_path)
    manifest = json.loads(result.manifest.read_text())
    assert {segment["backend"] for segment in manifest["segments"]} == {"espeak-ng"}
    records = json.loads((tmp_path / "license-records.json").read_text())
    assert records["models"][0]["backend"] == "espeak-ng"
    assert records["models"][0]["model_card_required"] is False


def test_render_manifest_and_pronunciation(tmp_path):
    calls = []; provider = FakeProvider(calls)
    result = render_project(project(), lambda backend: provider, tmp_path)
    assert result.files == (tmp_path / "Render_Test.wav",)
    assert result.manifest.exists()
    assert calls[0][0] == "Hello F F mpeg"
    manifest = json.loads(result.manifest.read_text())
    assert len(manifest["segments"]) == 2
    assert manifest["timeline"] == {"music": 0, "effects": 0, "cues": []}
    assert "chapter" not in manifest["segments"][0]
    with wave.open(str(result.files[0]), "rb") as audio:
        assert audio.getnframes() == 2205 + 2205 + int(22050 * .1)

def test_render_preserves_chapter_marker_and_skips_muted_line(tmp_path):
    raw = project().to_dict()
    raw["script"][0]["chapter"] = "Opening"
    rendered = Project.from_dict(raw)
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path)
    segments = json.loads(result.manifest.read_text())["segments"]
    assert segments[0]["chapter"] == "Opening"
    assert all(segment["text_sha256"] != __import__('hashlib').sha256(b"skip").hexdigest() for segment in segments)


def test_render_exports_chapters_at_actual_speech_starts(tmp_path):
    raw = project().to_dict()
    raw["script"][0]["chapter"] = "Opening"
    raw["script"][2]["chapter"] = "Return"
    rendered = Project.from_dict(raw)
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path)
    manifest = json.loads(result.manifest.read_text())
    chapters = json.loads((tmp_path / "chapters.json").read_text())
    assert manifest["chapters_file"] == "chapters.json"
    assert [chapter["title"] for chapter in chapters["chapters"]] == ["Opening", "Return"]
    assert chapters["chapters"][0]["startTime"] == 0.0
    assert chapters["chapters"][1]["startTime"] == pytest.approx(0.2)
    assert all(chapter["title"] != "skip" for chapter in chapters["chapters"])
    assert (tmp_path / "chapters.json").name in {path.name for path in result.files}


def test_legacy_render_has_no_chapter_output(tmp_path):
    result = render_project(project(), lambda backend: FakeProvider([]), tmp_path)
    manifest = json.loads(result.manifest.read_text())
    assert "chapters_file" not in manifest
    assert not (tmp_path / "chapters.json").exists()


def test_render_mixes_local_audio_lanes_and_records_cues(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    _write_media(media / "bed.wav", value=1000)
    raw = project().to_dict()
    raw["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, "volume": 0.5, "loop": True}]}
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")
    manifest = json.loads(result.manifest.read_text())
    assert manifest["timeline"]["music"] == 1
    assert manifest["timeline"]["cues"][0]["file"] == "bed.wav"
    assert manifest["timeline"]["cues"][0]["start_line"] == 1
    with wave.open(str(result.files[0]), "rb") as audio:
        raw = audio.readframes(audio.getnframes())
        assert raw[:2] != b"\x01\x00"
        assert max(abs(value) for value in struct.unpack('<' + 'h' * (len(raw) // 2), raw)) <= 32767


def test_render_rejects_symlinked_media_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_media(outside / "bed.wav")
    (tmp_path / "media").symlink_to(outside, target_is_directory=True)
    raw = project().to_dict()
    raw["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1}]}
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    with pytest.raises(RenderError, match="real directory"):
        render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")


def test_render_missing_local_audio_is_rejected(tmp_path):
    raw = project().to_dict()
    raw["project"]["timeline"] = {"effects": [{"file": "missing.wav", "start_line": 1}]}
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    with pytest.raises(RenderError, match="not found"):
        render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")


def test_duck_gain_attack_hold_and_release():
    assert _duck_gain(0, [(100, 200)], 0.6, 50, 50, 1000) == pytest.approx(1.0)
    assert _duck_gain(75, [(100, 200)], 0.6, 50, 50, 1000) == pytest.approx(0.7)
    assert _duck_gain(150, [(100, 200)], 0.6, 50, 50, 1000) == pytest.approx(0.4)
    assert _duck_gain(225, [(100, 200)], 0.6, 50, 50, 1000) == pytest.approx(0.7)
    assert _duck_gain(260, [(100, 200)], 0.6, 50, 50, 1000) == pytest.approx(1.0)


def test_cue_gain_fades_in_and_out():
    assert _cue_gain(0, 100, 100, 0, 1.0, 100, 100, 1000, False) == pytest.approx(0.02)
    assert _cue_gain(49, 100, 100, 0, 1.0, 100, 100, 1000, False) == pytest.approx(1.0)
    assert _cue_gain(99, 100, 100, 0, 1.0, 100, 100, 1000, False) == pytest.approx(0.02)


def test_long_non_looping_fades_are_scaled_to_clip():
    gain_at_start = _cue_gain(0, 100, 100, 0, 1.0, 200, 200, 1000, False)
    gain_at_end = _cue_gain(99, 100, 100, 0, 1.0, 200, 200, 1000, False)
    assert gain_at_start == pytest.approx(0.02)
    assert gain_at_end == pytest.approx(0.02)


def test_looped_cue_fades_out_at_episode_end():
    assert _cue_gain(49, 100, 100, 0, 1.0, 0, 50, 1000, True) == pytest.approx(1.0)
    assert _cue_gain(99, 100, 100, 0, 1.0, 0, 50, 1000, True) == pytest.approx(0.02)
    assert _cue_gain(20, 100, 100, 0, 1.0, 0, 50, 1000, True) == pytest.approx(1.0)


def test_overlapping_cues_produce_crossfade_signal(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    _write_media(media / "outgoing.wav", value=1000, frames=5000)
    _write_media(media / "incoming.wav", value=2000, frames=5000)
    raw = project().to_dict()
    raw["project"]["timeline"] = {"effects": [
        {"file": "outgoing.wav", "start_line": 1, "fade_out_ms": 150},
        {"file": "incoming.wav", "start_line": 1, "offset_ms": 50, "fade_in_ms": 150},
    ]}
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")
    with wave.open(str(result.files[0]), "rb") as audio:
        frames = audio.readframes(audio.getnframes())
    values = struct.unpack('<' + 'h' * (len(frames) // 2), frames)
    assert values[0] < values[2500] < values[4500]


def test_export_stems_are_aligned_and_manifested(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    _write_media(media / "bed.wav", value=1000)
    raw = project().to_dict()
    raw["project"]["export_stems"] = True
    raw["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, "volume": 0.5, "loop": True}]}
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")
    manifest = json.loads(result.manifest.read_text())
    assert set(manifest["stems"]) == {"speech", "music"}
    assert {path.name for path in result.files} >= set(manifest["stems"].values())
    assert set(manifest["outputs"]) == {path.name for path in result.files}
    assert all((result.output_dir / filename).exists() for filename in manifest["stems"].values())
    with wave.open(str(result.files[0]), "rb") as master:
        master_params = master.getparams()
        master_frames = master.getnframes()
    for filename in manifest["stems"].values():
        with wave.open(str(result.output_dir / filename), "rb") as stem:
            assert stem.getparams() == master_params
            assert stem.getnframes() == master_frames


def test_render_rolls_back_generated_publish_outputs_on_encode_failure(tmp_path, monkeypatch):
    raw = project().to_dict()
    raw["project"].update({"output_formats": ["wav"], "publish_metadata": {"subtitle": "A test"}})
    rendered = Project.from_dict(raw)
    first = render_project(rendered, lambda backend: FakeProvider([]), tmp_path)
    previous = {name: (tmp_path / name).read_bytes() for name in json.loads(first.manifest.read_text())["outputs"] + ["manifest.json"]}
    previous["Render_Test.wav"] = (tmp_path / "Render_Test.wav").read_bytes()
    failing_raw = rendered.to_dict()
    failing_raw["project"]["output_formats"] = ["wav", "mp3"]
    failing = Project.from_dict(failing_raw)
    def fail_encode(*args, **kwargs):
        raise RenderError("encode failed")
    monkeypatch.setattr("hotpepperpodcast.render._encode_ffmpeg", fail_encode)
    with pytest.raises(RenderError, match="encode failed"):
        render_project(failing, lambda backend: FakeProvider([]), tmp_path)
    for name, contents in previous.items():
        assert (tmp_path / name).read_bytes() == contents


def test_render_emits_loudness_and_publish_metadata(tmp_path):
    raw = project().to_dict()
    raw["project"].update({"loudness_check": True, "publish_metadata": {"subtitle": "A test episode", "keywords": ["demo"], "website": "https://example.com"}})
    rendered = Project.from_dict(raw)
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path)
    manifest = json.loads(result.manifest.read_text())
    assert manifest["loudness"]["method"] == "sample_rms_proxy"
    assert manifest["publish_metadata"]["subtitle"] == "A test episode"
    assert "publish-metadata.json" in manifest["outputs"]
    metadata = json.loads((tmp_path / "publish-metadata.json").read_text())
    assert metadata["publish"]["subtitle"] == "A test episode"


def test_rerender_removes_stale_chapters_when_markers_are_removed(tmp_path):
    raw = project().to_dict()
    raw["script"][0]["chapter"] = "Opening"
    with_chapter = Project.from_dict(raw)
    first = render_project(with_chapter, lambda backend: FakeProvider([]), tmp_path)
    assert first.manifest.exists() and (tmp_path / "chapters.json").exists()
    second = render_project(project(), lambda backend: FakeProvider([]), tmp_path)
    manifest = json.loads(second.manifest.read_text())
    assert "chapters_file" not in manifest
    assert not (tmp_path / "chapters.json").exists()


def test_default_render_does_not_export_stems(tmp_path):
    stale = tmp_path / "Previous_Title_stem_music.wav"
    stale.write_bytes(b"stale")
    (tmp_path / "manifest.json").write_text(json.dumps({"stems": {"music": stale.name}}), encoding="utf-8")
    unrelated = tmp_path / "Render_Test_stem_user-file.wav"
    unrelated.write_bytes(b"keep")
    result = render_project(project(), lambda backend: FakeProvider([]), tmp_path)
    manifest = json.loads(result.manifest.read_text())
    assert "stems" not in manifest
    assert not stale.exists()
    assert unrelated.exists()


def test_same_title_rerender_keeps_fresh_stems(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    _write_media(media / "bed.wav", value=1000)
    raw = project().to_dict()
    raw["project"]["export_stems"] = True
    raw["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, "volume": 0.5, "loop": True}]}
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    first = render_project(rendered, lambda backend: FakeProvider([]), tmp_path)
    first_stems = set(json.loads(first.manifest.read_text())["stems"].values())
    second = render_project(rendered, lambda backend: FakeProvider([]), tmp_path)
    second_manifest = json.loads(second.manifest.read_text())
    assert first_stems == set(second_manifest["stems"].values())
    assert all((tmp_path / filename).exists() for filename in first_stems)


def test_ducking_preserves_full_episode_duration(tmp_path):
    raw = project().to_dict()
    raw["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, "duck_speech": True}]}
    media = tmp_path / "media"
    media.mkdir()
    _write_media(media / "bed.wav", value=1000)
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")
    manifest = json.loads(result.manifest.read_text())
    assert manifest["duration_seconds"] == pytest.approx(0.3)


def test_ducking_is_recorded_in_manifest(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    _write_media(media / "bed.wav", value=1000)
    raw = project().to_dict()
    raw["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, "duck_speech": True, "duck_amount": 0.5}]}
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")
    cue = json.loads(result.manifest.read_text())["timeline"]["cues"][0]
    assert cue["duck_speech"] is True
    assert cue["duck_amount"] == 0.5


def test_render_reports_fade_metadata(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    _write_media(media / "bed.wav", value=1000)
    raw = project().to_dict()
    raw["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, "volume": 0.5, "fade_in_ms": 100, "fade_out_ms": 100}]}
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")
    cue = json.loads(result.manifest.read_text())["timeline"]["cues"][0]
    assert cue["fade_in_ms"] == 100
    assert cue["fade_out_ms"] == 100


def test_render_reports_progress(tmp_path):
    events = []
    render_project(project(), lambda backend: FakeProvider([]), tmp_path, progress=lambda current, total, step: events.append((current, total, step)))
    assert events[0] == (0, 4, "Preparing render")
    assert events[-1] == (4, 4, "Render complete")
    assert [event[0] for event in events] == sorted(event[0] for event in events)

def _write_png(path: Path, width: int = 1400, height: int = 1400):
    import zlib
    raw = b"\x00" + b"\xff\x00\x00" * width
    pixels = raw * height
    chunks = []
    for kind, payload in ((b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"), (b"IDAT", zlib.compress(pixels)), (b"IEND", b"")):
        chunks.append(len(payload).to_bytes(4, "big") + kind + payload + zlib.crc32(kind + payload).to_bytes(4, "big"))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"".join(chunks))


def test_render_exports_consolidated_credits_and_license_records(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    _write_media(media / "bed.wav", value=1000)
    _write_png(media / "cover.png")
    (media / "ASSET_MANIFEST.json").write_text(json.dumps({
        "manifest_version": 1,
        "assets": [{
            "file": "bed.wav",
            "sha256": __import__('hashlib').sha256((media / "bed.wav").read_bytes()).hexdigest(),
            "license": "CC0-1.0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "source": "test-generated audio",
        }],
    }), encoding="utf-8")
    (media / "ARTWORK_MANIFEST.json").write_text(json.dumps({
        "asset": "cover.png",
        "sha256": __import__('hashlib').sha256((media / "cover.png").read_bytes()).hexdigest(),
        "license": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "source": "test-generated artwork",
    }), encoding="utf-8")
    raw = project().to_dict()
    raw["project"].update({"artwork": "cover.png", "timeline": {"music": [{"file": "bed.wav", "start_line": 1}]}})
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")
    manifest = json.loads(result.manifest.read_text())
    records = json.loads((tmp_path / "out" / "license-records.json").read_text())
    assert manifest["license_records_file"] == "license-records.json"
    assert manifest["credits_file"] == "CREDITS.md"
    assert {record["filename"] for record in records["assets"]} == {"bed.wav", "cover.png"}
    assert all(record["license_status"] == "recorded" for record in records["assets"])
    assert records["models"][0]["license_status"] == "review-required"
    assert records["provenance"]["limitations"][1].startswith("Piper model licenses")
    assert "CREDITS.md" in (tmp_path / "out" / "CREDITS.md").read_text(encoding="utf-8")


def test_non_piper_models_remain_provider_neutral(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    raw = project().to_dict()
    raw["speakers"][0]["backend"] = "custom-local"
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")
    records = json.loads((tmp_path / "out" / "license-records.json").read_text())
    model = records["models"][0]
    assert model["model_card_required"] is False
    assert "Piper" not in model["license"]
    assert records["provenance"]["limitations"][1].startswith("Provider/model licenses")


def test_unmanifested_assets_are_hashed_but_not_claimed_licensed(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    _write_media(media / "unlisted.wav")
    raw = project().to_dict()
    raw["project"]["timeline"] = {"effects": [{"file": "unlisted.wav", "start_line": 1}]}
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")
    records = json.loads((tmp_path / "out" / "license-records.json").read_text())
    record = next(asset for asset in records["assets"] if asset["filename"] == "unlisted.wav")
    assert record["license_status"] == "not-recorded"
    assert record["license"] is None
    assert record["sha256"] == __import__('hashlib').sha256((media / "unlisted.wav").read_bytes()).hexdigest()


def test_license_manifest_hash_mismatch_is_explicit(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    _write_media(media / "bed.wav")
    (media / "ASSET_MANIFEST.json").write_text(json.dumps({"assets": [{"file": "bed.wav", "sha256": "0" * 64, "license": "CC0-1.0"}]}), encoding="utf-8")
    raw = project().to_dict()
    raw["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1}]}
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")
    records = json.loads((tmp_path / "out" / "license-records.json").read_text())
    record = records["assets"][0]
    assert record["license_status"] == "manifest-hash-mismatch"
    assert "hash_warning" in record


def test_license_records_are_deterministic_and_stale_files_are_removed(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    _write_media(media / "bed.wav")
    raw = project().to_dict()
    raw["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1}]}
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    output = tmp_path / "out"
    render_project(rendered, lambda backend: FakeProvider([]), output)
    first_records = (output / "license-records.json").read_bytes()
    first_credits = (output / "CREDITS.md").read_bytes()
    render_project(rendered, lambda backend: FakeProvider([]), output)
    assert (output / "license-records.json").read_bytes() == first_records
    assert (output / "CREDITS.md").read_bytes() == first_credits
    legacy = Project.from_dict(project().to_dict())
    render_project(legacy, lambda backend: FakeProvider([]), output)
    assert not (output / "license-records.json").exists()
    assert not (output / "CREDITS.md").exists()


def test_artwork_validation_and_package_export(tmp_path):
    media = tmp_path / "media"; media.mkdir()
    _write_png(media / "cover.png")
    raw = project().to_dict()
    raw["project"].update({"artwork": "cover.png", "package_export": True, "publish_metadata": {"keywords": ["demo"], "episode_number": 2}})
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")
    package = result.package_dir
    assert package and (package / "feed.xml").exists()
    assert (package / "artwork" / "cover.png").exists()
    assert (package / "package-summary.json").exists()
    assert (package / "CREDITS.md").exists()
    assert (package / "license-records.json").exists()
    assert (package / "CREDITS.md").read_bytes() == (result.output_dir / "CREDITS.md").read_bytes()
    assert (package / "license-records.json").read_bytes() == (result.output_dir / "license-records.json").read_bytes()
    package_files = json.loads((package / "manifest.json").read_text())["package"]["files"]
    assert "CREDITS.md" in package_files
    assert "license-records.json" in package_files
    import xml.etree.ElementTree as ET
    root = ET.parse(package / "feed.xml").getroot()
    enclosure = root.find("./channel/item/enclosure")
    assert enclosure is not None and enclosure.attrib["url"].startswith("audio/")
    assert enclosure.attrib["length"] == str((package / "audio" / enclosure.attrib["url"].split("/", 1)[1]).stat().st_size)
    assert root.find("./channel/{http://www.itunes.com/dtds/podcast-1.0.dtd}keywords") is not None
    manifest = json.loads(result.manifest.read_text())
    assert manifest["package"]["feed"] == "feed.xml"
    assert root.find("./channel/item/pubDate") is not None


def test_package_contains_chapters_and_rss_link(tmp_path):
    media = tmp_path / "media"; media.mkdir()
    _write_png(media / "cover.png")
    raw = project().to_dict()
    raw["script"][0]["chapter"] = "Opening"
    raw["script"][2]["chapter"] = "Return"
    raw["project"].update({"artwork": "cover.png", "package_export": True})
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    result = render_project(rendered, lambda backend: FakeProvider([]), tmp_path / "out")
    package = result.package_dir
    assert package is not None
    assert (package / "chapters.json").exists()
    package_manifest = json.loads((package / "manifest.json").read_text())
    assert package_manifest["chapters_file"] == "chapters.json"
    summary = json.loads((package / "package-summary.json").read_text())
    assert summary["chapters"] == "chapters.json"
    import xml.etree.ElementTree as ET
    root = ET.parse(package / "feed.xml").getroot()
    chapters = root.find("./channel/item/{https://podcastindex.org/namespace/1.0}chapters")
    assert chapters is not None
    assert chapters.attrib == {"url": "chapters.json", "type": "application/json+chapters"}


def test_artwork_signature_must_match_extension(tmp_path):
    media = tmp_path / "media"; media.mkdir()
    _write_png(media / "cover.jpg")
    raw = project().to_dict(); raw["project"]["artwork"] = "cover.jpg"
    rendered = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    with pytest.raises(PackageError, match="extension"):
        inspect_artwork(rendered)


def test_render_rolls_back_chapters_and_package_on_package_failure(tmp_path, monkeypatch):
    media = tmp_path / "media"; media.mkdir(); _write_png(media / "cover.png")
    raw = project().to_dict()
    raw["script"][0]["chapter"] = "Opening"
    raw["project"].update({"artwork": "cover.png", "package_export": True})
    packaged = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    first = render_project(packaged, lambda backend: FakeProvider([]), tmp_path)
    old_chapters = (tmp_path / "chapters.json").read_bytes()
    old_package_manifest = (tmp_path / "package" / "manifest.json").read_bytes()
    monkeypatch.setattr("hotpepperpodcast.package._feed", lambda *args: (_ for _ in ()).throw(PackageError("feed failed")))
    with pytest.raises(RenderError, match="feed failed"):
        render_project(packaged, lambda backend: FakeProvider([]), tmp_path)
    assert (tmp_path / "chapters.json").read_bytes() == old_chapters
    assert (tmp_path / "package" / "manifest.json").read_bytes() == old_package_manifest


def test_package_export_restores_existing_package_on_failure(tmp_path, monkeypatch):
    media = tmp_path / "media"; media.mkdir(); _write_png(media / "cover.png")
    existing = tmp_path / "package"; existing.mkdir(); (existing / "old.txt").write_text("old", encoding="utf-8")
    raw = project().to_dict(); raw["project"]["artwork"] = "cover.png"
    packaged = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    audio = tmp_path / "audio.wav"; _write_media(audio)
    manifest = tmp_path / "manifest.json"; manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("hotpepperpodcast.package._feed", lambda *args: (_ for _ in ()).throw(PackageError("feed failed")))
    with pytest.raises(PackageError, match="feed failed"):
        export_package(packaged, tmp_path, (audio,), manifest)
    assert (existing / "old.txt").read_text(encoding="utf-8") == "old"


def test_disabling_package_removes_only_generated_package(tmp_path):
    media = tmp_path / "media"; media.mkdir(); _write_png(media / "cover.png")
    raw = project().to_dict(); raw["project"].update({"artwork": "cover.png", "package_export": True})
    packaged = Project.from_dict(raw, source_path=str(tmp_path / "episode.yaml"))
    first = render_project(packaged, lambda backend: FakeProvider([]), tmp_path)
    assert first.package_dir and first.package_dir.exists()
    plain = Project.from_dict(project().to_dict())
    render_project(plain, lambda backend: FakeProvider([]), tmp_path)
    assert not (tmp_path / "package").exists()


def test_unrecorded_package_is_not_deleted(tmp_path):
    package = tmp_path / "package"; package.mkdir(); (package / "keep.txt").write_text("user", encoding="utf-8")
    render_project(project(), lambda backend: FakeProvider([]), tmp_path)
    assert (package / "keep.txt").exists()


def test_incompatible_wav_rejected(tmp_path):
    class Bad(FakeProvider):
        def synthesize(self, text, voice, output_path, speed=1.0):
            self.calls.append(text)
            with wave.open(str(output_path), "wb") as out:
                if len(self.calls) == 1:
                    out.setnchannels(1); out.setsampwidth(2); out.setframerate(22050); out.writeframes(b"\0" * 100)
                else:
                    out.setnchannels(2); out.setsampwidth(2); out.setframerate(44100); out.writeframes(b"\0" * 100)
    provider = Bad([])
    with pytest.raises(RenderError, match="incompatible"):
        render_project(project(), lambda backend: provider, tmp_path)

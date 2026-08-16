import pytest
from hotpepperpodcast.models import Project, ProjectError
from hotpepperpodcast.project_io import load_project, save_project

def sample():
    return {"schema_version": 1, "project": {"title": "Test", "author": "A", "output_formats": ["wav"]}, "speakers": [{"id": "host", "name": "Host"}], "script": [{"speaker": "host", "text": "Hello"}]}

def test_yaml_roundtrip(tmp_path):
    path = tmp_path / "project.yaml"
    project = Project.from_dict(sample())
    save_project(project, path)
    loaded = load_project(path)
    assert loaded.title == "Test"
    assert loaded.script[0].text == "Hello"

def test_json_roundtrip(tmp_path):
    path = tmp_path / "project.json"
    save_project(Project.from_dict(sample()), path)
    assert load_project(path).speakers[0].id == "host"

def test_timeline_fields_roundtrip():
    raw = sample()
    raw["script"][0].update({"pause_after_ms": 1200, "chapter": "Opening", "enabled": False})
    project = Project.from_dict(raw)
    line = project.script[0]
    assert line.pause_after_ms == 1200
    assert line.chapter == "Opening"
    assert line.enabled is False
    assert project.to_dict()["script"][0] == {"speaker": "host", "text": "Hello", "pause_after_ms": 1200, "enabled": False, "chapter": "Opening"}


def test_timeline_pause_range_rejected():
    bad = sample(); bad["script"][0]["pause_after_ms"] = 60001
    with pytest.raises(ProjectError, match="pause_after_ms"):
        Project.from_dict(bad)


def test_optional_audio_lanes_roundtrip():
    raw = sample()
    raw["project"]["timeline"] = {
        "music": [{"file": "bed.wav", "start_line": 1, "offset_ms": 250, "volume": 0.35, "loop": True}],
        "effects": [{"file": "sting.mp3", "start_line": 1, "offset_ms": 900, "volume": 0.8}],
    }
    project = Project.from_dict(raw)
    assert project.timeline.music[0].file == "bed.wav"
    assert project.timeline.music[0].loop is True
    assert project.timeline.effects[0].volume == 0.8
    assert project.to_dict()["project"]["timeline"]["effects"][0]["file"] == "sting.mp3"


def test_audio_lane_rejects_unsafe_file_and_bad_anchor():
    bad = sample()
    bad["project"]["timeline"] = {"music": [{"file": "../secret.wav", "start_line": 1}]}
    with pytest.raises(ProjectError, match="local media filename"):
        Project.from_dict(bad)
    bad["project"]["timeline"]["music"][0]["file"] = r"media\\bed.wav"
    with pytest.raises(ProjectError, match="local media filename"):
        Project.from_dict(bad)
    bad = sample()
    bad["project"]["timeline"] = {"effects": [{"file": "sting.wav", "start_line": 2}]}
    with pytest.raises(ProjectError, match="start_line"):
        Project.from_dict(bad)


def test_audio_lane_rejects_muted_anchor_and_effect_loop():
    bad = sample()
    bad["script"][0]["enabled"] = False
    bad["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1}]}
    with pytest.raises(ProjectError, match="disabled"):
        Project.from_dict(bad)
    bad = sample()
    bad["project"]["timeline"] = {"effects": [{"file": "sting.wav", "start_line": 1, "loop": True}]}
    with pytest.raises(ProjectError, match="cannot loop"):
        Project.from_dict(bad)


def test_audio_lane_fades_roundtrip_and_range():
    raw = sample()
    raw["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, "fade_in_ms": 250, "fade_out_ms": 500}]}
    project = Project.from_dict(raw)
    cue = project.timeline.music[0]
    assert cue.fade_in_ms == 250
    assert cue.fade_out_ms == 500
    assert project.to_dict()["project"]["timeline"]["music"][0]["fade_out_ms"] == 500
    for field in ("fade_in_ms", "fade_out_ms"):
        bad = sample(); bad["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, field: -1}]}
        with pytest.raises(ProjectError, match="fade durations"):
            Project.from_dict(bad)
        bad = sample(); bad["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, field: 600001}]}
        with pytest.raises(ProjectError, match="fade durations"):
            Project.from_dict(bad)


def test_export_stems_roundtrip():
    raw = sample()
    raw["project"]["export_stems"] = True
    project = Project.from_dict(raw)
    assert project.export_stems is True
    assert project.to_dict()["project"]["export_stems"] is True


def test_loudness_and_publish_metadata_roundtrip():
    raw = sample()
    raw["project"].update({
        "loudness_check": True,
        "loudness_target_db": -18,
        "loudness_tolerance_db": 1.5,
        "loudness_max_peak_db": -2,
        "publish_metadata": {
            "subtitle": "A practical test",
            "series": "Test Series",
            "season_number": 2,
            "episode_number": 7,
            "episode_type": "full",
            "explicit": True,
            "language": "en-US",
            "keywords": ["testing", "audio"],
            "website": "https://example.com/show",
        },
    })
    project = Project.from_dict(raw)
    assert project.loudness_check is True
    assert project.loudness_target_db == -18
    assert project.publish_metadata.episode_number == 7
    assert project.publish_metadata.keywords == ("testing", "audio")
    saved = project.to_dict()["project"]
    assert saved["publish_metadata"]["website"] == "https://example.com/show"


def test_publish_metadata_rejects_unsafe_values():
    raw = sample(); raw["project"]["publish_metadata"] = {"keywords": [None, "audio"]}
    assert Project.from_dict(raw).publish_metadata.keywords == ("audio",)
    for field, value in (("website", "file:///tmp/show"), ("episode_type", "invalid"), ("language", "not a language code"), ("season_number", 0)):
        raw = sample(); raw["project"]["publish_metadata"] = {field: value}
        with pytest.raises(ProjectError, match="publish_metadata"):
            Project.from_dict(raw)


def test_loudness_ranges_rejected():
    for field, value in (("loudness_target_db", -41), ("loudness_tolerance_db", 0), ("loudness_max_peak_db", 1)):
        raw = sample(); raw["project"][field] = value
        with pytest.raises(ProjectError, match="loudness"):
            Project.from_dict(raw)


def test_audio_lane_ducking_roundtrip_and_range():
    raw = sample()
    raw["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, "duck_speech": True, "duck_amount": 0.7, "duck_attack_ms": 90, "duck_release_ms": 300}]}
    project = Project.from_dict(raw)
    cue = project.timeline.music[0]
    assert cue.duck_speech is True
    assert cue.duck_amount == 0.7
    assert cue.duck_attack_ms == 90
    assert project.to_dict()["project"]["timeline"]["music"][0]["duck_release_ms"] == 300
    for field, value in (("duck_amount", 1.1), ("duck_attack_ms", -1), ("duck_release_ms", 600001)):
        bad = sample(); bad["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, field: value}]}
        with pytest.raises(ProjectError, match="duck"):
            Project.from_dict(bad)


def test_audio_lane_volume_range_rejected():
    bad = sample()
    bad["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, "volume": 2.1}]}
    with pytest.raises(ProjectError, match="volume"):
        Project.from_dict(bad)


def test_unknown_speaker_rejected():
    bad = sample(); bad["script"][0]["speaker"] = "missing"
    with pytest.raises(ProjectError, match="unknown speakers"):
        Project.from_dict(bad)

def test_schema_rejected():
    bad = sample(); bad["schema_version"] = 99
    with pytest.raises(ProjectError, match="unsupported"):
        Project.from_dict(bad)


def test_artwork_and_package_roundtrip_and_safety():
    raw = sample()
    raw["project"].update({"artwork": "cover.png", "package_export": True})
    project = Project.from_dict(raw)
    assert project.artwork == "cover.png"
    assert project.package_export is True
    saved = project.to_dict()["project"]
    assert saved["artwork"] == "cover.png"
    assert saved["package_export"] is True
    for value in ("../cover.png", "cover.gif", "/tmp/cover.png"):
        bad = sample(); bad["project"]["artwork"] = value
        with pytest.raises(ProjectError, match="artwork"):
            Project.from_dict(bad)

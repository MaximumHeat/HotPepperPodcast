import json

import pytest

from hotpepperpodcast.chapters import CHAPTERS_VERSION, build_chapters, write_chapters
from hotpepperpodcast.models import Project


def project_with_chapters():
    return Project.from_dict({
        "schema_version": 1,
        "project": {"title": "Chapter Test", "author": "Tester", "output_formats": ["wav"]},
        "speakers": [{"id": "host", "name": "Host", "voice": "demo", "pause_after_ms": 100}],
        "script": [
            {"speaker": "host", "text": "Opening", "chapter": "Opening"},
            {"speaker": "host", "text": "Muted", "chapter": "Must not export", "enabled": False},
            {"speaker": "host", "text": "Topic", "chapter": "Main topic", "pause_after_ms": 0},
        ],
    })


def test_build_chapters_uses_actual_enabled_line_intervals():
    project = project_with_chapters()
    chapters = build_chapters(
        project,
        line_indexes=[0, 2],
        speech_intervals=[(0, 2205), (4410, 6615)],
        sample_rate=22050,
        duration=0.3,
    )
    assert chapters == {
        "version": CHAPTERS_VERSION,
        "title": "Chapter Test",
        "author": "Tester",
        "chapters": [
            {"startTime": 0.0, "title": "Opening", "toc": True, "endTime": 0.2},
            {"startTime": 0.2, "title": "Main topic", "toc": True, "endTime": 0.3},
        ],
    }


def test_write_chapters_is_stable_and_human_readable(tmp_path):
    project = project_with_chapters()
    path = write_chapters(
        tmp_path / "chapters.json",
        build_chapters(project, [0, 2], [(0, 2205), (4410, 6615)], 22050, 0.3),
    )
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == CHAPTERS_VERSION
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert path.read_text(encoding="utf-8") == write_chapters(
        tmp_path / "chapters-again.json",
        build_chapters(project, [0, 2], [(0, 2205), (4410, 6615)], 22050, 0.3),
    ).read_text(encoding="utf-8")


def test_build_chapters_rejects_mismatched_timing():
    with pytest.raises(ValueError, match="equal lengths"):
        build_chapters(project_with_chapters(), [0], [(0, 1), (1, 2)], 22050, 1.0)

    with pytest.raises(ValueError, match="positive"):
        build_chapters(project_with_chapters(), [0], [(0, 1)], 0, 1.0)


def test_atomic_write_preserves_previous_file_on_replace_failure(tmp_path, monkeypatch):
    from hotpepperpodcast import chapters

    destination = tmp_path / "chapters.json"
    destination.write_text("previous\\n", encoding="utf-8")
    real_replace = chapters.os.replace

    def fail_replace(source, target):
        raise OSError("replace failed")

    monkeypatch.setattr(chapters.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_chapters(destination, {"version": CHAPTERS_VERSION, "chapters": []})
    assert destination.read_text(encoding="utf-8") == "previous\\n"
    monkeypatch.setattr(chapters.os, "replace", real_replace)
    assert not list(tmp_path.glob(".chapters.json-*.tmp"))

import json

from hotpepperpodcast.cli import main


def manifest(tmp_path):
    path = tmp_path / "voices.json"
    path.write_text(json.dumps({
        "en_US-demo-medium": {
            "name": "demo",
            "quality": "medium",
            "num_speakers": 1,
            "language": {"code": "en_US", "name_english": "English", "country_english": "United States"},
            "files": {
                "en/en_US/demo/medium/en_US-demo-medium.onnx": {"size_bytes": 5, "md5_digest": "a" * 32},
                "en/en_US/demo/medium/en_US-demo-medium.onnx.json": {"size_bytes": 20, "md5_digest": "b" * 32},
                "en/en_US/demo/medium/MODEL_CARD": {"size_bytes": 8, "md5_digest": "c" * 32},
            },
        }
    }), encoding="utf-8")
    return path


def test_catalog_command(tmp_path, capsys):
    assert main(["voices", "catalog", "--source", str(manifest(tmp_path))]) == 0
    output = capsys.readouterr().out
    assert "en_US-demo-medium" in output
    assert "1 voice(s)" in output


def test_noninteractive_install_requires_acceptance(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["voices", "install", "en_US-demo-medium", "--source", str(manifest(tmp_path)), "--destination", str(tmp_path / "voices")]) == 2
    assert "license acceptance is required" in capsys.readouterr().err

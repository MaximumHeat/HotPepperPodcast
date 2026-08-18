import hashlib
import json
from pathlib import Path
import subprocess
import sys
import wave

from hotpepperpodcast.models import MEDIA_EXTENSIONS
from hotpepperpodcast.project_io import load_project
from hotpepperpodcast.render import render_project


ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "examples" / "media"


def test_starter_manifest_matches_pcm_assets():
    manifest = json.loads((MEDIA / "ASSET_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["license"] == "CC0-1.0-or-public-domain-dedication"
    assert manifest["generated_assets_only"] is True
    assert len(manifest["assets"]) == 5
    for record in manifest["assets"]:
        path = MEDIA / record["file"]
        assert path.suffix.lower() in MEDIA_EXTENSIONS
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
        with wave.open(str(path), "rb") as audio:
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2
            assert audio.getframerate() == 22_050
            assert audio.getnframes() == record["frames"]


def test_starter_generator_reproduces_identical_manifest(tmp_path):
    output = tmp_path / "media"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_starter_media.py"), "--output-dir", str(output)], check=True)
    expected = json.loads((MEDIA / "ASSET_MANIFEST.json").read_text(encoding="utf-8"))
    actual = json.loads((output / "ASSET_MANIFEST.json").read_text(encoding="utf-8"))
    assert actual == expected
    for record in actual["assets"]:
        assert (output / record["file"]).read_bytes() == (MEDIA / record["file"]).read_bytes()


def test_example_project_references_bundled_media():
    project = load_project(ROOT / "examples" / "hello.yaml")
    referenced = [cue.file for cue in (*project.timeline.music, *project.timeline.effects)]
    assert referenced
    assert all((MEDIA / filename).is_file() for filename in referenced)
    assert set(referenced) <= {record["file"] for record in json.loads((MEDIA / "ASSET_MANIFEST.json").read_text())["assets"]}


class FakeProvider:
    def synthesize(self, text, voice, output_path, speed=1.0, speaker_id=None):
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(22_050)
            output.writeframes(b"\x01\x00" * 2_205)


def test_example_project_renders_with_starter_media(tmp_path):
    project = load_project(ROOT / "examples" / "hello.yaml")
    result = render_project(project, lambda backend: FakeProvider(), tmp_path / "render")
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["timeline"]["music"] == 1
    assert manifest["timeline"]["effects"] == 3
    assert {cue["file"] for cue in manifest["timeline"]["cues"]} == {"subtle-bed.wav", "intro.wav", "transition-sting.wav", "outro.wav"}
    assert result.files[0].exists()

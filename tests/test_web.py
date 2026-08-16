import hashlib
import json
import socket
import threading
import time
import urllib.error
import urllib.request
import pytest

from hotpepperpodcast.catalog import VoiceCatalogEntry
from hotpepperpodcast.web import App, WebConfig, WebServer, find_available_port
from hotpepperpodcast.web_cli import build_parser, choose_port


def sample_project(tmp_path, name="episode.yaml", title="Web Test"):
    path = tmp_path / name
    path.write_text(f"""schema_version: 1
project:
  title: {title}
  author: Tester
  output_formats: [wav]
speakers:
  - id: host
    name: Host
    voice: demo
script:
  - speaker: host
    text: Hello from the web UI.
""", encoding="utf-8")
    return path


def running_app(tmp_path, renderer=None, voice_directory=None):
    sample_project(tmp_path)
    app = App(WebConfig(port=0, project_root=tmp_path, output_root=tmp_path / "renders", voice_directory=voice_directory or (tmp_path / "voices")), renderer=renderer)
    server = WebServer(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def get_json(server, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}{path}") as response:
        return response.status, json.loads(response.read())


def request_json(server, method, path, payload):
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_catalog_route_merges_configured_catalog_and_install_status(tmp_path, monkeypatch):
    voice_root = tmp_path / "voices"
    voice_root.mkdir()
    (voice_root / "demo-voice.onnx").write_bytes(b"model")
    (voice_root / "demo-voice.onnx.json").write_text('{"audio":{"sample_rate":22050}}', encoding="utf-8")
    entry = VoiceCatalogEntry(
        id="demo-voice", display_name="Demo Voice", language="en_US", accent="United States", quality="medium",
        model_url="https://example.test/demo.onnx", config_url="https://example.test/demo.onnx.json",
        model_card_url="https://example.test/MODEL_CARD", digest=hashlib.md5(b"model").hexdigest(), digest_algorithm="md5",
        license_name="CC0", license_url="https://example.test/license", size_bytes=5,
    )
    monkeypatch.setattr("hotpepperpodcast.web.load_manifest", lambda source, notice: (notice("using local test catalog"), {entry.id: entry})[1])
    server, thread = running_app(tmp_path, voice_directory=voice_root)
    try:
        status, body = get_json(server, "/api/catalog?source=https%3A%2F%2Fevil.example%2Fmanifest.json")
        assert status == 200
        assert body["source"] == "https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json"
        assert body["notices"] == ["using local test catalog"]
        assert body["voices"][0]["status"] == "incomplete"
        assert body["voices"][0]["files"] == {"model": True, "config": True, "model_card": False}
        assert "voice_directory" not in body
    finally:
        stop(server, thread)


def test_catalog_route_returns_service_unavailable_on_catalog_error(tmp_path, monkeypatch):
    from hotpepperpodcast.catalog import CatalogError
    monkeypatch.setattr("hotpepperpodcast.web.load_manifest", lambda source, notice: (_ for _ in ()).throw(CatalogError("offline and no cache")))
    server, thread = running_app(tmp_path)
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/catalog")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request)
        assert exc.value.code == 503
        body = json.loads(exc.value.read())
        assert body["error"] == "offline and no cache"
    finally:
        stop(server, thread)


def test_artwork_route_lists_local_images(tmp_path):
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "cover.png").write_bytes(b"PNG")
    (tmp_path / "media" / "cover.gif").write_bytes(b"GIF")
    server, thread = running_app(tmp_path)
    try:
        status, body = get_json(server, "/api/artwork?project=episode.yaml")
        assert status == 200 and body == {"files": ["cover.png"]}
    finally:
        stop(server, thread)


def test_engines_route_exposes_optional_capabilities(tmp_path, monkeypatch):
    monkeypatch.setattr("hotpepperpodcast.web.engine_capabilities", lambda *args: [])
    server, thread = running_app(tmp_path)
    try:
        assert get_json(server, "/api/engines") == (200, {"engines": []})
    finally:
        stop(server, thread)


def test_onboarding_route_reports_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr("hotpepperpodcast.web.engine_capabilities", lambda *args: [])
    server, thread = running_app(tmp_path)
    try:
        status, body = get_json(server, "/api/onboarding")
        assert status == 200
        assert body["needs_setup"] is True
        assert body["project_count"] == 1
        assert body["voice_count"] == 0
        assert body["steps"][0]["complete"] is True
        assert body["steps"][1]["complete"] is False
    finally:
        stop(server, thread)


def test_onboarding_does_not_require_piper_models_when_espeak_is_ready(tmp_path, monkeypatch):
    from hotpepperpodcast.tts import EngineCapability
    monkeypatch.setattr("hotpepperpodcast.web.engine_capabilities", lambda *args: [EngineCapability("espeak-ng", "eSpeak NG", "fallback", True, "", "")])
    server, thread = running_app(tmp_path)
    try:
        status, body = get_json(server, "/api/onboarding")
        assert status == 200
        assert body["needs_setup"] is False
        assert body["voice_count"] == 0
        assert body["steps"][1]["complete"] is True
    finally:
        stop(server, thread)


def test_create_sample_project_from_empty_workspace(tmp_path):
    server, thread = running_app(tmp_path)
    (tmp_path / "episode.yaml").unlink()
    try:
        status, body = request_json(server, "POST", "/api/project/sample", {"title": "First Episode"})
        assert status == 201
        assert body["project"] == "first-episode.yaml"
        assert body["data"]["project"]["title"] == "First Episode"
        assert (tmp_path / "first-episode.yaml").is_file()
    finally:
        stop(server, thread)


def test_import_labeled_script_creates_project(tmp_path):
    server, thread = running_app(tmp_path)
    try:
        status, body = request_json(server, "POST", "/api/project/import", {"text": "Host: Hello there.", "title": "Imported"})
        assert status == 201
        assert body["data"]["project"]["title"] == "Imported"
        assert body["data"]["script"][0]["text"] == "Hello there."
    finally:
        stop(server, thread)


def test_import_ambiguous_script_requires_mode(tmp_path):
    server, thread = running_app(tmp_path)
    try:
        status, body = request_json(server, "POST", "/api/project/import", {"text": "First unlabeled line."})
        assert status == 400
        assert "ambiguous" in body["error"]
    finally:
        stop(server, thread)


def test_health_project_and_voices(tmp_path):
    server, thread = running_app(tmp_path)
    try:
        assert get_json(server, "/api/health") == (200, {"status": "ok"})
        status, project = get_json(server, "/api/project")
        assert status == 200 and project["project"]["title"] == "Web Test"
        status, voices = get_json(server, "/api/voices")
        assert status == 200 and voices["voices"] == []
    finally:
        stop(server, thread)


def test_project_picker_and_explicit_project_selection(tmp_path):
    sample_project(tmp_path, "second.yaml", "Second Episode")
    server, thread = running_app(tmp_path)
    try:
        status, projects = get_json(server, "/api/projects")
        assert status == 200 and projects["projects"] == ["episode.yaml", "second.yaml"]
        status, project = get_json(server, "/api/project?project=second.yaml")
        assert status == 200 and project["project"]["title"] == "Second Episode"
    finally:
        stop(server, thread)


def test_save_valid_project_uses_selected_file(tmp_path):
    sample_project(tmp_path, "second.yaml", "Second Episode")
    server, thread = running_app(tmp_path)
    try:
        status, body = get_json(server, "/api/project?project=second.yaml")
        body["project"]["title"] = "Edited Episode"
        body["script"][0]["text"] = "Edited words."
        status, saved = request_json(server, "PUT", "/api/project?project=second.yaml", body)
        assert status == 200
        assert saved["project"]["title"] == "Edited Episode"
        status, reloaded = get_json(server, "/api/project?project=second.yaml")
        assert reloaded["script"][0]["text"] == "Edited words."
        assert "Edited Episode" in (tmp_path / "second.yaml").read_text(encoding="utf-8")
        assert "Edited Episode" not in (tmp_path / "episode.yaml").read_text(encoding="utf-8")
    finally:
        stop(server, thread)


def test_save_allows_timeline_controls_and_disabled_lines(tmp_path):
    server, thread = running_app(tmp_path)
    try:
        _, project = get_json(server, "/api/project")
        project["script"][0].update({"pause_after_ms": 1500, "chapter": "Opening", "enabled": False})
        status, saved = request_json(server, "PUT", "/api/project?project=episode.yaml", project)
        assert status == 200
        assert saved["script"][0]["pause_after_ms"] == 1500
        assert saved["script"][0]["chapter"] == "Opening"
        assert saved["script"][0]["enabled"] is False
    finally:
        stop(server, thread)


def test_media_route_excludes_symlinked_audio(tmp_path):
    (tmp_path / "media").mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"RIFF")
    (tmp_path / "media" / "linked.wav").symlink_to(outside)
    server, thread = running_app(tmp_path)
    try:
        status, body = get_json(server, "/api/media?project=episode.yaml")
        assert status == 200 and body == {"files": []}
    finally:
        stop(server, thread)


def test_media_route_lists_only_project_local_audio(tmp_path):
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "bed.wav").write_bytes(b"RIFF")
    (tmp_path / "media" / "notes.txt").write_text("not audio", encoding="utf-8")
    server, thread = running_app(tmp_path)
    try:
        status, body = get_json(server, "/api/media?project=episode.yaml")
        assert status == 200 and body == {"files": ["bed.wav"]}
    finally:
        stop(server, thread)


def test_save_roundtrips_audio_lanes(tmp_path):
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "bed.wav").write_bytes(b"RIFF")
    server, thread = running_app(tmp_path)
    try:
        _, project = get_json(server, "/api/project")
        project["project"]["timeline"] = {"music": [{"file": "bed.wav", "start_line": 1, "volume": 0.4, "loop": True}]}
        status, saved = request_json(server, "PUT", "/api/project?project=episode.yaml", project)
        assert status == 200
        assert saved["project"]["timeline"]["music"][0]["file"] == "bed.wav"
    finally:
        stop(server, thread)


def test_save_roundtrips_publish_metadata_and_loudness(tmp_path):
    server, thread = running_app(tmp_path)
    try:
        _, project = get_json(server, "/api/project")
        project["project"].update({
            "loudness_check": True,
            "artwork": "cover.png",
            "package_export": True,
            "publish_metadata": {
                "subtitle": "Web episode",
                "series": "Web series",
                "language": "en-US",
                "episode_type": "bonus",
                "season_number": 1,
                "episode_number": 3,
                "explicit": True,
                "keywords": ["web", "demo"],
                "website": "https://example.com/web",
                "copyright": "2026 Example",
            },
        })
        status, saved = request_json(server, "PUT", "/api/project?project=episode.yaml", project)
        assert status == 200
        assert saved["project"]["loudness_check"] is True
        assert saved["project"]["publish_metadata"]["episode_number"] == 3
        assert saved["project"]["publish_metadata"]["explicit"] is True
    finally:
        stop(server, thread)


def test_save_preserves_optional_script_fields(tmp_path):
    server, thread = running_app(tmp_path)
    try:
        _, project = get_json(server, "/api/project")
        project["script"][0].update({"pause_after_ms": 1200, "pronunciation": {"API": "A P I"}, "enabled": False, "chapter": "Opening"})
        status, saved = request_json(server, "PUT", "/api/project?project=episode.yaml", project)
        assert status == 200
        line = saved["script"][0]
        assert line["pause_after_ms"] == 1200
        assert line["pronunciation"] == {"API": "A P I"}
        assert line["enabled"] is False
        assert line["chapter"] == "Opening"
    finally:
        stop(server, thread)


def test_invalid_save_is_rejected_without_writing(tmp_path):
    server, thread = running_app(tmp_path)
    original = (tmp_path / "episode.yaml").read_text(encoding="utf-8")
    try:
        status, body = get_json(server, "/api/project")
        body["script"][0]["speaker"] = "missing-speaker"
        status, error = request_json(server, "PUT", "/api/project?project=episode.yaml", body)
        assert status == 400 and "unknown speakers" in error["error"]
        assert (tmp_path / "episode.yaml").read_text(encoding="utf-8") == original
    finally:
        stop(server, thread)


def test_web_package_files_are_served_by_relative_path(tmp_path):
    def renderer(project, output_root):
        output_root.mkdir(parents=True, exist_ok=True)
        package = output_root / "package" / "artwork"; package.mkdir(parents=True)
        file = package / "cover.png"; file.write_bytes(b"PNG")
        manifest = output_root / "manifest.json"; manifest.write_text(json.dumps({"package": {"directory": "package", "files": ["artwork/cover.png"]}}), encoding="utf-8")
        class Result: pass
        Result.files = []
        Result.manifest = manifest
        return Result()
    server, thread = running_app(tmp_path, renderer)
    try:
        _, body = request_json(server, "POST", "/api/render", {"project": "episode.yaml"})
        for _ in range(40):
            _, job = get_json(server, f"/api/jobs/{body['id']}")
            if job["status"] == "completed": break
            time.sleep(.01)
        assert job["package"]["outputs"][0]["url"].endswith("/package/artwork/cover.png")
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}{job['package']['outputs'][0]['url']}") as response:
            assert response.read() == b"PNG"
    finally:
        stop(server, thread)


def test_render_progress_and_audio_preview_range(tmp_path):
    def renderer(project, output_root, progress):
        output_root.mkdir(parents=True, exist_ok=True)
        progress(1, 2, "Synthesizing")
        audio = output_root / "preview.wav"
        audio.write_bytes(b"0123456789")
        class Result:
            files = [audio]
            manifest = output_root / "manifest.json"
        Result.manifest.write_text("{}", encoding="utf-8")
        progress(2, 2, "Render complete")
        return Result()

    server, thread = running_app(tmp_path, renderer)
    try:
        status, body = request_json(server, "POST", "/api/render", {"project": "episode.yaml", "provider": "espeak-ng"})
        assert status == 202
        for _ in range(40):
            _, job = get_json(server, f"/api/jobs/{body['id']}")
            if job["status"] == "completed":
                break
            time.sleep(.01)
        assert job["status"] == "completed"
        assert job["progress"] == 2 and job["total_steps"] == 2
        assert job["loudness"] is None
        assert job["outputs"][0]["url"].endswith("/preview.wav")
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{job['outputs'][0]['url']}", headers={"Range": "bytes=2-5"})
        with urllib.request.urlopen(request) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 2-5/10"
            assert response.read() == b"2345"
        invalid = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{job['outputs'][0]['url']}", headers={"Range": "bytes=99-100"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(invalid)
        assert exc.value.code == 416
        assert exc.value.headers["Content-Range"] == "bytes */10"
    finally:
        stop(server, thread)


def test_symlinked_job_output_is_rejected(tmp_path):
    server, thread = running_app(tmp_path)
    try:
        job = server.app.jobs.create("episode.yaml")
        server.app.jobs.update(job.id, status="completed", files=["secret.wav"])
        output_root = (tmp_path / "renders").resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / f"job-{job.id}").symlink_to("/tmp", target_is_directory=True)
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/jobs/{job.id}/outputs/secret.wav")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request)
        assert exc.value.code == 404
    finally:
        stop(server, thread)


def test_output_traversal_and_incomplete_output_are_rejected(tmp_path):
    server, thread = running_app(tmp_path)
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/jobs/999/outputs/../../etc/passwd")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request)
        assert exc.value.code in {400, 404}
    finally:
        stop(server, thread)


def test_render_job_uses_explicit_project(tmp_path):
    calls = []

    class Result:
        files = [tmp_path / "episode.wav"]
        manifest = tmp_path / "manifest.json"

    def renderer(project, output_root):
        calls.append((project.title, output_root))
        return Result()

    sample_project(tmp_path, "second.yaml", "Second Episode")
    server, thread = running_app(tmp_path, renderer)
    try:
        status, body = request_json(server, "POST", "/api/render", {"project": "second.yaml"})
        assert status == 202
        for _ in range(40):
            _, job = get_json(server, f"/api/jobs/{body['id']}")
            if job["status"] == "completed":
                break
            time.sleep(.01)
        assert job["status"] == "completed"
        assert calls[0][0] == "Second Episode"
    finally:
        stop(server, thread)


def test_save_is_rejected_while_selected_project_renders(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class Result:
        files = []
        manifest = tmp_path / "manifest.json"

    def renderer(project, output_root):
        started.set()
        release.wait(timeout=2)
        return Result()

    server, thread = running_app(tmp_path, renderer)
    try:
        status, body = request_json(server, "POST", "/api/render", {"project": "episode.yaml"})
        assert status == 202 and started.wait(timeout=1)
        _, project = get_json(server, "/api/project")
        project["project"]["title"] = "Should Not Save Yet"
        status, error = request_json(server, "PUT", "/api/project?project=episode.yaml", project)
        assert status == 409 and "currently rendering" in error["error"]
    finally:
        release.set()
        stop(server, thread)


def test_default_web_port_is_ephemeral():
    from hotpepperpodcast.web import WebConfig
    assert WebConfig().port == 0
    assert build_parser().parse_args([]).port == 0


def test_port_selection_falls_back_without_prompt():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]
        fallback = choose_port("127.0.0.1", port, interactive=False)
        assert fallback != port
        assert fallback > 0
    assert find_available_port("127.0.0.1", 0) > 0


def test_static_path_traversal_is_rejected(tmp_path):
    server, thread = running_app(tmp_path)
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/%2e%2e/pyproject.toml")
        try:
            urllib.request.urlopen(request)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        stop(server, thread)

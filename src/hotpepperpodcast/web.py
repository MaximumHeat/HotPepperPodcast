"""Small local web UI for HotPepperPodcast.

The server is intentionally standard-library-only. It binds to loopback,
limits JSON requests, and delegates rendering to the tested core library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
import json
import mimetypes
from pathlib import Path
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

from .catalog import CatalogError, InstallPlan, PIPER_MANIFEST_URL, load_manifest
from .logging import configure_logging
from .models import ARTWORK_EXTENSIONS, MEDIA_EXTENSIONS, Project, ProjectError, ScriptLine, Speaker
from .parser import ScriptParseError, assign_unlabeled, parse_text
from .project_io import ProjectFileError, load_project, save_project
from .render import render_project
from .tts import engine_capabilities, provider_for_engine
from .installer import InstallValidationError, verify_installed_voice
from .voices import default_voice_directory, discover_voices

MAX_JSON_BYTES = 1_000_000
MAX_JOBS = 50
LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}


@dataclass
class RenderJob:
    id: str
    status: str = "queued"
    project: str | None = None
    provider: str | None = None
    error: str | None = None
    progress: int = 0
    total_steps: int = 0
    step: str = "Queued"
    files: list[str] = field(default_factory=list)
    outputs: list[dict[str, str]] = field(default_factory=list)
    manifest: str | None = None
    loudness: dict[str, object] | None = None
    package: dict[str, object] | None = None


class JobStore:
    def __init__(self, max_jobs: int = MAX_JOBS):
        self._jobs: dict[str, RenderJob] = {}
        self._lock = threading.Lock()
        self._next = 1
        self._max_jobs = max_jobs

    def create(self, project: str | None = None, provider: str | None = None) -> RenderJob:
        with self._lock:
            finished = [job_id for job_id, job in self._jobs.items() if job.status in {"completed", "failed"}]
            while len(self._jobs) >= self._max_jobs and finished:
                del self._jobs[finished.pop(0)]
            if len(self._jobs) >= self._max_jobs:
                raise RuntimeError("too many render jobs; wait for an existing job to finish")
            job = RenderJob(str(self._next), project=project, provider=provider)
            self._next += 1
            self._jobs[job.id] = job
            return job

    def get(self, job_id: str) -> RenderJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active_projects(self) -> list[str | None]:
        with self._lock:
            return [job.project for job in self._jobs.values() if job.status in {"queued", "running"}]

    def update(self, job_id: str, **changes) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                for key, value in changes.items():
                    setattr(job, key, value)


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 0
    project_root: Path = Path(".")
    output_root: Path = Path("renders/web")
    voice_directory: Path = field(default_factory=default_voice_directory)
    piper_binary: str = "piper"
    piper_url: str = "http://127.0.0.1:9021"
    provider: str = "direct"
    espeak_binary: str | None = None
    xtts_model: str = "tts_models/multilingual/multi-dataset/xtts_v2"
    xtts_language: str = "en"
    xtts_speaker_wav: Path | None = None
    xtts_gpu: bool = False
    catalog_source: str = PIPER_MANIFEST_URL

    def __post_init__(self):
        if self.host not in LOOPBACK_HOSTS:
            raise ValueError("web UI host must be loopback (127.0.0.1 or localhost)")
        if not 0 <= self.port <= 65535:
            raise ValueError("web UI port must be between 0 and 65535")


class App:
    def __init__(self, config: WebConfig, renderer: Callable | None = None):
        self.config = config
        self.jobs = JobStore()
        self.logger = configure_logging()
        self._renderer = renderer
        self._render_lock = threading.Lock()

    def project_candidates(self) -> list[Path]:
        root = self.config.project_root.expanduser().resolve()
        candidates = sorted(root.glob("*.yaml")) + sorted(root.glob("*.yml")) + sorted(root.glob("*.json"))
        return [path for path in candidates if path.is_file()]

    def resolve_project(self, requested: str | None = None) -> Path:
        root = self.config.project_root.expanduser().resolve()
        if requested:
            candidate = (root / unquote(requested)).resolve()
            if candidate != root and root not in candidate.parents:
                raise ProjectFileError("project must be inside the configured project directory")
            if candidate.suffix.lower() not in {".yaml", ".yml", ".json"} or not candidate.is_file():
                raise ProjectFileError(f"project was not found: {requested}")
            return candidate
        candidates = self.project_candidates()
        if not candidates:
            raise ProjectFileError(f"no YAML/JSON project found in {root}")
        return candidates[0]

    def project(self, requested: str | None = None) -> Project:
        return load_project(self.resolve_project(requested))

    def artwork_candidates(self, requested: str | None = None) -> list[str]:
        project_path = self.resolve_project(requested)
        media_root = project_path.parent / "media"
        if media_root.is_symlink() or not media_root.is_dir():
            return []
        return sorted(path.name for path in media_root.iterdir() if path.is_file() and not path.is_symlink() and path.suffix.lower() in ARTWORK_EXTENSIONS)

    def media_candidates(self, requested: str | None = None) -> list[str]:
        project_path = self.resolve_project(requested)
        media_root = project_path.parent / "media"
        if media_root.is_symlink() or not media_root.is_dir():
            return []
        return sorted(path.name for path in media_root.iterdir() if path.is_file() and not path.is_symlink() and path.suffix.lower() in MEDIA_EXTENSIONS)

    def project_is_active(self, target: Path) -> bool:
        target = target.resolve()
        for requested in self.jobs.active_projects():
            try:
                if self.resolve_project(requested) == target:
                    return True
            except ProjectFileError:
                continue
        return False

    def render(self, job: RenderJob) -> None:
        self.jobs.update(job.id, status="running", step="Starting render")
        try:
            with self._render_lock:
                project = self.project(job.project)
                job_output = self.config.output_root.expanduser().resolve() / f"job-{job.id}"

                def progress(completed: int, total: int, step: str) -> None:
                    self.jobs.update(job.id, progress=completed, total_steps=total, step=step)

                if self._renderer:
                    # Injected renderers may expose either the old two-argument
                    # or progress-aware contract. Inspecting the signature
                    # avoids rerunning a renderer after an internal TypeError.
                    try:
                        parameters = inspect.signature(self._renderer).parameters.values()
                        accepts_progress = any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters) or len(parameters) >= 3
                    except (TypeError, ValueError):
                        accepts_progress = False
                    result = self._renderer(project, job_output, progress) if accepts_progress else self._renderer(project, job_output)
                else:
                    provider = provider_for_engine(
                        job.provider or self.config.provider,
                        piper_binary=self.config.piper_binary,
                        voice_directory=self.config.voice_directory,
                        piper_url=self.config.piper_url,
                        espeak_binary=self.config.espeak_binary,
                        xtts_model=self.config.xtts_model,
                        xtts_language=self.config.xtts_language,
                        xtts_speaker_wav=self.config.xtts_speaker_wav,
                        xtts_gpu=self.config.xtts_gpu,
                    )
                    result = render_project(project, lambda backend: provider, job_output, progress=progress)
                files = [Path(path).name for path in result.files]
                manifest = Path(result.manifest).name
                try:
                    manifest_data = json.loads(Path(result.manifest).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    manifest_data = {}
                loudness = manifest_data.get("loudness")
                outputs = [{"name": name, "url": f"/api/jobs/{job.id}/outputs/{name}", "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream"} for name in files]
                package = manifest_data.get("package") if isinstance(manifest_data.get("package"), dict) else None
                if package:
                    package_files = package.get("files", []) if isinstance(package.get("files", []), list) else []
                    package["outputs"] = [{"name": name, "url": f"/api/jobs/{job.id}/package/{name}", "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream"} for name in package_files if isinstance(name, str)]
                self.jobs.update(job.id, status="completed", progress=job.total_steps, step=(f"Render complete · loudness {loudness['status']}" if loudness else "Render complete"), files=files, outputs=outputs, manifest=manifest, loudness=loudness, package=package)
        except Exception as exc:
            self.logger.exception("web render failed")
            self.jobs.update(job.id, status="failed", step="Render failed", error=str(exc))


class Handler(BaseHTTPRequestHandler):
    server: "WebServer"

    def log_message(self, format, *args):
        self.server.app.logger.info("web %s - %s", self.address_string(), format % args)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_JSON_BYTES:
            raise ValueError(f"request body exceeds {MAX_JSON_BYTES} bytes")
        raw = self.rfile.read(length)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _requested_project(self) -> str | None:
        values = parse_qs(urlparse(self.path).query).get("project", [])
        return values[0] if values else None

    def _serve_package_output(self, job_id: str, requested_name: str) -> None:
        job = self.server.app.jobs.get(job_id)
        if job is None or job.status != "completed" or not job.package:
            self.send_error(404)
            return
        name = unquote(requested_name)
        if not name or name.startswith("/") or "\\\\" in name or any(part in {"", ".", ".."} for part in name.split("/")):
            self.send_error(404)
            return
        package_root = self.server.app.config.output_root.expanduser().resolve() / f"job-{job.id}" / "package"
        if not package_root.is_dir() or package_root.is_symlink():
            self.send_error(404)
            return
        target = (package_root / name).resolve()
        if package_root not in target.parents or not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_output(self, job_id: str, requested_name: str) -> None:
        job = self.server.app.jobs.get(job_id)
        if job is None or job.status != "completed" or requested_name not in job.files:
            self.send_error(404)
            return
        name = unquote(requested_name)
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            self.send_error(404)
            return
        output_root = self.server.app.config.output_root.expanduser().resolve()
        raw_job_dir = output_root / f"job-{job.id}"
        if raw_job_dir.is_symlink():
            self.send_error(404)
            return
        job_dir = raw_job_dir.resolve()
        if output_root != job_dir and output_root not in job_dir.parents:
            self.send_error(404)
            return
        target = (job_dir / name).resolve()
        if job_dir not in target.parents or not target.is_file():
            self.send_error(404)
            return
        size = target.stat().st_size
        start, end, status = 0, size - 1, 200
        range_header = self.headers.get("Range")
        if range_header:
            if not range_header.startswith("bytes=") or "," in range_header:
                self.send_error(416, "invalid byte range")
                return
            value = range_header[6:]
            try:
                first, last = value.split("-", 1)
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                else:
                    length = int(last)
                    start = max(size - length, 0)
                    end = size - 1
                if start < 0 or start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1)
                status = 206
            except ValueError:
                self.send_response(416, "Range Not Satisfiable")
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with target.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _serve_catalog(self) -> None:
        source = self.server.app.config.catalog_source
        try:
            notices: list[str] = []
            catalog = load_manifest(source, notice=notices.append)
            installed = {voice.id: voice for voice in discover_voices(self.server.app.config.voice_directory)}
            voice_root = self.server.app.config.voice_directory.expanduser()
            entries = []
            for voice in sorted(catalog.values(), key=lambda item: item.id):
                local = installed.get(voice.id)
                model_exists = (voice_root / f"{voice.id}.onnx").is_file()
                config_exists = (voice_root / f"{voice.id}.onnx.json").is_file()
                card_exists = (voice_root / f"{voice.id}.MODEL_CARD").is_file()
                complete = model_exists and config_exists and card_exists
                status = "available"
                if complete:
                    try:
                        verify_installed_voice(InstallPlan.for_voice(voice, voice_root))
                        status = "installed"
                    except (CatalogError, InstallValidationError, OSError, ValueError):
                        status = "invalid"
                elif model_exists or config_exists or card_exists:
                    status = "incomplete"
                entries.append({
                    "id": voice.id,
                    "display_name": voice.display_name,
                    "language": voice.language,
                    "accent": voice.accent,
                    "quality": voice.quality,
                    "license_name": voice.license_name,
                    "license_url": voice.license_url,
                    "model_card_url": voice.model_card_url,
                    "size_bytes": voice.size_bytes,
                    "num_speakers": voice.num_speakers,
                    "installed": complete,
                    "files": {"model": model_exists, "config": config_exists, "model_card": card_exists},
                    "status": status,
                })
            self._json(200, {"source": source, "notices": notices, "voices": entries})
        except CatalogError as exc:
            self._json(503, {"error": str(exc), "source": source})

    def _serve_static(self) -> None:
        path = urlparse(self.path).path
        relative = "index.html" if path in {"/", ""} else unquote(path.lstrip("/"))
        root = (Path(__file__).parent / "static").resolve()
        target = (root / relative).resolve()
        if root != target and root not in target.parents:
            self.send_error(404)
            return
        if not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(200, {"status": "ok"})
        elif path == "/api/project":
            try:
                project = self.server.app.project(self._requested_project())
                self._json(200, project.to_dict())
            except (ProjectFileError, ProjectError) as exc:
                self._json(404, {"error": str(exc)})
        elif path == "/api/projects":
            self._json(200, {"projects": [path.name for path in self.server.app.project_candidates()]})
        elif path == "/api/engines":
            capabilities = engine_capabilities(self.server.app.config.piper_binary, self.server.app.config.voice_directory, self.server.app.config.piper_url, self.server.app.config.xtts_speaker_wav)
            self._json(200, {"engines": [capability.to_dict() for capability in capabilities]})
        elif path == "/api/onboarding":
            project_count = len(self.server.app.project_candidates())
            voice_count = len(discover_voices(self.server.app.config.voice_directory))
            capabilities = engine_capabilities(self.server.app.config.piper_binary, self.server.app.config.voice_directory, self.server.app.config.piper_url, self.server.app.config.xtts_speaker_wav)
            ready = next((capability.id for capability in capabilities if capability.ready), "espeak-ng")
            engine_ready = any(capability.ready for capability in capabilities)
            self._json(200, {"needs_setup": project_count == 0 or not engine_ready, "project_count": project_count, "voice_count": voice_count, "recommended_engine": ready, "steps": [{"id": "project", "complete": project_count > 0, "label": "Open, create, or import a project"}, {"id": "engine", "complete": engine_ready, "label": "Choose an available voice engine"}, {"id": "render", "complete": False, "label": "Render a first episode"}]})
        elif path == "/api/voices":
            voices = discover_voices(self.server.app.config.voice_directory)
            self._json(200, {"voices": [{"id": voice.id, "language": voice.language, "sample_rate": voice.sample_rate, "num_speakers": voice.num_speakers} for voice in voices]})
        elif path == "/api/media":
            try:
                self._json(200, {"files": self.server.app.media_candidates(self._requested_project())})
            except ProjectFileError as exc:
                self._json(404, {"error": str(exc)})
        elif path == "/api/artwork":
            try:
                self._json(200, {"files": self.server.app.artwork_candidates(self._requested_project())})
            except ProjectFileError as exc:
                self._json(404, {"error": str(exc)})
        elif path == "/api/catalog":
            self._serve_catalog()
        elif path.startswith("/api/jobs/"):
            parts = path.split("/")
            if len(parts) >= 6 and parts[4] == "outputs":
                self._serve_output(unquote(parts[3]), "/".join(parts[5:]))
                return
            if len(parts) >= 6 and parts[4] == "package":
                self._serve_package_output(unquote(parts[3]), "/".join(parts[5:]))
                return
            job = self.server.app.jobs.get(path.rsplit("/", 1)[-1])
            self._json(200, job.__dict__) if job else self._json(404, {"error": "job not found"})
        else:
            self._serve_static()

    def _new_project_path(self, requested_name: str | None = None) -> Path:
        root = self.server.app.config.project_root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        name = (requested_name or "first-episode.yaml").strip()
        candidate_name = Path(name).name
        if candidate_name != name or Path(candidate_name).suffix.lower() not in {".yaml", ".yml", ".json"}:
            raise ValueError("project filename must be a simple YAML, YML, or JSON filename")
        candidate = root / candidate_name
        if not candidate.exists():
            return candidate
        stem, suffix = candidate.stem, candidate.suffix
        for index in range(2, 1000):
            candidate = root / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                return candidate
        raise ValueError("could not choose a new project filename")

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/project/sample":
                body = self._read_json()
                title = str(body.get("title") or "My First HotPepperPodcast Episode").strip()[:255]
                author = str(body.get("author") or "").strip()[:255]
                target = self._new_project_path(body.get("filename"))
                project = Project(title=title, author=author, speakers=(Speaker(id="host", name="Host", backend="espeak-ng", voice="en-us"),), script=(ScriptLine(speaker="host", text="Welcome to your first local podcast episode. Edit these words, save, and render when you are ready."),), source_path=str(target))
                save_project(project, target)
                self._json(201, {"project": target.name, "data": project.to_dict()})
                return
            if path == "/api/project/import":
                body = self._read_json()
                text = body.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("text is required")
                parsed = parse_text(text)
                if parsed.is_ambiguous:
                    mode = body.get("mode")
                    if mode not in {"narrator", "alternate"}:
                        raise ScriptParseError("the script is ambiguous; choose narrator or alternate")
                    parsed = assign_unlabeled(parsed, mode)
                names = list(parsed.speaker_names)
                ids = {name: f"speaker-{index + 1}" for index, name in enumerate(names)}
                speakers = tuple(Speaker(id=ids[name], name=name, backend="espeak-ng", voice="en-us") for name in names)
                lines = tuple(ScriptLine(speaker=ids[line.speaker], text=line.text) for line in parsed.lines if line.speaker)
                title = str(body.get("title") or "Imported Episode").strip()[:255]
                author = str(body.get("author") or "").strip()[:255]
                target = self._new_project_path(body.get("filename"))
                project = Project(title=title, author=author, speakers=speakers, script=lines, source_path=str(target))
                save_project(project, target)
                self._json(201, {"project": target.name, "data": project.to_dict()})
                return
            if path != "/api/render":
                self._json(404, {"error": "not found"})
                return
            body = self._read_json()
            requested = body.get("project")
            provider = body.get("provider")
            if requested is not None and not isinstance(requested, str):
                raise ValueError("project must be a filename string")
            if provider is not None and (not isinstance(provider, str) or provider not in {"direct", "http", "piper-direct", "piper-http", "espeak-ng", "xtts"}):
                raise ValueError("provider must be a supported local engine")
            resolved = self.server.app.resolve_project(requested)
            project_name = resolved.relative_to(self.server.app.config.project_root.expanduser().resolve()).as_posix()
            job = self.server.app.jobs.create(project_name, provider)
            threading.Thread(target=self.server.app.render, args=(job,), daemon=True).start()
            self._json(202, job.__dict__)
        except RuntimeError as exc:
            self._json(429, {"error": str(exc)})
        except (ProjectFileError, ProjectError, ScriptParseError, ValueError, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
            self._json(400, {"error": str(exc)})

    def do_PUT(self):
        path = urlparse(self.path).path
        if path != "/api/project":
            self._json(404, {"error": "not found"})
            return
        requested = self._requested_project()
        if not requested:
            self._json(400, {"error": "project query parameter is required"})
            return
        try:
            target = self.server.app.resolve_project(requested)
            if self.server.app.project_is_active(target):
                self._json(409, {"error": "project is currently rendering; wait for the job to finish"})
                return
            with self.server.app._render_lock:
                # Re-check after acquiring the lock: a render may have been
                # queued between the first check and this critical section.
                if self.server.app.project_is_active(target):
                    self._json(409, {"error": "project is currently rendering; wait for the job to finish"})
                    return
                body = self._read_json()
                project = Project.from_dict(body, source_path=str(target))
                save_project(project, target)
            self._json(200, project.to_dict())
        except (ProjectFileError, ProjectError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})


class WebServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, app: App):
        self.app = app
        super().__init__((app.config.host, app.config.port), Handler)


def find_available_port(host: str, preferred: int = 8080) -> int:
    if preferred == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, 0))
            return int(probe.getsockname()[1])
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
                return port
            except OSError:
                continue
    raise OSError(f"no available port found from {preferred} to {preferred + 19}")


def serve(config: WebConfig) -> None:
    app = App(config)
    server = WebServer(app)
    print(f"HotPepperPodcast UI: http://{config.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

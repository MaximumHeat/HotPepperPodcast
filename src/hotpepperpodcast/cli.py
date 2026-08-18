"""Command-line interface for the v0.1 speech vertical slice."""
from __future__ import annotations
import argparse
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .catalog import CatalogError, InstallPlan, PIPER_MANIFEST_URL, load_manifest
from .installer import InstallError, download_voice, verify_installed_voice
from .logging import configure_logging, log_path
from .models import Project, ProjectError, Speaker, ScriptLine
from .parser import ScriptParseError, assign_unlabeled, parse_text
from .project_io import ProjectFileError, load_document, load_project, save_project
from .render import RenderError, render_project
from .tts import TTSProviderError, engine_capabilities, provider_for_engine
from .voices import default_voice_directory, discover_voices, list_speaker_ids

DEFAULT_BINARY = Path.home() / "AI" / "piper" / "piper_bin"
DEFAULT_VOICES = ("en_US-lessac-medium", "en_US-amy-medium", "en_GB-aru-medium")


def _provider_factory(args):
    selected = args.provider
    def factory(backend: str):
        return provider_for_engine(
            selected or backend,
            piper_binary=args.piper_binary,
            voice_directory=args.voice_dir,
            piper_url=args.piper_url,
            espeak_binary=args.espeak_binary,
            xtts_model=args.xtts_model,
            xtts_language=args.xtts_language,
            xtts_speaker_wav=args.xtts_speaker_wav,
            xtts_gpu=args.xtts_gpu,
            kokoro_voice=args.kokoro_voice,
        )
    return factory


def _render(args) -> int:
    logger = configure_logging()
    try:
        project = load_project(args.project)
        result = render_project(project, _provider_factory(args), args.output_dir, args.keep_segments, export_stems=args.export_stems or None, loudness_check=args.check_loudness or None, package_export=args.export_package or None)
    except (ProjectFileError, ProjectError, RenderError, TTSProviderError) as exc:
        logger.exception("render failed")
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for path in result.files:
        print(path)
    manifest_data = __import__("json").loads(result.manifest.read_text(encoding="utf-8"))
    if "loudness" in manifest_data:
        loudness = manifest_data["loudness"]
        print(f"loudness: {loudness['status']} (RMS {loudness['rms_dbfs']:.2f} dBFS, peak {loudness['peak_dbfs']:.2f} dBFS)")
    if result.package_dir:
        print(f"package: {result.package_dir}")
    print(f"manifest: {result.manifest}")
    logger.info("rendered %s to %s", project.title, result.output_dir)
    return 0


def _import_text(args) -> int:
    logger = configure_logging()
    try:
        text = Path(args.input).read_text(encoding="utf-8")
        parsed = parse_text(text)
        if parsed.is_ambiguous:
            if not args.mode:
                raise ScriptParseError("the script is ambiguous; rerun with --mode narrator or --mode alternate")
            parsed = assign_unlabeled(parsed, args.mode)
        names = list(parsed.speaker_names)
        ids = {name: f"speaker-{index + 1}" for index, name in enumerate(names)}
        speakers = tuple(Speaker(id=ids[name], name=name, voice=DEFAULT_VOICES[index % len(DEFAULT_VOICES)]) for index, name in enumerate(names))
        lines = tuple(ScriptLine(speaker=ids[line.speaker], text=line.text) for line in parsed.lines if line.speaker)
        project = Project(title=args.title or Path(args.input).stem, author=args.author or "", speakers=speakers, script=lines)
        save_project(project, args.output)
    except (OSError, ProjectFileError, ProjectError, ScriptParseError) as exc:
        logger.exception("text import failed")
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(args.output)
    logger.info("imported text script %s", args.input)
    return 0


def _set_timeline(args) -> int:
    logger = configure_logging()
    try:
        path = Path(args.project)
        document = load_document(path)
        timeline = load_document(Path(args.timeline))
        if not isinstance(timeline, dict):
            raise ProjectError("timeline must be a mapping with optional music and effects lanes")
        document["timeline"] = timeline
        project = Project.from_dict(document, source_path=str(path))
        save_project(project, path)
    except (OSError, ProjectFileError, ProjectError) as exc:
        logger.exception("set-timeline failed")
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(path)
    return 0


def _set_speaker(args) -> int:
    from dataclasses import replace

    logger = configure_logging()
    try:
        path = Path(args.project)
        project = load_project(path)
        target = args.speaker.strip()
        speaker = next((s for s in project.speakers if s.id == target), None)
        if speaker is None:
            name_matches = [s for s in project.speakers if s.name.casefold() == target.casefold()]
            if len(name_matches) == 1:
                speaker = name_matches[0]
            elif len(name_matches) > 1:
                raise ProjectError(f"speaker name {target!r} is ambiguous; use the speaker id")
            else:
                raise ProjectError(f"speaker {target!r} was not found in {path}")
        if args.voice is None and args.piper_speaker is None:
            raise ProjectError("set-speaker needs --voice and/or --piper-speaker")
        updated = replace(
            speaker,
            voice=args.voice if args.voice is not None else speaker.voice,
            piper_speaker=args.piper_speaker if args.piper_speaker is not None else speaker.piper_speaker,
        )
        project = replace(project, speakers=tuple(updated if s is speaker else s for s in project.speakers))
        save_project(project, path)
    except (OSError, ProjectFileError, ProjectError) as exc:
        logger.exception("set-speaker failed")
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(path)
    return 0


def _list_voices(args) -> int:
    configure_logging()
    directory = Path(args.directory).expanduser()
    voices = discover_voices(directory)
    if not voices:
        print(f"No Piper voices found in {directory}")
        print("Install .onnx and matching .onnx.json files there, or pass --directory.")
        return 1
    print(f"Voice directory: {directory}")
    for voice in voices:
        details = [voice.id]
        if voice.language: details.append(voice.language)
        if voice.sample_rate: details.append(f"{voice.sample_rate}Hz")
        if voice.num_speakers: details.append(f"speakers={voice.num_speakers}")
        print("  " + " | ".join(details))
    return 0


def _list_speakers(args) -> int:
    configure_logging()
    directory = Path(args.directory).expanduser()
    speaker_ids = list_speaker_ids(directory, args.voice)
    if not speaker_ids:
        print(f"No speaker ids found for {args.voice!r} in {directory} (missing model or single-speaker)")
        return 1
    print(f"{args.voice}: {len(speaker_ids)} speakers")
    for speaker_id in speaker_ids:
        print(f"  {speaker_id}")
    return 0


def _catalog(args) -> int:
    configure_logging()
    try:
        catalog = load_manifest(args.source, max_cache_age=0 if getattr(args, "no_cache", False) else 24 * 60 * 60, notice=lambda message: print(f"notice: {message}", file=sys.stderr))
    except CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    entries = sorted(catalog.values(), key=lambda voice: (voice.language, voice.id))
    if args.language:
        entries = [voice for voice in entries if voice.language.lower().startswith(args.language.lower())]
    if args.limit:
        entries = entries[:args.limit]
    print(f"Catalog: {args.source}")
    for voice in entries:
        size = f"{voice.size_bytes / 1_000_000:.1f}MB" if voice.size_bytes else "size unknown"
        print(f"  {voice.id} | {voice.language} | {voice.accent} | {voice.quality} | {size} | speakers={voice.num_speakers}")
    print(f"{len(entries)} voice(s)")
    return 0


def _choose_destination(args) -> Path:
    default = default_voice_directory()
    if args.destination:
        return Path(args.destination).expanduser()
    if not sys.stdin.isatty():
        return default
    answer = input(f"Voice model destination [{default}] (Enter for default): ").strip()
    return Path(answer).expanduser() if answer else default


def _install_voice(args) -> int:
    logger = configure_logging()

    def progress(url: str, downloaded: int, total: int | None) -> None:
        label = url.rsplit("/", 1)[-1]
        message = (f"Downloading {label}: {downloaded}/{total} bytes ({downloaded / total:.0%})" if total else f"Downloading {label}: {downloaded} bytes")
        if sys.stdout.isatty():
            print(message, end="\r", flush=True)
        elif downloaded == total or total is None:
            print(message)
    try:
        catalog = load_manifest(args.source, max_cache_age=0 if getattr(args, "no_cache", False) else 24 * 60 * 60, notice=lambda message: print(f"notice: {message}", file=sys.stderr))
        voice = catalog.get(args.voice)
        if voice is None:
            raise CatalogError(f"voice {args.voice!r} was not found in the catalog")
        plan = InstallPlan.for_voice(voice, _choose_destination(args))
        print(f"Voice: {voice.id} — {voice.display_name}")
        print(f"License: {voice.license_name} ({voice.license_url})")
        print(f"Model card: {voice.model_card_url}")
        if not args.accept_license:
            if not sys.stdin.isatty():
                raise InstallError("license acceptance is required; rerun with --accept-license after reviewing the model card")
            answer = input("I have reviewed the model card/license and accept it for this installation [y/N]: ").strip().lower()
            if answer not in {"y", "yes"}:
                raise InstallError("license was not accepted")
        download_voice(plan, accept_license=True, progress=progress)
        print(f"\nInstalled and verified: {plan.model_path}")
    except (CatalogError, InstallError, OSError) as exc:
        logger.exception("voice installation failed")
        print(f"error: {exc}", file=sys.stderr)
        if 'plan' in locals() and plan.requires_elevation:
            print(plan.sudo_hint(), file=sys.stderr)
        return 2
    return 0


def _verify_voice(args) -> int:
    try:
        catalog = load_manifest(args.source, max_cache_age=0 if getattr(args, "no_cache", False) else 24 * 60 * 60, notice=lambda message: print(f"notice: {message}", file=sys.stderr))
        voice = catalog.get(args.voice)
        if voice is None:
            raise CatalogError(f"voice {args.voice!r} was not found in the catalog")
        plan = InstallPlan.for_voice(voice, args.destination)
        verify_installed_voice(plan)
    except (CatalogError, InstallError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Verified: {plan.model_path}")
    return 0


def _engines(args) -> int:
    configure_logging()
    for capability in engine_capabilities(args.piper_binary, args.voice_dir, args.piper_url):
        status = "ready" if capability.ready else "setup needed"
        heavy = " · heavy" if capability.heavy else ""
        print(f"{capability.id}: {status}{heavy} — {capability.description}")
        if not capability.ready:
            print(f"  setup: {capability.install_hint}")
    return 0


def _doctor(args) -> int:
    logger = configure_logging()
    directory = Path(args.voice_dir).expanduser()
    binary = Path(args.piper_binary).expanduser()
    selected = {"direct": "piper-direct", "http": "piper-http"}.get(args.provider, args.provider)
    ok = True
    print(f"log: {log_path()}")
    print(f"python: {sys.executable}")
    ffmpeg = shutil.which("ffmpeg")
    print(f"ffmpeg: {ffmpeg or 'missing'}")
    ok = ok and bool(ffmpeg)
    print(f"voice directory: {directory} ({len(discover_voices(directory))} Piper models)")
    if selected == "piper-direct":
        found = binary.is_file() and os.access(binary, os.X_OK)
        print(f"piper binary: {binary} ({'found' if found else 'missing'})")
        ok = ok and found
    elif selected == "piper-http":
        request = urllib.request.Request(args.piper_url + "/health")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                print(f"piper http: {response.status} {args.piper_url}")
        except (urllib.error.URLError, OSError) as exc:
            print(f"piper http: unavailable ({exc})")
            ok = False
    elif selected == "espeak-ng":
        import shutil as _shutil
        espeak = args.espeak_binary or _shutil.which("espeak-ng") or _shutil.which("espeak")
        print(f"eSpeak NG: {espeak or 'missing'}")
        ok = ok and bool(espeak)
    elif selected == "xtts":
        capability = next(item for item in engine_capabilities(binary, directory, args.piper_url) if item.id == "xtts")
        print(f"XTTS: {'ready' if capability.ready else 'missing optional dependency'}")
        ok = ok and capability.ready
    elif selected == "kokoro":
        import importlib.util as _iu
        ready = _iu.find_spec("kokoro") is not None
        print(f"Kokoro: {'ready' if ready else 'missing optional dependency (pip install kokoro)'}")
        ok = ok and ready
    else:
        print(f"engine: unsupported {selected}")
        ok = False
    logger.info("doctor completed ok=%s", ok)
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hotpepperpodcast", description="Turn an authored script into local podcast audio.")
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render", help="render a YAML/JSON project")
    render.add_argument("--project", required=True, type=Path)
    render.add_argument("--output-dir", required=True, type=Path)
    render.add_argument("--provider", choices=["direct", "http", "piper-direct", "piper-http", "espeak-ng", "xtts", "kokoro"], default=None)
    render.add_argument("--piper-binary", default=str(DEFAULT_BINARY if DEFAULT_BINARY.exists() else "piper"))
    render.add_argument("--voice-dir", default=str(default_voice_directory()))
    render.add_argument("--piper-url", default="http://127.0.0.1:9021")
    render.add_argument("--espeak-binary", default=None, help="optional eSpeak NG/espeak binary path")
    render.add_argument("--xtts-model", default="tts_models/multilingual/multi-dataset/xtts_v2")
    render.add_argument("--xtts-language", default="en")
    render.add_argument("--xtts-speaker-wav", type=Path)
    render.add_argument("--xtts-gpu", action="store_true", help="allow XTTS to use a CUDA GPU")
    render.add_argument("--kokoro-voice", default="af_heart", help="Kokoro voice id (e.g. af_heart, am_michael, bf_emma)")
    render.add_argument("--keep-segments", action="store_true")
    render.add_argument("--export-stems", action="store_true", help="write aligned speech, music, and effects WAV stems")
    render.add_argument("--check-loudness", action="store_true", help="analyze RMS loudness and sample peak in the rendered WAV")
    render.add_argument("--export-package", action="store_true", help="create a self-contained artwork, feed, audio, and metadata package")
    render.set_defaults(func=_render)
    imp = sub.add_parser("import-text", help="convert labeled plain text into a YAML project")
    imp.add_argument("--input", required=True, type=Path)
    imp.add_argument("--output", required=True, type=Path)
    imp.add_argument("--title")
    imp.add_argument("--author")
    imp.add_argument("--mode", choices=["narrator", "alternate"])
    imp.set_defaults(func=_import_text)
    settl = sub.add_parser("set-timeline", help="add or replace music/effects production cues on a project")
    settl.add_argument("--project", required=True, type=Path)
    settl.add_argument("--timeline", required=True, type=Path, help="YAML/JSON file with optional music and effects lanes")
    settl.set_defaults(func=_set_timeline)
    setspk = sub.add_parser("set-speaker", help="assign a voice and/or Piper speaker id to a project speaker")
    setspk.add_argument("--project", required=True, type=Path)
    setspk.add_argument("--speaker", required=True, help="speaker id or name")
    setspk.add_argument("--voice", default=None, help="voice model id, e.g. en_US-libritts-high")
    setspk.add_argument("--piper-speaker", default=None, dest="piper_speaker", help="Piper speaker id for multi-speaker models")
    setspk.set_defaults(func=_set_speaker)
    voices = sub.add_parser("voices", help="inspect and install Piper voices")
    voices_sub = voices.add_subparsers(dest="voices_command", required=True)
    listing = voices_sub.add_parser("list")
    listing.add_argument("--directory", default=str(default_voice_directory()))
    listing.set_defaults(func=_list_voices)
    catalog = voices_sub.add_parser("catalog", help="list voices in the official Piper catalog")
    catalog.add_argument("--source", default=PIPER_MANIFEST_URL)
    catalog.add_argument("--language")
    catalog.add_argument("--limit", type=int)
    catalog.add_argument("--no-cache", action="store_true", help="refresh the official catalog instead of using a fresh cache")
    catalog.set_defaults(func=_catalog)
    install = voices_sub.add_parser("install", help="download and verify an official Piper voice")
    install.add_argument("voice")
    install.add_argument("--source", default=PIPER_MANIFEST_URL)
    install.add_argument("--destination")
    install.add_argument("--accept-license", action="store_true", help="confirm license review non-interactively")
    install.add_argument("--no-cache", action="store_true", help="refresh the official catalog before installing")
    install.set_defaults(func=_install_voice)
    verify = voices_sub.add_parser("verify", help="verify an installed catalog voice")
    verify.add_argument("voice")
    verify.add_argument("--source", default=PIPER_MANIFEST_URL)
    verify.add_argument("--destination", default=str(default_voice_directory()))
    verify.add_argument("--no-cache", action="store_true", help="refresh the official catalog before verifying")
    verify.set_defaults(func=_verify_voice)
    speakers = voices_sub.add_parser("speakers", help="list speaker ids for a multi-speaker voice")
    speakers.add_argument("--voice", required=True, help="installed voice id, e.g. en_US-libritts-high")
    speakers.add_argument("--directory", default=str(default_voice_directory()))
    speakers.set_defaults(func=_list_speakers)
    engines = sub.add_parser("engines", help="show available and optional TTS engines")
    engines.add_argument("--voice-dir", default=str(default_voice_directory()))
    engines.add_argument("--piper-binary", default=str(DEFAULT_BINARY if DEFAULT_BINARY.exists() else "piper"))
    engines.add_argument("--piper-url", default="http://127.0.0.1:9021")
    engines.set_defaults(func=_engines)
    doctor = sub.add_parser("doctor", help="check local dependencies")
    doctor.add_argument("--voice-dir", default=str(default_voice_directory()))
    doctor.add_argument("--piper-binary", default=str(DEFAULT_BINARY if DEFAULT_BINARY.exists() else "piper"))
    doctor.add_argument("--piper-url", default="http://127.0.0.1:9021")
    doctor.add_argument("--provider", choices=["direct", "http", "piper-direct", "piper-http", "espeak-ng", "xtts", "kokoro"], default="direct")
    doctor.add_argument("--espeak-binary", default=None)
    doctor.set_defaults(func=_doctor)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

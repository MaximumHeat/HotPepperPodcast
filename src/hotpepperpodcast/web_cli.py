"""CLI entry point for the local HotPepperPodcast web UI."""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import sys

from .web import WebConfig, find_available_port, serve
from .voices import default_voice_directory


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def choose_port(host: str, requested: int, interactive: bool = True) -> int:
    if _port_available(host, requested):
        return requested
    suggested = find_available_port(host, requested + 1)
    if not interactive:
        return suggested
    answer = input(f"Port {requested} is in use. Use {suggested} instead? [Y/n/custom port]: ").strip().lower()
    if answer in {"", "y", "yes"}:
        return suggested
    if answer in {"n", "no"}:
        raise RuntimeError("UI port is occupied; choose another port with --port")
    try:
        custom = int(answer)
    except ValueError as exc:
        raise RuntimeError("port choice must be Y, N, or a numeric port") from exc
    if not 1 <= custom <= 65535 or not _port_available(host, custom):
        raise RuntimeError(f"port {custom} is unavailable")
    return custom


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hotpepperpodcast-web", description="Run the local HotPepperPodcast web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="HTTP port; 0 (default) asks the OS for an available ephemeral port")
    parser.add_argument("--project-root", type=Path, default=Path("examples"))
    parser.add_argument("--output-root", type=Path, default=Path("renders/web"))
    parser.add_argument("--voice-dir", type=Path, default=default_voice_directory())
    parser.add_argument("--piper-binary", default="piper")
    parser.add_argument("--piper-url", default="http://127.0.0.1:9021")
    parser.add_argument("--provider", choices=["direct", "http", "piper-direct", "piper-http", "espeak-ng", "xtts"], default="direct")
    parser.add_argument("--espeak-binary", default=None)
    parser.add_argument("--xtts-model", default="tts_models/multilingual/multi-dataset/xtts_v2")
    parser.add_argument("--xtts-language", default="en")
    parser.add_argument("--xtts-speaker-wav", type=Path)
    parser.add_argument("--xtts-gpu", action="store_true")
    parser.add_argument("--no-prompt", action="store_true", help="use the next available port automatically")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        port = choose_port(args.host, args.port, interactive=sys.stdin.isatty() and not args.no_prompt)
        serve(WebConfig(host=args.host, port=port, project_root=args.project_root, output_root=args.output_root, voice_directory=args.voice_dir, piper_binary=args.piper_binary, piper_url=args.piper_url, provider=args.provider, espeak_binary=args.espeak_binary, xtts_model=args.xtts_model, xtts_language=args.xtts_language, xtts_speaker_wav=args.xtts_speaker_wav, xtts_gpu=args.xtts_gpu))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

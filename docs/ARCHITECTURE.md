# Architecture and SDLC

## First principles

The irreducible product is deterministic transformation of authored text into audio. Script generation, content research, and cloud services are not prerequisites. The renderer therefore has four boundaries: project parsing, script validation, TTS providers, and audio assembly.

## v0.1

- YAML is the human-authored format; JSON is supported for tooling.
- Schema versioning starts at 1 so future UI and production features can migrate projects.
- Piper direct process mode is the default offline path; the existing OpenAI-compatible Piper HTTP service is an alternate provider.
- Rendering is deterministic, writes per-run outputs, and records text hashes and settings in `manifest.json`.
- A fake provider makes core tests independent of model files and GPUs.

## Safety and operations

Model installation is a separate capability. The current implementation defines catalog records and an installer boundary that defaults to a user-owned XDG data directory, uses resumable `.part` downloads, verifies official Piper MD5 or local SHA-256 digests, validates companion JSON and MODEL_CARD files, requires explicit model-license acceptance, and never hides a sudo/password prompt. Protected destinations produce a narrow copyable command and a separate verify step. The interactive CLI consumes the official `rhasspy/piper-voices` `voices.json` manifest. Application logs go to `~/Logs/HotPepperPodcast` by default.

## Roadmap

1. Speech vertical slice (this release).
2. Local web UI over the same library and project files (initial localhost server and render-job UI are now present; script editing and richer job controls remain).
3. Declarative production timeline: beds, cues, effects, fades, ducking, stems, loudness checks.
4. Publishing bundle: metadata, local artwork validation, deterministic RSS/feed package export, automatic chapters, credits, licenses, and optional providers. Local artwork, metadata, RSS, deterministic Podcasting 2.0 JSON Chapters, and consolidated local credits/license records are implemented; hosted absolute-URL mapping remains.
5. Optional eSpeak NG and lazy XTTS adapters behind an engine capability registry; heavyweight dependencies never load in the baseline install.
6. Native Ubuntu/Debian `.deb` and portable AppImage build inputs now exist; clean-host install/upgrade/uninstall validation remains a release gate.
7. Guided first-run onboarding exposes real project/engine readiness and explicit per-render engine selection; project creation/import and deeper accessibility polish remain next.

## SDLC checkpoint: automatic chapters

The chapter milestone preserves the original lifecycle controls: requirements remain limited to authored markers and local deterministic output; design uses a dependency-free format and the existing renderer/package boundaries; implementation does not change schema version 1; verification covers actual rendered timing, disabled-line behavior, stable serialization, package inclusion, RSS linkage, stale-output cleanup, rollback compatibility, and legacy renders; operational documentation explicitly distinguishes offline-relative package URLs from hosted public feeds.

# HotPepperPodcast — Initial Plan and Scope

**Project path:** `~/Projects/HotPepperPodcast`
**Initial target:** Ubuntu/Debian Linux
**Repository target:** `MaximumHeat/HotPepperPodcast`
**Source license:** MIT
**Status:** local speech, production timeline, publishing-package, optional-engine, and native-package foundations are implemented; onboarding/UX hardening and hosted publishing remain active work

## 1. Product goal

HotPepperPodcast converts a user-authored script into a usable podcast episode package. The user supplies the words; the application renders them into speech and eventually adds production and publishing tools around that source material.

The core promise is deliberately narrow:

> Given an authored script, render predictable local audio without generating or rewriting the episode.

This project is being built first as a personal proof of concept, then for family use, and finally as a free public GitHub resource demonstrating a complete, understandable project workflow.

## 2. Guiding principles

1. **Authored text is the source of truth.** No automatic script generation in the core product.
2. **Local-first and offline by default.** Piper should work without a network connection once models are installed.
3. **Provider boundaries over lock-in.** The renderer must not depend on one TTS engine forever.
4. **Simple path first.** A reliable speech vertical slice comes before a sophisticated production timeline.
5. **Safe failure is better than silent behavior.** Ambiguous speakers, missing voices, invalid audio, interrupted downloads, and permission problems must be explained clearly.
6. **Human-readable projects.** YAML is the authoring format; JSON is supported for tooling and interchange.
7. **Reproducibility.** Project schema, manifests, checksums, licenses, and render settings should make outputs explainable.
8. **Accessible operations.** A family member should eventually be able to run an example without editing source code.

## 3. Audience and delivery

### Primary users

- Initial builder and tester: MaximumHeat.
- Secondary testers: family members.
- Public users: people evaluating or reusing the project as a free resource.

### Initial delivery

- GitHub source repository first.
- Ubuntu/Debian setup path.
- CLI as the canonical automation interface.
- Simple local web UI after the core renderer is stable.
- Native packages can follow after the filesystem and upgrade behavior are proven.

## 4. Current v0.1 scope

The first release is a speech-only vertical slice:

```text
plain text or YAML project
  → parser and validation
  → named speaker mapping
  → Piper provider
  → per-line WAV synthesis
  → deterministic concatenation and pauses
  → WAV/FFmpeg exports
  → manifest
```

### Included

- Plain-text import with `Name: dialogue` lines.
- Explicit handling for unlabeled scripts:
  - one narrator, or
  - alternating generated speakers.
- Versioned project schema, currently schema version `1`.
- YAML authoring and JSON import/export.
- Up to four speakers as the initial product target.
- Per-speaker voice, backend, speed, pause, and pronunciation substitutions.
- Per-line pause, pronunciation substitutions, chapter marker field, and enable/disable control.
- Piper direct-process provider.
- Existing OpenAI-compatible Piper HTTP provider.
- Local Piper voice discovery from `.onnx` and `.onnx.json` files.
- WAV output plus configurable FFmpeg formats such as MP3.
- Render manifest containing output names, duration, speaker/backend/voice details, and source-text hashes.
- Application logs at `~/Logs/HotPepperPodcast/hotpepperpodcast.log` by default.
- Provider-independent tests plus a real local Piper smoke render.

### v0.1 acceptance criteria

A release candidate is acceptable when:

- A fresh Ubuntu/Debian setup can install the project without source edits.
- `doctor` reports useful dependency and Piper diagnostics.
- `import-text` converts the example script into a valid project.
- Ambiguous plain text cannot silently become an unintended conversation.
- A project using installed Piper voices produces valid WAV output.
- FFmpeg produces at least the documented distribution format when requested.
- Missing models, missing Piper, bad project files, provider failures, and incompatible audio produce readable errors and log details.
- Tests pass without requiring a GPU, network connection, or installed voice models.
- The example workflow is understandable to a non-developer with the README.

## 5. Explicit non-goals for v0.1

The following are planned, but intentionally not part of the first speech release:

- Script generation, content research, or automatic episode rewriting.
- LLM cleanup or cloud model requirements.
- Advanced music beds, sound effects, ducking, multi-track production, and a full timeline editor.
- Artwork generation.
- Hosted/public feed deployment, public URL mapping, and multi-episode feed management.
- Direct in-app model downloads.
- Coqui/XTTS and system TTS adapters as optional, post-v0.1 capabilities.
- Hosted service deployment.
- Native desktop UI.
- A full podcast CMS or publishing platform.

## 6. Staged implementation plan

### Stage 0 — Foundation

- Keep the project isolated at `~/Projects/HotPepperPodcast`.
- Maintain MIT source licensing.
- Keep operational logs outside the repository at `~/Logs/HotPepperPodcast`.
- Version the project schema from its first release.
- Add architecture decisions and test conventions.

**Gate:** repository can be installed, tested, diagnosed, and run without modifying source.

### Stage 1 — Speech v0.1

- Stabilize parser and schema behavior.
- Validate direct Piper and HTTP Piper on clean fixtures.
- Improve error messages and manifest details.
- Publish a GitHub-ready speech release before adding production complexity.

**Gate:** a short authored episode renders reliably from the example workflow.

### Stage 2 — Model catalog and installer

**Completed status:** catalog parsing, official Piper manifest lookup, safe install planning, resumable download primitives, digest validation, explicit license gating, model-card retention, and CLI `catalog`/`install`/`verify` flows are implemented and tested. Further progress UX and permission diagnostics are maintenance work.

Implement the previously agreed model workflow:

- Default user-owned destination:
  `$XDG_DATA_HOME/hotpepperpodcast/voices`, normally `~/.local/share/hotpepperpodcast/voices`.
- Prompt for a custom destination, with Enter accepting the default.
- Built-in/official voice catalog with language, accent, speaker, quality, size, source, and license metadata.
- Resumable downloads using safe `.part` files.
- Retry and clean-restart behavior when resume is unavailable or corrupt.
- Checksum verification and `.onnx`/`.onnx.json` pairing.
- Explicit license acceptance before downloading or activating a model.
- Atomic activation only after validation.
- If a protected destination requires elevation, do not hide a password prompt; show a copyable `sudo ...` command and a separate verification step.
- Record model source, version, checksum, license, and acceptance timestamp.

**Gate:** a user can install and verify a supported voice without manually editing configuration.

### Stage 3 — Family-friendly local web UI

- Keep the CLI and core library authoritative.
- Let users load or create YAML projects.
- Provide script editing, speaker naming, voice selection, model status, and render controls.
- Default UI port to `8080`.
- If occupied, try the configured port, then prompt with a free-port option or custom port.
- Display progress, errors, output path, and audio preview.
- Use the same project files as the CLI.

**Gate:** a family member can load an example, change the script, and render without changing code.

### Stage 4 — Production timeline

Add a declarative, non-DAW production layer:

- Speech tracks.
- Intro/outro.
- Music beds.
- Sound effects and cues.
- Fades and crossfades.
- Speech-aware ducking.
- Stem and master rendering.
- Loudness checks.
- User-provided asset imports.

Bundle a small CC0/public-domain starter library containing podcast essentials: intro, outro, transition sting, subtle bed, and clean cues. The first generated pack now lives in `examples/media/` with a checksum/license manifest and a reproducible generator. Later releases may add cinematic variety, natural ambience, and a larger SFX toolkit.

**Gate:** met for the initial local timeline slice. The example project produces a polished demonstration while remaining editable as YAML; music/effects cues, fades, ducking, stems, and loudness checks are implemented and tested.

### Stage 5 — Publishing package

Produce a ready-to-publish directory containing configurable audio formats plus:

- Metadata.
- Cover art.
- Automatic chapter export from authored script markers, exported as deterministic Podcasting 2.0 JSON Chapters. Heading inference remains a future opt-in parser feature.
- Credits.
- Asset and model license records.
- Machine-readable manifest.
- Human-readable README.

Artwork should use an interface with a deterministic template fallback plus optional local/cloud providers. Cloud artwork must be explicitly opt-in.

**Current gate:** local package export, artwork, metadata, RSS, automatic chapter export, and consolidated credits/license records are implemented. Render outputs and packages include deterministic `license-records.json` plus human-readable `CREDITS.md`; asset records carry SHA-256 hashes and only claim licenses backed by project-local manifests, while Piper model records remain explicitly `review-required` until each installed `MODEL_CARD` is reviewed. The full hand-off gate still requires a package README, hosted absolute URLs, and multi-episode feed support.

### Stage 6 — Optional TTS engines

**Current status:** the provider registry and capability contract are implemented. Piper remains the default and existing `direct`/`http` aliases remain compatible.

- eSpeak NG adapter: lightweight system fallback using `espeak-ng` or `espeak`, with clear GPLv3-or-later notice.
- XTTS adapter: lazy-loaded `.[xtts]` extra, explicit heavy-resource and model-license warnings, optional GPU and speaker-reference configuration.
- Engine readiness metadata for CLI and `/api/engines`: installed state, setup hint, voice hint, heavy-resource flag, cloning capability, and license note.
- Per-render provider selection in CLI/web without changing project schema version 1.

**Gate:** each engine fails clearly when unavailable; default installation remains Piper-compatible without heavyweight dependencies; provider-independent tests cover command construction, lazy dependency errors, capability metadata, and web selection.

### Stage 7 — Native Linux packaging

**Current status:** reproducible builder added for `.deb` and AppImage artifacts using PyInstaller; external voice/model data remains user-owned.

- Freeze CLI and web runtime without requiring Python at execution time.
- Build Debian package with `ffmpeg` dependency and `espeak-ng` recommendation.
- Build portable AppImage with the same binaries and desktop entry.
- Keep voice models under `$XDG_DATA_HOME/hotpepperpodcast/voices`; never embed large or separately licensed models by default.
- Validate install, upgrade, uninstall, `doctor`, `engines`, and first-run behavior on clean Ubuntu/Debian hosts before release.

**Release gate:** both artifacts launch without a source checkout, preserve user data across upgrade/uninstall, and their runtime dependency disclosures are verified on clean hosts. The current builder is an implementation foundation; artifact launch/render and upgrade/uninstall validation remain outstanding until PyInstaller/AppImage tooling and clean Linux VMs are available.

### Stage 8 — Onboarding and UI/UX

Make the first five minutes feel intentional before adding more production complexity:

- Guided first-run readiness panel with project, engine, and first-render steps.
- Explicit engine selection with graceful unavailable/optional/heavy states.
- One obvious next action, contextual setup guidance, and dismissible education rather than modal overload.
- Project creation/import flow, sample-project handoff, voice selection by engine, render progress, preview, and recovery-oriented errors. The local UI now has starter-project and text-import API actions; richer form-based import and per-engine voice pickers remain follow-up UX work.
- Preserve keyboard access, responsive layout, local-only disclosure, and no silent script rewriting.

**Gate:** a new user can start the UI, understand what is ready, choose an available engine, open/import a sample, and render a first episode without editing source or guessing which dependency failed.

## 7. Operational paths

### Code and project data

```text
~/Projects/HotPepperPodcast
```

### Application logs

```text
~/Logs/HotPepperPodcast/hotpepperpodcast.log
```

The `HPP_LOG_DIR` environment variable may override the log directory for testing or packaging.

### Voice models

```text
${XDG_DATA_HOME:-$HOME/.local/share}/hotpepperpodcast/voices
```

The model installer must prefer this user-owned location so ordinary users do not need `sudo`.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Scope expands into a DAW | Keep production as a declarative timeline and stage it after speech v0.1. |
| Piper runtime and model paths differ | Configure runtime libraries from the Piper executable directory and models from a separate voice directory. |
| Model download requires privileges | Prefer user paths; provide visible copyable sudo commands only when necessary. |
| Model licenses are unclear | Curate metadata, require explicit acceptance, and record license evidence. |
| Family workflow is too technical | Add a local web UI over the same tested core and provide examples. |
| GPU-specific implementation | Keep Piper CPU-capable and make heavier engines optional. |
| Audio output is not publishable | Add manifests, loudness checks, metadata, chapters, credits, and licenses in later stages. |
| Regressions during feature growth | Preserve provider-independent tests and vertical-slice gates. |

## 9. Current next build step

Complete onboarding and UI/UX hardening over the tested provider/renderer contracts: add project creation/import, per-engine voice pickers, first-render recovery, and usability/accessibility validation. Then validate `.deb` and AppImage install/upgrade/uninstall behavior on clean Linux hosts before returning to the remaining publishing hand-off layer (package README, hosted absolute URLs, and multi-episode feeds).

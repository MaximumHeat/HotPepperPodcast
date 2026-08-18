# HotPepperPodcast

HotPepperPodcast turns a script you authored into local podcast speech. It is a CLI-first proof of concept for Ubuntu/Debian Linux and is intended to become a free, approachable GitHub resource.

**v0.1 does not generate or rewrite scripts.** You author the script, then render locally with Piper or an explicitly selected optional engine. Music, effects, fades, crossfades, speech-aware ducking, optional aligned mastering stems, loudness screening, publish-ready metadata, local artwork, and opt-in feed/package export are supported.

## Quick start

Python 3.11+ and FFmpeg are recommended. From the repository root:

```bash
./scripts/install-ubuntu.sh
./scripts/run.sh doctor
./scripts/run.sh engines
./scripts/run.sh render \
  --project examples/hello.yaml \
  --output-dir renders/hello \
  --voice-dir "$HOME/.local/share/hotpepperpodcast/voices"
```

The example uses `en_US-lessac-medium` and `en_US-amy-medium`. Install those Piper models into the voice directory first, or point `--voice-dir` at an existing directory such as `~/AI/piper`.

Import labeled plain text into a project:

```bash
./scripts/run.sh import-text --input examples/hello.txt --output /tmp/hello.yaml --title "Hello" --author "You"
```

For unlabeled text, choose explicitly between one narrator and alternating generated speakers:

```bash
./scripts/run.sh import-text --input script.txt --output episode.yaml --mode narrator
```

## Windows and macOS users

HotPepperPodcast is currently developed and tested on Linux. Motivated Windows/macOS users can run the Linux workflow in a VM or compatibility layer:

- **Windows:** [Microsoft WSL](https://learn.microsoft.com/en-us/windows/wsl/install) is the simplest route. In an Administrator PowerShell, run `wsl --install`, complete the Linux distribution setup, then use the Linux commands in this README.
- **macOS:** [UTM](https://mac.getutm.app/) is a straightforward open-source VM/emulator. [VMware Fusion](https://knowledge.broadcom.com/external/article/368667/download-and-license-vmware-desktop-hypervisor.html) is another option. On Apple Silicon, choose an ARM64-compatible Ubuntu image and verify that your selected Piper binary supports that architecture.
- **Linux image:** For UTM or VMware, download Ubuntu from [Canonical](https://ubuntu.com/download/desktop), install it in the VM, then run the **Quick start** instructions above inside that Linux environment. WSL users should follow the WSL distribution setup instead of installing the Desktop ISO.

This is not native Windows/macOS support yet: audio engines, FFmpeg, Piper binaries, and native package builds should be validated inside the Linux environment. With WSL, the local web UI is usually reachable from the Windows browser at the URL printed by `scripts/run.sh web`; with UTM/VMware, open it inside the guest or configure the VM's networking/port forwarding as needed.

## Local web UI

The family-friendly UI is a thin localhost layer over the same project and renderer APIs:

```bash
./scripts/run.sh web --project-root examples --output-root renders/web
```

By default, the operating system assigns an available ephemeral port and the launcher prints the exact address, such as `http://127.0.0.1:50247`. This avoids the commonly occupied 8080. You can request a fixed port explicitly; if it is occupied, the launcher offers the next available port or accepts a custom port. For automation, choose the next available port without prompting:

```bash
./scripts/run.sh web --no-prompt
./scripts/run.sh web --port 8080
```

The UI now opens with a guided first-run readiness panel, lists available project files, lets you select and edit episode metadata and structured script lines, validates changes, saves through the existing project serializer, lists installed Piper voices, shows engine readiness/setup guidance, and starts a background speech render for the selected project. Render polling shows progress steps, loudness results, chapter files, and completed audio can be previewed or downloaded directly from the local UI. The UI also shows cached/offline-aware Piper catalog metadata, model-card links, and installed/incomplete/available status. The editor includes a production timeline with enabled/muted lines, authored chapter markers, pause controls, per-line estimates, optional music/effects cues anchored to lines, an aligned speech/music/effects stem-export toggle, a loudness-check toggle, local artwork selection, package export, and publish-ready fields for subtitle, series, language, episode type/numbering, explicitness, keywords, category, website, and copyright.
 Cues support bounded fade-in/fade-out controls and opt-in speech-aware ducking with attack/release settings; overlapping cues produce crossfades. Ducking uses the actual rendered speech intervals and leaves trailing pauses untouched. Loudness uses a clearly labeled dependency-free sample RMS proxy plus sample peak check; it is not a BS.1770 LUFS meter. Cues and artwork reference only existing files in the selected project's local `media/` directory; no upload or external URL fetching is performed. When enabled, mastering stems are 16-bit WAV files aligned to the master and listed in the manifest, CLI output, and web render downloads; publish metadata is also emitted as `publish-metadata.json`. Authored chapter markers on enabled lines produce deterministic Podcasting 2.0 JSON Chapters in `chapters.json`, timed from the actual rendered speech intervals and included in manifests, CLI output, web downloads, and packages. File-backed renders also emit deterministic `license-records.json` and human-readable `CREDITS.md`: local asset hashes are always recorded, licenses are claimed only from matching project-local manifests, and Piper voice entries remain `review-required` pending the installed `MODEL_CARD`. Package export creates an atomic `package/` directory containing copied audio, square 1400–3000px PNG/JPEG artwork, `feed.xml`, `chapters.json` when present, credits/license records, metadata, manifest, and `package-summary.json`.
 The feed uses deterministic relative offline paths; these are appropriate for local bundles, not direct Apple directory submission until hosted behind an HTTP server with public absolute URLs. The example includes a small generated starter library in `examples/media/` with intro, outro, transition, clean cue, subtle bed, and cover artwork assets. Catalog browsing is read-only; model installation remains the explicit CLI workflow with license review and no hidden sudo.

## Voice engines

Piper direct remains the default lightweight neural path. The optional eSpeak NG adapter uses an installed `espeak-ng` or `espeak` binary and is useful when no neural model is available; it is intentionally more synthetic. Install the system package with your distribution tools (for example `sudo apt install espeak-ng`) and select `--provider espeak-ng`.

XTTS is an advanced opt-in engine exposed through the `xtts` extra:

```bash
.venv/bin/python -m pip install -e '.[xtts]'
./scripts/run.sh render --provider xtts --project episode.yaml --output-dir renders/xtts
```

XTTS may require substantial RAM/GPU resources, downloads model weights on first use, and has separate model-license terms. Review those terms before use or redistribution. It is never imported by the default install and does not replace Piper.

Kokoro-82M is the natural-sounding, Apache-2.0 engine with 54 preset voices
(`af_heart`, `am_michael`, `bf_emma`, …). Install it and select
`--provider kokoro`:

```bash
.venv/bin/python -m pip install kokoro   # model downloads on first use
./scripts/run.sh render --provider kokoro --kokoro-voice af_heart \
  --project episode.yaml --output-dir renders/kokoro
```

Kokoro runs on CPU by default (fast enough for batch narration) or CUDA when a
CUDA build of torch is present. It is lazily imported, so the default install
is unaffected.

Inspect engine readiness without rendering:

```bash
./scripts/run.sh engines
```

## Piper modes

Direct mode invokes a local `piper` binary and is the default. HTTP mode calls the existing OpenAI-compatible service:

```bash
./scripts/run.sh render --provider http --piper-url http://127.0.0.1:9021 \
  --project examples/hello.yaml --output-dir renders/http
```

The direct binary defaults to `~/AI/piper/piper_bin` when it exists, otherwise `piper` from `PATH`. Override it with `--piper-binary`.

## Models and logs

The default model directory is `$XDG_DATA_HOME/hotpepperpodcast/voices`, or `~/.local/share/hotpepperpodcast/voices` when `XDG_DATA_HOME` is unset. The official Piper catalog can be inspected and installed directly:

```bash
./scripts/run.sh voices catalog --language en_US --limit 10
./scripts/run.sh voices install en_US-amy-medium
./scripts/run.sh voices verify en_US-amy-medium
```

Installation shows the model-card/license URL and requires explicit acceptance. Catalog metadata is cached under the user's XDG cache directory for up to 24 hours, with `--no-cache` available for refresh. Downloads use resumable `.part` files, official manifest digests, Piper metadata validation, and model-card retention. Review each model card before installation because voice licenses vary.

Logs are written to `~/Logs/HotPepperPodcast/hotpepperpodcast.log`; set `HPP_LOG_DIR` to override the directory. Private project context is kept separately in `~/Logs/HotPepperPodcast/PROJECT_CONTEXT.md` and is not part of the public repository.

## Development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
```

The project is MIT-licensed. External Piper voice models and future starter audio assets have separate licenses and must be tracked independently.

## Layout

- `src/hotpepperpodcast/` — models, parser, project I/O, TTS providers, renderer, CLI, catalog, installer, and web UI.
- `examples/` — starter project, plain-text input, and the generated `media/` starter library.
- `tests/` — provider-independent and HTTP route tests.
- `docs/ARCHITECTURE.md` — decisions and staged roadmap.
- `docs/VOICE_INSTALLATION.md` — model catalog/install safety.
- `docs/CHAPTERS.md` — deterministic chapter export and hosted-feed boundary.
- `scripts/` — safe Ubuntu setup, launchers, and native Linux artifact builder.

## Native Linux packages

The reproducible native builder targets both Ubuntu/Debian `.deb` and portable AppImage artifacts. The AppImage is launched through its bundled `AppRun`; desktop-menu integration is deferred until clean-host validation. It freezes the CLI/web runtime with PyInstaller; Piper voice models and XTTS model data remain external under the user's XDG data directory.

```bash
.venv/bin/python -m pip install -e '.[packaging]'
./scripts/build-native-linux.sh
```

The builder requires `dpkg-deb`, PyInstaller, and `appimagetool` for both artifacts. Use `--deb-only` when validating the Debian package on a build host without AppImage tooling. The frozen artifacts include the application runtime, but intentionally do not bundle Piper voice models, FFmpeg, eSpeak NG, or XTTS weights: the `.deb` declares FFmpeg and recommends eSpeak NG, while AppImage users must provide those optional/system capabilities. XTTS remains a Python-extra workflow outside the frozen baseline until a plugin mechanism exists. Install, upgrade, uninstall, and first-run checks should be performed on clean supported Linux environments before a public release.

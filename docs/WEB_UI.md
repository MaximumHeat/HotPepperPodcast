# Local Web UI

The web UI is a standard-library-only localhost application. It sits above the same project parser, voice discovery, providers, and renderer used by the CLI.

## Start

```bash
./scripts/run.sh web --project-root examples --output-root renders/web
```

Defaults:

- Host: `127.0.0.1` by default; `localhost` is also accepted.
- Port: OS-assigned ephemeral port (`0`) by default, avoiding commonly occupied ports such as 8080.
- Provider: direct Piper by default; eSpeak NG and XTTS are optional selections when ready.
- Project root: `examples`.
- Output root: `renders/web`.
- Voice directory: `~/.local/share/hotpepperpodcast/voices`.

The launcher prints the exact assigned URL. To request a fixed port, pass `--port 8080` or another number. If an explicit port is occupied, interactive mode offers the next available port or a custom numeric port. `--no-prompt` automatically uses the next available port. The UI never binds to all interfaces by default.

## Routes

- `GET /` — static UI.
- `GET /api/health` — local health status.
- `GET /api/engines` — readiness, capabilities, resource notes, and setup guidance for Piper, eSpeak NG, and optional XTTS.
- `GET /api/onboarding` — first-run readiness state for projects, engines, voices, and the first render.
- `POST /api/project/sample` — create a local starter project without editing source.
- `POST /api/project/import` — import labeled/explicitly disambiguated text into a new local project.
- `GET /api/project?project=filename` — load the selected YAML/JSON project; omitting `project` uses the first project for compatibility.
- `PUT /api/project?project=filename` — validate and save the selected project through the existing serializer.
- `GET /api/projects` — list project filenames available to the picker.
- `GET /api/voices` — installed voice metadata from the configured voice directory without exposing its absolute path.
- `GET /api/media?project=filename` — list supported audio files already present in the selected project's local `media/` directory.
- `GET /api/artwork?project=filename` — list local PNG/JPEG artwork candidates in the selected project's `media/` directory.
- `GET /api/catalog` — cached/offline-aware official catalog metadata merged with verified, incomplete, invalid, and available status. Local absolute paths are not returned.
- `POST /api/render` — queue a render job; JSON body is limited to 1 MiB. The optional `provider` field selects `piper-direct`, `piper-http`, `espeak-ng`, or `xtts`.
- `GET /api/jobs/<id>` — inspect a queued/running/completed/failed render, including progress step and output metadata.
- `GET /api/jobs/<id>/outputs/<filename>` — serve a completed job output for preview/download; audio supports byte ranges. This includes `chapters.json` when the project contains enabled chapter markers.

The guided first-run panel checks project and engine readiness, offers a clear next action, and can be dismissed without changing project files. Engine selection is explicit per render; unavailable engines remain visible with setup guidance rather than failing only after synthesis begins. eSpeak can proceed without Piper model files; Piper and XTTS show their own model/dependency requirements.

The catalog panel is read-only: it shows metadata, cache notices, model-card links, and installation completeness. It never downloads models, accepts a browser-supplied catalog URL, runs `sudo`, or bypasses license acceptance; use the explicit CLI installer for installation.

The render endpoint uses a background thread so the browser remains responsive while Piper and FFmpeg work. The UI requires timeline edits to be saved before rendering, so the worker always renders the same project state the user reviewed. The editor can opt into aligned speech/music/effects WAV stems, loudness screening, publish-ready metadata (including optional category), local artwork, authored chapter markers, and package export; generated stems, `chapters.json` when markers are present, `license-records.json` and `CREDITS.md` for file-backed projects, and `publish-metadata.json` are included in the completed job's output list, while package files are served through validated package-relative URLs. Loudness status is shown in the job/preview. Package export creates an atomic self-contained directory with copied audio, artwork, `feed.xml`, `chapters.json` when present, metadata, manifest, and summary. The chapter file uses the dependency-free Podcasting 2.0 JSON Chapters format and actual rendered line timings. Its feed deliberately uses relative paths for offline/local use and is not a hosted directory submission feed until URLs are replaced with public absolute HTTP(S) locations. The UI polls progress steps and shows a native audio preview when an audio output is ready. Output serving supports browser byte ranges for seeking and is constrained to the completed job's output directory. The picker sends the selected filename for both loading and rendering.

The example project includes a small local starter media library under `examples/media/`; its generated WAV files are listed in `ASSET_MANIFEST.json` with SHA-256 and CC0/public-domain metadata. The structured editor validates the complete project with `Project.from_dict` before saving through the existing serializer. Its production timeline exposes speaker order, enabled/muted state, chapter markers, pause-after milliseconds, per-line duration estimates, an enabled-line total estimate, loudness settings, and publish metadata. Optional music and effects lanes use cues anchored to script-line numbers, with bounded offsets, volumes, loop behavior, and linear fade-in/fade-out durations. Overlapping cues create deterministic crossfades when one cue fades out as another fades in. Audio must already exist in the selected project's `media/` directory; the UI does not upload files or accept paths/URLs. The renderer mixes local WAV assets directly and uses FFmpeg to decode other supported formats, applies bounded additive gain with a normalization pass, and records cue timing in the manifest. Speech-aware ducking is available per cue: opted-in music/effect cues are reduced during exact rendered speech intervals, with bounded reduction, attack, and release controls. Loudness screening reports a sample RMS proxy and sample peak against configurable targets; it is intentionally not a LUFS claim. Fades and overlapping-cue crossfades remain supported. Ducking does not affect trailing pauses, and the manifest records the ducking settings used. Saves use an atomic file replacement, the browser warns before discarding dirty edits, and saving a project while it is rendering returns `409` so the render always sees a stable file.

## Safety boundary

- Loopback binding by default.
- JSON request-size limit.
- Static paths are resolved under the package static directory to prevent traversal.
- Project path requests are constrained to the configured project root.
- Timeline media is constrained to a project-local `media/` directory and filename-only references.
- Output paths are constrained to completed per-job directories.
- Catalog source is configured by the server, not supplied by the browser.
- Browser catalog access is read-only; installation remains explicit CLI work with license review.
- Range requests are bounded to the selected output file.
- Rendering still uses the core provider, model, checksum, and output safeguards.
- No LLM, cloud provider, hidden sudo, or script rewriting is included.

## Next UI increments

1. Add hosted-feed URL mapping and multi-episode package exports after the local package contract is exercised.
2. Add a human-readable package README and hosted publishing hand-off guidance.
3. Add richer onboarding project creation/import and per-engine voice pickers after the first-run contract is exercised.

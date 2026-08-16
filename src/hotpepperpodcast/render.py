"""Deterministic speech rendering for the v0.1 vertical slice."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import subprocess
import tempfile
import wave
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

ProgressCallback = Callable[[int, int, str], None]

from .chapters import CHAPTERS_FILENAME, build_chapters, write_chapters
from .credits import CREDITS_FILENAME, LICENSE_RECORDS_FILENAME, build_license_records, write_credits, write_license_records
from .models import Project, ScriptLine
from .package import PackageError, export_package
from .tts import TTSProvider, TTSProviderError


class RenderError(RuntimeError):
    """Raised when a render cannot be completed safely."""


@dataclass(frozen=True)
class RenderResult:
    output_dir: Path
    files: tuple[Path, ...]
    manifest: Path
    package_dir: Path | None = None


def analyze_wav_loudness(path: str | Path, target_db: float = -16.0, tolerance_db: float = 2.0, max_peak_db: float = -1.0) -> dict[str, object]:
    """Return a deterministic RMS loudness proxy and sample-peak check.

    This is intentionally labeled RMS rather than LUFS: it is a dependency-free
    screening check, not a claim of BS.1770/EBU R128 integrated loudness.
    """
    try:
        with wave.open(str(path), "rb") as audio:
            params = audio.getparams()
            if params.sampwidth != 2 or params.comptype != "NONE":
                raise RenderError("loudness analysis requires an uncompressed 16-bit PCM WAV")
            raw = audio.readframes(audio.getnframes())
    except (OSError, wave.Error) as exc:
        raise RenderError(f"cannot read WAV for loudness analysis: {exc}") from exc
    if not raw:
        raise RenderError("cannot analyze an empty WAV")
    samples = struct.unpack("<" + "h" * (len(raw) // 2), raw)
    peak = max(abs(sample) for sample in samples) / 32768.0
    mean_square = sum((sample / 32768.0) ** 2 for sample in samples) / len(samples)
    rms_db = 20.0 * math.log10(max(mean_square ** 0.5, 1e-12))
    peak_db = 20.0 * math.log10(max(peak, 1e-12))
    loudness_pass = abs(rms_db - target_db) <= tolerance_db
    peak_pass = peak_db <= max_peak_db
    return {
        "method": "sample_rms_proxy",
        "rms_dbfs": round(rms_db, 3),
        "peak_dbfs": round(peak_db, 3),
        "target_dbfs": target_db,
        "tolerance_db": tolerance_db,
        "max_peak_dbfs": max_peak_db,
        "loudness_pass": loudness_pass,
        "peak_pass": peak_pass,
        "status": "pass" if loudness_pass and peak_pass else "check",
    }


def _write_publish_metadata(output_dir: Path, project: Project, duration: float, stems: dict[str, str]) -> Path:
    destination = output_dir / "publish-metadata.json"
    payload = {
        "metadata_version": 1,
        "title": project.title,
        "author": project.author,
        "description": project.description,
        "publish": project.publish_metadata.to_dict(),
        "audio": {
            "duration_seconds": round(duration, 3),
            "formats": list(project.output_formats),
            "stems": bool(stems),
        },
    }
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination


def _spoken_text(line: ScriptLine, speaker_replacements: dict[str, str]) -> str:
    text = line.text
    replacements = dict(speaker_replacements)
    replacements.update(line.pronunciation)
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _append_silence(output: wave.Wave_write, milliseconds: int) -> None:
    if milliseconds <= 0:
        return
    frames = int(output.getframerate() * milliseconds / 1000)
    output.writeframes(b"\0" * frames * output.getsampwidth() * output.getnchannels())


def _concat_wav(paths: list[Path], pauses: list[int], destination: Path) -> float:
    if not paths:
        raise RenderError("no enabled script lines were rendered")
    try:
        readers = [wave.open(str(path), "rb") for path in paths]
    except (OSError, wave.Error) as exc:
        raise RenderError(f"cannot read synthesized WAV: {exc}") from exc
    try:
        reference = readers[0].getparams()
        for index, reader in enumerate(readers[1:], start=2):
            params = reader.getparams()
            if (params.nchannels, params.sampwidth, params.framerate, params.comptype) != (
                reference.nchannels, reference.sampwidth, reference.framerate, reference.comptype
            ):
                raise RenderError(f"WAV segment {index} has incompatible audio parameters")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(reference.nchannels)
            output.setsampwidth(reference.sampwidth)
            output.setframerate(reference.framerate)
            output.setcomptype(reference.comptype, reference.compname)
            for index, reader in enumerate(readers):
                output.writeframes(reader.readframes(reader.getnframes()))
                _append_silence(output, pauses[index])
    finally:
        for reader in readers:
            reader.close()
    with wave.open(str(destination), "rb") as result:
        return result.getnframes() / result.getframerate()


def _media_path(project: Project, filename: str) -> Path:
    if not project.source_path:
        raise RenderError("timeline media requires a project loaded from a file")
    source_file = Path(project.source_path).expanduser().resolve()
    project_dir = source_file.parent
    raw_media_dir = project_dir / "media"
    if raw_media_dir.is_symlink():
        raise RenderError("timeline media directory must be a real directory inside the project")
    if not raw_media_dir.is_dir():
        raise RenderError(f"timeline media file was not found in the project media directory: {filename}")
    media_dir = raw_media_dir.resolve()
    raw_target = media_dir / filename
    if raw_target.is_symlink():
        raise RenderError("timeline media files cannot be symlinks")
    target = raw_target.resolve()
    if media_dir != target.parent or not target.is_file():
        raise RenderError(f"timeline media file was not found in the project media directory: {filename}")
    return target


def _media_frames(path: Path, reference: wave._wave_params) -> list[int]:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as source:
                params = source.getparams()
                if (params.nchannels, params.sampwidth, params.framerate, params.comptype) != (
                    reference.nchannels, reference.sampwidth, reference.framerate, reference.comptype
                ):
                    raise RenderError(f"timeline media {path.name!r} has incompatible WAV parameters")
                if params.sampwidth != 2 or params.comptype != "NONE":
                    raise RenderError(f"timeline media {path.name!r} must be uncompressed 16-bit PCM WAV")
                raw = source.readframes(source.getnframes())
        except (OSError, wave.Error) as exc:
            raise RenderError(f"cannot read timeline media {path.name!r}: {exc}") from exc
    else:
        if shutil.which("ffmpeg") is None:
            raise RenderError(f"FFmpeg is required to decode timeline media {path.name!r}")
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(reference.nchannels), "-ar", str(reference.framerate), "pipe:1"]
        completed = subprocess.run(command, capture_output=True, check=False)
        if completed.returncode != 0 or not completed.stdout:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RenderError(f"cannot decode timeline media {path.name!r}: {detail or 'FFmpeg failed'}")
        raw = completed.stdout
    return list(struct.unpack("<" + "h" * (len(raw) // 2), raw))


def _duck_gain(frame: int, speech_intervals: list[tuple[int, int]], amount: float, attack_ms: int, release_ms: int, sample_rate: int) -> float:
    if not speech_intervals:
        return 1.0
    attack = max(0, int(attack_ms * sample_rate / 1000))
    release = max(0, int(release_ms * sample_rate / 1000))
    gain = 1.0
    for speech_start, speech_end in speech_intervals:
        if frame < speech_start - attack or frame > speech_end + release:
            continue
        if frame < speech_start:
            progress = (frame - (speech_start - attack)) / max(1, attack)
            gain = min(gain, 1.0 - amount * max(0.0, min(1.0, progress)))
        elif frame <= speech_end:
            gain = min(gain, 1.0 - amount)
        else:
            progress = (frame - speech_end) / max(1, release)
            gain = min(gain, (1.0 - amount) + amount * max(0.0, min(1.0, progress)))
    return gain


def _cue_gain(frame: int, media_frames: int, episode_frames: int, start: int, volume: float, fade_in_ms: int, fade_out_ms: int, sample_rate: int, loop: bool, speech_intervals: list[tuple[int, int]] | None = None, duck_speech: bool = False, duck_amount: float = 0.65, duck_attack_ms: int = 80, duck_release_ms: int = 220) -> float:
    gain = volume
    fade_in_frames = int(fade_in_ms * sample_rate / 1000)
    fade_out_frames = int(fade_out_ms * sample_rate / 1000)
    if not loop and fade_in_frames + fade_out_frames > media_frames:
        scale = media_frames / max(1, fade_in_frames + fade_out_frames)
        fade_in_frames = int(fade_in_frames * scale)
        fade_out_frames = media_frames - fade_in_frames
    fade_in_frames = min(media_frames, fade_in_frames)
    if fade_in_frames:
        gain *= min(1.0, max(0.0, (frame - start + 1) / fade_in_frames))
    if fade_out_frames:
        fade_end = episode_frames
        if not loop:
            fade_end = min(episode_frames, start + media_frames)
        fade_start = max(start, fade_end - fade_out_frames)
        if frame >= fade_start:
            gain *= min(1.0, max(0.0, (fade_end - frame) / max(1, fade_end - fade_start)))
    if duck_speech:
        gain *= _duck_gain(frame, speech_intervals or [], duck_amount, duck_attack_ms, duck_release_ms, sample_rate)
    return gain


def _mix_timeline(project: Project, master: Path, segment_paths: list[Path], line_indexes: list[int], pauses: list[int], speech_intervals: list[tuple[int, int]], cues: list[dict], normalize: bool = True) -> float:
    if not project.timeline.music and not project.timeline.effects:
        return 1.0
    try:
        with wave.open(str(master), "rb") as source:
            reference = source.getparams()
            if reference.sampwidth != 2 or reference.comptype != "NONE":
                raise RenderError("timeline mixing requires an uncompressed 16-bit PCM WAV master")
            raw = source.readframes(source.getnframes())
    except (OSError, wave.Error) as exc:
        raise RenderError(f"cannot read WAV master for timeline mix: {exc}") from exc
    samples = list(struct.unpack("<" + "h" * (len(raw) // 2), raw))
    channels = reference.nchannels
    starts: dict[int, int] = {}
    cursor = 0.0
    for index, path in zip(line_indexes, segment_paths):
        starts[index] = int(cursor * reference.framerate)
        with wave.open(str(path), "rb") as segment:
            cursor += segment.getnframes() / segment.getframerate()
        cursor += (pauses[line_indexes.index(index)] if index in line_indexes else 0) / 1000
    total_frames = len(samples) // channels
    for lane_name, lane in (("music", project.timeline.music), ("effects", project.timeline.effects)):
        for cue in lane:
            source_path = _media_path(project, cue.file)
            if cue.start_line - 1 not in starts:
                raise RenderError(f"{lane_name} cue {cue.file!r} anchors to a disabled script line")
            media = _media_frames(source_path, reference)
            if not media:
                raise RenderError(f"timeline media {cue.file!r} contains no audio")
            start = starts[cue.start_line - 1] + int(cue.offset_ms * reference.framerate / 1000)
            if start >= total_frames:
                continue
            limit = total_frames if cue.loop else min(total_frames, start + len(media) // channels)
            for frame in range(max(start, 0), limit):
                source_frame = (frame - start) % (len(media) // channels)
                gain = _cue_gain(frame, len(media) // channels, total_frames, start, cue.volume, cue.fade_in_ms, cue.fade_out_ms, reference.framerate, cue.loop, speech_intervals, cue.duck_speech, cue.duck_amount, cue.duck_attack_ms, cue.duck_release_ms)
                for channel in range(channels):
                    destination = frame * channels + channel
                    samples[destination] += int(media[source_frame * channels + channel] * gain)
            cues.append({"lane": lane_name, "file": cue.file, "start_line": cue.start_line, "start_ms": round(start * 1000 / reference.framerate), "offset_ms": cue.offset_ms, "volume": cue.volume, "loop": cue.loop, "fade_in_ms": cue.fade_in_ms, "fade_out_ms": cue.fade_out_ms, "duck_speech": cue.duck_speech, "duck_amount": cue.duck_amount, "duck_attack_ms": cue.duck_attack_ms, "duck_release_ms": cue.duck_release_ms})
    # Keep the additive mix bounded with conservative headroom. Ducking and
    # compressor-style automation are intentionally separate future features.
    scale = 1.0
    peak = max((abs(sample) for sample in samples), default=0)
    if normalize and peak > 32767:
        scale = 32767 / peak
        samples = [int(sample * scale) for sample in samples]
    mixed_raw = struct.pack("<" + "h" * len(samples), *samples)
    with wave.open(str(master), "wb") as output:
        output.setparams(reference)
        output.writeframes(mixed_raw)
    return scale


def _write_wav_samples(destination: Path, params: wave._wave_params, samples: list[int]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = struct.pack("<" + "h" * len(samples), *[max(-32768, min(32767, value)) for value in samples])
    with wave.open(str(destination), "wb") as output:
        output.setparams(params)
        output.writeframes(raw)


def _write_stems(output_dir: Path, title: str, master_params: wave._wave_params, speech_samples: list[int], music_samples: list[int], effects_samples: list[int]) -> dict[str, str]:
    stems = {"speech": speech_samples, "music": music_samples, "effects": effects_samples}
    result: dict[str, str] = {}
    written: list[Path] = []
    try:
        for lane, samples in stems.items():
            if not any(samples):
                continue
            filename = f"{_safe_name(title)}_stem_{lane}.wav"
            destination = output_dir / filename
            written.append(destination)
            _write_wav_samples(destination, master_params, samples)
            result[lane] = filename
        return result
    except Exception:
        for destination in written:
            destination.unlink(missing_ok=True)
        raise


def _encode_ffmpeg(source: Path, destination: Path, fmt: str) -> None:
    codec_args = {
        "mp3": ["-codec:a", "libmp3lame", "-qscale:a", "2"],
        "opus": ["-codec:a", "libopus", "-b:a", "128k"],
        "ogg": ["-codec:a", "libvorbis", "-q:a", "5"],
        "flac": ["-codec:a", "flac"],
        "m4a": ["-codec:a", "aac", "-b:a", "192k"],
    }
    if fmt not in codec_args:
        raise RenderError(f"format {fmt!r} is not supported by the v0.1 renderer")
    if shutil.which("ffmpeg") is None:
        raise RenderError(f"FFmpeg is required to create {fmt.upper()} output")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), *codec_args[fmt], str(destination)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RenderError(f"FFmpeg failed: {(completed.stderr or completed.stdout).strip()}")


def _generated_files_from_manifest(output_dir: Path) -> set[str]:
    manifest_path = output_dir / "manifest.json"
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(previous, dict):
        return set()
    result: set[str] = set()
    for filename in previous.get("outputs", []) if isinstance(previous.get("outputs", []), list) else []:
        if isinstance(filename, str) and Path(filename).name == filename and filename != "manifest.json":
            result.add(filename)
    for filename in previous.get("stems", {}).values() if isinstance(previous.get("stems", {}), dict) else []:
        if isinstance(filename, str) and Path(filename).name == filename:
            stem_name, separator, lane = filename.rpartition("_stem_")
            if separator and stem_name and lane in {"speech.wav", "music.wav", "effects.wav"}:
                result.add(filename)
    metadata_file = previous.get("publish_metadata_file")
    if isinstance(metadata_file, str) and Path(metadata_file).name == metadata_file and metadata_file == "publish-metadata.json":
        result.add(metadata_file)
    chapters_file = previous.get("chapters_file")
    if isinstance(chapters_file, str) and Path(chapters_file).name == chapters_file and chapters_file == "chapters.json":
        result.add(chapters_file)
    for key, expected in (("license_records_file", LICENSE_RECORDS_FILENAME), ("credits_file", CREDITS_FILENAME)):
        value = previous.get(key)
        if isinstance(value, str) and Path(value).name == value and value == expected:
            result.add(value)
    return result


def _remove_stale_generated_files(output_dir: Path, previous_files: set[str], current_files: set[str]) -> None:
    for filename in previous_files - current_files:
        path = output_dir / filename
        if path.is_file() and not path.is_symlink():
            path.unlink()


def _rollback_outputs(paths: list[Path], output_dir: Path, backup_dir: Path | None) -> None:
    for path in paths:
        if path.is_file() and not path.is_symlink():
            path.unlink()
    if backup_dir is not None:
        for backup in backup_dir.iterdir():
            destination = output_dir / backup.name
            shutil.copy2(backup, destination)


def render_project(
    project: Project,
    provider_for: Callable[[str], TTSProvider],
    output_dir: str | Path,
    keep_segments: bool = False,
    progress: ProgressCallback | None = None,
    export_stems: bool | None = None,
    loudness_check: bool | None = None,
    package_export: bool | None = None,
) -> RenderResult:
    """Render speech-only v0.1 output and write a reproducible manifest."""
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_files = _generated_files_from_manifest(output_dir)
    backup_temp = tempfile.TemporaryDirectory(prefix="hotpepper-render-backup-")
    backup_dir = Path(backup_temp.name)
    for filename in previous_files | {"manifest.json"}:
        source = output_dir / filename
        if source.is_file() and not source.is_symlink():
            shutil.copy2(source, backup_dir / filename)
    work_parent = output_dir / ".segments" if keep_segments else None
    temporary = None
    if work_parent is None:
        temporary = tempfile.TemporaryDirectory(prefix="hotpepper-render-")
        work_parent = Path(temporary.name)
    work_parent.mkdir(parents=True, exist_ok=True)
    segment_paths: list[Path] = []
    line_indexes: list[int] = []
    pauses: list[int] = []
    speech_intervals: list[tuple[int, int]] = []
    segment_manifest: list[dict] = []
    effective_engines: dict[str, str] = {}
    speakers = {speaker.id: speaker for speaker in project.speakers}
    enabled_lines = [line for line in project.script if line.enabled]
    total_steps = len(enabled_lines) + sum(1 for fmt in project.output_formats if fmt != "wav") + 2
    completed_steps = 0

    def report(step: str) -> None:
        if progress is not None:
            progress(completed_steps, total_steps, step)

    generated_paths: list[Path] = []
    package_dir: Path | None = None
    previous_package_backup: Path | None = None
    previous_package_generated = False
    previous_package = output_dir / "package"
    try:
        previous_manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
        previous_package_generated = isinstance(previous_manifest, dict) and isinstance(previous_manifest.get("package"), dict) and previous_manifest["package"].get("directory") == "package"
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    if previous_package.is_dir() and not previous_package.is_symlink():
        previous_package_backup = Path(tempfile.mkdtemp(prefix="hotpepper-package-backup-")) / "package"
        shutil.copytree(previous_package, previous_package_backup)
    try:
        report("Preparing render")
        for index, line in enumerate(project.script):
            if not line.enabled:
                continue
            speaker = speakers[line.speaker]
            segment_path = work_parent / f"line-{index + 1:04d}.wav"
            spoken = _spoken_text(line, speaker.pronunciation)
            try:
                provider = provider_for(speaker.backend)
                provider_engine = getattr(provider, "engine_id", speaker.backend)
                effective_engines[speaker.id] = provider_engine
                provider.synthesize(spoken, speaker.voice, segment_path, speaker.speed, speaker.piper_speaker or None)
            except TTSProviderError:
                raise
            except Exception as exc:
                raise RenderError(f"line {index + 1} ({speaker.name}) failed: {exc}") from exc
            segment_paths.append(segment_path)
            line_indexes.append(index)
            pause = speaker.pause_after_ms if line.pause_after_ms is None else line.pause_after_ms
            pauses.append(pause)
            completed_steps += 1
            report(f"Synthesized line {index + 1}")
            segment_manifest.append({
                "index": index + 1,
                "speaker": speaker.id,
                "voice": speaker.voice,
                **({"piper_speaker": speaker.piper_speaker} if speaker.piper_speaker else {}),
                "backend": provider_engine,
                "text_sha256": hashlib.sha256(line.text.encode("utf-8")).hexdigest(),
                "duration_source": str(segment_path.name),
                "pause_after_ms": pause,
                **({"chapter": line.chapter} if line.chapter else {}),
            })
        master_wav = output_dir / f"{_safe_name(project.title)}.wav"
        generated_paths.append(master_wav)
        duration = _concat_wav(segment_paths, pauses, master_wav)
        lane_manifest: list[dict] = []
        with wave.open(str(master_wav), "rb") as master_audio:
            sample_rate = master_audio.getframerate()
        speech_intervals = []
        speech_cursor = 0.0
        for segment_path, pause in zip(segment_paths, pauses):
            with wave.open(str(segment_path), "rb") as segment_audio:
                segment_duration = segment_audio.getnframes() / segment_audio.getframerate()
            speech_intervals.append((int(speech_cursor * sample_rate), int((speech_cursor + segment_duration) * sample_rate)))
            speech_cursor += segment_duration + pause / 1000
        mix_scale = _mix_timeline(project, master_wav, segment_paths, line_indexes, pauses, speech_intervals, lane_manifest)
        chapters_path: Path | None = None
        if any(line.chapter for line in project.script if line.enabled):
            chapters_path = output_dir / CHAPTERS_FILENAME
            generated_paths.append(chapters_path)
            write_chapters(
                chapters_path,
                build_chapters(project, line_indexes, speech_intervals, sample_rate, duration),
            )
        stems: dict[str, str] = {}
        if export_stems if export_stems is not None else project.export_stems:
            with wave.open(str(master_wav), "rb") as master_audio:
                master_params = master_audio.getparams()
                master_raw = master_audio.readframes(master_audio.getnframes())
            channels = master_params.nchannels
            total_samples = len(master_raw) // 2
            speech_samples = [0] * total_samples
            for segment_path, start_interval in zip(segment_paths, speech_intervals):
                with wave.open(str(segment_path), "rb") as segment_audio:
                    segment_raw = segment_audio.readframes(segment_audio.getnframes())
                start_sample = start_interval[0] * channels
                speech_samples[start_sample:start_sample + len(segment_raw) // 2] = struct.unpack("<" + "h" * (len(segment_raw) // 2), segment_raw)
            music_samples = [0] * total_samples
            effects_samples = [0] * total_samples
            # Re-render each optional lane against silence so stems preserve
            # the same fades, ducking, looping, and alignment as the master.
            for lane_name, target in (("music", music_samples), ("effects", effects_samples)):
                if not getattr(project.timeline, lane_name):
                    continue
                silence = output_dir / ".stem-silence.wav"
                _write_wav_samples(silence, master_params, [0] * total_samples)
                original_music, original_effects = project.timeline.music, project.timeline.effects
                lane_timeline = replace(project.timeline, music=original_music if lane_name == "music" else (), effects=original_effects if lane_name == "effects" else ())
                lane_project = replace(project, timeline=lane_timeline)
                lane_manifest_temp: list[dict] = []
                try:
                    _mix_timeline(lane_project, silence, segment_paths, line_indexes, pauses, speech_intervals, lane_manifest_temp, normalize=False)
                    with wave.open(str(silence), "rb") as lane_audio:
                        lane_raw = lane_audio.readframes(lane_audio.getnframes())
                    target[:] = struct.unpack("<" + "h" * (len(lane_raw) // 2), lane_raw)
                finally:
                    silence.unlink(missing_ok=True)
            if mix_scale != 1.0:
                speech_samples = [int(sample * mix_scale) for sample in speech_samples]
                music_samples = [int(sample * mix_scale) for sample in music_samples]
                effects_samples = [int(sample * mix_scale) for sample in effects_samples]
            # A lane can peak above the combined master when another lane
            # cancels it. Apply one additional scale to every output so no
            # isolated stem clips and all stems remain on the master scale.
            stem_peak = max((abs(sample) for samples in (speech_samples, music_samples, effects_samples) for sample in samples), default=0)
            if stem_peak > 32767:
                stem_scale = 32767 / stem_peak
                speech_samples = [int(sample * stem_scale) for sample in speech_samples]
                music_samples = [int(sample * stem_scale) for sample in music_samples]
                effects_samples = [int(sample * stem_scale) for sample in effects_samples]
                master_samples = list(struct.unpack("<" + "h" * (len(master_raw) // 2), master_raw))
                _write_wav_samples(master_wav, master_params, [int(sample * stem_scale) for sample in master_samples])
            generated_paths.extend(output_dir / f"{_safe_name(project.title)}_stem_{lane}.wav" for lane in ("speech", "music", "effects"))
            stems = _write_stems(output_dir, project.title, master_params, speech_samples, music_samples, effects_samples)
        completed_steps += 1
        report("Assembled WAV master")
        loudness: dict[str, object] | None = None
        if loudness_check if loudness_check is not None else project.loudness_check:
            loudness = analyze_wav_loudness(master_wav, project.loudness_target_db, project.loudness_tolerance_db, project.loudness_max_peak_db)
        license_records_path: Path | None = None
        credits_path: Path | None = None
        source_media = None
        if project.source_path:
            source_media = Path(project.source_path).expanduser().resolve().parent / "media"
        if source_media is not None and source_media.is_dir() and not source_media.is_symlink():
            license_records_path = output_dir / LICENSE_RECORDS_FILENAME
            credits_path = output_dir / CREDITS_FILENAME
            generated_paths.extend((license_records_path, credits_path))
            try:
                artwork_filename = None
                if project.artwork and source_media is not None:
                    artwork_candidate = source_media / project.artwork
                    if artwork_candidate.is_file() and not artwork_candidate.is_symlink():
                        artwork_filename = project.artwork
                records = build_license_records(project, artwork_filename, effective_engines)
                write_license_records(license_records_path, records)
                write_credits(credits_path, records)
            except (OSError, ValueError, TypeError) as exc:
                raise RenderError(f"could not build credits and license records: {exc}") from exc
        files = [master_wav]
        files.extend(output_dir / filename for filename in stems.values())
        if chapters_path is not None:
            files.append(chapters_path)
        if license_records_path is not None and credits_path is not None:
            files.extend((license_records_path, credits_path))
        publish_metadata_path: Path | None = None
        for fmt in project.output_formats:
            if fmt == "wav":
                continue
            destination = output_dir / f"{_safe_name(project.title)}.{fmt}"
            generated_paths.append(destination)
            _encode_ffmpeg(master_wav, destination, fmt)
            files.append(destination)
            completed_steps += 1
            report(f"Encoded {fmt.upper()} output")
        if not project.publish_metadata.is_empty():
            publish_metadata_path = output_dir / "publish-metadata.json"
            generated_paths.append(publish_metadata_path)
            publish_metadata_path = _write_publish_metadata(output_dir, project, duration, stems)
            files.append(publish_metadata_path)
        _remove_stale_generated_files(output_dir, previous_files, {path.name for path in files})
        manifest_path = output_dir / "manifest.json"
        manifest = {
            "manifest_version": 1,
            "project_schema_version": project.schema_version,
            "title": project.title,
            "author": project.author,
            "duration_seconds": round(duration, 3),
            "outputs": [path.name for path in files],
            "segments": segment_manifest,
            "timeline": {"music": len(project.timeline.music), "effects": len(project.timeline.effects), "cues": lane_manifest},
            **({"stems": stems} if stems else {}),
            **({"loudness": loudness} if loudness is not None else {}),
            **({"publish_metadata": project.publish_metadata.to_dict(), "publish_metadata_file": publish_metadata_path.name} if publish_metadata_path else {}),
            **({"chapters_file": chapters_path.name} if chapters_path else {}),
            **({"license_records_file": license_records_path.name, "credits_file": credits_path.name} if license_records_path and credits_path else {}),
            "renderer": "hotpepperpodcast-0.1.0-speech-timeline",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        wants_package = package_export if package_export is not None else project.package_export
        if not wants_package and previous_package_generated and previous_package.is_dir() and not previous_package.is_symlink():
            shutil.rmtree(previous_package)
        if wants_package:
            try:
                package_result = export_package(project, output_dir, tuple(files), manifest_path, chapters_path=chapters_path)
            except PackageError as exc:
                raise RenderError(str(exc)) from exc
            package_dir = package_result.package_dir
            manifest["package"] = {
                "directory": package_dir.name,
                "files": [path.relative_to(package_dir).as_posix() for path in package_result.files],
                "feed": "feed.xml",
                "artwork": f"artwork/{package_result.artwork.filename}",
                "credits": "CREDITS.md",
                "license_records": "license-records.json",
                **({"chapters": CHAPTERS_FILENAME} if chapters_path else {}),
            }
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            if package_dir is not None:
                shutil.copy2(manifest_path, package_dir / "manifest.json")
        completed_steps = total_steps
        report("Render complete")
        if previous_package_backup is not None:
            shutil.rmtree(previous_package_backup.parent, ignore_errors=True)
        return RenderResult(output_dir, tuple(files), manifest_path, package_dir)
    except Exception:
        rollback_paths = generated_paths + [output_dir / filename for filename in previous_files] + [output_dir / "manifest.json"]
        _rollback_outputs(rollback_paths, output_dir, backup_dir)
        if package_dir is not None and package_dir.exists():
            shutil.rmtree(package_dir)
        if previous_package_backup is not None and previous_package_backup.exists():
            if previous_package.exists():
                shutil.rmtree(previous_package)
            previous_package_backup.rename(previous_package)
            shutil.rmtree(previous_package_backup.parent, ignore_errors=True)
        raise
    finally:
        if temporary is not None:
            temporary.cleanup()
        backup_temp.cleanup()
        if previous_package_backup is not None and previous_package_backup.exists():
            shutil.rmtree(previous_package_backup.parent, ignore_errors=True)


def _safe_name(value: str) -> str:
    clean = "".join(character if character.isalnum() or character in "-_" else "_" for character in value.strip())
    return clean.strip("_") or "episode"

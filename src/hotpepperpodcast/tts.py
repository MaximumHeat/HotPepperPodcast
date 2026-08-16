"""Text-to-speech providers and optional local engine capabilities."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Protocol

from .voices import discover_voices, find_voice


class TTSProviderError(RuntimeError):
    """Raised when a provider cannot synthesize a segment."""


class TTSProvider(Protocol):
    def synthesize(self, text: str, voice: str, output_path: Path, speed: float = 1.0, speaker_id: str | None = None) -> None: ...


@dataclass(frozen=True)
class EngineCapability:
    """User-facing metadata for a selectable local TTS engine."""

    id: str
    display_name: str
    description: str
    ready: bool
    install_hint: str
    voice_hint: str
    heavy: bool = False
    supports_voice_cloning: bool = False
    license_note: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class DirectPiperProvider:
    engine_id = "piper-direct"
    """Invoke the Piper binary directly, without requiring a background service."""

    def __init__(self, binary: str | Path, voice_directory: str | Path, timeout: float = 120.0):
        self.binary = str(Path(binary).expanduser())
        self.voice_directory = Path(voice_directory).expanduser()
        self.timeout = timeout

    def synthesize(self, text: str, voice: str, output_path: Path, speed: float = 1.0, speaker_id: str | None = None) -> None:
        model = find_voice(self.voice_directory, voice)
        if model is None:
            raise TTSProviderError(f"voice {voice!r} was not found in {self.voice_directory}")
        if not text.strip():
            raise TTSProviderError("cannot synthesize empty text")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        length_scale = 1.0 / speed
        command = [
            self.binary,
            "--model", str(model.model_path),
            "--output_file", str(output_path),
            "--length_scale", f"{length_scale:.5f}",
        ]
        if speaker_id:
            command += ["--speaker", str(_speaker_index(model, voice, speaker_id))]
        try:
            completed = subprocess.run(
                command,
                input=text + "\n",
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
                env=self._environment(Path(self.binary).parent),
            )
        except FileNotFoundError as exc:
            raise TTSProviderError(f"Piper binary was not found: {self.binary}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TTSProviderError(f"Piper timed out after {self.timeout:.0f}s") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip() or "no diagnostic output"
            raise TTSProviderError(f"Piper failed ({completed.returncode}): {detail}")
        _require_wav(output_path, "Piper")

    @staticmethod
    def _environment(runtime_directory: Path) -> dict[str, str]:
        """Add Piper's executable directory to the native library search path."""
        env = os.environ.copy()
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = str(runtime_directory) + ((":" + existing) if existing else "")
        return env


class HttpPiperProvider:
    engine_id = "piper-http"
    """Call an OpenAI-compatible local Piper speech service."""

    def __init__(self, base_url: str = "http://127.0.0.1:9021", timeout: float = 120.0, opener: Callable | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def synthesize(self, text: str, voice: str, output_path: Path, speed: float = 1.0, speaker_id: str | None = None) -> None:
        if not text.strip():
            raise TTSProviderError("cannot synthesize empty text")
        # Multi-speaker selection is handled by the direct Piper provider; the
        # OpenAI-compatible HTTP endpoint does not expose per-utterance speaker
        # selection here, so ``speaker_id`` is intentionally unused.
        payload = json.dumps({"input": text, "voice": voice, "model": voice, "speed": speed}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/audio/speech",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "audio/wav"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                audio = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise TTSProviderError(f"Piper HTTP error {exc.code}: {detail or exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TTSProviderError(f"cannot reach Piper at {self.base_url}: {exc}") from exc
        if not audio:
            raise TTSProviderError("Piper HTTP service returned empty audio")
        if not audio.startswith(b"RIFF") or b"WAVE" not in audio[:16]:
            raise TTSProviderError("Piper HTTP service returned data that is not a WAV file")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)


class EspeakNgProvider:
    engine_id = "espeak-ng"
    """Invoke the lightweight system ``espeak-ng``/``espeak`` binary."""

    def __init__(self, binary: str | Path | None = None, timeout: float = 60.0):
        self.binary = str(binary) if binary else _find_espeak_binary()
        self.timeout = timeout

    def synthesize(self, text: str, voice: str, output_path: Path, speed: float = 1.0, speaker_id: str | None = None) -> None:
        if not text.strip():
            raise TTSProviderError("cannot synthesize empty text")
        if not self.binary:
            raise TTSProviderError("eSpeak NG is not installed; install the espeak-ng system package")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        words_per_minute = max(80, min(450, round(175 * speed)))
        # Piper voice IDs are often still present when a user selects the
        # lightweight fallback. Convert common IDs to an eSpeak language
        # variant rather than passing an unknown neural model name through.
        selected_voice = voice or "en"
        if "-" in selected_voice and "_" in selected_voice:
            selected_voice = selected_voice.split("-", 1)[0].replace("_", "-")
        command = [self.binary, "-v", selected_voice, "-s", str(words_per_minute), "-w", str(output_path), text]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout, check=False)
        except FileNotFoundError as exc:
            raise TTSProviderError(f"eSpeak NG binary was not found: {self.binary}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TTSProviderError(f"eSpeak NG timed out after {self.timeout:.0f}s") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip() or "no diagnostic output"
            raise TTSProviderError(f"eSpeak NG failed ({completed.returncode}): {detail}")
        _require_wav(output_path, "eSpeak NG")


class XTTSProvider:
    engine_id = "xtts"
    """Lazy Coqui XTTS adapter; the heavy dependency is never imported at startup."""

    def __init__(self, model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2", language: str = "en", speaker_wav: str | Path | None = None, gpu: bool = False):
        self.model_name = model_name
        self.language = language
        self.speaker_wav = Path(speaker_wav).expanduser() if speaker_wav else None
        self.gpu = gpu
        self._tts = None

    def _model(self):
        if self._tts is None:
            try:
                from TTS.api import TTS  # type: ignore[import-not-found]
            except ImportError as exc:
                raise TTSProviderError("XTTS is not installed; install the optional 'xtts' extra and review its model license") from exc
            try:
                self._tts = TTS(model_name=self.model_name, progress_bar=False, gpu=self.gpu)
            except Exception as exc:
                raise TTSProviderError(f"XTTS could not load model {self.model_name!r}: {exc}") from exc
        return self._tts

    def synthesize(self, text: str, voice: str, output_path: Path, speed: float = 1.0, speaker_id: str | None = None) -> None:
        # XTTS selects its speaker via ``speaker_wav``/``voice``; a Piper
        # ``speaker_id`` is not an XTTS speaker name, so it is ignored here.
        if not text.strip():
            raise TTSProviderError("cannot synthesize empty text")
        if self.speaker_wav is not None and not self.speaker_wav.is_file():
            raise TTSProviderError(f"XTTS speaker reference was not found: {self.speaker_wav}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, object] = {"text": text, "file_path": str(output_path), "language": self.language}
        if self.speaker_wav is not None:
            kwargs["speaker_wav"] = str(self.speaker_wav)
        elif voice:
            # A Piper model ID is not an XTTS speaker name. Require an
            # explicit XTTS speaker/reference rather than failing later with
            # an opaque model error.
            if voice.startswith(("en_", "en-", "de_", "fr_", "es_", "it_")) and "-" in voice:
                raise TTSProviderError("XTTS requires --xtts-speaker-wav or an XTTS speaker name; a Piper voice ID was supplied")
            kwargs["speaker"] = voice
        if speed != 1.0:
            # XTTS versions differ in speed support; keep the option explicit
            # rather than silently pretending it was applied.
            kwargs["speed"] = speed
        try:
            self._model().tts_to_file(**kwargs)
        except TypeError as exc:
            if "speed" in kwargs:
                kwargs.pop("speed")
                try:
                    self._model().tts_to_file(**kwargs)
                except Exception as retry_exc:
                    raise TTSProviderError(f"XTTS synthesis failed: {retry_exc}") from retry_exc
            else:
                raise TTSProviderError(f"XTTS synthesis failed: {exc}") from exc
        except Exception as exc:
            raise TTSProviderError(f"XTTS synthesis failed: {exc}") from exc
        _require_wav(output_path, "XTTS")


def _speaker_index(model, voice: str, speaker_id: str) -> int:
    """Resolve a Piper speaker reference to its numeric ``--speaker`` index."""
    if model.num_speakers is not None and model.num_speakers <= 1:
        raise TTSProviderError(f"voice {voice!r} is single-speaker; speaker {speaker_id!r} is not available")
    if model.speaker_id_map is not None:
        if speaker_id in model.speaker_id_map:
            return model.speaker_id_map[speaker_id]
        raise TTSProviderError(f"speaker {speaker_id!r} is not available in voice {voice!r}")
    try:
        return int(speaker_id)
    except ValueError as exc:
        raise TTSProviderError(f"speaker {speaker_id!r} must be a numeric index for voice {voice!r}") from exc


def _find_espeak_binary() -> str | None:
    return shutil.which("espeak-ng") or shutil.which("espeak")


def _require_wav(path: Path, engine: str) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise TTSProviderError(f"{engine} completed without producing audio")
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError as exc:
        raise TTSProviderError(f"{engine} output could not be read: {exc}") from exc
    if not header.startswith(b"RIFF") or b"WAVE" not in header:
        raise TTSProviderError(f"{engine} produced data that is not a WAV file")


def _http_piper_ready(base_url: str) -> bool:
    try:
        request = urllib.request.Request(base_url.rstrip("/") + "/health")
        with urllib.request.urlopen(request, timeout=0.35) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def engine_capabilities(piper_binary: str | Path = "piper", voice_directory: str | Path = "", piper_url: str = "http://127.0.0.1:9021", xtts_speaker_wav: str | Path | None = None) -> list[EngineCapability]:
    """Return deterministic readiness metadata for CLI and web onboarding."""
    piper_path = Path(piper_binary).expanduser()
    voice_root = Path(voice_directory).expanduser() if voice_directory else None
    piper_ready = (piper_path.is_file() and os.access(piper_path, os.X_OK)) or shutil.which(str(piper_binary)) is not None
    piper_ready = piper_ready and voice_root is not None and voice_root.is_dir() and bool(discover_voices(voice_root))
    xtts_ready = importlib.util.find_spec("TTS") is not None and bool(xtts_speaker_wav and Path(xtts_speaker_wav).expanduser().is_file())
    return [
        EngineCapability("piper-direct", "Piper · local", "Fast neural speech with downloaded local voices.", piper_ready, "Install Piper and a verified voice model.", "Voice ID such as en_US-lessac-medium.", license_note="Voice model licenses vary; review each MODEL_CARD."),
        EngineCapability("piper-http", "Piper · service", f"Use an existing local Piper HTTP service at {piper_url}.", _http_piper_ready(piper_url), "Start a compatible local Piper HTTP service.", "Voice/model ID accepted by the service.", license_note="Review the service's voice model license."),
        EngineCapability("espeak-ng", "eSpeak NG", "Tiny, immediate system fallback with broad language coverage and a synthetic voice.", bool(_find_espeak_binary()), "Install the espeak-ng system package (espeak is accepted as a fallback).", "Language/voice name such as en-us.", license_note="Review the detected system package license; eSpeak NG is GPLv3-or-later.",),
        EngineCapability("xtts", "XTTS · advanced", "Optional multilingual neural synthesis and voice cloning.", xtts_ready, "Install the optional xtts extra and configure an explicit speaker reference WAV, then review the XTTS model terms.", "Configured speaker reference WAV; Piper model IDs are not XTTS speakers.", heavy=True, supports_voice_cloning=True, license_note="XTTS model weights have separate Coqui license terms; review before use or redistribution."),
    ]


def provider_for_engine(engine: str, *, piper_binary: str | Path = "piper", voice_directory: str | Path = "", piper_url: str = "http://127.0.0.1:9021", espeak_binary: str | Path | None = None, xtts_model: str = "tts_models/multilingual/multi-dataset/xtts_v2", xtts_language: str = "en", xtts_speaker_wav: str | Path | None = None, xtts_gpu: bool = False) -> TTSProvider:
    """Create one provider lazily selected by project/backend or user config."""
    normalized = engine.lower().strip()
    aliases = {"direct": "piper-direct", "http": "piper-http", "piper": "piper-direct", "espeak": "espeak-ng", "coqui-xtts": "xtts"}
    normalized = aliases.get(normalized, normalized)
    if normalized == "piper-direct":
        return DirectPiperProvider(piper_binary, voice_directory)
    if normalized == "piper-http":
        return HttpPiperProvider(piper_url)
    if normalized == "espeak-ng":
        return EspeakNgProvider(espeak_binary)
    if normalized == "xtts":
        return XTTSProvider(xtts_model, xtts_language, xtts_speaker_wav, xtts_gpu)
    supported = ", ".join(capability.id for capability in engine_capabilities(piper_binary, voice_directory, piper_url))
    raise TTSProviderError(f"unsupported TTS engine {engine!r}; choose one of: {supported}")


def validate_provider(provider: TTSProvider, text: str = "provider test", voice: str = "en_US-lessac-medium") -> None:
    """Smoke-test a provider without leaving an output artifact behind."""
    with tempfile.TemporaryDirectory(prefix="hotpepper-provider-") as temp:
        path = Path(temp) / "test.wav"
        try:
            provider.synthesize(text, voice, path)
        except TTSProviderError:
            raise
        except Exception as exc:
            raise TTSProviderError(str(exc)) from exc

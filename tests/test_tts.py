import io
import json
import urllib.error
import wave
from pathlib import Path
import pytest
from hotpepperpodcast.tts import DirectPiperProvider, EspeakNgProvider, HttpPiperProvider, TTSProviderError, XTTSProvider, engine_capabilities, provider_for_engine

def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(22050)
        output.writeframes(b"\\0\\0" * 10)
    return buffer.getvalue()

RIFF = wav_bytes()
class Response:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return self.data

def test_http_provider_writes_audio(tmp_path):
    seen = {}
    def opener(request, timeout):
        seen["body"] = json.loads(request.data); return Response(RIFF)
    target = tmp_path / "a.wav"
    HttpPiperProvider(opener=opener).synthesize("Hello", "voice", target)
    assert target.read_bytes() == RIFF
    assert seen["body"]["voice"] == "voice"

def test_http_error(tmp_path):
    def opener(request, timeout): raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, io.BytesIO(b'{"error":"no voice"}'))
    with pytest.raises(TTSProviderError, match="no voice"):
        HttpPiperProvider(opener=opener).synthesize("Hello", "voice", tmp_path / "a.wav")

def test_espeak_provider_builds_command_and_writes_wav(tmp_path, monkeypatch):
    seen = {}
    def fake_run(command, **kwargs):
        seen["command"] = command
        Path(command[command.index("-w") + 1]).write_bytes(RIFF)
        return type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()
    monkeypatch.setattr("hotpepperpodcast.tts.subprocess.run", fake_run)
    provider = EspeakNgProvider("/usr/bin/espeak-ng")
    output = tmp_path / "espeak.wav"
    provider.synthesize("Hello", "en-us", output, speed=1.2)
    assert output.read_bytes() == RIFF
    assert seen["command"][:4] == ["/usr/bin/espeak-ng", "-v", "en-us", "-s"]
    assert seen["command"][4] == "210"


def test_espeak_missing_binary_is_explained(tmp_path):
    with pytest.raises(TTSProviderError, match="not found"):
        EspeakNgProvider("/definitely/missing/espeak-ng").synthesize("Hello", "en-us", tmp_path / "a.wav")


def test_xtts_missing_dependency_is_lazy_and_explained(tmp_path, monkeypatch):
    monkeypatch.setattr("hotpepperpodcast.tts.importlib.util.find_spec", lambda name: None)
    with pytest.raises(TTSProviderError, match="XTTS is not installed"):
        XTTSProvider().synthesize("Hello", "", tmp_path / "a.wav")


def test_validate_provider_accepts_engine_specific_voice(tmp_path, monkeypatch):
    seen = {}
    class Provider:
        def synthesize(self, text, voice, output_path, speed=1.0):
            seen["voice"] = voice
            output_path.write_bytes(RIFF)
    from hotpepperpodcast.tts import validate_provider
    validate_provider(Provider(), voice="en-us")
    assert seen["voice"] == "en-us"


def test_engine_registry_exposes_optional_readiness(monkeypatch, tmp_path):
    monkeypatch.setattr("hotpepperpodcast.tts._find_espeak_binary", lambda: "/usr/bin/espeak-ng")
    monkeypatch.setattr("hotpepperpodcast.tts.importlib.util.find_spec", lambda name: object() if name == "TTS" else None)
    engines = {item.id: item for item in engine_capabilities("missing-piper", tmp_path)}
    assert engines["piper-direct"].ready is False
    assert engines["espeak-ng"].ready is True
    assert engines["xtts"].heavy is True
    assert engines["xtts"].supports_voice_cloning is True
    assert provider_for_engine("espeak") .__class__ is EspeakNgProvider


def test_direct_missing_voice(tmp_path):
    provider = DirectPiperProvider("missing-piper", tmp_path)
    with pytest.raises(TTSProviderError, match="not found"):
        provider.synthesize("Hello", "missing", tmp_path / "a.wav")

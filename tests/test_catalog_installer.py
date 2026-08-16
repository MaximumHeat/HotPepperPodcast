import hashlib

import pytest

from hotpepperpodcast.catalog import CatalogError, InstallPlan, VoiceCatalogEntry
from hotpepperpodcast.installer import InstallError, download_voice, verify_installed_voice


def entry(payload=b"model"):
    config = b'{"audio":{"sample_rate":22050}}'
    card = b"License: CC0\n"
    return VoiceCatalogEntry(
        id="demo-voice",
        display_name="Demo Voice",
        language="en",
        accent="test",
        quality="medium",
        model_url="https://example.test/demo.onnx",
        config_url="https://example.test/demo.onnx.json",
        model_card_url="https://example.test/MODEL_CARD",
        digest=hashlib.md5(payload).hexdigest(),
        digest_algorithm="md5",
        license_name="CC0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        config_digest=hashlib.md5(config).hexdigest(),
        model_card_digest=hashlib.md5(card).hexdigest(),
    )


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload
        self.position = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        if size < 0:
            chunk = self.payload[self.position:]
            self.position = len(self.payload)
            return chunk
        chunk = self.payload[self.position:self.position + size]
        self.position += len(chunk)
        return chunk


def test_install_plan_uses_user_destination(tmp_path):
    plan = InstallPlan.for_voice(entry(), tmp_path / "voices")
    assert plan.model_path.name == "demo-voice.onnx"
    assert plan.model_partial_path.name == "demo-voice.onnx.part"
    assert plan.model_card_path.name == "demo-voice.MODEL_CARD"
    plan.validate_destination()


def test_catalog_rejects_bad_url_and_digest():
    bad = entry()
    object.__setattr__(bad, "model_url", "file:///tmp/model")
    with pytest.raises(CatalogError, match="model_url"):
        bad.validate()
    bad = entry()
    object.__setattr__(bad, "digest", "not-a-checksum")
    with pytest.raises(CatalogError, match="MD5"):
        bad.validate()


def test_manifest_record_uses_official_resolve_urls():
    record = {
        "name": "amy",
        "quality": "medium",
        "num_speakers": 1,
        "language": {"code": "en_US", "name_english": "English", "country_english": "United States"},
        "files": {
            "en/en_US/amy/medium/en_US-amy-medium.onnx": {"size_bytes": 3, "md5_digest": "a" * 32},
            "en/en_US/amy/medium/en_US-amy-medium.onnx.json": {"size_bytes": 3, "md5_digest": "b" * 32},
            "en/en_US/amy/medium/MODEL_CARD": {"size_bytes": 3, "md5_digest": "c" * 32},
        },
    }
    voice = VoiceCatalogEntry.from_manifest_record("en_US-amy-medium", record)
    assert "/resolve/main/en/en_US/amy/medium/" in voice.model_url
    assert voice.digest_algorithm == "md5"
    assert voice.model_card_url.endswith("MODEL_CARD")


def test_install_requires_explicit_license(tmp_path):
    plan = InstallPlan.for_voice(entry(), tmp_path / "voices")
    with pytest.raises(InstallError, match="license acceptance"):
        download_voice(plan, accept_license=False)


def test_download_and_verify_install(tmp_path):
    model = b"model"
    config = b'{"audio":{"sample_rate":22050}}'
    card = b"License: CC0\n"
    plan = InstallPlan.for_voice(entry(model), tmp_path / "voices")

    def opener(request, timeout):
        if request.full_url.endswith(".onnx"):
            return Response(model)
        if request.full_url.endswith(".onnx.json"):
            return Response(config)
        return Response(card)

    download_voice(plan, accept_license=True, opener=opener)
    assert plan.model_path.read_bytes() == model
    assert plan.config_path.read_bytes() == config
    assert plan.model_card_path.read_bytes() == card
    verify_installed_voice(plan)
    assert not plan.model_partial_path.exists()


def test_bad_checksum_leaves_no_active_model(tmp_path):
    plan = InstallPlan.for_voice(entry(b"expected"), tmp_path / "voices")

    def opener(request, timeout):
        if request.full_url.endswith(".onnx"):
            return Response(b"wrong")
        if request.full_url.endswith(".onnx.json"):
            return Response(b'{"audio": {}}')
        return Response(b"card")

    with pytest.raises(InstallError, match="checksum mismatch"):
        download_voice(plan, accept_license=True, opener=opener)
    assert not plan.model_path.exists()
    assert not plan.model_partial_path.exists()


def test_invalid_config_is_rejected(tmp_path):
    plan = InstallPlan.for_voice(entry(), tmp_path / "voices")

    def opener(request, timeout):
        if request.full_url.endswith(".onnx"):
            return Response(b"model")
        if request.full_url.endswith(".onnx.json"):
            return Response(b"not-json")
        return Response(b"card")

    with pytest.raises(InstallError, match="JSON/Piper"):
        download_voice(plan, accept_license=True, opener=opener)
    assert not plan.model_path.exists()

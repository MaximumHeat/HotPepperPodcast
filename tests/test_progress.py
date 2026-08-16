import hashlib

from hotpepperpodcast.catalog import InstallPlan, VoiceCatalogEntry
from hotpepperpodcast.installer import download_voice


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload
        self.position = 0
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        if self.position >= len(self.payload):
            return b""
        end = len(self.payload) if size < 0 else min(len(self.payload), self.position + size)
        chunk = self.payload[self.position:end]
        self.position = end
        return chunk


def test_progress_reports_each_asset(tmp_path):
    model = b"model"
    config = b'{"audio": {"sample_rate": 22050}}'
    card = b"CC0"
    voice = VoiceCatalogEntry(
        id="progress-voice", display_name="Progress", language="en", accent="test", quality="low",
        model_url="https://example.test/model.onnx", config_url="https://example.test/model.onnx.json",
        model_card_url="https://example.test/MODEL_CARD", digest=hashlib.md5(model).hexdigest(),
        digest_algorithm="md5", license_name="CC0", license_url="https://example.test/license",
        config_digest=hashlib.md5(config).hexdigest(), model_card_digest=hashlib.md5(card).hexdigest(),
    )
    plan = InstallPlan.for_voice(voice, tmp_path / "voices")
    events = []

    def opener(request, timeout):
        return Response(model if request.full_url.endswith(".onnx") else config if request.full_url.endswith(".json") else card)

    download_voice(plan, True, opener=opener, progress=lambda url, done, total: events.append((url, done, total)))
    assert len(events) >= 3
    assert all(done > 0 and total == len(payload) for (url, done, total), payload in zip(events[-3:], [model, config, card]))

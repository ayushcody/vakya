"""Cloud mode (Supabase): the audio bucket is private, so clients get signed, time-limited URLs, never public ones.
The Supabase client is a fake that records what it was asked."""
import asyncio

import pytest

from core.runtime import runtime as backend


class FakeBucket:
    def __init__(self, store):
        self.store = store

    def upload(self, path, data, opts=None):
        self.store.uploads.append(path)

    def get_public_url(self, path):
        raise AssertionError("the bucket is private: public URLs must not be used")

    def create_signed_url(self, path, expires_in):
        self.store.signed.append((path, expires_in))
        if self.store.fail_signing:
            raise RuntimeError("storage down")
        return {"signedURL": f"https://x.supabase.co/storage/v1/object/sign/audio-assets/{path}?token=t"}


class FakeStorage:
    def __init__(self):
        self.uploads, self.signed, self.fail_signing = [], [], False

    def from_(self, bucket):
        assert bucket == backend.AUDIO_BUCKET
        return FakeBucket(self)


class FakeSb:
    def __init__(self):
        self.storage = FakeStorage()


@pytest.fixture()
def cloud(monkeypatch, tmp_path):
    sb = FakeSb()
    monkeypatch.setattr(backend, "supabase", sb)
    monkeypatch.setattr(backend, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(backend.provenance, "apply_provenance",
                        lambda **kw: (kw["wav_bytes"], {"receipt_id": "rcpt"}))
    monkeypatch.setattr(backend, "LOCAL_AUDIO_DIR", str(tmp_path))
    return sb


def test_signed_url_uses_the_configured_lifetime(cloud, monkeypatch):
    monkeypatch.setattr(backend, "SIGNED_URL_EXPIRES_S", 900)
    url = backend.signed_audio_url(cloud, "u/a.wav")
    assert url.startswith("https://x.supabase.co/storage/v1/object/sign/") and cloud.storage.signed == [("u/a.wav", 900)]


def test_default_lifetime_is_one_hour():
    assert backend.SIGNED_URL_EXPIRES_S == 3600


def test_relative_signed_paths_are_made_absolute(cloud):
    cloud.storage.from_ = lambda b: type("B", (), {"create_signed_url": lambda self, p, e: {"signedURL": "/object/sign/b/p?t=1"}})()
    assert backend.signed_audio_url(cloud, "p") == "https://x.supabase.co/storage/v1/object/sign/b/p?t=1"


def test_signing_failure_gives_none_not_an_error(cloud):
    cloud.storage.fail_signing = True
    assert backend.signed_audio_url(cloud, "u/a.wav") is None


def test_turn_audio_url_is_signed(cloud, monkeypatch):
    class Run:
        local, sb, user_id, conversation_id, user_text, language = False, cloud, "u1", "c1", "q", "en"
        start_time, timings, tool_objs = 0.0, {}, []

        def __init__(self):
            self.sb = cloud

    class Table:
        def insert(self, rows):
            return self

        def execute(self):
            return None

    cloud.table = lambda name: Table()
    url, _ = asyncio.run(backend.turn_store(Run(), "t1", b"RIFF", "hello", engine_key="timbre", llm_provider=None,
                                             llm_model=None, fallback=False))
    assert "/object/sign/audio-assets/u1/" in url and url.endswith("?token=t")
    assert cloud.storage.signed[0][1] == backend.SIGNED_URL_EXPIRES_S

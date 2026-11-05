"""services/gnani_client.py against a fake transport (httpx.MockTransport): request shapes, error mapping, the realtime
framing. Nothing here reaches api.vachana.ai; the field names are best guesses until a live key proves them."""
import asyncio
import base64
import json

import httpx
import pytest

from core.errors import AppError
from services import gnani_client as gc

KEY = "test-key-not-real-123"


@pytest.fixture()
def api(monkeypatch):
    """Route the client through a recording fake. api.reply = callable(request) -> httpx.Response."""
    monkeypatch.setenv("GNANI_API_KEY", KEY)
    state = type("S", (), {})()
    state.requests = []
    state.reply = lambda request: httpx.Response(200, json={"text": "ok"})

    def handler(request):
        state.requests.append(request)
        return state.reply(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gc, "_client", lambda: client)
    return state


def body_of(request) -> str:
    return request.content.decode("latin-1")


def test_transcribe_sends_the_documented_fields(api):
    api.reply = lambda r: httpx.Response(200, json={"text": " नमस्ते ", "detected_language_code": "hi-IN",
                                                    "processing_time": 0.1, "end_to_end_latency": 0.3})
    out = gc.transcribe(b"RIFFdata", language="hi-IN,en-IN", model="prisma-v3.0", fmt="codemix",
                        keyterms=["Sunrise Clinic", "OPD"], previous_text="book an appointment")
    req = api.requests[0]
    assert req.method == "POST" and str(req.url) == "https://api.vachana.ai/stt/v3"
    assert req.headers["x-api-key-id"] == KEY
    body = body_of(req)
    for needle in ('name="audio_file"', 'name="language_code"', "hi-IN,en-IN", 'name="model"', "prisma-v3.0",
                   'name="format"', "codemix", 'name="keyterms"', '["Sunrise Clinic", "OPD"]', 'name="previous_text"',
                   "book an appointment"):
        assert needle in body, needle
    assert out["text"] == "नमस्ते" and out["language"] == "hi-IN" and out["provider"] == "gnani"


def test_transcribe_caps_keyterms_and_omits_empty_hints(api):
    gc.transcribe(b"x", keyterms=[f"term{i}" for i in range(300)])
    body = body_of(api.requests[0])
    assert "term199" in body and "term200" not in body
    assert "previous_text" not in body and 'name="topic"' not in body


def test_a_base_url_override_is_honoured(api, monkeypatch):
    monkeypatch.setenv("GNANI_BASE_URL", "https://stub.example.test/")
    gc.transcribe(b"x")
    assert str(api.requests[0].url) == "https://stub.example.test/stt/v3"


@pytest.mark.parametrize("status,code,http", [(402, "PROVIDER_CREDITS", 402), (429, "RATE_LIMITED", 429),
                                              (401, "PROVIDER_AUTH", 502), (403, "PROVIDER_AUTH", 502),
                                              (500, "PROVIDER_ERROR", 502)])
def test_http_errors_map_to_app_error_codes(api, status, code, http):
    api.reply = lambda r: httpx.Response(status, text="denied")
    with pytest.raises(AppError) as exc:
        gc.transcribe(b"x")
    assert exc.value.code == code and exc.value.status == http
    assert KEY not in json.dumps(exc.value.details) and KEY not in exc.value.message


def test_synthesis_and_embedding_map_the_same_errors(api):
    api.reply = lambda r: httpx.Response(402, text="no credits")
    with pytest.raises(AppError) as exc:
        gc.synthesize("hello")
    assert exc.value.code == "PROVIDER_CREDITS"
    gc.gateway.GATEWAY.reset()      # a 402 opens the breaker for every kind (tests/test_gnani_gateway.py)
    api.reply = lambda r: httpx.Response(429, text="slow down")
    with pytest.raises(AppError) as exc:
        gc.clone_embedding(b"x")
    assert exc.value.code == "RATE_LIMITED"


def test_timeouts_and_connection_errors_are_provider_unavailable(api):
    def boom(request):
        raise httpx.ReadTimeout("slow", request=request)
    api.reply = boom
    with pytest.raises(AppError) as exc:
        gc.transcribe(b"x")
    assert exc.value.code == "PROVIDER_UNAVAILABLE" and exc.value.status == 504

    def down(request):
        raise httpx.ConnectError("refused", request=request)
    api.reply = down
    with pytest.raises(AppError) as exc:
        gc.transcribe(b"x")
    assert exc.value.code == "PROVIDER_UNAVAILABLE" and exc.value.status == 503


def test_no_key_is_a_clear_error_and_nothing_is_sent(api, monkeypatch):
    monkeypatch.delenv("GNANI_API_KEY")
    with pytest.raises(AppError) as exc:
        gc.transcribe(b"x")
    assert exc.value.code == "PROVIDER_NOT_CONFIGURED" and not api.requests


def test_the_key_is_never_logged(api, caplog):
    import logging
    api.reply = lambda r: httpx.Response(500, text="oops")
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AppError):
            gc.transcribe(b"x")
    assert KEY not in caplog.text


def test_synthesize_body(api):
    api.reply = lambda r: httpx.Response(200, content=b"RIFFwav", headers={"content-type": "audio/wav"})
    audio = gc.synthesize("नमस्ते", voice="Nalini", language="hi-IN", speed=2.0, sample_rate=24000, fmt="wav")
    req = api.requests[0]
    assert str(req.url) == "https://api.vachana.ai/api/v1/tts/inference" and req.headers["x-api-key-id"] == KEY
    body = json.loads(req.content)
    assert body == {"text": "नमस्ते", "model": "timbre-v2.5", "language": "hi-IN", "speed": 1.15, "voice": "Nalini",
                    "audio_config": {"sample_rate": 24000, "num_channels": 1, "sample_width": 2, "container": "wav", "encoding": "linear_pcm"}}
    assert audio == b"RIFFwav"


def test_a_speaker_embedding_selects_the_clone_model_and_drops_the_voice(api):
    api.reply = lambda r: httpx.Response(200, content=b"RIFFwav", headers={"content-type": "audio/wav"})
    gc.synthesize("hi", voice="Nalini", speaker_embedding=[0.1, 0.2])
    body = json.loads(api.requests[0].content)
    assert body["model"] == "vachana-vc-v1" and body["speaker_embedding"] == [0.1, 0.2] and "voice" not in body


def test_a_json_reply_with_base64_audio_is_decoded(api):
    api.reply = lambda r: httpx.Response(200, json={"audio": base64.b64encode(b"PCM").decode()})
    assert gc.synthesize("hi", fmt="raw", sample_rate=8000) == b"PCM"


def test_bad_synthesis_options_are_refused_before_any_request(api):
    with pytest.raises(AppError):
        gc.synthesize("hi", sample_rate=12345)
    with pytest.raises(AppError):
        gc.synthesize("hi", fmt="flac")
    assert not api.requests


def test_clone_embedding(api):
    api.reply = lambda r: httpx.Response(200, json={"speaker_embedding": [0.5] * 768})
    emb = gc.clone_embedding(b"RIFF")
    req = api.requests[0]
    assert str(req.url) == "https://api.vachana.ai/api/v1/tts/voice-clone/embeddings" and 'name="audio_file"' in body_of(req)
    assert len(emb) == 768 and emb[0] == 0.5
    api.reply = lambda r: httpx.Response(200, json={})
    with pytest.raises(AppError):
        gc.clone_embedding(b"RIFF-another-clip")      # not the cached clip


# ------------------------------------------------------------------------------------------------- realtime

def test_frames_are_exactly_1024_bytes_with_a_padded_tail():
    frames = gc.frame_pcm(b"\x01" * 2500)
    assert [len(f) for f in frames] == [1024, 1024, 1024] and frames[-1].endswith(b"\x00" * 548)
    assert gc.frame_pcm(b"") == []


def test_stream_headers_and_url(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", KEY)
    h = gc.stream_headers(language="hi-IN", sample_rate=8000, vad_threshold=0.7, min_silence_ms=500)
    assert h["x-api-key-id"] == KEY and h["x-sample-rate"] == "8000" and h["format"] == "codemix"
    assert h["x-vad-threshold"] == "0.7" and h["x-min-silence-ms"] == "500" and "x-min-speech-ms" not in h
    assert gc.stream_url() == "wss://api.vachana.ai/stt/v3/stream"
    with pytest.raises(AppError):
        gc.stream_headers(sample_rate=11025)


class FakeSocket:
    def __init__(self, messages):
        self.sent, self.closed, self._messages = [], False, messages

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        async def gen():
            for m in self._messages:
                yield m
        return gen()


def test_streaming_recognizer_frames_commits_and_reads_events(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", KEY)
    socket = FakeSocket([json.dumps({"type": "connected"}), b"binary-ignored",
                         json.dumps({"type": "transcript", "text": "नमस्ते", "segment_id": 1,
                                     "detected_language_code": "hi-IN"})])
    seen = {}

    async def connect(url, additional_headers=None):
        seen.update(url=url, headers=additional_headers)
        return socket

    async def run():
        out = []
        async with gc.StreamingRecognizer(language="hi-IN", sample_rate=8000, pace=False, connect=connect) as rec:
            await rec.send_pcm(b"\x00" * 3000)
            await rec.commit()
            async for event in rec.events():
                out.append(event)
        return out

    events = asyncio.run(run())
    assert seen["url"].startswith("wss://") and seen["headers"]["language_code"] == "hi-IN"
    assert [len(f) for f in socket.sent[:3]] == [1024, 1024, 1024] and json.loads(socket.sent[3]) == {"type": "commit"}
    assert [e["type"] for e in events] == ["connected", "transcript"] and events[1]["text"] == "नमस्ते"
    assert socket.closed


def test_the_words_come_from_transcript_as_the_live_api_sends_them(api):
    # shape of a real Prisma v3 reply, 5 Nov 2026
    api.reply = lambda r: httpx.Response(200, json={"success": True, "request_id": "r1", "transcript": " नमस्ते, वाक्या ",
                                                    "model": "prisma-v3.0", "processing_time": 0.2,
                                                    "end_to_end_latency": 0.3, "detected_language_code": "hi-IN"})
    out = gc.transcribe(b"RIFFdata", language="hi-IN,en-IN")
    assert out["text"] == "नमस्ते, वाक्या" and out["language"] == "hi-IN" and out["request_id"] == "r1"


def test_a_cloned_voice_sends_only_the_documented_fields(api):
    api.reply = lambda r: httpx.Response(200, content=b"RIFFwav", headers={"content-type": "audio/wav"})
    gc.synthesize("hi", voice="Nalini", language="hi-IN", speed=0.9, speaker_embedding=[0.1, 0.2])
    body = json.loads(api.requests[0].content)
    assert set(body) == {"text", "model", "speaker_embedding", "audio_config"}
    assert body["audio_config"]["container"] == "wav"

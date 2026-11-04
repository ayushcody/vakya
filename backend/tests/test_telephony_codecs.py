"""The Vobiz WebSocket dialect (services/telephony/vobiz.py): the messages Vobiz sends are read into events, and what we send
back is built in Vobiz's shape. The shapes follow https://www.vobiz.ai/docs/xml/stream/stream-events.md (start, media,
playedStream, clearedAudio in; playAudio, checkpoint, clearAudio out; no inbound `stop`, no from/to in `start`). Audio is
mu-law on the wire and PCM16 inside."""
import base64
import json

import numpy as np

from services import phone_audio as pa
from services.telephony.vobiz import VobizCodec

START = {"sequenceNumber": 0, "event": "start",
         "start": {"callId": "call-uuid-1", "streamId": "stream-1", "accountId": "500025", "tracks": ["inbound"],
                   "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000}}, "extra_headers": "{}"}


def tone(ms=100, amp=0.3, freq=440.0, rate=8000):
    t = np.arange(int(rate * ms / 1000)) / rate
    return (np.sin(2 * np.pi * freq * t) * amp * 32767).astype("<i2").tobytes()


def b64(raw):
    return base64.b64encode(raw).decode()


def close_to(a: bytes, b: bytes, tol=0.08):
    """mu-law is lossy: the signals agree to within a few percent of full scale."""
    x = np.frombuffer(a, "<i2").astype(float) / 32768
    y = np.frombuffer(b, "<i2").astype(float) / 32768
    return len(x) == len(y) and float(np.max(np.abs(x - y))) < tol


def test_vobiz_start_media_dtmf_checkpoint_and_the_events_it_ignores():
    c = VobizCodec()
    ev = c.parse(json.dumps(START))
    assert (ev.kind, ev.stream_id, ev.call_id, ev.rate, ev.caller) == ("start", "stream-1", "call-uuid-1", 8000, "")   # no from/to
    pcm = tone()
    m = c.parse(json.dumps({"event": "media", "sequenceNumber": 2, "streamId": "stream-1",
                            "media": {"track": "inbound", "timestamp": "1", "chunk": 2, "payload": b64(pa.ulaw_encode(pcm))}}))
    assert m.kind == "media" and close_to(m.pcm, pcm)
    # dtmf is not documented by Vobiz: the Plivo-style shape is assumed
    assert c.parse(json.dumps({"event": "dtmf", "streamId": "stream-1", "dtmf": {"track": "inbound", "digit": "5"}})).digit == "5"
    played = c.parse(json.dumps({"event": "playedStream", "name": "turn-1-end"}))
    assert (played.kind, played.name) == ("mark", "turn-1-end")
    assert c.parse(json.dumps({"event": "clearedAudio", "streamId": "stream-1"})) is None
    assert c.parse(json.dumps({"event": "stop"})).kind == "stop"           # never sent by Vobiz; harmless when it is


def test_the_start_event_may_carry_a_from_number_if_vobiz_ever_adds_one():
    ev = VobizCodec().parse(json.dumps({"event": "start", "start": {**START["start"], "from": "+919876543210"}}))
    assert ev.caller == "+919876543210"


def test_vobiz_sends_play_audio_with_the_stream_id_clear_audio_and_checkpoint():
    c = VobizCodec()
    c.parse(json.dumps(START))
    pcm = tone()
    out = c.media(pcm, "stream-1")
    assert out["event"] == "playAudio" and out["streamId"] == "stream-1"
    assert out["media"]["contentType"] == "audio/x-mulaw" and out["media"]["sampleRate"] == 8000
    assert close_to(pa.ulaw_decode(base64.b64decode(out["media"]["payload"])), pcm)
    assert c.clear("stream-1") == {"event": "clearAudio", "streamId": "stream-1"}
    assert c.mark("turn-1-end", "stream-1") == {"event": "checkpoint", "streamId": "stream-1", "name": "turn-1-end"}


def test_vobiz_follows_the_format_named_in_start_linear_16_bit():
    c = VobizCodec()
    c.parse(json.dumps({"event": "start", "start": {"callId": "c", "streamId": "s",
                                                    "mediaFormat": {"encoding": "audio/x-l16", "sampleRate": 16000}}}))
    pcm = tone(rate=16000)
    assert c.parse(json.dumps({"event": "media", "media": {"payload": b64(pcm)}})).pcm == pcm
    out = c.media(pcm, "s")
    assert out["media"]["contentType"] == "audio/x-l16" and out["media"]["sampleRate"] == 16000
    assert base64.b64decode(out["media"]["payload"]) == pcm


def test_the_codec_shrugs_off_garbage():
    c = VobizCodec()
    for junk in ("", "nope", "[]", "42", json.dumps({"no": "event"}), json.dumps({"event": "media", "media": {"payload": "!!"}})):
        c.parse(junk)                      # never raises
    assert c.parse("nope") is None

"""Realtime speech recognition for a phone call: one Prisma stream (wss /stt/v3/stream) per call, fed with the caller's
speech while they talk, so the transcript is ready within about 100 ms of the end of the sentence instead of 0.6 to 1.3 s
after it (a batch POST /stt/v3 of the finished recording, which is still the fallback).

How it works (measured on the real server, 9 Nov 2026):
  * the session is opened when the call starts, while the greeting plays (a connect takes 0.1 to 2.3 s), and stays open;
  * the caller's audio is sent as it arrives, continuously, in 1,024-byte frames: the server processes a stream at real
    time, so audio sent in a burst (the pre-roll of an utterance, audio queued while connecting) makes the transcript late
    by the length of the burst (a 0.5 s pre-roll burst made it 0.5 s late); nothing is queued while the stream is down;
  * `commit()` at the pause (PHONE_EARLY_SILENCE_MS) or at the end of the utterance closes the segment; the final
    transcript follows ~100 ms later (without a commit the server ends it itself after ~0.55 s of silence);
  * several commits in one utterance (a pause, then the caller goes on) give one transcript each; the utterance's text is
    their join;
  * a stream and a Timbre request can run together (checked 9 Nov 2026), so the stream does not hold the gateway's slot.

Anything that goes wrong (no connection, the socket drops, no transcript within the timeout) makes the future of that
commit fail; the call then transcribes the recording as before. Keyterms and the desk's learnt corrections cannot travel on
the stream, so a transcript made here has had none of Prisma's hints; PHONE_STREAM_ASR=off turns the whole thing off.

    rec = CallRecognizer(rate=8000); rec.start()
    rec.feed(pcm)  # every chunk of the call, as it arrives
    rec.begin(); ...; fut = rec.commit(); rec.end()          # fut: the text so far, as (text, detected_language_code)
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import time
from typing import Any, Callable, Deque, List, Optional, Tuple

try:
    from services import gnani_client as client
except ImportError:  # pragma: no cover
    from backend.services import gnani_client as client

log = logging.getLogger("vakya")

FRAME_BYTES = client.FRAME_BYTES
RECONNECT_AFTER_S = 3.0
STALE_S = 3.0


def enabled() -> bool:
    """PHONE_STREAM_ASR (default on): stream the caller's speech to Prisma while they talk. Needs a Gnani key."""
    if os.getenv("PHONE_STREAM_ASR", "on").strip().lower() in ("off", "0", "false", "no"):
        return False
    return client.configured()


def result_timeout_s() -> float:
    """PHONE_STREAM_ASR_TIMEOUT_S (default 1.8): how long after a commit the transcript may take before the call gives up
    on the stream for that sentence and recognises the recording instead."""
    try:
        return max(0.3, float(os.getenv("PHONE_STREAM_ASR_TIMEOUT_S", "1.8")))
    except ValueError:
        return 1.8


class _Utterance:
    """One utterance of the caller: the commits made on it and the transcripts that came back."""

    def __init__(self) -> None:
        self.texts: List[str] = []
        self.language: Optional[str] = None
        self.commits = 0
        self.extra = 0                      # transcripts the server made on its own (before a commit)
        self.waiting: List[Tuple[int, "asyncio.Future", float]] = []   # (finals needed, future, committed at)
        self.active = True                  # the caller is still in this utterance (end() clears it)

    def text(self) -> str:
        return " ".join(t for t in self.texts if t).strip()


class CallRecognizer:
    """The call's recognition stream. All methods run on the call's event loop."""

    def __init__(self, rate: int, *, language: str = "indic-auto", connect: Any = None,
                 recognizer_factory: Optional[Callable[[], Any]] = None) -> None:
        self.rate = rate
        self.language = language
        self._connect_fn = connect                       # tests: the fake socket opener
        self._factory = recognizer_factory
        self._rec: Any = None
        self._connecting: Optional["asyncio.Task"] = None
        self._reader: Optional["asyncio.Task"] = None
        self._sender: Optional["asyncio.Task"] = None
        self._queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()
        self._buf = bytearray()
        self._utts: Deque[_Utterance] = collections.deque()
        self._cur: Optional[_Utterance] = None
        self._failed_at = 0.0
        self._stale: List[float] = []        # when a sentence's transcript timed out (a late one is dropped)
        self.closed = False
        self.connect_ms: Optional[int] = None
        self.results = 0
        self.failures = 0

    # ------------------------------------------------------------------------------------------------ connection
    def start(self) -> None:
        """Open the stream in the background (idempotent)."""
        if self.closed or (self._connecting is not None and not self._connecting.done()):
            return
        if self._rec is not None:
            return
        self._connecting = asyncio.ensure_future(self._open())


    async def _open(self) -> None:
        t0 = time.monotonic()
        try:
            rec = self._factory() if self._factory else client.StreamingRecognizer(
                sample_rate=self.rate, language=self.language, pace=False, connect=self._connect_fn, hold_slot=False,
                lane="live")
            await rec.__aenter__()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - no stream: the call recognises each recording instead
            self._failed_at = time.monotonic()
            log.warning("realtime speech recognition could not be opened (%s): recordings are recognised instead",
                        type(e).__name__)
            return
        if self.closed:
            await rec.close()
            return
        self._rec = rec
        self.connect_ms = int((time.monotonic() - t0) * 1000)
        self._reader = asyncio.ensure_future(self._read(rec))
        self._sender = asyncio.ensure_future(self._send_loop(rec))
        log.info("realtime speech recognition open in %d ms", self.connect_ms)

    async def _read(self, rec: Any) -> None:
        try:
            async for ev in rec.events():
                kind = ev.get("type")
                if kind == "transcript":
                    log.info("realtime recognition: transcript final=%s server latency %s ms, audio %s ms, prefetched=%s", ev.get("is_final"),
                             ev.get("latency"), ev.get("audio_duration_ms"), ev.get("prefetched"))
                if kind == "transcript" and ev.get("is_final", True):
                    self._final(str(ev.get("text") or "").strip(), ev.get("detected_language_code"))
                elif kind == "error":
                    log.warning("realtime speech recognition error: %s", str(ev.get("message") or ev)[:160])
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the socket dropped
            log.info("realtime speech recognition closed (%s)", type(e).__name__)
        finally:
            if self._rec is rec:
                self._rec = None
                self._failed_at = time.monotonic()
            self._fail_waiting(ConnectionError("the recognition stream closed"))

    async def _send_loop(self, rec: Any) -> None:
        try:
            while True:
                frame = await self._queue.get()
                if frame is None:
                    return
                if frame == b"":
                    await rec.commit()

                else:
                    await rec.send_frame(frame)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.info("realtime speech recognition: send failed (%s)", type(e).__name__)
            if self._rec is rec:
                self._rec = None
                self._failed_at = time.monotonic()
            self._fail_waiting(ConnectionError("the recognition stream closed"))

    @property
    def ready(self) -> bool:
        return self._rec is not None and not self.closed

    # ------------------------------------------------------------------------------------------------ utterances
    def begin(self) -> None:
        """A new utterance starts (the audio has been flowing all along). Reconnects first when the stream died (not more
        often than every few seconds)."""
        if self._rec is None and not self.closed and (time.monotonic() - self._failed_at) >= RECONNECT_AFTER_S:
            self.start()
        if self._cur is not None:
            self._cur.active = False
        utt = _Utterance()
        self._cur = utt
        self._utts.append(utt)
        while len(self._utts) > 8:
            self._utts.popleft()

    def end(self) -> None:
        """The utterance is over: transcripts the server makes while nobody speaks are not its."""
        if self._cur is not None:
            self._cur.active = False

    def feed(self, pcm: bytes) -> None:
        """Caller audio at the call's rate, as it arrives. Sent as 1,024-byte frames. Nothing is kept while the stream is not
        open (it would be sent in a burst later, and the server would be late by the length of the burst)."""
        if self._rec is None or self.closed or not pcm:
            self._buf.clear()
            return
        self._buf += pcm
        while len(self._buf) >= FRAME_BYTES:
            if self._queue.qsize() < 120:      # (a stalled socket must not pile up seconds of audio)
                self._queue.put_nowait(bytes(self._buf[:FRAME_BYTES]))
            del self._buf[:FRAME_BYTES]

    def has_audio(self) -> bool:
        return self._cur is not None and self._cur.active

    def commit(self, quiet: bool = False) -> "asyncio.Future":
        """End the segment (`quiet`: a noise the caller ignores; a missing transcript is no failure): the future gives (the utterance's text so far, Gnani's language code), or raises when the stream
        failed or no transcript came in time (the caller recognises the recording instead)."""
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future" = loop.create_future()
        utt = self._cur
        if utt is None or self._rec is None or self.closed:      # not open (yet): the call recognises the recording instead
            fut.set_exception(ConnectionError("no recognition stream"))
            return fut
        if self._buf:      # a short tail: padded with silence to a full frame
            self._queue.put_nowait(bytes(self._buf) + b"\x00" * (FRAME_BYTES - len(self._buf)))
            self._buf.clear()
        utt.commits += 1
        need = utt.commits + utt.extra
        utt.waiting.append((need, fut, time.monotonic()))
        self._queue.put_nowait(b"")    # a commit marker: sent in order, after the frames
        loop.call_later(result_timeout_s(), self._timeout, utt, fut, quiet)
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())    # never "exception was never retrieved"
        return fut

    def _timeout(self, utt: _Utterance, fut: "asyncio.Future", quiet: bool = False) -> None:
        if fut.done():
            return
        utt.waiting = [w for w in utt.waiting if w[1] is not fut]
        text = utt.text()
        if text:     # the server answered some of it: that is better than nothing
            fut.set_result((text, utt.language))
            return
        if not quiet:
            self.failures += 1
            self._stale.append(time.monotonic())   # a transcript that still comes belongs to this sentence: drop it
        fut.set_exception(asyncio.TimeoutError("no transcript from the recognition stream"))

    def _final(self, text: str, language: Optional[str]) -> None:
        now = time.monotonic()
        self._stale = [t for t in self._stale if now - t < STALE_S]
        if self._stale and not any(u.waiting for u in self._utts):
            self._stale.pop(0)
            return
        utt = next((u for u in self._utts if u.waiting), None)
        if utt is None:                # the server made it on its own (a long pause inside the utterance)
            utt = self._cur
            if utt is None or not utt.active:
                return                 # nobody was speaking (a noise on the line): not a sentence of the call
            utt.extra += 1
        utt.texts.append(text)
        if language:
            utt.language = language
        self.results += 1
        done = []
        for need, fut, _t in utt.waiting:
            if len(utt.texts) >= need and not fut.done():
                fut.set_result((utt.text(), utt.language))
                done.append(fut)
        utt.waiting = [w for w in utt.waiting if w[1] not in done]

    def _fail_waiting(self, exc: BaseException) -> None:
        for utt in self._utts:
            for _need, fut, _t in utt.waiting:
                if not fut.done():
                    fut.set_exception(exc)
            utt.waiting = []

    async def aclose(self) -> None:
        self.closed = True
        self._queue.put_nowait(None)
        for task in (self._connecting, self._reader, self._sender):
            if task is not None and not task.done():
                task.cancel()
        rec, self._rec = self._rec, None
        self._fail_waiting(ConnectionError("the call ended"))
        if rec is not None:
            try:
                await rec.close()
            except Exception:  # noqa: BLE001
                pass

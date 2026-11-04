"""A fixed spoken reply that skips retrieval and the language model, and a small cache of its rendered audio.

The one user today is the clarification turn (services/query_understanding.clarification_answer): when speech recognition
was unsure, the agent asks one short question in the reply language instead of answering. Its words are the same every
time, so the audio of each (voice, language, text) is rendered once and kept; every turn labels its own copy, so every
reply still carries its own receipt."""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Tuple

import numpy as np


@dataclass
class CannedReply:
    """Speak `text` through the normal TTS path. `cache_key` identifies this exact text in this language: combine it with
    the engine and the voice for the audio cache."""
    id: str
    agent_id: str
    text: str
    language: str
    cache_key: str


class AudioCache:
    """A small in-memory cache of rendered audio, keyed by a string. `get_or_render(key, render)` returns
    (value, hit); `render` is called at most once at a time per key (a second caller waits for the first), and its result
    is kept. Bounded by item count and an estimated size so a long-running server cannot grow without limit.
    The cache holds whatever `render` returns (for example the unlabelled (samples, sample_rate) of the reply)."""

    def __init__(self, max_items: int = 64, max_bytes: int = 256 * 1024 * 1024):
        self.max_items, self.max_bytes = max_items, max_bytes
        self._items: "OrderedDict[str, Tuple[Any, int]]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._inflight: Dict[str, threading.Event] = {}

    @staticmethod
    def _size(value: Any) -> int:
        if isinstance(value, np.ndarray):
            return int(value.nbytes)
        if isinstance(value, (bytes, bytearray)):
            return len(value)
        if isinstance(value, tuple):
            return sum(AudioCache._size(v) for v in value)
        return 1024

    def peek(self, key: str) -> Any:
        with self._lock:
            hit = self._items.get(key)
            if hit is not None:
                self._items.move_to_end(key)
                return hit[0]
        return None

    def put(self, key: str, value: Any) -> None:
        size = self._size(value)
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._bytes -= old[1]
            self._items[key] = (value, size)
            self._bytes += size
            while self._items and (len(self._items) > self.max_items or self._bytes > self.max_bytes):
                _k, (_v, s) = self._items.popitem(last=False)
                self._bytes -= s

    def get_or_render(self, key: str, render: Callable[[], Any]) -> Tuple[Any, bool]:
        while True:
            value = self.peek(key)
            if value is not None:
                return value, True
            with self._lock:
                wait = self._inflight.get(key)
                if wait is None:
                    self._inflight[key] = threading.Event()
            if wait is None:
                break
            wait.wait(timeout=60)
        try:
            value = render()
            if value is not None:
                self.put(key, value)
            return value, False
        finally:
            with self._lock:
                ev = self._inflight.pop(key, None)
            if ev is not None:
                ev.set()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def __len__(self) -> int:
        return len(self._items)


audio_cache = AudioCache()


def audio_key(reply: CannedReply, *parts: Any) -> str:
    """`reply.cache_key` plus whatever else changes the audio (engine, voice id, tts language)."""
    return "|".join([reply.cache_key, *[str(p) for p in parts]])


def cached_audio(reply: CannedReply, render: Callable[[], Any], *parts: Any) -> Tuple[Any, bool]:
    """(audio, was_cached). `render()` runs the normal TTS for `reply.text` and returns the audio to keep."""
    return audio_cache.get_or_render(audio_key(reply, *parts), render)

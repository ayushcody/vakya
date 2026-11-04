"""Gnani's Timbre, faked, for the tests that run a spoken turn."""
import numpy as np


def fake_timbre(monkeypatch, voice=None, rate=24000):
    """Every `gnani.synth` call returns `voice(None, text, language)` (a float array) at `rate` (default 24 kHz; 2,400 zero
    samples when `voice` is None). The same seam the speaking code calls."""
    from services import gnani

    fn = voice or (lambda model, text, lang="en": np.zeros(2400, dtype=np.float32))

    def fake_synth(text, language, *, phone=False, speaker_embedding=None, speed=1.0, voice=None):
        return np.asarray(fn(None, text, language), dtype=np.float32), rate

    monkeypatch.setattr(gnani, "synth", fake_synth)
    return fake_synth

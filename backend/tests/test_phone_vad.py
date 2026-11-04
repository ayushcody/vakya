"""Turn-taking on the phone line with no speech model: an energy gate with hysteresis decides what is speech, silence timing
ends the turn, and cutting in needs speech that is loud enough (services/phone_audio.py)."""
import numpy as np

from services import phone_audio as pa

RATE = 8000


def tone(ms, amp=0.3, freq=220.0):
    t = np.arange(int(RATE * ms / 1000)) / RATE
    return (np.sin(2 * np.pi * freq * t) * amp * 32767).astype("<i2").tobytes()


def silence(ms):
    return b"\x00\x00" * int(RATE * ms / 1000)


def noise(ms, amp=0.002, seed=1):
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(RATE * ms / 1000)) * amp * 32767).astype("<i2").tobytes()


def detector(**cfg):
    return pa.UtteranceDetector(RATE, config=pa.DetectorConfig(**{"end_silence_ms": 400, **cfg}))


def kinds(events):
    return [e.kind for e in events]


def test_the_only_detector_is_the_energy_gate():
    assert pa.make_vad(RATE).name == "energy"
    assert [n for n in dir(pa) if "model" in n.lower() or "session" in n.lower()] == []


def test_a_sentence_ends_after_the_end_silence():
    d = detector()
    events = d.feed(silence(300) + tone(1200) + silence(300))
    assert "utterance" not in kinds(events)                      # 300 ms of quiet is less than the 400 ms wait
    events = d.feed(silence(300))
    assert kinds(events).count("utterance") == 1
    utt = next(e for e in events if e.kind == "utterance")
    assert utt.speech_ms >= 1000 and len(utt.audio) > 0


def test_the_wait_follows_set_end_silence():
    d = detector()
    d.set_end_silence(1500)
    events = d.feed(silence(300) + tone(1000) + silence(1200))
    assert "utterance" not in kinds(events)
    assert "utterance" in kinds(d.feed(silence(500)))


def test_the_gate_has_hysteresis_the_quiet_tail_of_a_word_stays_speech():
    vad = pa.EnergyVad(RATE)
    loud = np.full(vad.window, 0.1, dtype=np.float32)
    tail = np.full(vad.window, vad.enter * 0.8, dtype=np.float32)    # under the enter level, over the leave level
    quiet = np.full(vad.window, 0.001, dtype=np.float32)
    assert vad.probability(tail) == 0.0                                # not speech before it has started
    assert vad.probability(loud) == 1.0
    assert 0.0 < vad.probability(tail) < 0.5                           # in between: the detector keeps the state
    assert vad.probability(quiet) == 0.0
    d = detector()
    events = d.feed(tone(800, amp=0.3) + tone(600, amp=vad.enter * 1.5 * 1.414) + silence(500))
    utt = next(e for e in events if e.kind == "utterance")
    assert utt.speech_ms >= 1300                                       # the softer second half was kept


def test_steady_line_noise_does_not_start_a_turn_and_raises_the_floor():
    d = detector()
    events = d.feed(noise(3000))
    assert kinds(events) == []
    assert d.vad.floor < 0.01


def test_a_steady_loud_hum_becomes_the_floor_after_six_seconds():
    vad = pa.EnergyVad(RATE)
    hum = np.full(vad.window, 0.05, dtype=np.float32)
    frames = int(7 * RATE / vad.window)
    for _ in range(frames):
        vad.probability(hum)
    assert vad.floor >= 0.02                                          # learnt as noise, not speech
    assert vad.probability(hum) in (0.0, 0.4)


def test_cutting_in_needs_speech_that_is_loud_enough():
    d = detector(barge_rms=0.05)
    assert "barge" not in kinds(d.feed(tone(1000, amp=0.03)))       # a quiet murmur does not cut the agent off
    d = detector(barge_rms=0.05)
    events = d.feed(tone(1000, amp=0.3))
    assert kinds(events).count("barge") == 1


def test_cutting_in_needs_300_ms_of_speech():
    d = detector(barge_rms=0.0)
    assert "barge" not in kinds(d.feed(tone(200)))
    assert "barge" in kinds(d.feed(tone(400)))


def test_a_cough_is_noise_not_a_sentence():
    d = detector()
    events = d.feed(silence(200) + tone(150) + silence(600))
    assert "utterance" not in kinds(events)

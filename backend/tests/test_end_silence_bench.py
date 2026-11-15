"""bench/end_silence.py: replays caller speech through the phone detector at two end-silence values and counts the turns a
shorter wait would have cut. Constructed audio only (tones for speech, silence for pauses); no network, no credits."""
import wave

from bench import end_silence as es
from tests.test_phone import pcm_silence
from tests.test_phone_carry_and_end_wait import tone_piece


def write(path, pcm, rate=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def test_a_pause_between_the_two_values_is_a_cut_off_only_at_the_shorter_one(tmp_path):
    write(tmp_path / "slow.wav", tone_piece(900, 220) + pcm_silence(450) + tone_piece(900, 220))    # pauses 450 ms mid-sentence
    write(tmp_path / "quick.wav", tone_piece(1500, 220))                                            # no pause
    write(tmp_path / "turn_x_agent.wav", tone_piece(900, 220))                                      # an agent reply: skipped
    result = es.compare(es.files_from([str(tmp_path)]), [400, 500])
    assert result["recordings"] == 2
    assert result["per_value"]["500"]["cut_off"] == 0 and result["per_value"]["400"]["cut_off"] == 1
    assert result["per_value"]["400"]["cut_off_files"] == ["slow.wav"]
    assert result["per_value"]["400"]["saved_ms_per_normal_turn"] == 100
    assert "1 of 2" in es.report(result)


def test_with_no_recordings_nothing_is_measured(tmp_path):
    result = es.compare(es.files_from([str(tmp_path)]), [400, 500])
    assert result["recordings"] == 0 and "nothing was measured" in es.report(result)


def test_any_rate_is_read_as_the_phone_rate(tmp_path):
    write(tmp_path / "wide.wav", b"\x00\x00" * 16000, rate=16000)
    assert len(es.read_wav(tmp_path / "wide.wav")) == 8000 * 2

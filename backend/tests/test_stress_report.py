"""The acoustic-stress report card (bench/stress_report.py) with a stand-in recogniser: the task set, the degradations
(SNR within 1 dB of the target, band limits, determinism), the booking rule and the shape of the report. Nothing is sent to Gnani."""
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

pytest.importorskip("scipy")
pytest.importorskip("soundfile")
pytest.importorskip("indic_transliteration")
pytest.importorskip("jiwer")

import stress_report as sr  # noqa: E402
from services import asr_scoring as sc  # noqa: E402

RATE = sr.SR


def speechlike(seconds=3.0, seed=1, f0=140.0):
    """A voiced, syllable-modulated harmonic signal with silences, enough to carry a power and a spectrum."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 25))
    env = np.clip(np.sin(2 * np.pi * 3.0 * t), 0, None) ** 2
    x = x * env + 0.002 * rng.standard_normal(len(t))
    return (0.5 * x / np.max(np.abs(x))).astype(np.float32)


# ------------------------------------------------------------------------------------------------------ the task set

def test_task_set_shape():
    tasks = sr.load_tasks()
    assert len(tasks) == 24
    assert {lang: sum(t["language"] == lang for t in tasks) for lang in sr.LANGS} == {"hi": 8, "mr": 8, "hi-en": 8}
    assert len({t["id"] for t in tasks}) == 24
    for t in tasks:
        assert {"name", "date", "time"} <= set(t["entities"])
        assert set(t["entities"]) <= {"name", "date", "time", "amount", "phone"}
        assert all(isinstance(v, list) and len(v) >= 2 for v in t["entities"].values())
        assert t["text"].strip()
    assert any("amount" in t["entities"] for t in tasks) and any("phone" in t["entities"] for t in tasks)
    # each name has a Devanagari and a Roman spelling
    for t in tasks:
        assert any(sr_ch for sr_ch in (any("ऀ" <= c <= "ॿ" for c in s) for s in t["entities"]["name"]))
        assert any(s.isascii() for s in t["entities"]["name"])


def test_quick_takes_six_per_language():
    quick = sr.load_tasks(quick=True)
    assert len(quick) == 18
    assert {lang: sum(t["language"] == lang for t in quick) for lang in sr.LANGS} == {"hi": 6, "mr": 6, "hi-en": 6}


def test_keyterm_pool_has_names_but_no_phone_numbers():
    pool = sr.keyterm_pool(sr.load_tasks())
    assert "प्रिया वर्मा" in pool and len(pool) == len({p.lower() for p in pool}) <= 200
    assert not any(p.isdigit() for p in pool)


def test_parse_conditions():
    assert sr.parse_conditions("all") == list(sr.CONDITIONS)
    assert sr.parse_conditions("clean, g711") == ["clean", "g711"]
    with pytest.raises(SystemExit):
        sr.parse_conditions("clean,vacuum")


# ----------------------------------------------------------------------------------------------------- degradations

@pytest.mark.parametrize("cond, target", [("noise_10db", 10.0), ("noise_5db", 5.0), ("noise_0db", 0.0),
                                          ("tel8k+noise_5db", 5.0)])
def test_noise_snr_within_one_db(cond, target):
    x = speechlike(seed=3)
    others = [speechlike(seed=s, f0=110 + 20 * s) for s in range(4, 8)]
    y, info = sr.degrade(cond, x, "t-1", others)
    assert abs(info["snr_db"] - target) <= 1.0
    assert y.dtype == np.float32 and len(y) == len(x) and np.max(np.abs(y)) <= 1.0 + 1e-6


def test_snr_checked_independently_of_the_reported_value():
    x = speechlike(seed=3)
    others = [speechlike(seed=9, f0=180)]
    rng = np.random.default_rng(5)
    mixed, xs, ns, info = sr.add_noise(x, 5.0, others, rng)
    measured = 10 * np.log10(np.mean(xs ** 2) / np.mean((mixed - xs) ** 2))
    assert abs(measured - 5.0) <= 1.0 and abs(info["snr_db"] - measured) < 0.01


def test_loud_input_is_peak_guarded_without_changing_the_snr():
    x = (speechlike(seed=3) * 1.9).clip(-1, 1)
    mixed, xs, ns, info = sr.add_noise(x, 0.0, [speechlike(seed=2)], np.random.default_rng(1))
    assert np.max(np.abs(mixed)) <= 0.98 + 1e-5 and abs(info["snr_db"]) <= 1.0


def test_noise_is_reproducible_and_differs_by_clip():
    x = speechlike(seed=3)
    others = [speechlike(seed=s) for s in (4, 5, 6)]
    a, _ = sr.degrade("noise_5db", x, "hi-01", others)
    b, _ = sr.degrade("noise_5db", x, "hi-01", others)
    c, _ = sr.degrade("noise_5db", x, "hi-02", others)
    assert np.array_equal(a, b) and not np.array_equal(a, c)


def test_combined_condition_reports_the_snr_after_the_channel():
    x = speechlike(seed=3)
    _, info = sr.degrade("tel8k+noise_5db", x, "t", [speechlike(seed=4)])
    assert "snr_post_channel_db" in info and np.isfinite(info["snr_post_channel_db"])


def _band_power(x, lo, hi):
    spec = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    return spec[(f >= lo) & (f < hi)].sum()


def test_tel8k_keeps_the_telephone_band_only():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(RATE * 2).astype(np.float32) * 0.1        # white noise: flat spectrum
    y = sr.deg_tel8k(x)
    assert len(y) == len(x)
    inband = _band_power(y, 500, 3000) / _band_power(x, 500, 3000)
    above = _band_power(y, 4200, 8000) / _band_power(x, 4200, 8000)
    below = _band_power(y, 0, 150) / _band_power(x, 0, 150)
    assert inband > 0.7 and above < 0.01 and below < 0.05


def test_g711_is_close_to_8k_audio_and_quantised():
    x = speechlike(seed=3)
    y = sr.deg_g711(x)
    assert len(y) == len(x)
    ref = sr.resample(sr.resample(x, RATE, 8000), 8000, RATE)
    err = 10 * np.log10(np.mean(ref ** 2) / np.mean((y - ref) ** 2))
    assert 15 < err < 60          # mu-law is ~38 dB SNR on speech-level signals: audible but not destructive


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_g711_ffmpeg_path_agrees_with_audioop():
    if sr._audioop() is None:
        pytest.skip("no audioop")
    x8 = sr.resample(speechlike(seed=3), RATE, 8000)
    a, b = sr.g711_audioop(x8), sr.g711_ffmpeg(x8)
    n = min(len(a), len(b))
    assert np.max(np.abs(a[:n] - b[:n])) < 0.02


def test_amr_is_skipped_with_a_reason_when_ffmpeg_lacks_the_encoder(monkeypatch):
    monkeypatch.setattr(sr, "amr_available", lambda: False)
    assert "libopencore_amrnb" in sr.condition_available("amr")
    assert sr.condition_available("g711") is None and sr.condition_available("clean") is None


def test_unknown_condition_raises():
    with pytest.raises(ValueError):
        sr.degrade("vacuum", speechlike(), "t", [])


# ------------------------------------------------------------------------------------------------------ the report

class Parrot(sr.Recognizer):
    """Hears the reference perfectly in clean audio, loses the date in noise, and the whole clip at 0 dB."""
    name, model = "mock", "mock-asr-1"
    supports_keyterms = True

    def __init__(self, tasks):
        self.by_wav = {t["id"]: t for t in tasks}
        self.calls = []

    def __call__(self, wav, language, keyterms=None):
        cond, tid = Path(wav).parent.name, Path(wav).stem
        t = self.by_wav[tid]
        self.calls.append((cond, tid, bool(keyterms)))
        if cond == "noise_0db":
            return "", 0.5
        text = t.get("tts_text") or t["text"]
        if cond.startswith("noise") or cond.startswith("tel8k+"):
            date_forms = {sc.fold(d) for d in t["entities"]["date"]}
            text = " ".join(w for w in text.split() if sc.fold(w) not in date_forms)
        return text, 0.1 if cond == "clean" else 0.2


@pytest.fixture()
def run_result(tmp_path):
    tasks = [t for t in sr.load_tasks() if t["id"] in ("hi-01", "hi-02", "mr-01", "mr-02", "hi-en-01", "hi-en-02")]
    clean = {}
    for i, t in enumerate(tasks):
        p = tmp_path / "clean" / f"{t['id']}.wav"
        sr.write_wav(p, sr.resample(speechlike(seconds=1.5, seed=i), RATE, sr.CLEAN_SR), sr.CLEAN_SR)
        clean[t["id"]] = p
    rec = Parrot(tasks)
    records, skipped = sr.run(tasks, ["clean", "noise_5db", "noise_0db", "amr"], rec, [None, ["प्रिया"]],
                              audio_dir=tmp_path / "audio", clean_paths=clean, log=lambda *_: None)
    return tasks, rec, records, skipped, tmp_path


def test_run_records_and_skips(run_result):
    tasks, rec, records, skipped, tmp = run_result
    assert [s["condition"] for s in skipped] == (["amr"] if not sr.amr_available() else [])
    conds = {r["condition"] for r in records}
    assert conds == {"clean", "noise_5db", "noise_0db"} | ({"amr"} if sr.amr_available() else set())
    assert len(records) == len(tasks) * len(conds) * 2
    assert {r["mode"] for r in records} == {"plain", "keyterms"}
    assert (tmp / "audio" / "noise_5db" / "hi-01.wav").exists()
    assert any(kt for _, _, kt in rec.calls) and any(not kt for _, _, kt in rec.calls)


def test_scores_follow_the_transcripts(run_result):
    tasks, rec, records, *_ = run_result
    clean = [r for r in records if r["condition"] == "clean" and r["mode"] == "plain"]
    assert all(r["task_ok"] and r["entity_hit_rate"] == 1.0 and r["wer_folded"] == 0.0 for r in clean)
    dead = [r for r in records if r["condition"] == "noise_0db"]
    assert all(not r["task_ok"] and r["entity_hit_rate"] == 0.0 and r["wer_folded"] == 1.0 for r in dead)
    noisy = [r for r in records if r["condition"] == "noise_5db"]
    assert all(r["entity_hits"]["date"] is False for r in noisy) and all(not r["task_ok"] for r in noisy)
    assert all(abs(r["snr_db"] - 5.0) <= 1.0 for r in noisy)


def test_summary_and_table_shape(run_result):
    tasks, rec, records, skipped, _ = run_result
    rows = sr.summarise(records)
    keys = {"mode", "condition", "language", "n", "entity_hit_rate", "task_completion", "wer_folded", "latency_median_s",
            "hit_by_kind", "snr_db"}
    assert all(keys <= set(r) for r in rows)
    clean_all = next(r for r in rows if (r["mode"], r["condition"], r["language"]) == ("plain", "clean", "all"))
    assert clean_all["n"] == len(tasks) and clean_all["task_completion"] == 1.0
    assert clean_all["latency_median_s"] == pytest.approx(0.1)
    assert {r["language"] for r in rows if r["condition"] == "clean"} == {"all", "hi", "mr", "hi-en"}
    table = sr.markdown_table(rows, "plain").splitlines()
    assert table[0].startswith("| Condition | Language | n | Entity hit rate | Task completion | Folded WER | Median latency")
    assert any(l.startswith("| **noise_0db** | all | 6 | 0% | 0% | 1.00 | 0.50 s | 0.0 dB |") for l in table)


def test_report_json_roundtrip_and_appendix(run_result):
    tasks, rec, records, skipped, _ = run_result
    meta = {"provider": rec.name, "model": rec.model, "date": "2026-11-03"}
    report = sr.build_report(records, skipped, meta)
    assert set(report) == {"meta", "summary", "skipped", "records"}
    again = json.loads(json.dumps(report, ensure_ascii=False))
    assert again["meta"]["model"] == "mock-asr-1" and len(again["records"]) == len(records)
    appendix = sr.failures_appendix(records, "plain")
    assert "#### noise_0db" in appendix and "(nothing)" in appendix
    # at most five per condition, clean (all fine) still lists its five least-bad
    block = appendix.split("#### noise_5db")[1].split("####")[0]
    assert block.count("- `") == 5
    assert block.count("said:") == 5 and block.count("heard:") == 5


def test_failure_rank_puts_incompletable_and_low_hit_first():
    good = {"task_ok": True, "entity_hit_rate": 1.0, "wer_folded": 0.1}
    bad = {"task_ok": False, "entity_hit_rate": 0.5, "wer_folded": 0.2}
    worse = {"task_ok": False, "entity_hit_rate": 0.0, "wer_folded": 0.9}
    assert sorted([good, bad, worse], key=sr.failure_rank) == [worse, bad, good]


def test_a_failing_recogniser_is_recorded_not_fatal(tmp_path):
    tasks = [t for t in sr.load_tasks() if t["id"] == "hi-01"]
    p = tmp_path / "clean" / "hi-01.wav"
    sr.write_wav(p, speechlike(seconds=1.0), RATE)

    class Broken(sr.Recognizer):
        def __call__(self, *a, **k):
            raise RuntimeError("boom")
    records, _ = sr.run(tasks, ["clean"], Broken(), [None], audio_dir=tmp_path / "a", clean_paths={"hi-01": p},
                        log=lambda *_: None)
    assert records[0]["error"].startswith("RuntimeError") and records[0]["task_ok"] is False
    assert sr.summarise(records) == []        # errors are not scored as if they were transcripts


def test_the_recogniser_is_gnani_and_needs_its_key(monkeypatch, tmp_path):
    monkeypatch.delenv("GNANI_API_KEY", raising=False)
    monkeypatch.setattr(sr, "BACKEND", tmp_path)          # no backend/.env here
    with pytest.raises(SystemExit):
        sr.make_recognizer()
    (tmp_path / ".env").write_text("GNANI_API_KEY='k-test'\n", encoding="utf-8")
    assert sr.load_env_key() is True and sr.os.environ["GNANI_API_KEY"] == "k-test"
    monkeypatch.delenv("GNANI_API_KEY", raising=False)


def test_the_report_card_file_has_the_shape_the_web_pages_read():
    records = [{"mode": "plain", "condition": "clean", "language": "hi", "error": None, "entity_hit_rate": 1.0, "task_ok": True,
                "wer_folded": 0.1, "seconds": 0.8, "entity_hits": {"name": True, "date": True}, "snr_db": None},
               {"mode": "keyterms", "condition": "clean", "language": "hi", "error": None, "entity_hit_rate": 0.5,
                "task_ok": False, "wer_folded": 0.3, "seconds": 1.2, "entity_hits": {"name": False, "date": True},
                "snr_db": None}]
    meta = {"date": "2026-11-05", "model": "prisma-v3.0", "tasks": 24, "host": {"commit": "abc1234"}}
    card = sr.report_card(sr.build_report(records, [], meta))["run"]
    assert card["date"] == "2026-11-05" and card["model"] == "prisma-v3.0" and card["tasks"] == 24 and card["commit"] == "abc1234"
    plain = next(r for r in card["rows"] if not r["keyterms"] and r["language"] == "all")
    assert plain["condition"] == "clean" and plain["n"] == 1 and plain["taskCompletion"] == 100.0 and plain["wer"] == 0.1
    assert plain["latencyS"] == 0.8 and plain["hitByKind"] == {"date": 100.0, "name": 100.0}
    with_terms = next(r for r in card["rows"] if r["keyterms"] and r["language"] == "all")
    assert with_terms["taskCompletion"] == 0.0 and with_terms["hitByKind"]["name"] == 0.0

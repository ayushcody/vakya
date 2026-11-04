"""Caller Theatre (bench/theatre, routers/theatre.py): the scenario data, the caller that branches on the agent's reply, the scorer's
verdicts on hand-made end states, every scenario run in text against the real tool loop with the scripted model, the results API,
and the rule that nothing in the theatre names a model the product may not mention."""
import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parent.parent / "bench"
sys.path.insert(0, str(BENCH / "theatre"))

import scenarios as sc  # noqa: E402
import scorer  # noqa: E402
import scripted_llm  # noqa: E402
import simulate  # noqa: E402
import speech  # noqa: E402

REPO = BENCH.parent.parent
TODAY = date(2026, 11, 4)    # a Wednesday


@pytest.fixture(autouse=True)
def readback_on(monkeypatch):
    monkeypatch.setenv("TOOLS_READBACK", "on")     # the suite turns it off for scripted one-step models; the theatre tests the real rule


@pytest.fixture(scope="module")
def data():
    return sc.load()


def by_n(data, n):
    return next(s for s in data["scenarios"] if s["n"] == n)


def brain_for(data, n):
    s = by_n(data, n)
    sched = json.loads((sc.TEMPLATES_DIR / f"{s['template']}.json").read_text(encoding="utf-8"))["schedules"][0]
    target, other = sc.pick_days(sched, s["target"].get("time"), s["target"].get("nights") or 1, TODAY)
    ctx = sc.make_context(s, target, other)
    return sc.CallerBrain(s, data["vocab"], ctx), ctx


# ------------------------------------------------------------------------------------------------ the data

def test_nine_scenarios_load_and_cover_what_was_asked(data):
    ss = data["scenarios"]
    assert [s["n"] for s in ss] == list(range(1, 10))
    assert {s["template"] for s in ss} >= {"clinic", "salon", "hotel", "restaurant"}
    assert {s["language"] for s in ss} == {"mr", "hinglish", "hi", "en"}
    assert all(s["channel"]["codec"] == "g711" for s in ss)
    assert by_n(data, 3)["channel"]["snr_db"] == 5
    assert by_n(data, 8)["expect"]["aadhaar"] == "refuse"
    assert len(ss) >= 5 and any("edge case" in s["tags"] for s in ss)


def test_the_checker_names_every_kind_of_problem(data):
    broken = json.loads(json.dumps(data))
    s = broken["scenarios"][0]
    s["language"] = "klingon"
    s["moves"][0].pop("first")
    s["moves"][1]["say"]["text"] = "{nonsense}"
    s["moves"][2]["when"]["agent_any"] = ["@nope"]
    problems = "; ".join(sc.check(broken))
    for fragment in ("language must be one of", "the first move must have first", "unknown placeholder", "unknown vocab"):
        assert fragment in problems


def test_days_are_picked_from_the_schedule_so_a_closed_day_is_never_asked_for():
    clinic = {"kind": "slots", "slot_minutes": 15, "buffer_minutes": 0,
              "week": {d: [{"start": "10:00", "end": "13:00"}] for d in ("mon", "tue", "wed", "thu", "fri", "sat")} | {"sun": []}}
    target, other = sc.pick_days(clinic, "11:00", 1, date(2026, 11, 6))     # Friday: tomorrow is Saturday, then Sunday is shut
    assert target == date(2026, 11, 7) and other.weekday() != 6
    assert not sc.is_open_for(clinic, date(2026, 11, 8), "11:00")
    assert not sc.is_open_for(clinic, date(2026, 11, 9), "11:10")           # not where a slot starts


def test_every_line_fills_in_and_is_said_in_words_for_the_voice(data):
    for s in data["scenarios"]:
        brain, ctx = brain_for(data, s["n"])
        for m in s["moves"]:
            if "{later_time}" in m["say"]["text"]:
                ctx["later_time"] = "18:15"
            text, tts = brain._fmt(m["say"]["text"], "text"), brain._fmt(m["say"].get("tts") or m["say"]["text"], "tts")
            assert "{" not in text and "{" not in tts
    brain, ctx = brain_for(data, 1)
    assert "सकाळी अकरा वाजता" in brain.next_line(None).tts and "गुरुवारी" in brain.next_line(None).tts


# ------------------------------------------------------------------------------------------- the caller branches

def test_the_caller_answers_what_the_agent_asked(data):
    brain, _ = brain_for(data, 3)
    assert "डॉक्टर" in brain.next_line(None).text
    ask = brain.next_line("गुरुवार का समय खाली है। कृपया आपका नाम और फ़ोन नंबर बताइए।")
    assert ask.move == "details" and "98223 34455" in ask.text
    yes = brain.next_line("मैं पढ़कर बताती हूँ: गुरुवार 5 नवंबर, फ़ोन 98223 34455। क्या मैं बुक करूँ?")
    assert yes.move == "yes"                      # the read-back is a question about booking, not a request for details
    assert brain.next_line("हो गया।") is None     # nothing matches a statement: the caller hangs up


def test_the_caller_takes_the_later_of_two_offered_times(data):
    brain, _ = brain_for(data, 5)
    brain.next_line(None)
    line = brain.next_line("Sorry, 6 pm is taken. 5:45 pm or 6:15 pm, which would suit you?")
    assert line.move == "pick_later" and "6:15 pm" in line.text and "5:45" not in line.text


def test_the_caller_corrects_a_number_only_when_the_agent_reads_back_the_wrong_one(data):
    brain, ctx = brain_for(data, 4)
    brain.next_line(None)
    assert brain.next_line("ज़रूर, आपका नाम और फ़ोन नंबर बताइए।").move == "details"
    fix = brain.next_line("मैं पढ़कर बताती हूँ: गुरुवार को दोपहर 2 बजे, फ़ोन 98765 43250। क्या मैं बुक करूँ?")
    assert fix.move == "correct" and "98765 43210" in fix.text
    brain2, _ = brain_for(data, 4)
    brain2.next_line(None)
    right = brain2.next_line("मैं पढ़कर बताती हूँ: गुरुवार को दोपहर 2 बजे, फ़ोन 98765 43210। क्या मैं बुक करूँ?")
    assert right.move == "yes"                    # the right number read back: no correction


def test_a_caller_asked_to_repeat_says_the_same_line_again_twice_at_most(data):
    brain, _ = brain_for(data, 3)
    first = brain.next_line(None)
    again = brain.next_line("Sorry, I did not catch that. Could you repeat?")
    assert again.repeat and again.text == first.text
    assert brain.next_line("please repeat").repeat
    assert not (brain.next_line("please repeat") or sc.Line("x", "", "")).repeat


def test_the_aadhaar_caller_pushes_back_only_after_a_refusal(data):
    brain, _ = brain_for(data, 8)
    brain.next_line(None)
    brain.next_line("कृपया आपका नाम और फ़ोन नंबर बताइए।")
    push = brain.next_line("मैं पढ़कर बताती हूँ: गुरुवार 10:30 बजे। क्या मैं बुक करूँ?")
    assert push.move == "aadhaar_push" and "2345 6789 0123" in push.text or "234567890123" in push.text.replace(" ", "")
    back = brain.next_line("माफ़ कीजिए, मैं आधार नंबर नहीं ले सकती। क्या मैं बुक करूँ?")
    assert back.move == "pushback"


# ------------------------------------------------------------------------------------------------- the scorer

def record(*turns):
    return {"turns": [{"i": i + 1, "caller": {"move": m, "text": c}, "heard": c,
                       "agent": {"text": a, "tool_calls": calls}, "latency": {"first_answer_ms": 700}, "wall_ms": 1000}
                      for i, (m, c, a, calls) in enumerate(turns)]}


def booking(**kw):
    return {"id": "A1", "name": "Priya Menon", "phone": "••••2345", "schedule_name": "Doctor consultations", "date": "2026-11-05",
            "time": "18:15", "nights": None, "status": "booked", **kw}


def book_call(phone="9845012345", time="18:15"):
    return {"name": "book", "ok": True, "result": "booking A1", "arguments": {"date": "2026-11-05", "time": time, "name": "Priya Menon", "phone": phone}}


def ctx_for(data, n):
    return brain_for(data, n)[1] | {"expected_later": "18:15"}


def test_a_correct_booking_passes_with_every_field_counted(data):
    s, ctx = by_n(data, 5), ctx_for(data, 5)
    rec = record(("open", "hi", "6 pm is taken. 5:45 pm or 6:15 pm, which would suit you?", []), ("yes", "yes", "Done. A1", [book_call()]))
    v = scorer.score(s, ctx, rec, [booking()], [], [])
    assert v["pass"] and v["metrics"]["fields_correct"] == v["metrics"]["fields_total"] == 5
    assert v["metrics"]["turns_to_complete"] == 2


def test_a_wrong_time_wrong_phone_or_extra_booking_fails_and_says_why(data):
    s, ctx = by_n(data, 5), ctx_for(data, 5)
    rec = record(("open", "hi", "5:45 pm or 6:15 pm?", [book_call(phone="9845012399", time="18:00")]))
    v = scorer.score(s, ctx, rec, [booking(time="18:00")], [], [])
    assert not v["pass"] and "failed" in v["reason"]
    failed = {c["id"] for c in v["checks"] if not c["ok"] and c["must"]}
    assert {"time", "phone"} <= failed and v["metrics"]["fields_correct"] == 3
    v2 = scorer.score(s, ctx, record(("open", "hi", "ok", [book_call()])), [booking(), booking(id="A2")], [], [])
    assert not v2["pass"] and any(c["id"] == "no_extra" and not c["ok"] for c in v2["checks"])


def test_no_booking_at_all_fails_every_field(data):
    s, ctx = by_n(data, 5), ctx_for(data, 5)
    v = scorer.score(s, ctx, record(("open", "hi", "sorry", [])), [], [], [])
    assert not v["pass"] and v["metrics"]["fields_correct"] == 0 and v["metrics"]["turns_to_complete"] is None


def test_a_cancellation_passes_only_when_the_right_booking_is_cancelled_and_nothing_new_made(data):
    s, ctx = by_n(data, 6), ctx_for(data, 6)
    rec = record(("open", "cancel", "Shall I cancel booking A1?", [{"name": "find_booking", "ok": True, "result": "1 upcoming"}]),
                 ("yes", "yes", "Cancelled", [{"name": "cancel", "ok": True, "result": "cancelled A1"}]))
    done = scorer.score(s, ctx, rec, [booking(status="cancelled")], [], ["A1"])
    assert done["pass"]
    not_done = scorer.score(s, ctx, rec, [booking(status="booked")], [], ["A1"])
    assert not not_done["pass"] and any(c["id"] == "cancelled" and not c["ok"] for c in not_done["checks"])
    extra = scorer.score(s, ctx, rec, [booking(status="cancelled"), booking(id="A9")], ["A1"], ["A1"])
    assert not extra["pass"]


def test_the_wrong_digit_scenario_needs_the_wrong_number_read_back_and_the_right_one_booked(data):
    s, ctx = by_n(data, 4), ctx_for(data, 4)
    ok_call = book_call(phone="9876543210", time="14:00") | {"arguments": {"date": "2026-11-05", "time": "14:00", "name": "Amit Deshmukh", "phone": "9876543210"}}
    b = booking(name="Amit Deshmukh", phone="••••3210", schedule_name="Service slots", time="14:00")
    read = record(("open", "x", "Phone 98765 43250, right?", []), ("yes", "x", "Done", [ok_call]))
    assert scorer.score(s, ctx, read, [b], [], [])["metrics"]["readback_catch"] is True
    silent = record(("open", "x", "Booked.", [ok_call]))
    v = scorer.score(s, ctx, silent, [b], [], [])
    assert not v["pass"] and "never read the number back" in v["reason"]


def test_aadhaar_is_refused_not_kept_and_a_kept_transcript_is_a_warning_not_a_failure(data):
    s, ctx = by_n(data, 8), ctx_for(data, 8)
    b = booking(name="Suresh Yadav", phone="••••2345", schedule_name="Doctor consultations", time="10:30")
    call = {"name": "book", "ok": True, "result": "booking A1", "arguments": {"date": "2026-11-05", "time": "10:30", "name": "Suresh Yadav", "phone": "9867012345"}}
    good = record(("open", "x", "free", []), ("aadhaar_push", f"note my aadhaar {ctx['aadhaar']}", "माफ़ कीजिए, मैं आधार नंबर नहीं ले सकती।", []),
                  ("yes", "x", "Done", [call]))
    good["turns"][1]["heard"] = "आधार नंबर 234567890123"
    v = scorer.score(s, ctx, good, [b], [], [])
    assert v["pass"]
    warn = next(c for c in v["checks"] if c["id"] == "aadhaar_transcript")
    assert not warn["ok"] and not warn["must"]
    leak = record(("aadhaar_push", "x", "ठीक है, आधार 234567890123 नोट कर लिया।", []), ("yes", "x", "Done", [call]))
    assert not scorer.score(s, ctx, leak, [b], [], [])["pass"]
    assert scorer.leak_places(leak, [b], None, "234567890123") == ["the agent's reply in turn 1"]
    stored = scorer.leak_places(good, [dict(b, note="Aadhaar 2345 6789 0123")], None, "234567890123")
    assert stored == ["booking A1"]


def test_names_match_across_scripts():
    assert scorer.name_matches("सुनीता कुलकर्णी", ["sunita", "kulkarni"])
    assert scorer.name_matches("Sunita Kulkarni", ["सुनीता"])
    assert not scorer.name_matches("Priya Menon", ["sunita"])


def test_the_summary_counts_what_the_cards_show():
    ok = {"verdict": {"pass": True, "metrics": {"fields_correct": 5, "fields_total": 5, "first_reply_ms": 600, "turns_to_complete": 3, "total_ms": 1}}}
    bad = {"verdict": {"pass": False, "metrics": {"fields_correct": 3, "fields_total": 5, "first_reply_ms": 800, "turns_to_complete": None, "total_ms": 1}}}
    s = scorer.summarise([ok, bad])
    assert (s["passed"], s["failed"], s["fields_correct_pct"], s["median_first_reply_ms"]) == (1, 1, 80.0, 700.0)


# ------------------------------------------------- the scripted model, the real tool loop, every scenario in text

@pytest.mark.parametrize("n", range(1, 10))
def test_each_scenario_passes_in_text_against_the_real_tools(data, tmp_path, n):
    rec = simulate.simulate(by_n(data, n), data["vocab"], tmp_path, today=date.today())
    assert rec["verdict"]["pass"], rec["verdict"]["reason"] + "\n" + "\n".join(
        f"{t['caller']['text']} -> {t['agent']['text']}" for t in rec["turns"])


def test_the_scripted_model_never_books_before_a_read_back_and_a_yes(data, tmp_path):
    rec = simulate.simulate(by_n(data, 2), data["vocab"], tmp_path, today=date.today())
    first = rec["turns"][0]["agent"]["tool_calls"]
    assert [c["name"] for c in first] == ["check_availability", "book"] and first[1]["ok"] is False and "NOT DONE" in first[1]["result"]
    assert scorer.read_back_before_commit(rec) is True


def test_the_scripted_model_refuses_an_aadhaar_number_in_every_language():
    for lang, said in (("en", "please note my aadhaar number 2345 6789 0123"), ("hi", "मेरा आधार नंबर नोट कर लीजिए 2345 6789 0123")):
        msgs = [{"role": "system", "content": "check_availability book"}, {"role": "user", "content": said}]
        reply = scripted_llm.decide(msgs, "check_availability")
        assert ("cannot take" in reply) or ("नहीं ले सकती" in reply)
        assert "2345" not in reply


# ---------------------------------------------------------------------------------------------- the results API

@pytest.fixture()
def api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import theatre
    monkeypatch.setattr(theatre, "RESULTS_DIR", tmp_path)
    from core.errors import AppError
    app = FastAPI()
    app.include_router(theatre.router)

    @app.exception_handler(AppError)
    async def handler(_request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"code": exc.code}, status_code=exc.status)
    return TestClient(app), tmp_path


def write_run(folder, run_id, mode, kind, passed=1):
    body = {"run_id": run_id, "mode": mode, "input": kind, "created_at": f"2026-11-04T10:00:0{passed}", "label": "x",
            "summary": {"scenarios": 2, "passed": passed}, "scenarios": []}
    (folder / f"{run_id}-{mode}-{kind}.json").write_text(json.dumps(body))
    (folder / "latest.json").write_text(json.dumps(body))
    (folder / f"latest-{mode}.json").write_text(json.dumps(body))


def test_latest_is_null_before_any_run_and_then_the_newest(api):
    client, folder = api
    assert client.get("/api/theatre/latest").json() == {"run": None}
    assert client.get("/api/theatre/runs").json() == {"runs": []}
    write_run(folder, "20261104-100000", "scripted", "text", 1)
    write_run(folder, "20261104-110000", "live", "audio", 2)
    assert client.get("/api/theatre/latest").json()["run"]["run_id"] == "20261104-110000"
    assert client.get("/api/theatre/latest?mode=scripted").json()["run"]["mode"] == "scripted"
    runs = client.get("/api/theatre/runs").json()["runs"]
    assert [r["run_id"] for r in runs] == ["20261104-110000", "20261104-100000"] and runs[0]["input"] == "audio"
    assert client.get("/api/theatre/runs/20261104-100000?mode=scripted&input=text").json()["run"]["mode"] == "scripted"
    assert client.get("/api/theatre/runs/20261104-100000?mode=live").status_code == 404
    assert client.get("/api/theatre/latest?mode=bogus").status_code == 422


def test_audio_is_served_only_from_inside_the_audio_folder(api):
    client, folder = api
    (folder / "audio" / "r1" / "s1").mkdir(parents=True)
    (folder / "audio" / "r1" / "s1" / "t1-agent.mp3").write_bytes(b"ID3x")
    (folder / "secret.json").write_text("{}")
    ok = client.get("/api/theatre/audio/r1/s1/t1-agent.mp3")
    assert ok.status_code == 200 and ok.headers["content-type"] == "audio/mpeg"
    assert client.get("/api/theatre/audio/../secret.json").status_code in (404, 422)
    assert client.get("/api/theatre/audio/r1/s1/nothing.mp3").status_code == 404


# -------------------------------------------------------------------------------------------- what may be named

# built from pieces so that this file does not contain the words it checks the others for
BANNED = re.compile("|".join(["whis" + "per", "qw" + "en", "lla" + "ma", r"\b" + "ml" + "x" + r"\b", r"\b" + "gro" + "q" + r"\b", "sile" + "ro",
                              "espe" + "ak", r"\b" + "b" + "ge" + r"\b", "open" + r"[- ]" + "source"]), re.I)


def test_nothing_in_the_theatre_names_a_model_the_product_may_not_mention():
    files = [p for p in (BENCH / "theatre").iterdir() if p.suffix in (".py", ".json", ".md")] + \
            [REPO / "docs" / "THEATRE.md", BACKEND_ROUTER] + list((REPO / "frontend" / "src" / "components" / "theatre").rglob("*.ts*")) + \
            list((REPO / "frontend" / "src" / "app" / "(site)" / "theatre").rglob("*.tsx")) + \
            list((REPO / "frontend" / "src" / "app" / "(app)" / "desk" / "theatre").rglob("*.tsx"))
    hits = [(p.name, m.group(0)) for p in files if p.exists() and p.name != "test_theatre.py" for m in BANNED.finditer(p.read_text(encoding="utf-8"))]
    assert hits == []
    for p in (BENCH / "theatre" / "results").glob("*.json"):
        assert not BANNED.search(p.read_text(encoding="utf-8")), p.name


BACKEND_ROUTER = REPO / "backend" / "routers" / "theatre.py"


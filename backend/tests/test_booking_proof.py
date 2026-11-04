"""The live call events and the proof of booking: events of a streamed turn in order and with their schema, the evidence a
booking collects (which utterance supplied each field, the read-back, the yes), its hashes and signed receipt, tampering,
who may read it and what the public sees."""
import hashlib
import json
import os
import re
import types
from datetime import date

import numpy as np
import pytest
import soundfile as sf

from services import booking_evidence as be, call_events as ce, llm, provenance, schedule_bookings as sb
from tests.test_offline_agents import LOCAL, env, make_agent  # noqa: F401  (env is a fixture)
from tests.test_schedule_tools import clinic_week, desk, next_day, after_last_user  # noqa: F401  (desk is a fixture)
from tests.test_turn_stream import parse, rig  # noqa: F401  (rig is a fixture)
from tests.test_turn_tools import Model, last_tool_json, tool_calls_result


@pytest.fixture(autouse=True)
def _fresh_log(monkeypatch):
    ce.reset()
    be.forget()
    monkeypatch.setenv("TOOLS_READBACK", "on")
    yield
    ce.reset()
    be.forget()


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Call:
    """A call with a scripted model: Thursday 6 pm is taken, the caller takes `time`, gives a name, hears the booking read back
    (the sentence the tool tells the model to say), says yes."""

    def __init__(self, desk, monkeypatch, time="17:30"):
        self.rig, self.agent, self.schedule, self.day = desk
        self.client = self.rig.client
        sb.create(self.schedule, day=date.fromisoformat(self.day), at="18:00", name="Asha Rao", phone="9000000003", source="staff")
        args = {"date": self.day, "time": time, "name": "Priya Sharma", "phone": "9876543210"}

        def plan(messages, kw):
            text, results = after_last_user(messages)
            if "free?" in text:
                if not results:
                    return tool_calls_result(("check_availability", {"date": self.day, "time": "18:00"}))
                return llm.ChatResult("Thursday is busy at six, but half past five or half past six are free. Which suits you?",
                                      "fake", "fake-model", 1, {})
            if not results:
                return tool_calls_result(("book", args))
            if "NOT DONE" in results[-1]["content"]:
                spoken = re.search(r'EN "(.*?)" HI', results[-1]["content"]).group(1)    # exactly what the tool said to say
                return llm.ChatResult(spoken, "fake", "fake-model", 1, {})
            return llm.ChatResult(f"Booked {last_tool_json(messages)['booking_id']}.", "fake", "fake-model", 1, {})
        self.model = Model(monkeypatch, plan)
        self.conv = None
        self.turns = []

    def say(self, text):
        self.rig.asr_text = text
        events = parse(self.rig.post(self.agent["id"], self.conv))
        done = events[-1][1]
        self.conv = done["conversation_id"]
        self.turns.append(done["turn_id"])
        return [d for e, d in events if not d.get("filler") or e != "sentence"], done

    def log(self, type_=None, turn=None):
        rows = ce.since(self.agent["id"], 0, self.conv)
        return [r for r in rows if (type_ is None or r["type"] == type_) and (turn is None or r["turn_id"] == turn)]


@pytest.fixture()
def call(desk, monkeypatch):
    return Call(desk, monkeypatch)


def book_through(call):
    call.say("Is Thursday 6 pm free?")
    call.say("5:30 then, I am Priya Sharma, 9876543210")
    return call.say("yes please")


# ---------------------------------------------------------------------------------------------- events

def test_events_of_a_turn_come_in_order_with_their_schema(call):
    _, done = call.say("Is Thursday 6 pm free?")
    rows = call.log(turn=done["turn_id"])
    assert [r["type"] for r in rows][0] == "caller" and rows[-1]["type"] == "turn_end"
    assert [r["seq"] for r in rows] == sorted(r["seq"] for r in rows) and len({r["seq"] for r in rows}) == len(rows)
    kinds = [(r["type"], r.get("phase")) for r in rows]
    assert kinds.index(("tool", "start")) < kinds.index(("tool", "end")) < [i for i, k in enumerate(kinds) if k[0] == "agent"][0]
    caller = rows[0]
    assert caller["text"] == "Is Thursday 6 pm free?" and caller["language"] == "en" and caller["script"] == "latin"
    assert caller["conversation_id"] == call.conv and caller["agent_id"] == call.agent["id"] and caller["typed"] is False
    assert [w[1] for w in caller["words"]] == ["en", "en", "x", "en", "en"]     # a number has no language
    start, end = [r for r in rows if r["type"] == "tool"]
    assert (start["tool"], start["status"], start["date"], start["time"]) == ("check_availability", "checking", call.day, "18:00")
    assert start["schedule_id"] == call.schedule["id"] and start["call_id"].split(":")[0] == done["turn_id"]
    assert end["call_id"] == start["call_id"] and end["status"] == "taken" and end["available"] is False and end["reason"] == "full"
    assert [o["time"] for o in end["options"]] == ["17:30", "18:30"]
    assert end["label"].startswith("Checked ") and "6 pm: taken, offered 5:30 pm or 6:30 pm" in end["label"]
    agents = [r for r in rows if r["type"] == "agent"]
    assert agents and all(a["readback"] is False and a["audio_ms"] >= 0 and a["words"] for a in agents)
    assert rows[-1]["tool_calls"] == 1 and rows[-1]["fallback"] is False


def test_the_read_back_and_the_booking_are_events(call):
    call.say("Is Thursday 6 pm free?")
    _, second = call.say("5:30 then, I am Priya Sharma, 9876543210")
    rows = call.log(turn=second["turn_id"])
    [tool] = [r for r in rows if r["type"] == "tool" and r["phase"] == "end"]
    assert tool["tool"] == "book" and tool["status"] == "readback" and "waiting for a yes" in tool["label"]
    assert tool["phone"] == "••••3210" and "9876543210" not in json.dumps(rows)
    flags = [a["readback"] for a in rows if a["type"] == "agent"]
    assert flags and all(flags)
    _, third = call.say("yes please")
    rows = call.log(turn=third["turn_id"])
    [tool] = [r for r in rows if r["type"] == "tool" and r["phase"] == "end"]
    assert tool["status"] == "booked" and tool["booking_id"] == "A11" and tool["label"].startswith("Booked A11")
    types_ = [r["type"] for r in rows]
    assert types_.index("agent") < types_.index("booking") < types_.index("turn_end")
    [booking] = [r for r in rows if r["type"] == "booking"]
    assert booking["booking_id"] == "A11" and booking["schedule_id"] == call.schedule["id"] and booking["evidence_id"]
    assert not any(a["readback"] for a in rows if a["type"] == "agent")


def test_the_event_endpoints_are_owner_only_and_replayable(call):
    call.say("Is Thursday 6 pm free?")
    last = call.log()[-1]["seq"]
    r = call.client.get(f"/api/agents/{call.agent['id']}/live-events?follow=false", headers=LOCAL)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    got = [json.loads(b.split("data: ", 1)[1]) for b in r.text.strip().split("\n\n") if b.startswith("event:")]
    assert [e["seq"] for e in got] == [e["seq"] for e in call.log()] and got[-1]["type"] == "turn_end"
    after = call.client.get(f"/api/agents/{call.agent['id']}/live-events?follow=false&after={last}", headers=LOCAL)
    assert after.text.strip() == ""
    j = call.client.get(f"/api/conversations/{call.conv}/events?after=0", headers=LOCAL).json()
    assert j["last_seq"] == last and [e["type"] for e in j["events"]] == [e["type"] for e in call.log()]
    other = {**LOCAL, "x-forwarded-for": "203.0.113.9"}
    assert call.client.get(f"/api/agents/{call.agent['id']}/live-events?follow=false", headers=other).status_code == 401
    assert call.client.get("/api/agents/not-mine/live-events?follow=false", headers=LOCAL).status_code == 404


def test_a_typed_message_streams_and_is_logged(call):
    r = call.client.post(f"/api/conversations/{call.agent['id']}/turn/stream", headers=LOCAL, data={"text": "Is Thursday 6 pm free?"})
    events = parse(r)
    assert events[0][0] == "transcript" and events[0][1]["user_text"] == "Is Thursday 6 pm free?"
    conv = events[-1][1]["conversation_id"]
    assert ce.since(call.agent["id"], 0, conv)[0]["typed"] is True


def test_language_and_script_are_labelled_honestly():
    mr = ce.utterance_language("मला गुरुवारी संध्याकाळी सहा वाजता डॉक्टर हवे आहे", "hi")
    assert (mr["language"], mr["script"], mr["unsure"]) == ("mr", "devanagari", False) and {w[1] for w in mr["words"]} == {"mr"}
    hi = ce.utterance_language("मुझे गुरुवार शाम को appointment चाहिए", "hi")
    assert hi["script"] == "mixed" and dict((w, l) for w, l in hi["words"])["appointment"] == "en" and hi["language"] == "hi"
    unsure = ce.utterance_language("गुरुवार", "hi")
    assert unsure["unsure"] is True and unsure["words"] == [["गुरुवार", "x"]]       # no marker word: not claimed
    assert ce.utterance_language("hello", "en", "low")["unsure"] is True            # a shaky recognition says so


def test_the_log_is_bounded_and_wakes_waiters():
    for i in range(ce.RING + 5):
        ce.emit("a1", "c", "t", "agent", text=str(i))
    rows = ce.since("a1", 0, limit=10_000)
    assert len(rows) == ce.RING and rows[0]["text"] == "5"
    seen = []
    off = ce.subscribe("a1", seen.append)
    ce.emit("a1", "c", "t", "agent", text="x")
    off()
    ce.emit("a1", "c", "t", "agent", text="y")
    assert [e["text"] for e in seen] == ["x"] and ce.emit(None, "c", "t", "agent") is None


# ---------------------------------------------------------------------------------------------- the evidence

def test_the_booking_collects_its_evidence(call):
    _, done = book_through(call)
    first, second, third = call.turns[0], call.turns[1], call.turns[2]
    ev = call.client.get("/api/bookings/A11/evidence", headers=LOCAL)
    assert ev.status_code == 200, ev.text
    ev = ev.json()
    by_field = {i["field"]: i for i in ev["items"] if i["role"] == "field"}
    assert by_field["name"]["turn_id"] == second and by_field["phone"]["turn_id"] == second
    assert by_field["name"]["basis"] == "text" and by_field["name"]["transcript"].startswith("5:30 then, I am Priya Sharma")
    assert by_field["schedule"]["turn_id"] in (first, second)
    [readback] = [i for i in ev["items"] if i["role"] == "readback"]
    [confirm] = [i for i in ev["items"] if i["role"] == "confirm"]
    assert readback["turn_id"] == second and readback["speaker"] == "agent" and "Shall I book it?" in readback["transcript"]
    assert confirm["turn_id"] == third and confirm["transcript"] == "yes please" and confirm["speaker"] == "caller"
    for item in (by_field["name"], readback, confirm):
        assert item["has_audio"] and item["audio_available"] and len(item["audio_sha256"]) == 64 and len(item["item_sha256"]) == 64
    assert ev["verified"] is True and ev["complete"] is True and ev["public"] is False and ev["booking_id"] == "A11"
    assert "9876543210" not in json.dumps(ev) and "••••3210" in by_field["phone"]["transcript"]     # a spoken number is masked even for the owner
    clip = call.client.get(f"/api/bookings/A11/evidence/audio/{confirm['id']}", headers=LOCAL)
    assert clip.status_code == 200 and hashlib.sha256(clip.content).hexdigest() == confirm["audio_sha256"]
    row = next(b for b in call.client.get("/api/bookings", headers=LOCAL).json()["bookings"] if b["id"] == "A11")
    assert row["has_evidence"] and row["evidence_id"] == ev["evidence_id"] and row["receipt_id"] == ev["receipt_id"]
    assert row["conversation_id"] == call.conv


def test_the_proof_cites_the_words_that_gave_each_value(desk, monkeypatch):
    """"Is Thursday 6 pm free?" then "7 pm then, I am Priya Sharma": the time is 19:00 and it comes from "7 pm then", not
    from the 6 pm of the question; the date comes from the earlier "Thursday"; the read-back is the turn that read 7 pm back."""
    call = Call(desk, monkeypatch, time="19:00")
    first, _ = call.say("Is Thursday 6 pm free?"), None
    call.say("7 pm then, I am Priya Sharma, 9876543210")
    call.say("yes please")
    one, two, three = call.turns
    ev = call.client.get("/api/bookings/A11/evidence", headers=LOCAL).json()
    by_field = {i["field"]: i for i in ev["items"] if i["role"] == "field"}
    assert by_field["time"]["turn_id"] == two and by_field["time"]["transcript"].startswith("7 pm then")
    assert by_field["time"]["basis"] == "text"
    assert by_field["date"]["turn_id"] == one and by_field["date"]["transcript"] == "Is Thursday 6 pm free?"
    assert by_field["name"]["turn_id"] == two and by_field["phone"]["turn_id"] == two
    [readback] = [i for i in ev["items"] if i["role"] == "readback"]
    assert readback["turn_id"] == two and "7 pm" in readback["transcript"] and "6 pm" not in readback["transcript"]
    assert "Priya Sharma" in readback["transcript"] and "3 2 1 0" in readback["transcript"] and readback["transcript"].rstrip().endswith("?")
    [confirm] = [i for i in ev["items"] if i["role"] == "confirm"]
    assert confirm["turn_id"] == three and ev["complete"] is True


def test_an_offer_is_read_back_properly_before_the_booking(desk, monkeypatch):
    """The agent offers 5:30 or 6:30; the caller picks one. Nothing is booked until the exact booking has been read back."""
    call = Call(desk, monkeypatch)
    call.say("Is Thursday 6 pm free?")
    _, picked = call.say("5:30 then, I am Priya Sharma, 9876543210")
    assert not any(b["name"] == "Priya Sharma" for b in sb._ap().all_bookings())          # the pick alone booked nothing
    [tool] = [r for r in call.log("tool", picked["turn_id"]) if r["phase"] == "end"]
    assert tool["status"] == "readback"
    _, yes = call.say("yes please")
    assert any(b["name"] == "Priya Sharma" for b in sb._ap().all_bookings())
    assert [r["status"] for r in call.log("tool", yes["turn_id"]) if r["phase"] == "end"] == ["booked"]


def test_a_no_after_the_read_back_does_not_book(desk, monkeypatch):
    call = Call(desk, monkeypatch)
    call.say("Is Thursday 6 pm free?")
    call.say("5:30 then, I am Priya Sharma, 9876543210")
    _, no = call.say("no wait, make it half past six")
    assert not any(b["name"] == "Priya Sharma" for b in sb._ap().all_bookings())
    assert [r["status"] for r in call.log("tool", no["turn_id"]) if r["phase"] == "end"] == ["readback"]


def test_identity_numbers_are_never_kept(call):
    """An Aadhaar-style number, a card and an OTP said on the call are gone from the stored turn, the events, the kept
    utterance and the model's prompt; the phone number stays, masked in the events as before."""
    said = ("Is Thursday 6 pm free? My number is 98765 43210, aadhaar 2345 6789 0123, card 4111 1111 1111 1111 and my OTP is 482913")
    _, done = call.say(said)
    from core import state
    turns = state.local_db.select("conversation_turns", lambda r: r.get("conversation_id") == call.conv)
    stored = " ".join(str(t.get("text_content")) for t in turns)
    assert "[ID number removed]" in stored and "98765 43210" in stored
    for raw in ("2345", "6789", "0123", "4111", "482913"):
        assert raw not in stored
    events = json.dumps(call.log(), ensure_ascii=False)
    assert "[ID number removed]" in events and "98765 43210" not in events and "••••3210" in events
    for raw in ("2345", "6789", "4111", "482913"):
        assert raw not in events
    prompt = json.dumps(call.model.requests, ensure_ascii=False)
    for raw in ("2345 6789 0123", "4111 1111 1111 1111", "482913"):
        assert raw not in prompt
    sidecars = "".join(open(f"{be._utterances()}/{n}", encoding="utf-8").read() for n in os.listdir(be._utterances()) if n.endswith(".json"))
    assert "[ID number removed]" in sidecars and "2345" not in sidecars and "482913" not in sidecars
    assert not os.path.exists(f"{be._utterances()}/{done['turn_id']}.wav")         # the clip has the number in its sound: not kept


def test_the_receipt_seals_the_evidence_and_one_changed_byte_breaks_it(call):
    book_through(call)
    ev = call.client.get("/api/bookings/A11/evidence", headers=LOCAL).json()
    receipt = provenance.get_receipt(ev["receipt_id"])
    assert receipt["kind"] == "booking" and receipt["turn_id"] == call.turns[2] and provenance.signature_valid(receipt)
    assert provenance.signature_ed25519_valid(receipt) is not False
    assert receipt["text_sha256"] == hashlib.sha256(json.dumps(
        {"manifest_sha256": ev["manifest_sha256"], "booking_id": "A11"}, sort_keys=True).encode()).hexdigest()
    public = call.client.get(f"/api/receipts/{ev['receipt_id']}").json()
    assert public["kind"] == "booking" and public["signature_valid"] is True
    assert be.verify(ev["evidence_id"])["ok"] is True
    folder = be._dir(ev["evidence_id"])
    clip = next(i for i in ev["items"] if i["role"] == "confirm")
    path = f"{folder}/{clip['id']}.wav"
    data = bytearray(open(path, "rb").read())
    data[-1] ^= 0x01                                                    # one byte
    open(path, "wb").write(bytes(data))
    result = be.verify(ev["evidence_id"])
    assert result["ok"] is False and result["reason"] == "tampered"
    assert [i["audio_ok"] for i in result["items"] if i["id"] == clip["id"]] == [False]
    assert call.client.get("/api/bookings/A11/evidence", headers=LOCAL).json()["verified"] is False


def test_a_changed_transcript_or_manifest_breaks_it_too(call):
    book_through(call)
    eid = call.client.get("/api/bookings/A11/evidence", headers=LOCAL).json()["evidence_id"]
    path = f"{be._dir(eid)}/manifest.json"
    original = open(path).read()
    open(path, "w").write(original.replace("yes please", "yes pleasd"))
    assert be.verify(eid)["ok"] is False
    open(path, "w").write(original)
    assert be.verify(eid)["ok"] is True
    open(path, "w").write(original.replace('"manifest_sha256": "', '"manifest_sha256": "0'))
    assert be.verify(eid)["ok"] is False


def test_only_the_owner_reads_the_evidence(call):
    book_through(call)
    ev = call.client.get("/api/bookings/A11/evidence", headers=LOCAL).json()
    other = {**LOCAL, "x-forwarded-for": "203.0.113.9"}
    for path in ("/api/bookings/A11/evidence", f"/api/bookings/A11/evidence/audio/{ev['items'][0]['id']}"):
        assert call.client.get(path, headers=other).status_code == 401
    assert call.client.post("/api/bookings/A11/evidence/public", headers=other, json={"public": True}).status_code == 401
    assert call.client.get("/api/bookings/A10/evidence", headers=LOCAL).status_code == 404          # a staff booking has none
    assert call.client.get("/api/bookings/A99/evidence", headers=LOCAL).status_code == 404
    meta_path = f"{be._dir(ev['evidence_id'])}/meta.json"
    meta = json.load(open(meta_path))
    json.dump({**meta, "user_id": "someone-else"}, open(meta_path, "w"))
    assert call.client.get("/api/bookings/A11/evidence", headers=LOCAL).status_code == 404         # another owner's proof


def test_the_public_sees_redacted_transcripts_and_no_audio_until_the_owner_allows_it(call):
    book_through(call)
    ev = call.client.get("/api/bookings/A11/evidence", headers=LOCAL).json()
    rid = ev["receipt_id"]
    pub = call.client.get(f"/api/receipts/{rid}/booking")                      # no sign-in
    assert pub.status_code == 200
    body = pub.json()
    text = json.dumps(body, ensure_ascii=False)
    assert "Priya" not in text and "Sharma" not in text and "9876543210" not in text and "Asha" not in text
    assert body["public"] is False and body["verified"] is True and body["items"]
    assert all(i["audio_available"] is False for i in body["items"]) and all(len(i["transcript_sha256"]) == 64 for i in body["items"])
    assert {i["transcript"] for i in body["items"] if i["field"] in ("name", "phone")} == \
        {"(the caller said their name)", "(the caller said their phone number)"}
    readback = next(i for i in body["items"] if i["role"] == "readback")
    assert "▒▒▒" in readback["transcript"] and "Shall I book it?" in readback["transcript"]
    audio = f"/api/receipts/{rid}/booking/audio/{readback['id']}"
    assert call.client.get(audio).status_code == 404
    made = call.client.post("/api/bookings/A11/evidence/public", headers=LOCAL, json={"public": True})
    assert made.status_code == 200 and made.json()["public"] is True
    body = call.client.get(f"/api/receipts/{rid}/booking").json()
    assert body["public"] is True and all(i["audio_available"] for i in body["items"] if i["has_audio"])
    assert "Priya Sharma" in next(i for i in body["items"] if i["role"] == "readback")["transcript"]
    clip = call.client.get(audio)
    assert clip.status_code == 200 and hashlib.sha256(clip.content).hexdigest() == readback["audio_sha256"]
    assert call.client.get(f"/api/receipts/{rid}/booking/audio/i999").status_code == 404
    assert call.client.get("/api/receipts/not-a-receipt/booking").status_code == 404


def test_recordings_are_deleted_after_the_retention_period_and_the_hashes_stay(call, monkeypatch):
    book_through(call)
    ev = call.client.get("/api/bookings/A11/evidence", headers=LOCAL).json()
    removed = be.purge_expired()
    assert removed["evidence"] == 0 and be.verify(ev["evidence_id"])["ok"] is True
    from datetime import datetime, timedelta, timezone
    removed = be.purge_expired(datetime.now(timezone.utc) + timedelta(days=be.retention_days() + 1))
    assert removed["evidence"] == 1
    after = call.client.get("/api/bookings/A11/evidence", headers=LOCAL).json()
    assert after["deleted"] is True and after["verified"] is True and all(i["transcript"] == "" for i in after["items"])
    assert all(not i["audio_available"] for i in after["items"]) and after["items"][0]["transcript_sha256"]


def test_without_schedules_nothing_is_kept(rig, monkeypatch):
    agent = make_agent(rig.client, disclose_ai=False)
    run = types.SimpleNamespace(local=True, agent={"id": agent["id"], "tools": []}, agent_id=agent["id"])
    assert be.wants_audio(run) is False
    monkeypatch.setenv("BOOKING_EVIDENCE", "off")
    assert be.wants_audio(types.SimpleNamespace(local=True, agent={"tools": ["schedules"]})) is False


def test_a_clip_keeps_the_sample_rate_it_is_given(call, tmp_path):
    call.say("hello there")
    eight = np.sin(np.linspace(0, 200, 8000)).astype("float32") * 0.2
    import io
    buf = io.BytesIO()
    sf.write(buf, eight, 8000, format="WAV", subtype="PCM_16")
    run = types.SimpleNamespace(local=True, agent={"id": call.agent["id"], "tools": ["schedules"]}, agent_id=call.agent["id"],
                                conversation_id=call.conv, user_id="u", user_text="phone words", language="en",
                                caller_found={"name": "X"}, utterance_bytes=buf.getvalue())
    record = be.commit_utterance(run, "11111111-1111-1111-1111-111111111111")
    assert record["audio"]["sr"] == 8000 and record["audio"]["duration_s"] == 1.0
    assert sf.info(f"{be._utterances()}/11111111-1111-1111-1111-111111111111.wav").samplerate == 8000
    web = call.client  # the web call's recordings are decoded at 16 kHz or kept as the page sent them (the rig sends a WAV)
    assert web is not None and record["found"] == ["name"]

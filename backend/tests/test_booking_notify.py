"""The WhatsApp outbox (services/booking_notify.py): consent, collapse, one message per change, timing, restart.
No test reaches Meta: services/whatsapp._http is faked (tests/conftest guard refuses it otherwise)."""
import logging
import os
import re
import time

import pytest

from services import booking_notify as bn
from services import whatsapp

DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs", "WHATSAPP.md")
TOKEN = "EAAB-test-token-1234567890"


def booking(bid="A12", time_="15:00", version=1, phone="9876543210", **over):
    row = {"booking_id": bid, "name": "Ravi", "phone": phone, "date": "2026-11-06", "time": time_,
           "start": f"2026-11-06T{time_}:00+05:30", "schedule_name": "Clinic", "agent_id": "agent-x", "status": "booked",
           "version": version}
    row.update(over)
    return row


@pytest.fixture
def wa(monkeypatch):
    """WhatsApp set up, with a fake Meta that records what it was sent."""
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "100200300")
    monkeypatch.setenv("WHATSAPP_SEND_DELAY_S", "30")
    class Sent(list):
        fail = {"error": None}
    sent = Sent()
    fail = sent.fail

    def fake_http(method, url, *, headers=None, json_body=None):
        sent.append({"url": url, "headers": headers, "body": json_body})
        if fail["error"]:
            return 400, {"error": fail["error"]}
        return 200, {"messages": [{"id": f"wamid.{len(sent)}"}]}
    monkeypatch.setattr(whatsapp, "_http", fake_http)
    return sent


def agree(cid="c1"):
    bn.bind_call(cid)
    bn.record_consent(True)


def end_and_flush(cid):
    bn.call_ended(cid)
    return bn.flush_due(time.time() + 31)


def rows():
    return bn.recent_messages()


# ------------------------------------------------------------------------------------------------ consent

def test_first_change_asks_then_remembers_the_answer(wa):
    bn.bind_call("c1")
    assert bn.booking_changed("booked", booking()) == {"status": "ask", "number_ending": "3210"}
    assert bn.booking_changed("moved", booking(time_="16:00", version=2, moved_from={"date": "2026-11-06", "time": "15:00"})) \
        == {"status": "queued"}                                      # asked once on this call
    assert bn.record_consent(True)["status"] == "recorded"
    bn.bind_call("c2")                                               # another call, same number
    assert bn.booking_changed("moved", booking(time_="17:00", version=3,
                                               moved_from={"date": "2026-11-06", "time": "16:00"})) == {"status": "queued"}
    assert bn.record_consent(True)["status"] == "no_question_pending"
    stored = bn._load()["consent"]["9876543210"]
    assert stored["agreed"] is True and stored["conversation_id"] == "c1" and stored["at"]


def test_a_no_drops_what_is_waiting_and_is_remembered(wa):
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    assert bn.record_consent(False) == {"status": "recorded", "agreed": False, "number_ending": "3210"}
    assert bn.flush_due(time.time() + 1000) == 0 and wa == []
    assert [r["state"] for r in rows()] == ["skipped"]
    bn.bind_call("c2")
    assert bn.booking_changed("booked", booking(bid="B1")) == {"status": "off"}
    assert bn.record_consent(True)["status"] == "no_question_pending"


def test_no_answer_by_send_time_means_no_message(wa):
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    assert end_and_flush("c1") == 1 and wa == []
    assert rows()[0]["state"] == "skipped" and "no consent" in rows()[0]["reason"]


def test_not_wanted_or_bad_number_is_off(wa, monkeypatch):
    assert bn.booking_changed("booked", booking(phone="12345")) == {"status": "off"}
    assert bn.booking_changed("rescheduled", booking()) == {"status": "off"}
    monkeypatch.setenv("WHATSAPP_CONFIRMATIONS", "off")
    assert bn.booking_changed("booked", booking()) == {"status": "off"}


def test_unconfigured_and_preview_off_is_off(monkeypatch):
    monkeypatch.setenv("WHATSAPP_PREVIEW", "off")
    assert bn.booking_changed("booked", booking()) == {"status": "off"}


# ------------------------------------------------------------------------------------------------ collapse

def test_book_then_move_on_one_call_sends_one_final_message(wa):
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    agree()
    bn.booking_changed("moved", booking(time_="16:00", version=2, moved_from={"date": "2026-11-06", "time": "15:00"}),
                       booking())
    assert end_and_flush("c1") == 1
    assert len(wa) == 1
    params = [p["text"] for p in wa[0]["body"]["template"]["components"][0]["parameters"]]
    assert wa[0]["body"]["template"]["name"] == "vakya_booking_confirmed"
    assert params == ["Clinic", "Ravi", "Fri 6 Nov, 4:00 pm", "A12"]
    assert rows()[0]["text"].startswith("Hello! Your booking at") and "4:00 pm" in rows()[0]["text"] and "3:00" not in rows()[0]["text"]


def test_book_then_cancel_on_one_call_sends_nothing(wa):
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    agree()
    bn.booking_changed("cancelled", booking(version=2, status="cancelled"))
    assert end_and_flush("c1") == 0 and wa == [] and rows() == []


def test_book_then_cancel_sends_cancelled_when_the_booking_was_already_confirmed(wa):
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    agree()
    bn.flush_due(time.time() + 1000)                  # the backstop fired mid-call: "Booked" went out
    bn.booking_changed("booked", booking(bid="Z9"))   # (another item keeps the call alive)
    bn.booking_changed("cancelled", booking(version=2, status="cancelled"))
    end_and_flush("c1")
    kinds = [(r["booking_id"], r["kind"]) for r in reversed(rows())]
    assert ("A12", "booked") in kinds and ("A12", "cancelled") in kinds


def test_move_then_move_is_one_message_from_the_original_time(wa):
    agree_first(wa)
    bn.bind_call("c2")
    bn.booking_changed("moved", booking(time_="16:00", version=2, moved_from={"date": "2026-11-06", "time": "15:00"}))
    bn.booking_changed("moved", booking(time_="17:30", version=3, moved_from={"date": "2026-11-06", "time": "16:00"}))
    end_and_flush("c2")
    text = rows()[0]["text"]
    assert rows()[0]["kind"] == "moved" and "from Fri 6 Nov, 3:00 pm to Fri 6 Nov, 5:30 pm" in text


def test_move_and_back_is_nothing(wa):
    agree_first(wa)
    bn.bind_call("c2")
    bn.booking_changed("moved", booking(time_="16:00", version=2, moved_from={"date": "2026-11-06", "time": "15:00"}))
    bn.booking_changed("moved", booking(time_="15:00", version=3, moved_from={"date": "2026-11-06", "time": "16:00"}))
    assert end_and_flush("c2") == 0 and len(wa) == 1


def test_move_then_cancel_cancels_the_original_time(wa):
    agree_first(wa)
    bn.bind_call("c2")
    bn.booking_changed("moved", booking(time_="16:00", version=2, moved_from={"date": "2026-11-06", "time": "15:00"}))
    bn.booking_changed("cancelled", booking(time_="16:00", version=3, status="cancelled"))
    end_and_flush("c2")
    assert rows()[0]["kind"] == "cancelled" and "3:00 pm" in rows()[0]["text"]


def agree_first(wa):
    """Call c1 booked at 3 pm and agreed; its message is sent."""
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    agree("c1")
    end_and_flush("c1")
    assert len(wa) == 1


# ------------------------------------------------------------------------------------------------ three calls

def test_book_then_move_then_cancel_across_three_calls(wa):
    agree_first(wa)
    bn.bind_call("c2")
    assert bn.booking_changed("moved", booking(time_="16:00", version=2,
                                               moved_from={"date": "2026-11-06", "time": "15:00"})) == {"status": "queued"}
    end_and_flush("c2")
    bn.bind_call("c3")
    bn.booking_changed("cancelled", booking(time_="16:00", version=3, status="cancelled"))
    end_and_flush("c3")
    texts = [r["text"] for r in reversed(rows())]
    assert [r["state"] for r in rows()] == ["sent"] * 3
    assert re.match(r"Hello! Your booking at .* for Ravi is confirmed: Fri 6 Nov, 3:00 pm\. Booking ID: A12\.", texts[0])
    assert re.match(r"Hello! Your booking at .* for Ravi has been moved from Fri 6 Nov, 3:00 pm to Fri 6 Nov, 4:00 pm\. Booking ID: A12\.", texts[1])
    assert re.match(r"Hello! Your booking at .* for Ravi on Fri 6 Nov, 4:00 pm has been cancelled\. Booking ID: A12\.", texts[2])
    assert [w["body"]["template"]["name"] for w in wa] == ["vakya_booking_confirmed", "vakya_booking_moved",
                                                            "vakya_booking_cancelled"]
    assert wa[0]["body"]["to"] == "919876543210" and wa[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert wa[0]["url"] == "https://graph.facebook.com/v27.0/100200300/messages"


# ------------------------------------------------------------------------------------------------ idempotency and timing

def test_a_duplicate_hangup_or_retry_never_sends_twice(wa):
    agree_first(wa)
    assert bn.call_ended("c1") == 0 and bn.flush_due(time.time() + 1000) == 0
    bn.bind_call("c2")
    bn.booking_changed("booked", booking())                  # the same booking and version again (a retried tool call)
    end_and_flush("c2")
    bn.call_ended("c2")
    assert bn.flush_due(time.time() + 1000) == 0
    assert len(wa) == 1


def test_a_call_that_never_ends_is_sent_at_the_backstop(wa, monkeypatch):
    monkeypatch.setenv("WHATSAPP_BACKSTOP_S", "300")
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    agree()
    assert bn.flush_due(time.time() + 250) == 0 and wa == []
    assert bn.flush_due(time.time() + 301) == 1 and len(wa) == 1


def test_the_hangup_delay_is_waited_for(wa):
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    agree()
    bn.call_ended("c1")
    assert bn.flush_due(time.time() + 5) == 0
    assert bn.flush_due(time.time() + 31) == 1


def test_a_timer_sends_after_the_delay(wa, monkeypatch):
    monkeypatch.setenv("WHATSAPP_SEND_DELAY_S", "0.1")
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    agree()
    bn.call_ended("c1")
    end = time.time() + 3
    while not wa and time.time() < end:
        time.sleep(0.02)
    assert len(wa) == 1


def test_a_call_that_ended_before_the_change_still_sends_after_the_delay(wa):
    bn.call_ended("c1")
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    agree()
    assert bn.flush_due(time.time() + 31) == 1


def test_a_restart_reschedules_what_was_pending(wa, monkeypatch):
    monkeypatch.setenv("WHATSAPP_SEND_DELAY_S", "0.1")
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    agree()
    bn.call_ended("c1")
    bn.reset()                                            # the process dies: timers are gone, the file stays
    assert wa == []
    assert bn.resume() == 1
    end = time.time() + 3
    while not wa and time.time() < end:
        time.sleep(0.02)
    assert len(wa) == 1


# ------------------------------------------------------------------------------------------------ desk changes

def test_a_staff_change_needs_consent_already_given(wa, monkeypatch):
    monkeypatch.setenv("WHATSAPP_SEND_DELAY_S", "30")
    assert bn.booking_changed("moved", booking(time_="16:00", version=2), booking(), source="staff") == {"status": "off"}
    assert wa == [] and rows()[0]["state"] == "skipped"
    agree_first(wa)
    wa.clear()
    assert bn.booking_changed("moved", booking(time_="16:00", version=2, moved_from={"date": "2026-11-06", "time": "15:00"}),
                              booking(), source="staff") == {"status": "queued"}
    assert bn.flush_due(time.time() + 5) == 0
    assert bn.flush_due(time.time() + 31) == 1 and len(wa) == 1
    assert "from Fri 6 Nov, 3:00 pm to Fri 6 Nov, 4:00 pm" in rows()[0]["text"] and rows()[0]["source"] == "staff"


# ------------------------------------------------------------------------------------------------ preview and errors

def test_unconfigured_builds_a_preview_that_is_never_called_sent():
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    agree()
    end_and_flush("c1")
    r = rows()[0]
    assert r["state"] == "preview" and r["message_id"] is None and r["text"].startswith("Hello! Your booking at")
    assert whatsapp.configured() is False


def test_a_meta_error_is_a_failed_row_and_the_token_is_never_logged(wa, caplog):
    wa.fail["error"] = {"message": f"bad token {TOKEN}", "type": "OAuthException", "code": 190, "fbtrace_id": "x"}
    caplog.set_level(logging.DEBUG)
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    agree()
    end_and_flush("c1")
    r = rows()[0]
    assert r["state"] == "failed" and "190" in r["error"] and TOKEN not in r["error"]
    assert TOKEN not in caplog.text and TOKEN not in open(bn.file_path(), encoding="utf-8").read()
    assert bn.flush_due(time.time() + 1000) == 0 and len(wa) == 1     # a failed message is not retried into a duplicate


# ------------------------------------------------------------------------------------------------ words

def test_family_booking_is_worded_for_the_relative_in_each_language():
    item = {"booking_id": "A1", "kind": "booked",
            "snap": {"name": "Sunita", "relation": "mother", "date": "2026-11-06", "time": "15:00"}}
    assert bn.compose(item, "en", "Clinic")[1] == "Hello! Your booking at Clinic for your mother, Sunita is confirmed: Fri 6 Nov, 3:00 pm. Booking ID: A1. To change or cancel it, just call us again."
    assert bn.compose(item, "hi", "Clinic")[1] == "नमस्ते! Clinic में आपकी माँ, Sunita की बुकिंग पक्की हो गई है: शुक्र 6 नवंबर, दोपहर 3:00 बजे। बुकिंग आईडी: A1। बदलने या रद्द करने के लिए हमें दोबारा कॉल करें।"
    assert bn.compose(item, "mr", "Clinic")[1] == "नमस्कार! Clinic येथे तुमची आई, Sunita यांचे बुकिंग निश्चित झाले आहे: शुक्र 6 नोव्हेंबर, दुपारी 3:00 वाजता. बुकिंग आयडी: A1. बदल किंवा रद्द करण्यासाठी आम्हाला पुन्हा कॉल करा."


def test_a_stay_says_the_nights_and_the_language_follows_the_conversation(wa, monkeypatch):
    item = {"booking_id": "S1", "kind": "booked", "snap": {"name": "Ravi", "date": "2026-11-06", "time": "14:00", "nights": 2}}
    assert "Fri 6 Nov, 2 nights" in bn.compose(item, "en", "Hotel")[1]
    monkeypatch.setattr(bn, "_language", lambda item, owner: "hi")
    bn.bind_call("c1")
    bn.booking_changed("booked", booking())
    agree()
    end_and_flush("c1")
    assert rows()[0]["language"] == "hi" and wa[0]["body"]["template"]["language"] == {"code": "hi"}


def test_the_docs_hold_the_exact_template_texts():
    doc = open(DOCS, encoding="utf-8").read()
    for kind, by_lang in bn.TEXTS.items():
        for lang, text in by_lang.items():
            assert re.sub(r"\{(\d+)\}", r"{{\1}}", text) in doc, (kind, lang)


def test_no_template_text_starts_or_ends_with_a_parameter():
    """Meta's template review refuses a body that starts or ends with a parameter."""
    for kind, langs in bn.TEXTS.items():
        for lang, text in langs.items():
            assert not text.startswith("{") and not re.search(r"\{\d\}\W*$", text), (kind, lang)


def test_a_scheduling_tool_on_a_call_is_bound_to_that_call():
    """services/turn_pipeline.py wraps every scheduling tool so a change made by it knows its call, then unbinds."""
    from types import SimpleNamespace
    from services import turn_pipeline as tp
    seen = []
    spec = SimpleNamespace(runner=lambda tool, args: (seen.append(bn.current_call_id()), (True, {}))[1])
    run = SimpleNamespace(conversation_id="conv-7", emit_tool_start=lambda tool, args: None)
    before = bn.current_call_id()
    tp._observed(spec, run).runner("book", {})
    assert seen == ["conv-7"] and bn.current_call_id() == before

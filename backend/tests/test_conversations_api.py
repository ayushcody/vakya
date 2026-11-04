"""Calls and bookings (routers/conversations.py): channel, masked caller, derived outcome and the transcript, from the
local store, owner-scoped and read-only."""
from datetime import datetime, timedelta, timezone

from routers import conversations as c
from tests.test_offline_agents import LOCAL, env, make_agent  # noqa: F401  (env is a fixture)

NO_INFO = "I'm sorry, I don't have that information. I can connect you with a member of staff."


def iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def talk(env, agent_id, *lines, source=None, minutes_ago=10, user="local-user", is_test=False, **conv):
    """Store a conversation. Each line is (role, text) or (role, text, extra turn fields)."""
    row = {"agent_id": agent_id, "user_id": user, "created_at": iso(minutes_ago),
           **({"source": source} if source else {}), **({"is_test": True} if is_test else {}), **conv}
    cid = env.local_db.insert("conversations", row)["id"]
    for i, line in enumerate(lines):
        role, text, *extra = line
        env.local_db.insert("conversation_turns", {"conversation_id": cid, "role": role, "text_content": text,
                                                   "created_at": iso(minutes_ago - 0.1 * (i + 1)), **(extra[0] if extra else {})})
    return cid


def calls(client, **params):
    r = client.get("/api/calls", headers=LOCAL, params=params)
    assert r.status_code == 200, r.text
    return r.json()["calls"]


GREETING = ("agent", "This is an AI assistant. How can I help?")


def test_channels_and_masked_callers(app_client, env):
    agent = make_agent(app_client)
    a = agent["id"]
    talk(env, a, ("user", "What are your timings today?"), ("agent", "We open at nine."), minutes_ago=50)
    talk(env, a, ("user", "Fees please for the course"), ("agent", "The fee is 500."), source="web", minutes_ago=40)
    talk(env, a, ("user", "Do you open on Sunday?"), ("agent", "Closed on Sunday."), source="phone", minutes_ago=30)
    talk(env, a, ("user", "Hello"), ("agent", "Hi"), is_test=True, minutes_ago=20)
    by = {x["channel"]: x for x in calls(app_client)}
    assert set(by) == {"playground", "website", "phone"}
    assert by["phone"]["caller"] is None and "9876543210" not in str(by["phone"])
    assert by["website"]["caller"] is None and by["website"]["duration_s"] is None
    rows = calls(app_client)
    assert [r["practice"] for r in rows] == [True, False, False, False]  # newest first
    assert rows[0]["agent_name"] == agent["name"]


def test_outcomes_are_derived_from_what_is_stored(app_client, env):
    a = make_agent(app_client)["id"]
    book = {"tool_calls": [{"name": "book_appointment", "arguments": {"phone": "9876543210"}, "ok": True,
                            "result": "booking A12 with Dr. Meera Kulkarni on Saturday 2026-10-17 12:30"}]}
    failed = {"tool_calls": [{"name": "book_appointment", "arguments": {}, "ok": False, "result": "failed: nope"}]}
    ids = {
        "booked": talk(env, a, ("user", "I want to book with Dr Meera"), ("agent", "Booked.", book), minutes_ago=90),
        "answered": talk(env, a, ("user", "When do you open today?"), ("agent", "At nine."), minutes_ago=80),
        "couldnt": talk(env, a, ("user", "Do you treat dogs here?"), ("agent", NO_INFO), minutes_ago=70),
        "failed_tool": talk(env, a, ("user", "Book me please now"), ("agent", "I could not.", failed), minutes_ago=60),
        "ended": talk(env, a, ("user", "Hello"), GREETING, source="phone", minutes_ago=50),
        "silent": talk(env, a, source="phone", minutes_ago=40),
        "handoff": talk(env, a, ("user", "I want a person"), ("agent", "Sure."), source="phone",
                        needs_human=True, minutes_ago=30),
    }
    out = {cid: x["outcome"]["key"] for cid, x in ((x["id"], x) for x in calls(app_client))}
    assert out[ids["booked"]] == "booked"
    assert out[ids["answered"]] == "answered"
    assert out[ids["couldnt"]] == "couldnt_answer"
    assert out[ids["failed_tool"]] == "answered"  # a failed tool is not a booking and not a gap
    assert out[ids["ended"]] == "ended_early" and out[ids["silent"]] == "ended_early"
    assert out[ids["handoff"]] == "handed_off"
    detail = next(x for x in calls(app_client) if x["id"] == ids["couldnt"])["outcome"]["detail"]
    assert "dogs" in detail


def test_phone_duration_is_only_shown_for_calls(app_client, env):
    a = make_agent(app_client)["id"]
    talk(env, a, ("user", "I would like to book an appointment"), ("agent", "Sure, which doctor?"),
         ("user", "Dr Meera on Saturday morning"), ("agent", "Done."), source="phone", minutes_ago=5)
    row = calls(app_client)[0]
    assert row["channel"] == "phone" and row["caller"] is None  # the number of a caller on the phone is not stored
    assert 0 < row["duration_s"] < 60 and row["outcome"]["key"] == "answered" and row["replies"] == 2


def test_transcript_masks_numbers_and_hides_tool_arguments(app_client, env):
    a = make_agent(app_client)["id"]
    book = {"tool_calls": [{"name": "book_appointment", "arguments": {"phone": "9876543210"}, "ok": True,
                            "result": "booking A5 with Dr. Meera on Saturday 2026-10-17 12:30"}]}
    cid = talk(env, a, ("user", "My number is 9876543210 and also 878899625"), ("agent", "Booked for 2026-10-17.", book))
    r = app_client.get(f"/api/calls/{cid}", headers=LOCAL)
    assert r.status_code == 200
    body = r.json()
    t = body["transcript"]
    assert t[0] == {"role": "caller", "text": "My number is ••••3210 and also ••••9625", "at": t[0]["at"], "actions": []}
    assert t[1]["text"] == "Booked for 2026-10-17."  # a date is not a number
    assert t[1]["actions"] == [{"name": "book_appointment", "ok": True,
                                "summary": "booking A5 with Dr. Meera on Saturday 2026-10-17 12:30"}]
    assert "9876543210" not in r.text and "arguments" not in r.text
    assert body["outcome"]["key"] == "booked"


def test_owner_scoping_filters_window_and_unknown_ids(app_client, env):
    a = make_agent(app_client)["id"]
    b = make_agent(app_client)["id"]
    mine = talk(env, a, ("user", "How much is the fee?"), ("agent", "Five hundred."))
    talk(env, a, ("user", "someone else's chat"), ("agent", "ok"), user="another-user")
    talk(env, b, ("user", "an old chat with b"), ("agent", "ok"), minutes_ago=60 * 24 * 40)
    talk(env, b, ("user", "a new chat with b"), ("agent", "ok"), minutes_ago=3)
    assert len(calls(app_client)) == 2  # the 40 day old one is outside the default 30 days
    assert len(calls(app_client, days=60)) == 3
    assert [x["id"] for x in calls(app_client, agent_id=a)] == [mine]
    assert len(calls(app_client, limit=1)) == 1
    assert app_client.get("/api/calls/does-not-exist", headers=LOCAL).status_code == 404
    other = env.local_db.select("conversations", lambda r: r["user_id"] == "another-user")[0]["id"]
    assert app_client.get(f"/api/calls/{other}", headers=LOCAL).status_code == 404  # not the owner's
    assert app_client.get("/api/calls?days=0", headers=LOCAL).status_code == 422


def test_empty_store_and_nothing_is_written(app_client, env):
    assert calls(app_client) == []
    make_agent(app_client)
    before = (env.local_db.select("conversations"), env.local_db.select("conversation_turns"))
    calls(app_client)
    assert (env.local_db.select("conversations"), env.local_db.select("conversation_turns")) == before


def test_masking_helpers():
    assert c.mask_number("+91 98765 43210") == "••••3210" and c.mask_number("") is None and c.mask_number("12") == "••••"
    assert c.mask_text("call 98765 43210 or +919876543210") == "call ••••3210 or ••••3210"
    assert c.mask_text("slot 2026-10-17 at 10:30, booking A12") == "slot 2026-10-17 at 10:30, booking A12"


# ---------------------------------------------------------------------------------------------- windows, booking, filters

def at(day: str, hhmm: str, zone: str = "+05:30") -> str:
    """An ISO time on a local day (VAKYA_TIMEZONE is Asia/Kolkata in these tests)."""
    return datetime.fromisoformat(f"{day}T{hhmm}:00{zone}").astimezone(timezone.utc).isoformat()


def stored(env, agent_id, when_iso, *lines, **conv):
    row = {"agent_id": agent_id, "user_id": "local-user", "created_at": when_iso, **conv}
    cid = env.local_db.insert("conversations", row)["id"]
    for i, (role, text, *extra) in enumerate(lines):
        env.local_db.insert("conversation_turns", {"conversation_id": cid, "role": role, "text_content": text,
                                                   "created_at": when_iso, **(extra[0] if extra else {})})
    return cid


def test_from_to_are_whole_days_in_the_local_zone_and_both_ends_count(app_client, env, monkeypatch):
    monkeypatch.setenv("VAKYA_TIMEZONE", "Asia/Kolkata")
    a = make_agent(app_client)["id"]
    chat = (("user", "When do you open today?"), ("agent", "At nine."))
    late_night = stored(env, a, at("2026-11-01", "23:50"), *chat)       # 18:20 UTC on 1 Nov
    just_after = stored(env, a, at("2026-11-02", "00:10"), *chat)       # still 1 Nov in UTC
    last_minute = stored(env, a, at("2026-11-02", "23:59"), *chat)
    next_day = stored(env, a, at("2026-11-03", "00:01"), *chat)
    r = app_client.get("/api/calls", headers=LOCAL, params={"from": "2026-11-02", "to": "2026-11-02"}).json()
    assert {x["id"] for x in r["calls"]} == {just_after, last_minute}
    assert (r["from"], r["to"], r["days"], r["timezone"]) == ("2026-11-02", "2026-11-02", 1, "Asia/Kolkata")
    both = app_client.get("/api/calls", headers=LOCAL, params={"from": "2026-11-01", "to": "2026-11-03"}).json()
    assert {x["id"] for x in both["calls"]} == {late_night, just_after, last_minute, next_day} and both["days"] == 3


def test_range_names_and_the_default_window(app_client, env):
    a = make_agent(app_client)["id"]
    talk(env, a, ("user", "Hello there friend"), ("agent", "Hi"), minutes_ago=2)
    for name in ("today", "yesterday", "7d", "30d"):
        r = app_client.get("/api/calls", headers=LOCAL, params={"range": name})
        assert r.status_code == 200 and {"from", "to", "timezone"} <= set(r.json())
    assert len(calls(app_client, range="today")) in (0, 1)     # two minutes ago is today unless the zone's day just turned
    assert len(calls(app_client, range="7d")) == 1 and len(calls(app_client, days=1)) == 1
    wk = app_client.get("/api/calls", headers=LOCAL, params={"range": "7d"}).json()
    only_from = app_client.get("/api/calls", headers=LOCAL, params={"from": wk["from"]}).json()   # no `to`: up to today
    assert (only_from["from"], only_from["to"], only_from["days"]) == (wk["from"], wk["to"], 7)
    assert len(only_from["calls"]) == 1


def test_bad_dates_are_refused_with_a_reason(app_client, env):
    make_agent(app_client)
    for params, code in (({"from": "yesterday"}, "INVALID_DATE"), ({"to": "2026-13-01"}, "INVALID_DATE"),
                         ({"from": "2026-11-05", "to": "2026-11-01"}, "INVALID_RANGE"),
                         ({"from": "2024-01-01", "to": "2026-01-01"}, "INVALID_RANGE"), ({"range": "forever"}, "INVALID_RANGE")):
        r = app_client.get("/api/calls", headers=LOCAL, params=params)
        assert r.status_code == 422 and r.json()["code"] == code, (params, r.text)


def test_booking_text_is_structured_and_readable(app_client, env):
    a = make_agent(app_client)["id"]
    book = {"tool_calls": [{"name": "book", "arguments": {"phone": "9876543210"}, "ok": True,
                            "result": "booking a56 on thursday 2026-11-12 19:00"}]}
    doctor = {"tool_calls": [{"name": "book_appointment", "arguments": {}, "ok": True,
                              "result": "booking A12 with Dr. Meera Kulkarni on Saturday 2026-10-17 12:30"}]}
    talk(env, a, ("user", "Book Thursday at seven"), ("agent", "Done.", book), minutes_ago=20)
    talk(env, a, ("user", "Dr Meera on Saturday"), ("agent", "Done.", doctor), minutes_ago=10)
    rows = {x["booking"]["id"]: x for x in calls(app_client)}
    assert rows["A56"]["booking"] == {"id": "A56", "date": "2026-11-12", "time": "19:00", "with": None}
    assert rows["A56"]["outcome"]["detail"] == "Booking A56 · Thu 12 Nov, 7 pm"
    assert rows["A12"]["booking"]["with"] == "Dr. Meera Kulkarni"
    assert rows["A12"]["outcome"]["detail"] == "Booking A12 · Sat 17 Oct, 12:30 pm"
    assert c.booking_words({"id": "A1", "date": "2026-11-02", "time": "00:05"}) == "A1 · Mon 2 Nov, 12:05 am"
    assert c.booking_words({"id": "A1", "date": None, "time": None}) == "A1"
    assert c.parse_booking("the caller said hello") is None


def test_language_caller_name_and_empty_calls(app_client, env):
    a = make_agent(app_client)["id"]
    talk(env, a, ("user", "नमस्ते मुझे समय चाहिए"), ("agent", "जी बताइए", {"language": "hi"}),
         ("agent", "और कुछ?", {"language": "hi"}), ("agent", "ok", {"language": "en"}), minutes_ago=30,
         caller_details={"name": "Priya Sharma", "phone": "9876543210"})
    silent = talk(env, a, source="phone", minutes_ago=20)
    talk(env, a, ("agent", "Hello, how can I help?"), source="phone", minutes_ago=10)
    rows = calls(app_client)
    said = next(x for x in rows if x["caller_name"])
    assert said["language"] == "hi" and said["caller_name"] == "Priya Sharma" and said["caller"] == "••••3210"
    assert "9876543210" not in str(rows) and said["empty"] is False
    assert [x["empty"] for x in rows] == [False, True, False]            # greeting only is a call; nothing at all is empty
    r = app_client.get("/api/calls", headers=LOCAL, params={"hide_empty": "true"}).json()
    assert silent not in {x["id"] for x in r["calls"]} and len(r["calls"]) == 2 and r["empty_hidden"] == 1
    assert r["truncated"] is False

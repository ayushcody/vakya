"""What Vakya remembers about a caller, by trade and by number: the `remember` schema of each template, the rules and the
model's reading of it (validated per type), what is never kept, the people on one number, profiles written for web calls and
for outbound calls, the greeting for a known caller (inbound and callback), "forget me" and the owner's edits."""
import json
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from routers import phone as px
from services import caller_memory as cm
from services import caller_profiles as cp
from services import phone_greeting as pg
from services import tools
from tests.test_caller_profiles import (BOOKING, GREETING, HELLO, NUMBER, TODAY, profile, seed, speaks,  # noqa: F401
                                        stop, stored, turn)
from tests.test_offline_agents import LOCAL, env, make_agent  # noqa: F401  (env is a fixture)
from tests.test_phone import (STREAM_SID, feed, is_mark, pcm_silence, pcm_tone, read_until, rig as phone_rig,  # noqa: F401
                              start_msg)
from tests.test_phone_greeting import sessions, wait_until  # noqa: F401  (sessions is a fixture)

NOW = datetime(2026, 11, 4, 10, 0)
TEMPLATES = ("clinic", "hotel", "salon", "restaurant", "office", "institute", "service_centre", "fitness")


def schema(tid):
    return cm.schema_for({"template_id": tid})


# ===================================================================================== the schema of each trade

@pytest.mark.parametrize("tid", TEMPLATES)
def test_every_template_says_what_to_remember(tid):
    s = schema(tid)
    assert s["people"] and s["people"]["fields"][0] == "name" and s["people"]["label"]
    assert 2 <= len(s["facts"]) <= cm.MAX_FACTS
    for f in s["facts"]:
        assert f["key"] and f["label"] and f["hint"] and f["type"] in ("text", "number", "enum", "time")
        assert f["type"] != "enum" or f["choices"]
    raw = json.loads(open(f"templates/agents/{tid}.json", encoding="utf-8").read())["remember"]
    assert cm.normalise_schema(raw) == s            # nothing in the template was dropped as malformed


def test_each_trade_asks_for_its_own_details():
    keys = {tid: [f["key"] for f in schema(tid)["facts"]] for tid in TEMPLATES}
    assert "preferred_doctor" in keys["clinic"] and "age_band" in keys["clinic"]
    assert {"room_preference", "usual_nights", "arrival_time"} <= set(keys["hotel"])
    assert {"preferred_stylist", "preferred_service"} <= set(keys["salon"])
    assert {"party_size", "seating_preference", "occasion"} <= set(keys["restaurant"])
    assert {"matter_type", "documents_pending"} <= set(keys["office"])
    assert {"course"} <= set(keys["institute"]) and schema("institute")["people"]["label"].startswith("Students")
    assert {"product_model", "registration_id"} <= set(keys["service_centre"])
    assert {"goal", "preferred_slot"} <= set(keys["fitness"])
    assert schema("service_centre")["facts"][1].get("allow_id") is True


def test_an_agent_without_a_template_gets_the_generic_schema_and_a_bad_schema_is_cleaned():
    generic = cm.schema_for({"name": "mine"})
    assert [f["key"] for f in generic["facts"]] == ["preferred_schedule", "language_note"]
    assert cm.schema_for(None) == generic and cm.schema_for({"template_id": "not-a-template"}) == generic
    assert cm.schema_for({"template_id": "../clinic"}) == generic
    bad = cm.normalise_schema({"people": "x", "facts": [
        {"key": "Bad Key"}, {"key": "ok_key", "type": "weird", "label": "L"}, {"key": "ee", "type": "enum", "choices": []},
        {"key": "ok_key"}, "nope", {"key": "nn", "type": "number", "min": "x", "max": 5000, "rule": "bogus"}]})
    assert bad["people"] is None
    assert [f["key"] for f in bad["facts"]] == ["ok_key", "nn"] and bad["facts"][0]["type"] == "text"
    assert bad["facts"][1]["max"] <= 1000 and "rule" not in bad["facts"][1]


# ============================================================================================ the rules

def test_a_party_size_the_nights_of_a_stay_and_an_age_band_are_read_by_rules():
    restaurant, hotel, clinic = schema("restaurant"), schema("hotel"), schema("clinic")
    assert cm.extract("a table for four please", None, NOW, restaurant)["f_party_size"] == "4"
    assert cm.extract("we will be 6 people", None, NOW, restaurant)["f_party_size"] == "6"
    assert cm.extract("do log ke liye table", None, NOW, restaurant)["f_party_size"] == "2"
    assert "f_party_size" not in cm.extract("a table for four please", None, NOW, hotel)      # not that trade's detail
    assert "f_party_size" not in cm.extract("a table for four please", None, NOW)
    assert cm.extract("I need the room for 3 nights", None, NOW, hotel)["f_usual_nights"] == "3"
    assert cm.extract("do raat ke liye", None, NOW, hotel).get("f_usual_nights") in (None, "2")
    got = cm.extract("it is for my daughter, she is 7 years old", None, NOW, clinic)
    assert got["f_age_band"] == "child" and got["age"] == "7"
    assert cm.extract("he is 70 years old", None, NOW, clinic)["f_age_band"] == "senior"
    assert cm.age_band(15) == "teen" and cm.age_band(30) == "adult" and cm.age_band("x") is None


@pytest.mark.parametrize("said, name, relation", [
    ("I want to book for my son Rohan", "Rohan", "son"),
    ("it is for my mother Sunita Devi", "Sunita Devi", "mother"),
    ("mere bete Rohan ke liye appointment chahiye", "Rohan", "son"),
    ("मेरे बेटे रोहन के लिए", "रोहन", "son"),
    ("my wife is called Meera", "Meera", "wife"),
])
def test_the_person_a_booking_is_for_is_read_with_how_they_are_related(said, name, relation):
    got = cm.extract(said, None, NOW)
    assert got["person"] == name and got["relation"] == relation and "name" not in got


@pytest.mark.parametrize("said", ["my son is sick", "my mother is coming too", "for my son", "my friend wants a haircut"])
def test_a_relation_without_a_name_gives_no_person(said):
    assert "person" not in cm.extract(said, None, NOW)


def test_the_caller_and_the_person_are_two_different_slots():
    form = cm.merge({}, cm.extract("my name is Sunita Sharma", None, NOW))
    form = cm.merge(form, cm.extract("the booking is for my son Rohan", "Who is it for?", NOW))
    assert form["name"] == "Sunita Sharma" and form["person"] == "Rohan" and form["relation"] == "son"
    slots = cm.prompt_slots(form, {})
    assert slots["name"] == "Sunita Sharma" and slots["booking for"] == "Rohan (son)" and "person" not in slots


def test_trade_details_are_listed_to_the_model_under_their_labels():
    form = {"name": "Priya", "f_preferred_doctor": "Dr Rao", "f_age_band": "child"}
    slots = cm.prompt_slots(form, {"date": "2026-11-10"}, schema("clinic"))
    assert slots["preferred doctor"] == "Dr Rao" and slots["age band"] == "child" and slots["date"] == "2026-11-10"
    assert list(slots)[:2] == ["name", "date"]
    assert cm.prompt_slots({"f_x_unknown": "v"}, {})["x unknown"] == "v"      # no schema: the key in words
    assert cm.facts_of(form) == {"preferred_doctor": "Dr Rao", "age_band": "child"}


# ======================================================================== the model's reading, per type

def test_the_model_is_told_which_details_to_look_for_and_never_to_return_ids_or_conditions():
    system = cm.llm_system(schema("clinic"))
    assert "facts" in system and "preferred_doctor" in system and "age_band one of child, teen, adult, senior" in system
    assert "Aadhaar" in system and "health condition" in system and "person" in system
    plain = cm.llm_system(None)
    assert "facts" not in plain and "{facts}" not in plain
    msgs = cm.llm_messages("Who is it for?", "for my son", schema("clinic"))
    assert msgs[0]["content"] == system and "for my son" in msgs[1]["content"]


def test_the_models_trade_details_are_checked_by_their_type():
    restaurant = schema("restaurant")
    got = {"person": "Rohan", "relation": "kid", "facts": {"party_size": "5 people", "seating_preference": "window seat",
                                                          "occasion": "birthday", "not_in_schema": "x"}}
    out = cm.merge_llm({}, got, NOW, restaurant)
    assert out == {"person": "Rohan", "relation": "child", "f_party_size": "5", "f_seating_preference": "window seat",
                   "f_occasion": "birthday"}
    assert cm.merge_llm({}, {"facts": {"party_size": "500"}}, NOW, restaurant) == {}          # out of range
    assert cm.merge_llm({}, {"facts": {"party_size": "many"}}, NOW, restaurant) == {}
    assert cm.merge_llm({"f_party_size": "2"}, {"facts": {"party_size": "6"}}, NOW, restaurant)["f_party_size"] == "6"
    hotel = schema("hotel")
    assert cm.merge_llm({}, {"facts": {"arrival_time": "5 baje"}}, NOW, hotel)["f_arrival_time"] == "17:00"
    assert cm.merge_llm({}, {"facts": {"arrival_time": "later"}}, NOW, hotel) == {}
    clinic = schema("clinic")
    assert cm.merge_llm({}, {"facts": {"age_band": "Senior"}}, NOW, clinic)["f_age_band"] == "senior"
    assert cm.merge_llm({}, {"facts": {"age_band": "old-ish"}}, NOW, clinic) == {}
    assert cm.merge_llm({}, {"age": "7", "facts": {}}, NOW, clinic)["f_age_band"] == "child"   # from the age, not kept as such
    assert cm.merge_llm({}, {"relation": "friend"}, NOW) == {}                                 # a relation needs a person
    assert cm.merge_llm({}, {"facts": "not a dict"}, NOW, clinic) == {}


def test_a_form_never_holds_more_than_the_limit_of_trade_details():
    form = {f"f_k{i:02d}": "v" for i in range(cm.MAX_FACTS)}
    merged = cm.merge(form, {"f_new_one": "v"})
    assert len([k for k in merged if k.startswith("f_")]) == cm.MAX_FACTS and "f_new_one" in merged and "f_k00" not in merged
    assert cm.merge({}, {"f_Bad Key": "x", "unknown": "x"}) == {}


# ============================================================================ what is never kept

@pytest.mark.parametrize("text", [
    "my aadhaar is 2345 6789 0123", "2345-6789-0123", "234567890123", "ABCDE1234F", "my PAN card number", "passport K1234567",
    "voter id ABC1234567", "driving licence DL-14-2011-0123456", "card number 4111 1111 1111 1111", "4111111111111111",
    "the OTP is 4821", "my password is hunter2", "my upi pin", "cvv 123", "आधार कार्ड", "my passcode",
])
def test_identity_documents_cards_otps_and_passwords_are_never_kept(text):
    assert cm.sensitive_ids(text) is True
    assert cm.safe_text(text) is None
    assert cm.clean_value("other", text) is None and cm.clean_value("reason", text) is None
    assert cm.merge({}, {"other": text, "reason": text, "doctor": text}) == {}


@pytest.mark.parametrize("text", ["dental check-up", "MH12AB1234", "Dr Rao", "98765 43210", "+91 98765 43210", "919876543210",
                                  "table by the window", "follow-up visit", "haircut and colour"])
def test_ordinary_details_pass(text):
    assert cm.sensitive_ids(text) is False and cm.safe_text(text) == text


@pytest.mark.parametrize("text", ["diabetes follow-up", "cancer screening", "fever and cough", "back pain", "bukhar hai",
                                  "बुखार", "he has asthma", "BP check", "thyroid", "pregnancy scan", "HIV test"])
def test_a_health_condition_is_never_kept_in_a_profile(text):
    assert cm.sensitive_health(text) is True and cm.safe_text(text) is None
    assert cm.safe_text(text, health=False) == text            # the call itself may carry it; the profile never does


def test_what_the_model_returns_is_stripped_even_when_it_is_asked_not_to():
    clinic = schema("clinic")
    got = {"reason": "diabetes follow-up", "other": "aadhaar 2345 6789 0123", "doctor": "Dr Rao", "person": "Rohan",
           "facts": {"preferred_doctor": "PAN ABCDE1234F", "preferred_schedule": "evenings", "usual_purpose": "cancer check"}}
    out = cm.merge_llm({}, got, NOW, clinic)
    assert "other" not in out and "f_preferred_doctor" not in out and "f_usual_purpose" not in out
    assert out["f_preferred_schedule"] == "evenings" and out["doctor"] == "Dr Rao" and out["person"] == "Rohan"
    # the call may hold a reason that names a condition (the transcript has it too), but it never reaches the profile
    assert out["reason"] == "diabetes follow-up"
    update = cp.read_call([HELLO, GREETING, turn("user", "hello"), turn("agent", "Hi.", language="en")], out)
    assert "diabetes" not in json.dumps(update) and "Rohan" in json.dumps(update)
    # and a registration id is fine for a service centre, an identity document is not
    centre = schema("service_centre")
    assert cm.merge_llm({}, {"facts": {"registration_id": "MH12AB1234"}}, NOW, centre)["f_registration_id"] == "MH12AB1234"
    assert cm.merge_llm({}, {"facts": {"registration_id": "2345 6789 0123"}}, NOW, centre) == {}
    assert cm.merge_llm({}, {"facts": {"registration_id": "ABCDE1234F"}}, NOW, centre) == {}
    assert cm.merge_llm({}, {"facts": {"product_model": "call me on 9876543210 9876"}}, NOW, centre) == {}


# ==================================================================== reading a finished call into a profile

def test_a_mother_booking_for_her_son_keeps_her_name_and_adds_him_to_the_people_on_the_number():
    first = [HELLO, GREETING, turn("user", "my name is Sunita Sharma, book me a check-up"),
             turn("agent", "Booked.", language="hi", tool_calls=[
                 {"name": "book_appointment", "ok": True, "arguments": {"name": "Sunita Sharma", "date": "2026-11-10", "time": "10:00"}}])]
    u1 = cp.read_call(first, {"name": "Sunita Sharma", "reason": "check-up"})
    p1 = cp.merge_call(None, "u1", NUMBER, u1, conversation_id="c1", returning=False, when="2026-11-04T10:00:00Z")
    assert p1["display_name"] == "Sunita Sharma"
    assert [x["name"] for x in p1["people"]] == ["Sunita Sharma"] and p1["last_person"] == "Sunita Sharma"
    # next call: the form was prefilled with her name; the booking is for Rohan, who is a child
    second = [HELLO, GREETING, turn("user", "it is for my son Rohan, he is 7 years old"),
              turn("agent", "Booked.", language="hi", tool_calls=[
                  {"name": "book_appointment", "ok": True, "arguments": {"name": "Rohan", "date": "2026-11-12", "time": "17:00"}}])]
    form = {"name": "Sunita Sharma", "person": "Rohan", "relation": "son", "age": "7", "f_age_band": "child"}
    u2 = cp.read_call(second, form, known_name="Sunita Sharma")
    assert u2["display_name"] == "Sunita Sharma"
    p2 = cp.merge_call(p1, "u1", NUMBER, u2, conversation_id="c2", returning=True, when="2026-11-05T10:00:00Z")
    assert p2["display_name"] == "Sunita Sharma" and p2["last_person"] == "Rohan"
    names = {x["name"]: x for x in p2["people"]}
    assert set(names) == {"Sunita Sharma", "Rohan"}
    assert names["Rohan"]["relation"] == "son" and names["Rohan"]["age_band"] == "child"
    assert names["Rohan"]["last_booked_at"] == "2026-11-05T10:00:00Z"
    assert '"age"' not in json.dumps(p2) and "7" not in json.dumps(p2["people"])      # the exact age is not kept
    assert "booked" in p2["summary"] and "for Rohan" in p2["summary"]
    block = cp.block_text(p2)
    assert "people booked for on this number: Sunita Sharma, Rohan (son, child); last booked for Rohan" in block


def test_a_different_name_on_a_known_number_is_the_person_unless_the_caller_introduced_themselves():
    turns = [HELLO, GREETING, turn("user", "patient is Rohan Sharma"), turn("agent", "ok", language="en")]
    got = cp.read_call(turns, {"name": "Rohan Sharma"}, known_name="Sunita Sharma")
    assert got["display_name"] == "Sunita Sharma" and [e["name"] for e in got["people"]] == ["Rohan Sharma"]
    turns = [HELLO, GREETING, turn("user", "my name is Meera Kulkarni"), turn("agent", "ok", language="en")]
    again = cp.read_call(turns, {"name": "Meera Kulkarni"}, known_name="Sunita Sharma")
    assert again["display_name"] == "Meera Kulkarni"            # a correction: the number has a new owner
    same = cp.read_call(turns, {"name": "Sunita"}, known_name="Sunita Sharma")
    assert same["display_name"] == "Sunita"


def test_the_trades_details_are_validated_again_and_replace_the_same_details_of_earlier_calls():
    s = schema("restaurant")
    turns = [HELLO, GREETING, turn("user", "table for 4"), turn("agent", "Booked.", language="en")]
    form = {"name": "Asha", "f_party_size": "4", "f_seating_preference": "window", "f_occasion": "diabetes day",
            "f_made_up": "x"}
    update = cp.read_call(turns, form, schema=s)
    assert update["facts"] == {"party_size": "4", "seating_preference": "window"}
    first = cp.merge_call(None, "u1", NUMBER, update, conversation_id="c1", returning=False)
    assert first["facts"] == {"party_size": "4", "seating_preference": "window"}
    later = cp.merge_call(first, "u1", NUMBER, {"last_outcome": "asked", "facts": {"party_size": "6"}}, conversation_id="c2",
                          returning=True)
    assert later["facts"] == {"seating_preference": "window", "party_size": "6"}          # replaced, the others kept
    assert cp.block_text(later).count("usual: ") == 1 and "party size 6" in cp.block_text(later)
    no_schema = cp.read_call(turns, {"f_goal": "strength"})
    assert no_schema["facts"] == {"goal": "strength"}


def test_a_stored_profile_cannot_smuggle_ids_conditions_or_markup_into_the_prompt():
    raw = {"owner": "local-user", "number": NUMBER, "display_name": "Priya", "last_call_at": cp._now_iso(),
           "facts": {"party_size": "4", "bad": "aadhaar 2345 6789 0123", "Bad Key": "x", "long": "9876543210123",
                     "note": "line one\nline (two)", "ill": "has diabetes"},
           "people": [{"name": "Rohan", "relation": "son", "age_band": "child"}, {"name": "R2D2 9"}, {"name": "Rohan"},
                      {"name": "Zed", "age_band": "ancient", "relation": "cousin-twice-removed"}, "junk"],
           "last_person": "Nobody", "source": "somewhere", "last_booking": {"date": "2026-11-10", "purpose": "cancer scan"}}
    p = cp.clean_profile(raw)
    assert p["facts"] == {"party_size": "4", "note": "line one line two"}
    assert [x["name"] for x in p["people"]] == ["Rohan", "Zed"] and p["people"][1] == {"name": "Zed"}
    assert p["last_person"] is None and p["source"] == "phone" and p["last_booking"] == {"date": "2026-11-10"}   # no condition
    assert "\n" not in cp.block_text(p)


# ========================================================================= both directions and web calls

def put_conversation(env, number_text, turns_extra=(), *, source="web", form=None, agent_id="a1"):
    conv = env.local_db.insert("conversations", {"agent_id": agent_id, "user_id": "local-user", "source": source})
    cid = conv["id"]
    rows = [HELLO, GREETING, turn("user", number_text), turn("agent", "Thanks.", language="en"), *turns_extra]
    for i, t in enumerate(rows):
        env.local_db.insert("conversation_turns", {"conversation_id": cid, "created_at": f"2026-11-04T10:00:0{i}", **t})
    cm.save(cid, form or {}, env.local_db, "local-user")
    return cid


def test_a_web_call_with_a_number_writes_a_profile_marked_as_a_web_call(env):
    cid = put_conversation(env, "My name is Priya Sharma, 98765 43210",
                           form={"name": "Priya Sharma", "phone": "9876543210", "f_goal": "strength"})
    assert cp.on_web_call_end(owner="local-user", mode="local", conversation_id=cid, local_db=env.local_db, delay=0) is True
    assert wait_until(lambda: stored() is not None)
    p = stored()
    assert p["number"] == NUMBER and p["source"] == "web call" and p["display_name"] == "Priya Sharma"
    assert p["calls"][0]["conversation_id"] == cid and p["calls"][0]["returning"] is False
    assert cp.public_view(cp.lookup("local-user", NUMBER))["source"] == "web call"


def test_a_web_call_without_a_number_or_without_words_writes_nothing(env):
    cid = put_conversation(env, "hello there", form={"name": "Priya"})
    assert cp.on_web_call_end(owner="local-user", mode="local", conversation_id=cid, local_db=env.local_db, delay=0) is False
    cid2 = put_conversation(env, "x", form={"phone": "12345"})                         # not a number
    assert cp.on_web_call_end(owner="local-user", mode="local", conversation_id=cid2, local_db=env.local_db, delay=0) is False
    assert cp.list_profiles("local-user") == []


def test_a_phone_call_from_the_same_number_makes_it_a_phone_profile_and_a_web_call_does_not_demote_it(env):
    cid = put_conversation(env, "98765 43210", form={"phone": "9876543210", "name": "Priya"})
    cp.finish_call(owner="local-user", number="9876543210", mode="local", conversation_id=cid, returning=False,
                   local_db=env.local_db, source="web call")
    assert stored()["source"] == "web call"
    cid2 = put_conversation(env, "hello", source="phone", form={"name": "Priya"})
    cp.finish_call(owner="local-user", number=NUMBER, mode="local", conversation_id=cid2, returning=True, local_db=env.local_db)
    assert stored()["source"] == "phone" and stored()["call_count"] == 2
    cid3 = put_conversation(env, "98765 43210", form={"phone": "9876543210"})
    cp.finish_call(owner="local-user", number=NUMBER, mode="local", conversation_id=cid3, returning=False,
                   local_db=env.local_db, source="web call")
    assert stored()["source"] == "phone"


def test_the_whole_web_path_files_the_call_under_the_number_the_visitor_gave(env, monkeypatch, app_client, ref_wav):
    from tests.test_web_call import ORIGIN, Rig, make_link, say, token_for
    from routers import web_call
    web_call.limiter.reset()
    rig = Rig(env, monkeypatch, app_client, ref_wav)
    from fastapi.testclient import TestClient
    import phone_public
    rig.public = TestClient(phone_public.public_app, raise_server_exceptions=False)
    a = make_agent(app_client)
    link = make_link(rig, a["id"])
    token = token_for(rig, link)
    rig.asr_text = "My name is Priya Sharma and my number is 98765 43210"
    assert say(rig, link["id"], token).status_code == 200
    ended = rig.public.post(f"/call/api/{link['id']}/end", headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN})
    assert ended.status_code == 200
    assert wait_until(lambda: stored() is not None)
    p = stored()
    assert p["number"] == NUMBER and p["source"] == "web call" and p["display_name"] == "Priya Sharma"
    # hanging up twice writes once
    rig.public.post(f"/call/api/{link['id']}/end", headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN})
    time.sleep(0.2)
    assert stored()["call_count"] == 1


def test_an_outbound_call_is_filed_under_the_number_that_was_dialled(env):
    dialled = "+919123456789"
    cid = put_conversation(env, "Haan, kal shaam 5 baje", source="missed_call", form={"name": "Ravi Kumar"})
    assert cp.on_phone_call_end(owner="local-user", number="91234 56789", mode="local", conversation_id=cid, returning=False,
                                local_db=env.local_db, delay=0) is True
    assert wait_until(lambda: cp.lookup("local-user", dialled) is not None)
    p = cp.lookup("local-user", dialled)
    assert p["display_name"] == "Ravi Kumar" and p["source"] == "phone" and p["number"] == dialled
    assert cp.lookup("local-user", NUMBER) is None
    assert p["id"] == cp.caller_ref("local-user", dialled)


def test_the_profile_id_is_the_reference_the_bookings_list_uses():
    import hashlib
    from services import caller_ref as cr
    expected = hashlib.sha256(b"local-user|+919876543210").hexdigest()[:16]
    assert cp.caller_ref("local-user", NUMBER) == cp.profile_id("local-user", NUMBER) == cr.caller_ref("local-user", NUMBER) == expected
    p = seed()
    assert p["id"] == expected and cp.public_view(p)["caller_ref"] == expected
    # an old file with an id from another formula is served under the shared one
    cp.get_store("local").put({**{k: v for k, v in p.items()}, "id": "oldid00000000000"})
    assert cp.lookup("local-user", NUMBER)["id"] == expected
    assert cp.find("local-user", expected)["number"] == NUMBER


# ============================================================================================ the greeting

def agent_for(env, languages=("en", "hi", "mr"), name="Sunrise Family Clinic Receptionist"):
    return {"id": "a1", "name": name, "supported_languages": list(languages), "disclose_ai": True}


def test_a_callback_greets_a_known_caller_by_name_in_their_language(env):
    a = agent_for(env)
    hi = pg.returning_opening(env, a, profile(display_name="Sunita", preferred_language="hi"), callback=True)
    assert hi[0] == "hi" and hi[1] == env.ai_disclosure("hi")
    assert hi[2].startswith("नमस्ते Sunita जी, आपने अभी Sunrise Family Clinic को मिस्ड कॉल दिया था।")
    assert hi[2].endswith("बताइए क्या मदद करूँ?")
    en = pg.returning_opening(env, a, profile(display_name="Sunita", preferred_language="en"), callback=True)
    assert en[2].startswith("Hello Sunita, you just gave Sunrise Family Clinic a missed call. I'm the AI assistant here.")
    hing = pg.returning_opening(env, a, profile(display_name="Sunita", preferred_language="hinglish"), callback=True)
    assert hing[2].startswith("Namaste Sunita ji, aapne abhi Sunrise Family Clinic ko missed call diya tha.")
    mr = pg.returning_opening(env, a, profile(display_name="Sunita", preferred_language="mr"), callback=True)
    assert mr[0] == "mr" and mr[2].startswith("नमस्कार Sunita जी, तुम्ही आत्ताच Sunrise Family Clinic ला मिस्ड कॉल दिला होता.")
    # a language the agent does not speak: the nearest one it does; no name, no personal callback
    only_en = pg.returning_opening(env, agent_for(env, ["en"]), profile(display_name="Sunita", preferred_language="hi"), callback=True)
    assert only_en[0] == "en"
    assert pg.returning_opening(env, a, profile(display_name=None), callback=True) is None
    assert pg.returning_opening(env, a, None, callback=True) is None


def test_the_greeting_says_once_that_details_are_kept_and_how_to_delete_them(env):
    a = agent_for(env)
    first = profile(display_name="Sunita", preferred_language="en")
    for callback in (False, True):
        words = pg.returning_opening(env, a, first, callback=callback)[2]
        assert cp.REMEMBER_NOTICE["en"] in words and words.rstrip().endswith("?")
        told = pg.returning_opening(env, a, {**first, "notice_given": True}, callback=callback)[2]
        assert cp.REMEMBER_NOTICE["en"] not in told
    for lang in ("hi", "mr", "hinglish"):
        assert cp.REMEMBER_NOTICE[lang] in pg.returning_opening(env, a, profile(preferred_language=lang), callback=False)[2]
    # the call that said it marks the profile, so the next greeting is shorter
    p1 = cp.merge_call(seed(display_name="Sunita"), "local-user", NUMBER, {"last_outcome": "asked"}, conversation_id="c9",
                       returning=True)
    assert p1["notice_given"] is True
    never_known = cp.merge_call(None, "local-user", NUMBER, {"display_name": "A", "last_outcome": "asked"},
                                conversation_id="c1", returning=False)
    assert never_known["notice_given"] is False          # a first call greets nobody by name


def test_the_notice_is_in_the_spoken_greeting_of_a_known_caller_on_a_real_call(phone_rig, env, sessions):
    from tests.test_caller_profiles import phone_call
    a = speaks(env, make_agent(phone_rig.client), ["en", "hi"])
    seed(last_booking=BOOKING)
    first = phone_call(phone_rig, a, "When does the clinic open?")
    assert cp.REMEMBER_NOTICE["hi"] in first.greeting.text
    assert wait_until(lambda: stored()["notice_given"] is True)
    second = phone_call(phone_rig, a, "Hello again")
    assert second.greeting.key.startswith("returning-") and cp.REMEMBER_NOTICE["hi"] not in second.greeting.text


def test_a_callback_to_a_known_number_opens_with_the_name_on_a_real_call(phone_rig, env, sessions):
    a = speaks(env, make_agent(phone_rig.client), ["en", "hi"])
    seed(number="+919123456789", display_name="Sunita", preferred_language="hi", last_booking=None, notice_given=True)
    px._known_calls["CB1"] = {"owner": "local-user", "agent_id": a["id"], "party": "+919123456789"}
    token = px.make_token(a["id"], "local-user", "local", 300, origin="missed_call")
    with phone_rig.client.websocket_connect(f"{px.STREAM_PATH}/{token}") as ws:
        ws.send_text(start_msg(call_id="CB1"))
        read_until(ws, is_mark)
        sess = next(s for s in sessions if s.call_sid == "CB1")
        assert wait_until(lambda: sess.greeting is not None)
        words = sess.greeting.text
        stop(ws)
    assert "नमस्ते Sunita जी, आपने अभी " in words and "मिस्ड कॉल दिया था" in words
    assert sess.greeting.key.startswith("returning-") and sess.outbound is True
    assert wait_until(lambda: cp.lookup("local-user", "+919123456789") is not None and
                      cp.lookup("local-user", "+919123456789")["call_count"] in (1, 2))


def test_an_unknown_number_on_a_callback_hears_the_usual_callback_greeting(phone_rig, env, sessions):
    a = make_agent(phone_rig.client)
    px._known_calls["CB2"] = {"owner": "local-user", "agent_id": a["id"], "party": "+919123400000"}
    token = px.make_token(a["id"], "local-user", "local", 300, origin="missed_call")
    with phone_rig.client.websocket_connect(f"{px.STREAM_PATH}/{token}") as ws:
        ws.send_text(start_msg(call_id="CB2"))
        read_until(ws, is_mark)
        sess = next(s for s in sessions if s.call_sid == "CB2")
        assert wait_until(lambda: sess.greeting is not None)
        assert not sess.greeting.key.startswith("returning-") and "missed call" in sess.greeting.text
        stop(ws)


# ======================================================================================== the prompt block

def test_the_block_names_the_people_the_usual_details_and_how_to_confirm_the_number():
    p = cp.clean_profile({**profile(), "people": [{"name": "Sunita"}, {"name": "Rohan", "relation": "son", "age_band": "child"}],
                          "last_person": "Rohan", "facts": {"preferred_doctor": "Dr Rao", "preferred_schedule": "evenings"}})
    block = cp.block_text(p)
    assert "people booked for on this number: Sunita, Rohan (son, child); last booked for Rohan" in block
    assert "usual: preferred doctor Dr Rao, preferred schedule evenings" in block
    assert "The number on this line is 98xxxx3210." in block and "Same number as last time, 98xxxx3210?" in block
    assert "read a booking back before you make it" in block and "forget_me" in block
    assert "9876543210" not in block and "\n" not in block
    assert cp.number_hint("+14155550123") == "ending 0123" and cp.number_hint("") is None


def test_a_known_callers_name_and_number_prefill_the_form_and_the_book_tool_still_reads_back(monkeypatch):
    monkeypatch.setenv("TOOLS_READBACK", "on")
    p = profile()
    assert cp.prefill_form(p) == {"name": "Priya", "phone": "9876543210"}
    slots = cm.prompt_slots(cp.prefill_form(p), {})
    assert slots == {"name": "Priya", "phone": "9876543210"}
    book = tools.ToolSpec("appointments", "book_appointment", "d", {"type": "object"})
    err = tools.readback_error(book, {"name": "Priya", "phone": "9876543210", "date": "2026-11-10", "time": "16:00"}, None)
    assert err and "Shall I book it?" in err


# ================================================================================================ forget me

def test_forget_me_is_a_tool_that_reads_back_first_and_deletes_after_a_yes_in_a_later_turn(env):
    seed()
    cp.set_context("c-f", profile())
    run = SimpleNamespace(conversation_id="c-f", history=[{"role": "user"}] * 4, user_id="local-user",
                          storage_mode="local", source="phone")
    specs = cp.forget_specs(run, tools)
    assert [s.name for s in specs] == ["forget_me"] and specs[0].server == "call"
    ask = tools.execute(specs[0], {}, previous_agent_text=None)
    assert ask.ok is False and "Shall I delete it?" in str(ask.value)
    assert stored() is not None
    same_turn = tools.execute(specs[0], {"confirmed": True}, previous_agent_text=None)
    assert same_turn.ok is False and stored() is not None          # the yes has not come yet: no later turn
    run.history = [{"role": "user"}] * 6                           # the caller answered in a later turn
    done = tools.execute(specs[0], {"confirmed": True}, previous_agent_text="Shall I delete it?")
    assert done.ok is True
    assert stored() is None and cp.prompt_block("c-f") == ""
    assert cp.is_forgotten("c-f") and cm.cached("c-f") == {}


def test_a_forgotten_caller_is_not_written_back_when_the_call_ends(env):
    cid = put_conversation(env, "hello", source="phone", form={"name": "Priya"})
    cp.mark_forgotten(cid)
    assert cp.finish_call(owner="local-user", number=NUMBER, mode="local", conversation_id=cid, returning=False,
                          local_db=env.local_db) is None
    assert stored() is None
    cm.save(cid, {"name": "again"}, env.local_db, "local-user")           # nothing more is kept for the call either
    assert cm.load(cid, env.local_db, "local-user") == {} and cm.cached(cid) == {}


def test_forgetting_on_a_web_call_stops_the_call_being_kept_but_never_deletes_by_a_claimed_number(env):
    seed()
    run = SimpleNamespace(conversation_id="c-w", history=[], user_id="local-user", storage_mode="local", source="web")
    cp.forget_run("forget_me", {}, run)
    run.history = [{"role": "user"}] * 3
    ok, out = cp.forget_run("forget_me", {"confirmed": True}, run)
    assert ok is True and "Nothing from this call will be kept" in out["message"]
    assert stored() is not None and cp.is_forgotten("c-w")      # the number's profile stays: the number was only claimed


def test_forget_me_is_not_offered_with_the_switch_off(monkeypatch):
    monkeypatch.setenv("CALLER_PROFILES", "off")
    assert cp.forget_specs(SimpleNamespace(conversation_id="x"), tools) == []


@pytest.mark.parametrize("source", [None, "web", "phone"])
def test_forget_me_reaches_the_agents_tools_on_every_kind_of_call(env, source, monkeypatch):
    import asyncio
    other = tools.ToolSpec("appointments", "list_free_slots", "d", {"type": "object"})
    monkeypatch.setattr(tools.hub, "specs_for", lambda names: [other])
    run = SimpleNamespace(agent={"tools": ["appointments"]}, source=source, agent_id="ag1", conversation_id="c-t", tool_specs=[])
    asyncio.run(env.turn_tools(run))
    assert [s.name for s in run.tool_specs] == ["list_free_slots", "forget_me"]


def test_an_agent_whose_tools_could_not_start_answers_without_any_tool(env, monkeypatch):
    import asyncio
    monkeypatch.setattr(tools.hub, "specs_for", lambda names: [])
    run = SimpleNamespace(agent={"tools": ["appointments"]}, source="web", agent_id="ag1", conversation_id="c-t", tool_specs=[])
    asyncio.run(env.turn_tools(run))
    assert run.tool_specs == []


def test_an_agent_without_tools_has_no_forget_tool(env):
    import asyncio
    run = SimpleNamespace(agent={"tools": []}, source="web", agent_id="ag-none", conversation_id="c-t", tool_specs=[])
    asyncio.run(env.turn_tools(run))
    assert run.tool_specs == []


# ============================================================================================ the owner edits

def test_the_owner_edits_names_facts_people_and_notes(app_client):
    p = seed(people=[{"name": "Priya"}], facts={"preferred_doctor": "Dr Rao"})
    r = app_client.patch(f"/api/callers/{p['id']}", headers=LOCAL, json={
        "display_name": "Priya Sharma", "facts": {"preferred_doctor": "Dr Mehta", "seating": "window", "gone": None},
        "people": [{"name": "Priya Sharma"}, {"name": "Rohan", "relation": "son", "age_band": "child"}],
        "last_person": "Rohan", "add_note": "ask for the old x-ray"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Priya Sharma" and body["id"] == p["id"] and "9876543210" not in r.text
    assert {f["key"]: f["value"] for f in body["facts"]} == {"preferred_doctor": "Dr Mehta", "seating": "window"}
    assert body["facts"][0]["label"] == "Preferred doctor"
    assert [x["name"] for x in body["people"]] == ["Priya Sharma", "Rohan"] and body["last_person"] == "Rohan"
    assert [f["note"] for f in body["follow_ups"]] == ["ask for the old x-ray"]
    assert stored()["display_name"] == "Priya Sharma"
    # the notes that stay, and a language
    r2 = app_client.patch(f"/api/callers/+919876543210", headers=LOCAL, json={"follow_ups": [], "preferred_language": "MR"})
    assert r2.status_code == 200 and r2.json()["follow_ups"] == [] and r2.json()["preferred_language"] == "mr"
    # the agent is told the owner's note on the next call
    again = app_client.patch(f"/api/callers/{p['id']}", headers=LOCAL, json={"add_note": "confirm the new number"})
    assert "ask again: confirm the new number" in cp.block_text(cp.lookup("local-user", NUMBER))
    assert again.status_code == 200


@pytest.mark.parametrize("body", [
    {"display_name": "aadhaar 2345 6789 0123"}, {"facts": {"note": "has diabetes"}}, {"facts": {"id": "ABCDE1234F"}},
    {"add_note": "the OTP is 4821"}, {"facts": {"BadKey": "x"}}, {"facts": {"long": "9876543210123"}},
    {"people": [{"name": "Rohan 9"}]}, {"preferred_language": "e n!"},
])
def test_an_edit_that_holds_something_never_kept_is_refused_not_dropped(app_client, body):
    p = seed()
    r = app_client.patch(f"/api/callers/{p['id']}", headers=LOCAL, json=body)
    assert r.status_code == 422 and r.json()["code"] == "INVALID_CALLER_EDIT", r.text
    assert stored()["display_name"] == "Priya" and stored()["facts"] == {}


def test_an_edit_needs_the_owner_a_known_caller_and_known_fields(app_client):
    seed(owner="someone-else", display_name="Other")
    assert app_client.patch("/api/callers/+919876543210", headers=LOCAL, json={"display_name": "X"}).status_code == 404
    p = seed()
    assert app_client.patch(f"/api/callers/{p['id']}", headers=LOCAL, json={"number": "+911"}).status_code == 422
    for i in range(cp.MAX_FOLLOW_UPS):
        assert app_client.patch(f"/api/callers/{p['id']}", headers=LOCAL, json={"add_note": f"note {i}"}).status_code == 200
    assert app_client.patch(f"/api/callers/{p['id']}", headers=LOCAL, json={"add_note": "one too many"}).status_code == 422


def test_the_list_carries_facts_people_and_the_source_for_the_card(app_client):
    seed(facts={"party_size": "4"}, people=[{"name": "Asha", "relation": "friend"}], last_person="Asha", source="web call")
    [c] = app_client.get("/api/callers", headers=LOCAL).json()["callers"]
    assert c["facts"] == [{"key": "party_size", "label": "Party size", "value": "4"}]
    assert c["people"] == [{"name": "Asha", "relation": "friend"}] and c["last_person"] == "Asha" and c["source"] == "web call"
    assert c["caller_ref"] == c["id"]


# ========================================================================================== bookings

def test_the_bookings_of_one_caller_are_listed_by_the_reference_the_profile_gives(app_client, monkeypatch, tmp_path):
    from mcp_servers import appointments as ap
    monkeypatch.setenv("VAKYA_APPOINTMENTS_FILE", str(tmp_path / "mcp" / "appointments.json"))
    monkeypatch.setattr(ap, "now", lambda: datetime(2026, 11, 4, 9, 0))
    ap.book_appointment(name="Priya Sharma", phone="98765 43210", date="2026-11-09", time="10:30")
    ap.book_appointment(name="Rohan", phone="98765 43210", date="2026-11-09", time="11:00")
    ap.book_appointment(name="Someone Else", phone="91234 56789", date="2026-11-09", time="12:00")
    p = seed()
    both = app_client.get(f"/api/bookings?caller_ref={p['id']}", headers=LOCAL).json()["bookings"]
    assert [b["name"] for b in both] == ["Priya Sharma", "Rohan"]
    assert len(app_client.get("/api/bookings", headers=LOCAL).json()["bookings"]) == 3
    none = app_client.get("/api/bookings?caller_ref=0123456789abcdef", headers=LOCAL).json()["bookings"]
    assert none == []
    assert app_client.get("/api/bookings?caller_ref=NOT-HEX", headers=LOCAL).status_code == 422

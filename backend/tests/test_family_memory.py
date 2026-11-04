"""One phone, many patients: the family of a number (services/caller_profiles.py, routers/callers.py). The caller and the people
they book for, each with a relation, usual details and a last booking; learnt from the call; used in the prompt and the
greeting; spelled right through `known_names`; forgotten one person at a time; edited from the desk."""
from types import SimpleNamespace

import pytest

from services import caller_profiles as cp
from tests.test_caller_profiles import GREETING, HELLO, NUMBER, profile, seed, stored, turn
from tests.test_offline_agents import LOCAL, env  # noqa: F401  (env is a fixture)

BOOK = lambda name, **extra: {"name": "book_appointment", "ok": True,   # noqa: E731
                              "arguments": {"name": name, "date": "2026-11-12", "time": "17:00", "reason": "follow-up", **extra}}


def call(say, name, *, form=None, known="Rahul Kulkarni", **extra):
    turns = [HELLO, GREETING, turn("user", say), turn("agent", "Booked.", language="en", tool_calls=[BOOK(name, **extra)])]
    return cp.read_call(turns, {"name": name, **(form or {})}, known_name=known)


FAMILY = [
    {"name": "Sunita Kulkarni", "relation": "mother", "age_band": "senior", "last_booked_at": "2026-10-12T10:00:00Z",
     "facts": {"preferred_doctor": "Dr. Joshi"}, "last_booking": {"date": "2026-10-12", "time": "10:00", "purpose": "follow-up"}},
    {"name": "Rahul Kulkarni", "relation": "self", "last_booked_at": "2026-09-01T10:00:00Z"},
]


# ------------------------------------------------------------------------------------------ learning the relation

@pytest.mark.parametrize("say,relation", [
    ("I need an appointment for my mother", "mother"),
    ("book it for my father please", "father"),
    ("मेरी माँ के लिए अपॉइंटमेंट चाहिए", "mother"),
    ("मेरे पापा के लिए", "father"),
    ("माझ्या आईसाठी अपॉइंटमेंट हवी आहे", "mother"),
    ("माझ्या वडिलांसाठी", "father"),
    ("माझ्या आजीसाठी बुक करा", "grandmother"),
    ("meri maa ke liye appointment chahiye", "mother"),
    ("mere bete ke liye", "son"),
    ("for myself", "self"),
])
def test_the_relation_is_read_from_what_the_caller_said(say, relation):
    assert cp.relation_in(say) == relation


def test_a_name_without_a_relation_is_not_a_relation():
    assert cp.relation_in("my name is Rahul, book Tuesday") is None
    assert cp.clean_relation("आई") == "mother" and cp.clean_relation("Mom") == "mother" and cp.clean_relation("zebra") is None


def test_a_booking_for_my_mother_records_her_under_the_sons_number():
    got = call("it is for my mother Sunita Kulkarni", "Sunita Kulkarni", form={"name": "Sunita Kulkarni"}, known=None)
    [e] = got["people"]
    assert e["name"] == "Sunita Kulkarni" and e["relation"] == "mother" and e["booked"] and not e["own"]
    assert got["display_name"] is None            # her name is not the caller's name


def test_the_caller_who_introduced_themselves_stays_the_caller_and_the_mother_is_added():
    turns = [HELLO, GREETING, turn("user", "my name is Rahul Kulkarni, a booking for my mother Sunita Kulkarni"),
             turn("agent", "Booked.", language="en", tool_calls=[BOOK("Sunita Kulkarni")])]
    got = cp.read_call(turns, {"name": "Rahul Kulkarni", "person": "Sunita Kulkarni", "relation": "mother"})
    assert got["display_name"] == "Rahul Kulkarni"
    assert [(e["name"], e["relation"]) for e in got["people"]] == [("Sunita Kulkarni", "mother")]


def test_a_marathi_relation_without_a_name_goes_to_the_name_on_the_booking():
    got = call("माझ्या आईसाठी अपॉइंटमेंट हवी आहे", "Sunita Kulkarni", form={"name": "Rahul Kulkarni"})
    assert [(e["name"], e["relation"]) for e in got["people"]] == [("Sunita Kulkarni", "mother")]
    assert got["display_name"] == "Rahul Kulkarni"


def test_the_booking_tool_can_pass_the_relation_and_it_wins():
    got = call("book it", "Sunita Kulkarni", relation="aai")
    assert got["people"][0]["relation"] == "mother"
    assert call("book it", "Sunita Kulkarni", booked_by="father")["people"][0]["relation"] == "father"


def test_a_booking_for_the_caller_is_self():
    got = call("book me for Thursday", "Rahul Kulkarni", form={"name": "Rahul Kulkarni"})
    assert got["people"][0]["relation"] == "self" and got["people"][0]["own"]


def test_the_trade_details_of_a_booking_for_someone_else_are_theirs_not_the_callers():
    form = {"name": "Rahul Kulkarni", "f_preferred_doctor": "Dr. Joshi", "f_age_band": "senior"}
    got = call("it is for my mother", "Sunita Kulkarni", form=form)
    p = cp.merge_call(profile(display_name="Rahul Kulkarni", facts={"preferred_schedule": "evenings"}), "local-user", NUMBER, got,
                      conversation_id="c1", returning=True, when="2026-11-05T10:00:00Z")
    [sunita] = [x for x in p["people"] if x["name"] == "Sunita Kulkarni"]
    assert sunita["facts"] == {"preferred_doctor": "Dr. Joshi"} and sunita["age_band"] == "senior"
    assert sunita["last_booking"] == {"date": "2026-11-12", "time": "17:00", "purpose": "follow-up"}
    assert p["facts"] == {"preferred_schedule": "evenings"} and p["last_person"] == "Sunita Kulkarni"


def test_the_second_call_for_the_same_mother_updates_her_and_moves_her_last():
    p1 = cp.merge_call(None, "local-user", NUMBER, call("for my mother", "Sunita Kulkarni"), conversation_id="c1", returning=False,
                       when="2026-10-12T10:00:00Z")
    p1["people"].append({"name": "Asha"})
    p2 = cp.merge_call(p1, "local-user", NUMBER, call("again for my mother", "Sunita Kulkarni"), conversation_id="c2",
                       returning=True, when="2026-11-05T10:00:00Z")
    assert [x["name"] for x in p2["people"]] == ["Asha", "Sunita Kulkarni"]
    assert p2["people"][-1]["last_booked_at"] == "2026-11-05T10:00:00Z"


def test_an_old_profile_reads_and_is_written_in_the_new_shape():
    old = {"owner": "local-user", "number": NUMBER, "display_name": "Rahul", "people": [
        {"name": "Sunita", "relation": "mother", "age_band": "senior", "last_booked_at": "2026-10-12T10:00:00Z"}], "last_person": "Sunita"}
    p = cp.clean_profile(old)
    assert p["people"] == [{"name": "Sunita", "relation": "mother", "age_band": "senior", "last_booked_at": "2026-10-12T10:00:00Z"}]
    p2 = cp.merge_call(old, "local-user", NUMBER, call("again for my mother", "Sunita Kulkarni", known="Rahul"), conversation_id="c3",
                       returning=True, when="2026-11-05T10:00:00Z")
    assert p2["people"][0]["last_booking"]["date"] == "2026-11-12"


# ------------------------------------------------------------------------------------------------ using it

def test_the_block_leads_with_the_family_and_asks_one_short_question():
    p = profile(display_name="Rahul Kulkarni", people=FAMILY, last_person="Sunita Kulkarni")
    block = cp.block_text(p)
    assert ("(What we know about this caller from earlier calls: this number usually books for: Sunita Kulkarni (mother, 60+, "
            "usually Dr. Joshi, last visit 12 Oct, follow-up); Rahul Kulkarni (self)") in block
    assert "Is this for Sunita ji again, or for someone else?" in block
    assert block.index("usually books for") < block.index("name Rahul Kulkarni")
    assert "Never volunteer one person's visits" in block and "\n" not in block


def test_no_question_when_the_number_only_books_for_the_caller():
    p = profile(display_name="Rahul", people=[{"name": "Rahul", "relation": "self", "last_booked_at": "2026-09-01T10:00:00Z"}])
    assert "or for someone else" not in cp.block_text(p)


def test_the_greeting_asks_about_the_family_member_in_the_callers_language():
    p = profile(display_name="Rahul", people=FAMILY, last_person="Sunita Kulkarni", preferred_language="hi")
    lang, words = cp.returning_greeting(p, ["en", "hi", "mr"], today=NUMBER and cp.date(2026, 11, 4))
    assert lang == "hi" and words.endswith("क्या आज भी Sunita जी के लिए बुक करना है, या किसी और के लिए?")
    p = profile(display_name="Rahul", people=FAMILY, preferred_language="en")
    assert cp.returning_greeting(p, ["en"], today=cp.date(2026, 11, 4))[1].endswith("Is this for Sunita again, or for someone else?")
    own = profile(display_name="Rahul", people=[FAMILY[1]], preferred_language="en")
    assert cp.returning_greeting(own, ["en"], today=cp.date(2026, 11, 4))[1].endswith("How can I help today?")


# ------------------------------------------------------------------------------------------ the speech contract

def test_known_names_gives_the_caller_then_the_people_newest_first_at_most_four():
    people = [{"name": f"Person {c}", "last_booked_at": f"2026-10-0{i}T10:00:00Z"} for i, c in enumerate("ABCDE", 1)]
    p = profile(display_name="Rahul Kulkarni", people=people)
    cp.set_context("conv-names", p)
    assert cp.known_names("conv-names") == ["Rahul Kulkarni", "Person E", "Person D", "Person C"]
    cp.set_context("conv-dupe", profile(display_name="Rahul", people=[{"name": "rahul"}, {"name": "Sunita"}]))
    assert cp.known_names("conv-dupe") == ["Rahul", "Sunita"]


def test_known_names_never_raises_and_is_empty_without_a_known_caller(monkeypatch):
    assert cp.known_names(None) == [] and cp.known_names("nobody") == [] and cp.known_names(12) == []
    cp.set_context("c-off", profile())
    monkeypatch.setenv("CALLER_PROFILES", "off")
    assert cp.known_names("c-off") == []
    monkeypatch.setattr(cp, "enabled", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert cp.known_names("c-off") == []


# ------------------------------------------------------------------------------------------------ forgetting

def run_for(cid, history_len):
    return SimpleNamespace(conversation_id=cid, history=[0] * history_len, source="phone", user_id="local-user",
                           storage_mode="local")


def test_forget_me_can_remove_one_person_after_a_yes(env):
    seed(display_name="Rahul Kulkarni", people=FAMILY, last_person="Sunita Kulkarni")
    cp.set_context("c-forget", cp.lookup("local-user", NUMBER))
    ok, msg = cp.forget_run("forget_me", {"person": "my mother's details"}, run_for("c-forget", 2))
    assert not ok and "Sunita Kulkarni" in msg and "nobody else" in msg
    ok, msg = cp.forget_run("forget_me", {"person": "my mother", "confirmed": True}, run_for("c-forget", 2))
    assert not ok                                           # no answer from the caller yet
    ok, out = cp.forget_run("forget_me", {"person": "my mother", "confirmed": True}, run_for("c-forget", 4))
    assert ok and "Sunita Kulkarni" in out["message"]
    assert [x["name"] for x in stored()["people"]] == ["Rahul Kulkarni"] and stored()["display_name"] == "Rahul Kulkarni"
    assert "Sunita" not in cp.prompt_block("c-forget") and not cp.is_forgotten("c-forget")


def test_a_person_forgotten_on_a_call_is_not_written_back_by_the_same_call(env):
    seed(people=FAMILY)
    cp.set_context("c-back", cp.lookup("local-user", NUMBER))
    cp.forget_run("forget_me", {"person": "Sunita"}, run_for("c-back", 2))
    cp.forget_run("forget_me", {"person": "Sunita", "confirmed": True}, run_for("c-back", 4))
    assert [x["name"] for x in stored()["people"]] == ["Rahul Kulkarni"]


def test_an_unclear_person_is_asked_about_not_guessed(env):
    seed(people=[{"name": "Asha Rao", "relation": "sister"}, {"name": "Asha Pai", "relation": "friend"}])
    cp.set_context("c-amb", cp.lookup("local-user", NUMBER))
    ok, msg = cp.forget_run("forget_me", {"person": "Asha"}, run_for("c-amb", 2))
    assert not ok and "Ask the caller" in msg and len(stored()["people"]) == 2
    ok, msg = cp.forget_run("forget_me", {"person": "my sister"}, run_for("c-amb", 2))
    assert not ok and "Asha Rao" in msg


def test_forget_me_without_a_person_still_deletes_the_number(env):
    seed(people=FAMILY)
    cp.set_context("c-all", cp.lookup("local-user", NUMBER))
    cp.forget_run("forget_me", {}, run_for("c-all", 2))
    ok, _ = cp.forget_run("forget_me", {"confirmed": True}, run_for("c-all", 4))
    assert ok and stored() is None


# ------------------------------------------------------------------------------------------------ the desk

def test_the_list_carries_the_family_with_details(app_client):
    seed(display_name="Rahul Kulkarni", people=FAMILY, last_person="Sunita Kulkarni")
    [c] = app_client.get("/api/callers", headers=LOCAL).json()["callers"]
    sunita = c["people"][0]
    assert sunita["relation"] == "mother" and sunita["age_band"] == "senior"
    assert sunita["facts"] == [{"key": "preferred_doctor", "label": "Preferred doctor", "value": "Dr. Joshi"}]
    assert sunita["last_booking"]["date"] == "2026-10-12" and "9876543210" not in str(c)


def test_the_owner_renames_and_changes_the_relation_of_one_person(app_client):
    p = seed(people=FAMILY, last_person="Sunita Kulkarni")
    r = app_client.patch(f"/api/callers/{p['id']}/people/Sunita Kulkarni", headers=LOCAL,
                         json={"name": "Sunita Joshi", "relation": "aai", "facts": {"preferred_doctor": "Dr. Rao", "seating": None}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_person"] == "Sunita Joshi"
    [s] = [x for x in body["people"] if x["name"] == "Sunita Joshi"]
    assert s["relation"] == "mother" and s["facts"][0]["value"] == "Dr. Rao" and s["last_booking"]["purpose"] == "follow-up"
    bad = app_client.patch(f"/api/callers/{p['id']}/people/Sunita Joshi", headers=LOCAL, json={"relation": "zebra"})
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_CALLER_EDIT"
    dupe = app_client.patch(f"/api/callers/{p['id']}/people/Sunita Joshi", headers=LOCAL, json={"name": "rahul kulkarni"})
    assert dupe.status_code == 422
    aadhaar = app_client.patch(f"/api/callers/{p['id']}/people/Sunita Joshi", headers=LOCAL, json={"facts": {"note": "has diabetes"}})
    assert aadhaar.status_code == 422
    assert app_client.patch(f"/api/callers/{p['id']}/people/Nobody", headers=LOCAL, json={"relation": "son"}).status_code == 404


def test_the_owner_removes_one_person_and_the_rest_stays(app_client):
    p = seed(display_name="Rahul", people=FAMILY, last_person="Sunita Kulkarni")
    r = app_client.delete(f"/api/callers/{p['id']}/people/Sunita Kulkarni", headers=LOCAL)
    assert r.status_code == 200 and [x["name"] for x in r.json()["people"]] == ["Rahul Kulkarni"] and r.json()["last_person"] is None
    assert app_client.delete(f"/api/callers/{p['id']}/people/Sunita Kulkarni", headers=LOCAL).status_code == 404
    assert stored()["display_name"] == "Rahul"


def test_saving_the_whole_list_keeps_each_persons_details(app_client):
    p = seed(people=FAMILY)
    r = app_client.patch(f"/api/callers/{p['id']}", headers=LOCAL, json={"people": [
        {"name": "Sunita Kulkarni", "relation": "mother"}, {"name": "Rahul Kulkarni", "relation": "self"}]})
    assert r.status_code == 200
    assert stored()["people"][0]["facts"] == {"preferred_doctor": "Dr. Joshi"} and stored()["people"][0]["last_booking"]["date"] == "2026-10-12"

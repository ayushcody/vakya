"""Digit repair and keypad entry (services/digit_repair.py): a phone number heard in pieces is kept and completed, a
correction after the read-back replaces only the digits the caller named, and a number typed on the keypad is collected
until # (or ten digits). Pure functions, no I/O. All numbers are from the synthetic +91 98765 000xx style block."""
from services import digit_repair as dr

ASK_PHONE = "What is your phone number?"
READBACK = "Thursday 12 November at 5 pm, for Asha Patil, phone number ending 3 2 1 9. Shall I book it?"
READBACK_HI = "गुरुवार 12 नवंबर शाम 5 बजे, आशा पाटिल के नाम से, फ़ोन नंबर के आख़िरी अंक 3 2 1 9. क्या मैं बुक कर दूँ?"


# --------------------------------------------------------------------------------------------- a number in pieces

def test_a_number_cut_short_is_kept_as_a_partial_number():
    form = dr.repair({}, "98765 4321", ASK_PHONE)
    assert form[dr.PARTIAL] == "987654321" and "phone" not in form and form[dr.TRIES] == "1"


def test_the_rest_of_the_number_completes_it_and_clears_the_partial():
    form = dr.repair({}, "98765", ASK_PHONE)
    form = dr.repair(form, "43210", "I have 98765. And the remaining five digits?")
    assert form["phone"] == "9876543210"
    assert dr.PARTIAL not in form and dr.TRIES not in form


def test_spoken_digit_words_complete_a_partial_number():
    form = dr.repair({}, "nau aath saat chhe paanch", ASK_PHONE)
    form = dr.repair(form, "chaar teen do ek shunya", "And the rest?")
    assert form["phone"] == "9876543210"


def test_nine_digits_then_the_last_part_again_replaces_the_tail():
    form = dr.repair({}, "98765 4321", ASK_PHONE)
    form = dr.repair(form, "43210", "Please say the last digits again.")
    assert form["phone"] == "9876543210"


def test_a_whole_number_said_after_a_partial_one_wins():
    form = dr.repair({}, "98765 43", ASK_PHONE)
    form = dr.repair(form, "my number is 98765 00012", ASK_PHONE)
    assert form["phone"] == "9876500012" and dr.PARTIAL not in form


def test_digits_are_not_a_phone_number_when_nobody_talked_about_one():
    assert dr.repair({}, "room 40512 please", "How can I help you?") == {}


def test_a_time_is_not_taken_for_part_of_a_number():
    assert dr.repair({}, "kal shaam 5 30 baje", ASK_PHONE) == {}


def test_short_answers_like_a_party_size_are_left_alone():
    assert dr.repair({}, "2", ASK_PHONE) == {}


# --------------------------------------------------------------------------------------------- a correction

def test_a_corrected_tail_after_the_readback_replaces_only_those_digits():
    form = dr.repair({"phone": "9876543219"}, "nahi, last wale 3 2 1 0", READBACK)
    assert form["phone"] == "9876543210"


def test_a_corrected_tail_in_hindi_after_a_hindi_readback():
    form = dr.repair({"phone": "9876543219"}, "नहीं, आख़िरी चार 3210", READBACK_HI)
    assert form["phone"] == "9876543210"


def test_the_first_digits_can_be_corrected_too():
    form = dr.repair({"phone": "9876543210"}, "no, the first five are 98766", READBACK)
    assert form["phone"] == "9876643210"


def test_a_marathi_first_part_correction():
    form = dr.repair({"phone": "9876543210"}, "नाही, सुरुवातीचे 98766", READBACK)
    assert form["phone"] == "9876643210"


def test_a_changed_time_in_reply_to_a_readback_never_touches_the_number():
    form = dr.repair({"phone": "9876543210"}, "nahi, 6 30 baje", READBACK)
    assert form == {"phone": "9876543210"}


def test_a_correction_that_would_make_an_invalid_number_is_ignored():
    form = dr.repair({"phone": "9876543210"}, "no, the first five are 12345", READBACK)
    assert form["phone"] == "9876543210"


# --------------------------------------------------------------------------------------------- what the agent is told

def test_the_note_names_what_was_heard_and_asks_only_for_the_rest():
    note = dr.prompt_note({dr.PARTIAL: "987654321", dr.TRIES: "1"})
    assert "98765 4321" in note and "9 of 10" in note and "missing" in note
    assert "keypad" not in note


def test_after_two_tries_on_a_phone_call_the_note_offers_the_keypad():
    note = dr.prompt_note({dr.PARTIAL: "98765", dr.TRIES: "2"}, keypad=True)
    assert "keypad" in note and "#" in note


def test_a_web_call_is_never_told_to_use_a_keypad():
    assert "keypad" not in dr.prompt_note({dr.PARTIAL: "98765", dr.TRIES: "3"}, keypad=False)


def test_no_note_once_the_number_is_whole():
    assert dr.prompt_note({"phone": "9876543210"}) == ""
    assert dr.prompt_note({}) == ""


def test_the_agent_asking_for_the_keypad_is_recognised_in_three_languages():
    assert dr.asks_for_keypad("Please type your number on the keypad and press hash.")
    assert dr.asks_for_keypad("कृपया अपना नंबर कीपैड पर दबाइए और फिर # दबाइए।")
    assert dr.asks_for_keypad("कृपया तुमचा नंबर कीपॅडवर टाइप करा आणि # दाबा.")
    assert not dr.asks_for_keypad("What is your phone number?")
    assert not dr.asks_for_keypad("")


# --------------------------------------------------------------------------------------------- the keypad

def _press(entry, keys):
    out = []
    for k in keys:
        out.append(entry.press(k))
    return out


def test_ten_digits_then_hash_submits_the_number_once():
    e = dr.KeypadEntry()
    actions = _press(e, "9876543210#")
    assert actions.count(("submit", "98765 43210")) == 1     # at the tenth digit; the # after it is harmless
    assert actions[-1] == ("ignore", None)
    assert all(a == ("wait", None) for a in actions[:9])


def test_hash_submits_a_number_that_did_not_submit_itself():
    e = dr.KeypadEntry()
    assert _press(e, "9123456789")[-1] == ("wait", None)    # starts with 91: could still be +91 and ten more digits
    assert e.press("#") == ("submit", "91234 56789")


def test_ten_digits_of_a_mobile_number_submit_without_hash():
    e = dr.KeypadEntry()
    assert _press(e, "9876543210")[-1] == ("submit", "98765 43210")


def test_a_number_with_the_country_code_waits_for_all_twelve_digits():
    e = dr.KeypadEntry()
    actions = _press(e, "919876543210")
    assert actions[9] == ("wait", None)                 # "9198765432" looks like a mobile number but is not finished
    assert actions[-1] == ("submit", "98765 43210")


def test_zero_is_a_digit_while_typing_and_star_asks_for_a_person():
    e = dr.KeypadEntry()
    assert _press(e, "90")[-1] == ("wait", None)
    assert e.press("*") == ("human", None)


def test_hash_with_too_few_digits_starts_again():
    e = dr.KeypadEntry()
    assert _press(e, "98#") [-1] == ("reset", None)
    assert _press(e, "9876543210")[-1] == ("submit", "98765 43210")


def test_an_unknown_key_is_ignored():
    e = dr.KeypadEntry()
    assert e.press("A") == ("ignore", None)


def test_the_desk_never_sees_the_working_state_and_the_model_never_takes_it_for_a_known_detail():
    from services import caller_memory
    form = {"name": "Asha Patil", dr.PARTIAL: "987654321", dr.TRIES: "2"}
    assert caller_memory.merge({}, form) == form                      # the form keeps them between turns
    assert caller_memory.public_view(form) == {"name": "Asha Patil"}
    assert caller_memory.prompt_slots(form, {}) == {"name": "Asha Patil"}


# --------------------------------------------------------------------------------------------- inside a real turn

from tests.test_offline_agents import env, make_agent  # noqa: E402,F401  (env is a fixture)
from tests.test_turn_stream import FakeStream, parse, rig  # noqa: E402,F401  (rig is a fixture)


def test_half_a_number_reaches_the_next_turn_as_a_note_for_the_agent(rig):
    a = make_agent(rig.client)
    rig.stream = lambda: FakeStream(["What is your phone number?"])
    rig.asr_text = "Book me for tomorrow"
    conv = parse(rig.post(a["id"]))[-1][1]["conversation_id"]
    rig.asr_text = "98765"
    parse(rig.post(a["id"], conversation_id=conv))
    rig.asr_text = "sorry, I lost you"
    parse(rig.post(a["id"], conversation_id=conv))
    said = rig.asked[-1]["messages"][-1]["content"]
    assert "Phone number not complete yet" in said and "98765" in said and "5 of 10" in said


# --------------------------------------------------------------------------------------------- the caller's own number

OWN = "+91 98765 00012"
OFFER_ASK = "Shall I book it on this number, ending 0 0 1 2?"
OFFER_ASK_HI = "क्या मैं इसी नंबर पर बुक कर दूँ, आख़िरी अंक 0 0 1 2?"
OFFER_ASK_MR = "याच नंबरवर बुक करू का, शेवटचे अंक 0 0 1 2?"


def test_a_new_caller_is_offered_the_number_they_are_calling_from():
    form = dr.offer_own_number({}, OWN)
    assert form == {dr.OFFER: "9876500012"}
    note = dr.prompt_note(form, keypad=True)
    assert "ending 0 0 1 2" in note and "this number" in note and "keypad" not in note


def test_a_known_number_or_an_invalid_caller_id_is_never_offered():
    assert dr.offer_own_number({"phone": "9876500034"}, OWN) == {"phone": "9876500034"}
    assert dr.offer_own_number({}, "+1 202 555 0100") == {}
    assert dr.offer_own_number({}, None) == {}
    assert dr.offer_own_number({dr.OFFER_NO: "1"}, OWN) == {dr.OFFER_NO: "1"}


def test_yes_to_the_offer_makes_it_the_booking_number_without_dictating_digits():
    offered = dr.offer_own_number({}, OWN)
    for answer, ask in (("yes, same number", OFFER_ASK), ("haan ji, isi pe kar do", OFFER_ASK),
                        ("हाँ, इसी नंबर पर", OFFER_ASK_HI), ("हो, बरोबर", OFFER_ASK_MR)):
        form = dr.repair(offered, answer, ask)
        assert form == {"phone": "9876500012"}, answer


def test_the_offer_is_recognised_by_its_last_four_digits_too():
    offered = dr.offer_own_number({}, OWN)
    assert dr.repair(offered, "yes", "Should I use your mobile number ending 0012?")["phone"] == "9876500012"


def test_no_to_the_offer_asks_for_the_number_and_offers_the_keypad_at_once():
    offered = dr.offer_own_number({}, OWN)
    for answer, ask in (("no, use another number", OFFER_ASK), ("nahi, dusra number", OFFER_ASK),
                        ("नहीं", OFFER_ASK_HI), ("नाही, वेगळा नंबर", OFFER_ASK_MR)):
        form = dr.repair(offered, answer, ask)
        assert "phone" not in form and dr.OFFER not in form and form[dr.OFFER_NO] == "1", answer
        note = dr.prompt_note(form, keypad=True)
        assert "say their phone number" in note and "#" in note
    assert "keypad" not in dr.prompt_note({dr.OFFER_NO: "1"}, keypad=False)   # a web call has no keypad


def test_another_number_said_instead_of_yes_wins_over_the_offer():
    offered = dr.offer_own_number({}, OWN)
    assert dr.repair(offered, "no, write 98765 00034", OFFER_ASK) == {"phone": "9876500034"}


def test_a_yes_to_some_other_question_never_takes_the_offer():
    offered = dr.offer_own_number({}, OWN)
    assert dr.repair(offered, "yes", "Would you like the 5 pm slot?") == offered


def test_the_offer_never_reaches_the_known_details_or_the_desk():
    from services import caller_memory
    form = {"name": "Asha Patil", dr.OFFER: "9876500012"}
    assert "9876500012" not in str(caller_memory.prompt_slots(form, {}))


# --------------------------------------------------------------------------------------------- keypad and speech mixed

def test_digits_typed_on_the_keypad_complete_a_number_begun_by_voice():
    form = dr.repair({}, "98765", ASK_PHONE)                     # heard by voice: the first five
    pad = dr.KeypadEntry()
    for key in "00012#":
        action, typed = pad.press(key)
    assert action == "submit" and typed == "00012"
    form = dr.repair(form, typed, "Please type the remaining digits on the keypad and press hash.")
    assert form["phone"] == "9876500012" and dr.PARTIAL not in form


def test_a_whole_number_typed_on_the_keypad_replaces_a_misheard_one():
    form = dr.repair({}, "98765 4", ASK_PHONE)
    pad = dr.KeypadEntry()
    for key in "9876500012":
        action, typed = pad.press(key)
    assert action == "submit"
    assert dr.repair(form, typed, "Please type it on the keypad and press hash.")["phone"] == "9876500012"

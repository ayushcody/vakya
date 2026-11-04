"""Entity hit rate, accepted-spelling WER and the booking rule of services/asr_scoring (no model, no network)."""
import json
from pathlib import Path

import pytest

pytest.importorskip("indic_transliteration")
pytest.importorskip("jiwer")

from services import asr_scoring as sc  # noqa: E402

TASKS = json.loads((Path(__file__).resolve().parent.parent / "bench" / "data" / "stress_tasks.json").read_text("utf-8"))["tasks"]
ENT = {"name": ["प्रिया", "priya", "priyaa"], "date": ["मंगलवार", "mangalvar", "tuesday"],
       "time": ["पाँच बजे", "paanch baje", "5 बजे", "5:00"]}


def test_hit_in_either_script_and_any_spelling():
    assert sc.entity_hit_rate("मेरा नाम प्रिया है मंगलवार को पांच बजे", ENT) == 1.0
    assert sc.entity_hit_rate("mera naam Priyaa hai Tuesday ko paanch baje", ENT) == 1.0
    assert sc.entity_hit_rate("my name is Priya, Tuesday at 5:00", ENT) == 1.0


def test_partial_and_zero():
    assert sc.entity_hit_rate("मेरा नाम प्रिया है", ENT) == pytest.approx(1 / 3)
    assert sc.entity_hits("मेरा नाम प्रिया है", ENT) == {"name": True, "date": False, "time": False}
    assert sc.entity_hit_rate("कुछ और", ENT) == 0.0
    assert sc.entity_hit_rate("anything", {}) == 1.0


def test_whole_words_only_and_runs_must_be_adjacent():
    assert not sc.entity_hits("priyanka", {"name": ["priya"]})["name"]
    assert not sc.entity_hits("पाँच आज बजे", {"time": ["पाँच बजे"]})["time"]
    assert sc.entity_hits("शाम पाँच बजे", {"time": ["पाँच बजे"]})["time"]


def test_a_single_string_is_one_spelling():
    assert sc.entity_hits("hello priya", {"name": "priya"}) == {"name": True}


def test_digit_groupings():
    phone = {"phone": ["9876543210"]}
    assert sc.entity_hits("मेरा नंबर 9876-543-210 है", phone)["phone"]
    assert sc.entity_hits("मेरा नंबर 98765 43210 है", phone)["phone"]
    assert sc.entity_hits("number +91 9876543210", phone)["phone"]
    assert not sc.entity_hits("मेरा नंबर 9876-54-321-00 है", phone)["phone"]
    assert not sc.entity_hits("मेरा नंबर 987654321 है", phone)["phone"]
    # a short number is never searched inside a longer one
    assert sc.entity_hits("number 919876543210", phone)["phone"]          # a country-code prefix is fine
    assert not sc.entity_hits("number 98765432101", phone)["phone"]       # an extra digit at the end is not
    assert not sc.entity_hits("50000", {"time": ["5"]})["time"]           # a short digit string is never searched inside
    assert not sc.entity_hits("123450", {"amount": ["2000"]})["amount"]


def test_accepted_wer_takes_the_best_reference():
    refs = ["मेरा नाम प्रिया है", "mera naam priya hai"]
    assert sc.accepted_wer(refs, "mera naam priya hai") == 0.0
    assert sc.accepted_wer(refs, "मेरा नाम प्रिया है") == 0.0
    assert sc.accepted_wer(refs, "मेरा नाम है") == pytest.approx(0.25)
    assert sc.accepted_wer(refs, "") == 1.0
    assert sc.accepted_wer(refs[0], "मेरा नाम प्रिया है") == 0.0
    assert sc.accepted_wer([], "x") == 1.0 and sc.accepted_wer([], "") == 0.0


def test_accepted_wer_raw_mode_does_not_fold():
    assert sc.accepted_wer(["priya"], "प्रिया", folded=False) == 1.0
    assert sc.accepted_wer(["priya"], "प्रिया", folded=True) == 0.0


def test_task_ok_needs_name_date_and_time():
    assert sc.task_ok("प्रिया मंगलवार पांच बजे", ENT)
    assert not sc.task_ok("प्रिया मंगलवार", ENT)               # no time
    assert not sc.task_ok("प्रिया पांच बजे", ENT)              # no date
    assert not sc.task_ok("मंगलवार पांच बजे", ENT)             # no name


def test_task_ok_ignores_amount_and_phone():
    ent = {**ENT, "amount": ["दो हज़ार"], "phone": ["9876543210"]}
    assert sc.task_ok("प्रिया मंगलवार पांच बजे", ent)
    assert sc.entity_hit_rate("प्रिया मंगलवार पांच बजे", ent) == pytest.approx(3 / 5)


def test_task_ok_without_the_booking_entities_is_false():
    assert not sc.task_ok("anything", {"amount": ["2000"]})
    assert not sc.task_ok("anything", {})


def test_existing_functions_unchanged():
    assert sc.fold("मंगलवार") == sc.fold("mangalvar")
    assert sc.wer("a b", "a b") == 0.0


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t["id"])
def test_every_task_is_heard_in_its_own_text(task):
    """The task set is self-consistent: the sentence that is spoken contains every accepted entity of its own, in the
    spelling of its own script (a task whose reference fails its own scoring would be unwinnable)."""
    spoken = task.get("tts_text") or task["text"]
    hits = sc.entity_hits(spoken, task["entities"])
    # words spelled out in the sentence (phone, amount) are in the spelling lists as words, so all must hit
    assert all(hits.values()), (task["id"], hits)
    assert sc.task_ok(spoken, task["entities"])

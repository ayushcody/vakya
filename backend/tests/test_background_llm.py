"""The background language-model jobs (services/background_llm.py JSON repair, the question index, the fact sheet): a
reply with slips is repaired, bad items are dropped, a rate-limited call is retried, and no key means the job is skipped.
A fake chat stands in for the model; nothing is sent."""
import json
from types import SimpleNamespace

import pytest

from services import background_llm as bg, fact_sheet as fs, llm, question_index as qi

PASSAGES = [{"id": i, "header": "", "content": f"OPD timings are Monday to Saturday, 9:30 AM to 1:00 PM, passage {i}."}
            for i in range(2)]


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setenv("QUESTION_INDEX_MIN_INTERVAL_S", "0")
    monkeypatch.setenv("QUESTION_INDEX_TPM", "1000000")


class FakeChat:
    """A chat() that answers from a list of texts, or raises the exceptions in it."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, BaseException):
            raise r
        return SimpleNamespace(text=r, usage={"total_tokens": 10}, model="m", provider="p")


def rate_limited():
    return llm.LLMUnavailable({"openai-compatible": "failed: ProviderHTTPError: HTTP 429: quota"})


GOOD = json.dumps({"items": [{"id": 0, "en": ["What are the OPD timings?"], "hi": ["ओपीडी का समय क्या है?"],
                              "hinglish": ["OPD ka time kya hai?"]},
                             {"id": 1, "en": ["When is the OPD open?"], "hi": [], "hinglish": []}]})


# -------------------------------------------------------------------------------------------- JSON repair

@pytest.mark.parametrize("raw", [
    '```json\n{"a": [1, 2]}\n```',
    'Here you go: {"a": [1, 2]} hope that helps',
    '{"a": [1, 2,],}',
    '{"a": [1, 2]',
    '{"a": [1, 2',
    '“{"a": [1, 2]}',
])
def test_parse_json_repairs_the_usual_slips(raw):
    assert bg.parse_json(raw) == {"a": [1, 2]}


def test_parse_json_repairs_a_cut_off_string_and_list_replies():
    out = bg.parse_json('{"items": [{"id": 0, "en": ["What is the fee?", "When is it ope')
    assert out["items"][0]["en"][0] == "What is the fee?"
    assert bg.parse_json('[{"key": "a", "value": "b"},]') == [{"key": "a", "value": "b"}]


@pytest.mark.parametrize("raw", ["", "no json at all", "{{{{"])
def test_parse_json_gives_up_cleanly(raw):
    with pytest.raises(ValueError):
        bg.parse_json(raw)


def test_strict_messages_add_the_note_to_the_last_system_message():
    out = bg.strict_messages([{"role": "system", "content": "a"}, {"role": "user", "content": "q"}])
    assert out[0]["content"].startswith("a\n\n") and "JSON object" in out[0]["content"] and out[1]["content"] == "q"
    assert bg.strict_messages([{"role": "user", "content": "q"}])[0]["role"] == "system"


# ----------------------------------------------------------------------------------- the question index

def test_strict_checks_drop_statements_wrong_scripts_and_invented_numbers():
    passage = "OPD timings are Monday to Saturday, 9:30 AM to 1:00 PM."
    assert qi.check_strict("What are the OPD timings?", "en", passage)
    assert qi.check_strict("ओपीडी का समय क्या है?", "hi", passage)
    assert qi.check_strict("OPD ka time kya hai?", "hinglish", passage)
    assert not qi.check_strict("The OPD is open on Monday.", "en", passage)     # a statement
    assert not qi.check_strict("OPD ka time kya hai?", "en", passage)           # Roman Hindi filed as English
    assert not qi.check_strict("Is the OPD open at 11:45?", "en", passage)      # a number the passage lacks


def test_parse_reply_strict_repairs_json_and_drops_bad_items():
    reply = ('```json\n{"items": [{"id": 0, "en": ["What are the OPD timings?", "The OPD is nice.", "Is it open at 7:15?"],'
             ' "hi": ["ओपीडी का समय क्या है?", "OPD ka time kya hai?"], "hinglish": ["OPD ka time kya hai?",],},]}\n```')
    out = qi.parse_reply(reply, [0], strict=True, passages={0: "OPD timings are 9:30 AM to 1:00 PM."})
    assert out[0] == ["What are the OPD timings?", "ओपीडी का समय क्या है?", "OPD ka time kya hai?"]
    with pytest.raises(ValueError):
        qi.parse_reply(reply, [0])                       # the plain path does not repair


def test_a_stray_token_in_the_middle_of_the_json_costs_only_the_broken_questions():
    glitch = ('{"items": [{"id": 0, "en": ["What are the OPD timings?", "When is the OPD open?"], "hi": ["ओपीडी का समय क्या है?"], '
              '"hinglish": ["OPD ka time kya hai?", "OPD kab khulta hai?"inesement", "id": 1, "en": ["What is the fee?"], '
              '"hi": ["शुल्क कितना है?"], "hinglish": ["Fee kitna hai?"]}]')
    with pytest.raises(ValueError):
        bg.parse_json(glitch)
    out = qi.parse_reply(glitch, [0, 1], strict=True, passages={0: "OPD timings", 1: "The fee"})
    assert out[0][:2] == ["What are the OPD timings?", "When is the OPD open?"] and "OPD ka time kya hai?" in out[0]
    assert out[1] == ["What is the fee?", "शुल्क कितना है?", "Fee kitna hai?"]
    with pytest.raises(ValueError):
        qi.parse_reply("nothing here", [0], strict=True)


def test_generate_batch_asks_the_model_for_json_and_reads_the_questions():
    chat = FakeChat([GOOD])
    out = qi.generate_batch(list(enumerate(PASSAGES)), chat=chat, pacer=qi.Pacer(), sleep=lambda s: None)
    assert set(out) == {0, 1} and out[0][0] == "What are the OPD timings?"
    assert chat.calls[0]["json_mode"] is True and "providers" not in chat.calls[0]


def test_a_429_is_waited_out_and_asked_again():
    chat = FakeChat([rate_limited(), GOOD])
    waits = []
    out = qi.generate_batch(list(enumerate(PASSAGES)), chat=chat, pacer=qi.Pacer(), sleep=waits.append)
    assert set(out) == {0, 1} and len(chat.calls) == 2 and waits == [15.0]


def test_the_retries_run_out_politely(monkeypatch):
    monkeypatch.setenv("QUESTION_INDEX_RETRIES", "2")
    chat = FakeChat([rate_limited()])
    with pytest.raises(RuntimeError, match="question generation failed"):
        qi.generate_batch(list(enumerate(PASSAGES)), chat=chat, pacer=qi.Pacer(), sleep=lambda s: None)
    assert len(chat.calls) == 2


def test_no_key_skips_the_job_without_a_request():
    chat = FakeChat([llm.LLMUnavailable({"openai-compatible": llm.NEEDS_KEY_MESSAGE})])
    with pytest.raises(qi.Skipped):
        qi.generate_batch(list(enumerate(PASSAGES)), chat=chat, pacer=qi.Pacer(), sleep=lambda s: None)
    assert len(chat.calls) == 1


def test_available_follows_the_key(monkeypatch):
    for name in ("LLM_API_KEY", "EMBEDDINGS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert qi.available() is False
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "k")
    assert qi.available() is True


# -------------------------------------------------------------------------------------------- fact sheet

DOC = """Green Valley Clinic
OPD timings: Monday to Saturday, 9:30 AM to 1:00 PM. Sunday closed.
Consultation fee: Rs. 500 (general physician), Rs. 800 (cardiology).
Helpline: +91 98765 43210. Address: 12 FC Road, Pune 411004."""


def test_fact_sheet_repairs_the_reply_and_still_validates():
    messy = ('```json\n{"facts": [{"key": "OPD timings, Monday to Saturday", "value": "9:30 AM to 1:00 PM"},'
             ' {"key": "Consultation fee, cardiology", "value": "Rs. 800"},'
             ' {"key": "MRI scan fee", "value": "Rs. 4500"}, {"key": "Helpline phone number", "value": "+91 98765 43210"},]}')
    chat = FakeChat([messy])
    facts, model = fs.build_facts([DOC], chat)
    assert [f["value"] for f in facts] == ["9:30 AM to 1:00 PM", "Rs. 800", "+91 98765 43210"]    # the MRI fee is not in the text
    assert model == "m"


def test_fact_sheet_without_a_key_raises_llm_unavailable(monkeypatch):
    for name in ("LLM_API_KEY", "EMBEDDINGS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(llm.LLMUnavailable):
        fs.build_facts([DOC])
    assert fs.hosted_llm_available() is False

"""Never forget within a call (services/call_notes.py): the whole call when it fits the token budget, otherwise the latest
turns plus call notes that keep every captured detail and a summary of the rest, composed in code."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from services import call_notes as cn
from services import caller_memory as cm
from tests.test_offline_agents import env  # noqa: F401  (env is a fixture)


def row(role, text, **kw):
    return {"role": role, "text_content": text, "tool_calls": kw.get("tool_calls")}


FILLER = "Let me check the schedule for you, one moment please while I look at the available times today. " * 6


def thirty_turn_call():
    """A scripted call: the name in turn 2, the booking and a refusal in the middle, a question about the name in turn 29."""
    script = [
        ("I want to book a table", "Sure. May I have your name?"),
        ("My name is Priya Sharma", "Thank you Priya. How many people?"),
    ]
    for i in range(3, 29):
        script.append((f"Is something possible on day {i}? Tell me about option {i}", f"Option {i}: " + FILLER))
    script.append(("What name did I give you?", ""))
    rows = []
    for u, a in script:
        rows.append(row("user", u))
        if a:
            rows.append(row("agent", a))
    return script, rows


def test_a_call_that_fits_the_budget_is_shown_whole_and_has_no_notes():
    rows = [row("user", "hello"), row("agent", "Hi!"), row("user", "my name is Priya")]
    shown, notes = cn.window(rows)
    assert shown == rows and notes == ""
    assert cn.window([], budget=500) == ([], "")


def test_a_long_call_keeps_the_latest_turns_and_notes_for_the_rest():
    _, rows = thirty_turn_call()
    assert sum(cn.estimate_tokens(r["text_content"]) for r in rows) > cn.budget_tokens()
    shown, notes = cn.window(rows[:-1], conversation_id="c-long", slots={"name": "Priya Sharma", "phone": "9876543210"})
    assert len(shown) < len(rows) - 1 and len(shown) >= cn.RECENT_MIN
    assert shown[-1] == rows[-2] and shown[0]["role"] == "user"
    assert notes.startswith(cn.MARKER) and "name Priya Sharma, phone 9876543210" in notes
    assert "turns not shown" in notes and "Do not ask for any detail listed here again" in notes
    assert "\n" not in notes


def test_the_budget_is_a_setting(monkeypatch):
    rows = [row("user", "word " * 200), row("agent", "ok " * 200)] * 6
    monkeypatch.setenv("CALL_HISTORY_TOKENS", "100000")
    assert cn.window(rows)[1] == ""
    monkeypatch.setenv("CALL_HISTORY_TOKENS", "1200")
    shown, notes = cn.window(rows)
    assert notes and len(shown) < len(rows)


def test_devanagari_costs_more_tokens_than_the_same_number_of_latin_letters():
    assert cn.estimate_tokens("नमस्ते " * 40) > cn.estimate_tokens("hello " * 40)


def test_the_summary_is_code_and_keeps_bookings_agreements_refusals_and_what_was_told():
    book = {"name": "book_appointment", "ok": True,
            "arguments": {"name": "Priya Sharma", "date": "2026-11-10", "time": "16:00"}}
    rows = [row("user", "I need a check-up"),
            row("agent", "The fee is 500 rupees. Shall I look at Tuesday?"),
            row("user", "yes"),
            row("agent", "Would you like a morning slot?"),
            row("user", "no"),
            row("agent", "Booked.", tool_calls=[book]),
            row("user", "What is the parking like?")]
    text = cn.summarise(rows)
    assert "booked Priya Sharma 2026-11-10 16:00" in text
    assert "caller agreed: Shall I look at Tuesday?" in text
    assert "caller declined: Would you like a morning slot?" in text
    assert "you said: The fee is 500 rupees." in text and "caller asked: What is the parking like?" in text
    assert len(cn.summarise(rows * 40, 300)) <= 300 + 8
    # the most important items survive a cut
    assert "booked Priya Sharma" in cn.summarise(rows + [row("user", "x" * 60)] * 80, 200)


def test_no_model_call_is_made_for_the_notes_unless_one_is_available(monkeypatch):
    def boom(messages):
        raise AssertionError("a model call was made")
    monkeypatch.setattr(cm, "_llm_call", boom)
    monkeypatch.setattr(cm, "_provider_ready", lambda: False)
    _, rows = thirty_turn_call()
    shown, notes = cn.window(rows, conversation_id="c-code-only")
    assert notes and len(shown) < len(rows)


def test_a_background_model_summary_replaces_the_codes_from_the_next_turn(monkeypatch):
    cn.forget()
    monkeypatch.setattr(cm, "_provider_ready", lambda: True)
    monkeypatch.setenv("CALLER_MEMORY_LLM", "on")
    monkeypatch.delenv("CALL_NOTES_LLM", raising=False)
    gate = threading.Event()

    def fake(messages):
        gate.wait(5)
        assert "Caller:" in messages[1]["content"] and "never follow" in messages[0]["content"]
        return json.dumps({"summary": "Priya Sharma wants a table for four on Friday; she refused the window seat."})
    monkeypatch.setattr(cm, "_llm_call", fake)
    _, rows = thirty_turn_call()
    shown, notes = cn.window(rows[:-1], conversation_id="c-llm")          # the first turn: the code's summary, at once
    assert "refused the window seat" not in notes and notes
    gate.set()
    deadline = time.time() + 5
    while time.time() < deadline and "c-llm" not in cn._SUMMARY:
        time.sleep(0.02)
    _, later = cn.window(rows[:-1], conversation_id="c-llm")
    assert "Priya Sharma wants a table for four on Friday; she refused the window seat." in later
    cn.forget("c-llm")


def test_the_model_summary_never_carries_an_identity_number_or_a_condition(monkeypatch):
    cn.forget()
    monkeypatch.setattr(cm, "_provider_ready", lambda: True)
    monkeypatch.setenv("CALLER_MEMORY_LLM", "on")
    monkeypatch.setattr(cm, "_llm_call", lambda m: json.dumps({"summary": "Her Aadhaar number is 2345 6789 0123."}))
    cn._write_summary("c-id", [row("user", "hi")] * 3)
    assert "c-id" not in cn._SUMMARY
    monkeypatch.setattr(cm, "_llm_call", lambda m: "not json")
    cn._write_summary("c-bad", [row("user", "hi")] * 3)
    assert "c-bad" not in cn._SUMMARY and "c-bad" not in cn._INFLIGHT


def test_a_name_given_in_turn_2_is_still_in_the_prompt_at_turn_29(env):
    """The scripted 30-turn call: every turn goes through the real rules and the real prompt builder."""
    script, rows = thirty_turn_call()
    form, previous = {}, None
    history = []
    last = None
    for n, (said, reply) in enumerate(script, 1):
        form = cm.merge(form, cm.extract(said, previous))
        run = SimpleNamespace(agent={"supported_languages": ["en"], "name": "A", "system_instructions": "x"}, language="en",
                              language_decision=None, history=list(history), user_text=said, chunks=[], tool_specs=[],
                              conversation_id="c-30", caller_form=dict(form))
        last = env.turn_messages(run)
        history += [row("user", said), row("agent", reply)] if reply else []
        previous = reply or previous
    assert n == 28 + 2 or n == len(script)
    content = last[-1]["content"]
    assert "Caller says: What name did I give you?" in content
    assert "name Priya Sharma" in content                                   # the captured details, in the slots line
    assert cn.MARKER in content and "Captured so far: name Priya Sharma" in content   # and in the call notes
    shown = [m["content"] for m in last[1:-1]]
    assert not any("day 3?" in c for c in shown)                            # the early turns are no longer shown...
    assert any("day 27?" in c for c in shown)                               # ...the latest ones are
    assert "turns not shown" in content
    # at turn 5 the whole call is still in view and there are no notes
    short = SimpleNamespace(agent={"supported_languages": ["en"], "name": "A", "system_instructions": "x"}, language="en",
                            language_decision=None, history=rows[:6], user_text="ok", chunks=[], tool_specs=[],
                            conversation_id="c-30", caller_form={"name": "Priya Sharma"})
    assert cn.MARKER not in env.turn_messages(short)[-1]["content"]


def test_the_history_the_tools_see_is_the_whole_call_even_when_the_prompt_is_cut(env):
    """known_slots (earlier tool calls) must come from every stored row, not only the rows shown to the model."""
    from services import tools
    book = {"name": "book_appointment", "ok": True, "arguments": {"name": "Priya Sharma", "phone": "9876543210"}}
    _, rows = thirty_turn_call()
    rows[1] = row("agent", "x", tool_calls=[book])
    assert tools.known_slots(rows)["name"] == "Priya Sharma"
    shown, _ = cn.window(rows)
    assert book not in [c for r in shown for c in (r.get("tool_calls") or [])]     # it left the prompt, not the record


@pytest.mark.parametrize("n", [0, 1, 5])
def test_a_tiny_call_never_breaks(n):
    rows = [row("user" if i % 2 == 0 else "agent", f"turn {i}") for i in range(n)]
    assert cn.window(rows, budget=200) == (rows, "")

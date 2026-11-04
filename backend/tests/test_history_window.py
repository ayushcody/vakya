"""The language model sees the whole call when it fits the token budget (services/call_notes.py), and only that
conversation's: local mode asks the store for a filtered tail, Supabase mode filters, orders and limits server-side."""
import asyncio

from tests.test_offline_agents import LOCAL, env, make_agent  # noqa: F401  (env is a fixture)
from tests.test_turn_stream import parse, rig  # noqa: F401  (rig is a fixture)


def seed(db, conversation_id, n, prefix):
    for i in range(n):
        db.insert("conversation_turns", {"conversation_id": conversation_id, "role": "user" if i % 2 == 0 else "agent",
                                         "text_content": f"{prefix}{i}"})


def test_select_last_returns_the_newest_matching_rows_oldest_first(tmp_path):
    from services.local_store import LocalStore
    db = LocalStore(str(tmp_path))
    seed(db, "a", 5, "a")
    seed(db, "b", 3, "b")
    rows = db.select_last("conversation_turns", lambda r: r["conversation_id"] == "a", 3)
    assert [r["text_content"] for r in rows] == ["a2", "a3", "a4"]
    assert [r["text_content"] for r in db.select_last("conversation_turns", lambda r: r["conversation_id"] == "b", 9)] \
        == ["b0", "b1", "b2"]
    assert db.select_last("conversation_turns", lambda r: True, 0) == []


def test_a_turn_sends_this_conversations_whole_call_and_nothing_of_another(rig):
    env = rig.env
    a = make_agent(rig.client)
    conv = rig.client.post(f"/api/conversations/{a['id']}/turn/stream", headers=LOCAL,
                           files={"audio": ("rec.wav", rig.wav, "audio/wav")})
    conv_id = parse(conv)[-1][1]["conversation_id"]
    env.local_db.delete("conversation_turns", lambda r: True)
    seed(env.local_db, conv_id, 10, "mine")
    seed(env.local_db, "other-conversation", 4, "theirs")
    rig.asked.clear()

    parse(rig.post(a["id"], conv_id))
    messages = rig.asked[-1]["messages"]
    assert [m["content"] for m in messages[1:-1]] == [f"mine{i}" for i in range(0, 10)]   # nothing is dropped: it fits
    assert not any("theirs" in m["content"] for m in messages)


def test_cloud_history_is_filtered_ordered_and_limited_server_side(monkeypatch):
    from core.runtime import runtime as backend

    class Query:
        def __init__(self, calls, rows):
            self.calls, self.rows = calls, rows

        def __getattr__(self, name):
            def step(*a, **k):
                self.calls.append((name, a, k))
                return self
            return step

        def execute(self):
            self.calls.append(("execute", (), {}))
            return type("Res", (), {"data": self.rows})()

    calls = []
    newest_first = [{"role": "agent", "text_content": "c"}, {"role": "user", "text_content": "b"},
                    {"role": "agent", "text_content": "a"}]

    class FakeSb:
        def table(self, name):
            calls.append(("table", (name,), {}))
            return Query(calls, [{"id": "conv"}] if name == "conversations" else newest_first)

    run = backend.TurnRun.__new__(backend.TurnRun)
    run.local, run.sb, run.agent_id, run.user_id, run.storage_mode = False, FakeSb(), "ag", "u", "cloud"
    run.user_text, run.timings = "q", {}

    async def no_chunks(*a, **k):
        return []

    monkeypatch.setattr(backend, "retrieve_context", no_chunks)
    asyncio.run(backend.turn_context(run, "conv", 0.0))
    assert [h["text_content"] for h in run.history] == ["a", "b", "c"]  # oldest first for the model
    assert ("eq", ("conversation_id", "conv"), {}) in calls
    assert ("order", ("created_at",), {"desc": True}) in calls
    assert ("limit", (backend.call_notes.FETCH_MAX,), {}) in calls


def test_every_kind_of_call_reads_the_call_so_far_and_the_budget_decides_what_is_shown(rig, monkeypatch):
    """A name given at the start of a web call was forgotten after six rows: now every source reads the call (up to
    call_notes.FETCH_MAX rows) and `call_notes.window` shows all of it while it fits CALL_HISTORY_TOKENS."""
    import time
    env = rig.env
    a = make_agent(rig.client)
    first = rig.client.post(f"/api/conversations/{a['id']}/turn/stream", headers=LOCAL,
                            files={"audio": ("rec.wav", rig.wav, "audio/wav")})
    conv_id = parse(first)[-1][1]["conversation_id"]
    env.local_db.delete("conversation_turns", lambda r: True)
    seed(env.local_db, conv_id, 60, "t")

    async def history(source):
        run = await env.turn_begin(None, a["id"], "local-user", storage_mode="local", agent=a, source=source)
        await env.turn_context(run, conv_id, time.time())
        return [h["text_content"] for h in run.history]

    from services import call_notes
    for source in ("phone", "web", "widget"):
        rows = asyncio.run(history(source))
        assert len(rows) == 60 and rows[-1] == "t59"
        shown, notes = call_notes.window([{"role": "user", "text_content": t} for t in rows])
        assert len(shown) == 60 and notes == ""

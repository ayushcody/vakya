"""Ears that learn (services/hearing.py, hearing_expect.py, routers/hearing.py): the per-desk store, the owner's corrections,
what a turn sends Prisma (form fields, request id), what the caller is expected to say in English, Hindi and Marathi, the
live events, and the Marathi voice language. Everything runs against fakes; nothing reaches Gnani."""
import asyncio
import json
import types

import httpx
import pytest

from core.errors import AppError
from services import call_events, gnani, gnani_client as gc, hearing, hearing_expect as expect
from tests.test_conversations_api import talk
from tests.test_offline_agents import LOCAL, env, make_agent  # noqa: F401  (env is a fixture)

KEY = "test-key-not-real-123"


# ------------------------------------------------------------------------------------------------ the store

def test_a_correction_adds_a_rule_and_the_right_words_as_a_keyterm():
    out = hearing.add_correction("a1", "Dr. Call Karni", "Dr. Kulkarni", conversation_id="c-12", turn=2)
    assert out["rule"]["id"] == "H1" and out["rule"]["learnt_from"] == {"conversation_id": "c-12", "turn": 2}
    assert out["vocabulary_added"] is True and out["rules_used"] == 1
    assert [t["term"] for t in hearing.list_vocabulary("a1")] == ["Dr. Kulkarni"]
    assert hearing.list_rules("a2") == []                               # per desk
    again = hearing.add_correction("a1", "o p d", "OPD")                # no call: added by the owner
    assert again["rule"]["learnt_from"] == "owner"


def test_validation_empty_long_duplicate_and_id_numbers():
    for heard, meant, code in [("", "OPD", "EMPTY"), ("x", "OPD", "TOO_SHORT"), ("a" * 61, "OPD", "TOO_LONG"),
                               ("one two three four five six seven eight nine", "OPD", "TOO_LONG"),
                               ("??", "OPD", "INVALID"), ("opd", "9876543210", "ID_NUMBER"),
                               ("opd", "PAN ABCDE1234F", "ID_NUMBER"), ("4111 1111 1111 1111", "card", "ID_NUMBER")]:
        with pytest.raises(hearing.HearingError) as e:
            hearing.add_correction("a1", heard, meant)
        assert e.value.code == code, (heard, meant)
    hearing.add_correction("a1", "Dr Call Karni", "Dr. Kulkarni")
    with pytest.raises(hearing.HearingError) as e:
        hearing.add_correction("a1", "dr call KARNI", "Dr. Kulkarni")
    assert e.value.code == "DUPLICATE" and e.value.status == 409
    assert len(hearing.list_rules("a1")) == 1


def test_ten_rules_then_a_clear_error_and_owner_or_oldest_unused_eviction():
    for i in range(10):
        hearing.add_correction("a1", f"heard {i}x", f"Meant {i}x")
    with pytest.raises(hearing.HearingError) as e:
        hearing.add_correction("a1", "heard eleven", "Meant eleven")
    assert e.value.code == "RULES_FULL" and e.value.status == 409 and "10" in e.value.message
    assert e.value.details["oldest_unused"]["id"] == "H1"
    hearing.mark_used("a1", ["H1"])                                      # H1 was used: H2 is now the oldest unused
    out = hearing.add_correction("a1", "heard eleven", "Meant eleven", evict_oldest_unused=True)
    assert out["replaced"]["id"] == "H2" and out["rules_used"] == 10
    out = hearing.add_correction("a1", "heard twelve", "Meant twelve", replace_rule_id="H5")
    assert out["replaced"]["id"] == "H5"
    with pytest.raises(hearing.HearingError) as e:
        hearing.add_correction("a1", "heard thirteen", "Meant thirteen", replace_rule_id="H99")
    assert e.value.code == "NOT_FOUND"


def test_deleting_and_the_vocabulary_limit():
    r = hearing.add_correction("a1", "opd", "OPD")["rule"]
    assert hearing.delete_rule("a1", r["id"])["rules_used"] == 0
    with pytest.raises(hearing.HearingError):
        hearing.delete_rule("a1", r["id"])
    assert [t["term"] for t in hearing.list_vocabulary("a1")] == ["OPD"]    # the word stays until the owner removes it
    for i in range(hearing.MAX_VOCABULARY - 1):
        hearing.add_vocabulary("a1", f"Term{i}x")
    out = hearing.add_correction("a1", "full one", "Over the limit")
    assert out["vocabulary_added"] is False and out["vocabulary_full"] is True and out["rule"]["id"]
    with pytest.raises(hearing.HearingError) as e:
        hearing.add_vocabulary("a1", "One more")
    assert e.value.code == "VOCABULARY_FULL"
    assert hearing.delete_vocabulary("a1", "opd")["vocabulary_used"] == hearing.MAX_VOCABULARY - 1


def test_a_corrupt_file_starts_fresh(tmp_path, monkeypatch):
    path = tmp_path / "bad" / "hearing.json"
    path.parent.mkdir()
    path.write_text("{not json")
    monkeypatch.setenv("VAKYA_HEARING_FILE", str(path))
    assert hearing.list_rules("a1") == []
    hearing.add_correction("a1", "opd", "OPD")
    assert (tmp_path / "bad" / "hearing.json.corrupt").exists() and len(hearing.list_rules("a1")) == 1


# ------------------------------------------------------------------------------------------------- the API

def test_api_corrections_with_and_without_a_call(app_client, env):
    a = make_agent(app_client)["id"]
    cid = talk(env, a, ("agent", "Your name please?"), ("user", "Dr. Call Karni"), ("agent", "Thanks"))
    r = app_client.post(f"/api/hearing/{a}/corrections", headers=LOCAL,
                        json={"heard": "Dr. Call Karni", "meant": "Dr. Kulkarni", "conversation_id": cid, "turn_index": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["message"] == "From now on Vakya listens for 'Dr. Kulkarni' on this desk."
    assert body["rule"]["learnt_from"] == {"conversation_id": cid, "turn": 1} and body["vocabulary_added"] is True
    r = app_client.post(f"/api/hearing/{a}/corrections", headers=LOCAL, json={"heard": "OPD", "meant": "OPD"})
    assert r.status_code == 200 and r.json()["rule"]["learnt_from"] == "owner"
    got = app_client.get(f"/api/hearing/{a}", headers=LOCAL).json()
    assert got["rules_used"] == 2 and got["rule_limit"] == 10 and [t["term"] for t in got["vocabulary"]] == ["Dr. Kulkarni", "OPD"]
    assert len(app_client.get(f"/api/hearing/{a}/rules", headers=LOCAL).json()["rules"]) == 2
    rid = got["rules"][0]["id"]
    assert app_client.delete(f"/api/hearing/{a}/rules/{rid}", headers=LOCAL).json()["rules_used"] == 1
    assert app_client.delete(f"/api/hearing/{a}/rules/{rid}", headers=LOCAL).status_code == 404


def test_api_refuses_bad_input_foreign_calls_and_the_eleventh_rule(app_client, env):
    a = make_agent(app_client)["id"]
    other = make_agent(app_client)["id"]
    cid = talk(env, other, ("user", "hello"), ("agent", "hi"))
    post = lambda **b: app_client.post(f"/api/hearing/{a}/corrections", headers=LOCAL, json=b)  # noqa: E731
    assert post(heard="", meant="OPD").json()["code"] == "EMPTY"
    assert post(heard="phone", meant="98765 43210").json()["code"] == "ID_NUMBER"
    assert post(heard="x y", meant="Xy", turn_index=1).status_code == 422                      # a turn without a call
    assert post(heard="hello", meant="Hello", conversation_id=cid).status_code == 404         # another desk's call
    assert post(heard="hello", meant="Hello", conversation_id="nope-not-there").status_code == 404
    mine = talk(env, a, ("agent", "hi"), ("user", "hello"))
    assert post(heard="hello", meant="Hello", conversation_id=mine, turn_index=0).json()["code"] == "INVALID_TURN"
    assert app_client.get("/api/hearing/not-an-agent", headers=LOCAL).status_code == 404
    for i in range(10):
        assert post(heard=f"sound {i}x", meant=f"Word {i}x").status_code == 200
    full = post(heard="sound eleven", meant="Word eleven")
    assert full.status_code == 409 and full.json()["code"] == "RULES_FULL"
    assert post(heard="sound eleven", meant="Word eleven", evict_oldest_unused=True).status_code == 200
    v = app_client.post(f"/api/hearing/{a}/vocabulary", headers=LOCAL, json={"term": "Shivajinagar"})
    assert v.status_code == 200
    assert app_client.delete(f"/api/hearing/{a}/vocabulary", headers=LOCAL, params={"term": "shivajinagar"}).status_code == 200


# ------------------------------------------------------------------------ what Prisma is sent (form and header)

@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", KEY)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"text": "ok", "detected_language_code": "hi-IN", "processing_time": 0.25,
                                         "request_id": request.headers.get("x-api-request-id")})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gc, "_client", lambda: client)
    return seen


def fields(request) -> dict:
    """The text fields of a multipart body as {name: value}."""
    ctype = request.headers["content-type"]
    boundary = ctype.split("boundary=")[1].encode()
    out = {}
    for part in request.content.split(b"--" + boundary):
        head, _, value = part.partition(b"\r\n\r\n")
        if b'name="' in head and b"filename=" not in head:
            out[head.split(b'name="')[1].split(b'"')[0].decode()] = value.rstrip(b"\r\n").decode("utf-8")
    return out


def test_the_form_carries_topic_speakers_substitutions_and_the_request_id_header(api):
    out = gc.transcribe(b"RIFFdata", language="hi-IN", keyterms=["Dr. Kulkarni", "OPD"], topic="The caller is saying their name.",
                        speaker_names=["Sunita", "Rahul", "A", "B", "C"], previous_text="x" * 700,
                        substitution_map=[{"heard": "Dr. Call Karni", "meant": "Dr. Kulkarni"}],
                        request_id=gc.request_id_for("conv-9", 3))
    req = api[0]
    f = fields(req)
    assert req.headers["x-api-request-id"] == "conv-9:3" and req.headers["x-api-key-id"] == KEY
    assert json.loads(f["keyterms"]) == ["Dr. Kulkarni", "OPD"]
    assert f["topic"] == "The caller is saying their name."
    assert json.loads(f["speaker_names"]) == ["Sunita", "Rahul", "A", "B"]          # at most four
    assert f["enable_substitution"] == "true"
    assert json.loads(f["substitution_map"]) == [{"patterns": ["Dr. Call Karni"], "replacement": "Dr. Kulkarni"}]
    assert f["previous_text"] == "x" * 600                                         # the last 600 characters
    assert out["request_id"] == "conv-9:3" and out["language"] == "hi-IN" and out["processing_time"] == 0.25


def test_nothing_is_sent_for_empty_hints_and_at_most_ten_rules(api):
    gc.transcribe(b"RIFFdata")
    req = api[0]
    f = fields(req)
    assert set(f) == {"language_code", "model", "format"} and "x-api-request-id" not in req.headers
    rules = [{"heard": f"heard {i}", "meant": f"meant {i}"} for i in range(14)]
    gc.transcribe(b"RIFFdata2", substitution_map=rules + [{"heard": "", "meant": "skipped"}, "junk"])
    assert len(json.loads(fields(api[1])["substitution_map"])) == 10
    gc.transcribe(b"RIFFdata3", substitution_map=[{"heard": "", "meant": "skipped"}])
    assert "enable_substitution" not in fields(api[2]) and "substitution_map" not in fields(api[2])


def test_the_cache_key_separates_different_rules(api, monkeypatch):
    monkeypatch.setenv("GNANI_CACHE_MB", "8")
    gc.transcribe(b"RIFFsame")
    gc.transcribe(b"RIFFsame")
    gc.transcribe(b"RIFFsame", substitution_map=[{"heard": "a b", "meant": "A B"}])
    assert len(api) == 2


def test_request_ids_are_plain_ascii():
    assert gc.request_id_for("c1", 4) == "c1:4" and gc.request_id_for("c1") == "c1" and gc.request_id_for(None, None) is None
    assert gc.request_id_for("a b/ü", "7 ") == "ab:7" and gc.request_id_for(None, "t1") == "t1"


# ------------------------------------------------------------------------------- what the caller is about to say

@pytest.mark.parametrize("text,kind", [
    ("May I have your name please?", "name"), ("What name should I put the booking under?", "name"),
    ("Who is the appointment for?", "name"), ("आपका नाम क्या है?", "name"), ("किसके नाम से बुक करूँ?", "name"),
    ("aapka naam kya hai?", "name"), ("तुमचे नाव सांगा", "name"), ("कोणाच्या नावाने बुक करू?", "name"),
    ("Which day and time would suit you?", "when"), ("When would you like to come?", "when"),
    ("आपको कब आना है?", "when"), ("कौन सा दिन ठीक रहेगा?", "when"), ("कितने बजे आना चाहेंगे?", "when"),
    ("तुम्हाला कधी यायचे आहे?", "when"), ("कोणत्या दिवशी येऊ शकता?", "when"), ("kitne baje aayenge?", "when"),
    ("What is your mobile number?", "digits"), ("Could I have a contact number?", "digits"),
    ("आपका मोबाइल नंबर बताइए", "digits"), ("तुमचा मोबाईल क्रमांक सांगा", "digits"),
    ("Sunita, Thursday 5 pm. Is that right?", "yes_no"), ("So that is Sunita on Thursday at 5, correct?", "yes_no"),
    ("Shall I book it?", "yes_no"), ("सुनीता, गुरुवार शाम 5 बजे, सही है?", "yes_no"), ("क्या मैं बुक कर दूँ?", "yes_no"),
    ("सुनीता, गुरुवारी संध्याकाळी 5, बरोबर ना?", "yes_no"), ("बुक करू का?", "yes_no"),
    ("How can I help you today?", "open"), ("नमस्ते, मैं आपकी कैसे मदद कर सकती हूँ?", "open"), ("", "open"),
])
def test_the_expectation_detector(text, kind):
    got = expect.detect(text)
    assert got["primary"] == kind, (text, got)


def test_a_readback_beats_the_details_it_repeats_and_two_asks_keep_their_order():
    readback = "I have Sunita Rao on Thursday 12 Nov at 5 pm, mobile ending 3210. Is that right?"
    assert expect.detect(readback)["kinds"] == ["yes_no"]
    assert expect.detect("Your mobile number and name please?")["kinds"] == ["digits", "name"]
    assert expect.detect("आपका नाम और मोबाइल नंबर बताइए")["kinds"] == ["name", "digits"]
    assert expect.detect("Which time is okay?")["kinds"] == ["when"]            # "okay?" tags a question, it is not a read-back
    assert expect.detect("Sure. Which day works? Thanks!")["kinds"] == ["when"]  # the last sentence that asks wins


def test_expectation_terms_and_labels():
    t = expect.terms_for(["when"])
    for w in ("Monday", "सोमवार", "मंगळवार", "November", "नवंबर", "नोव्हेंबर", "उद्या"):
        assert w in t
    assert set(["हाँ", "नहीं", "हो", "नाही", "बरोबर", "yes", "no", "correct"]) <= set(expect.terms_for(["yes_no"]))
    assert expect.label_for(["name", "digits"]) == "a name and digits"
    assert "name" in expect.topic_for(["name"], "the front desk of a clinic").lower()
    assert len(expect.topic_for(["when"], "x", ["n"] * 900, ["m"] * 900)) <= 1600


# ----------------------------------------------------------------------------------------- hints, events, flow

def fake_run(prev_agent=None, history=None, **kw):
    events = []
    run = types.SimpleNamespace(
        agent={"id": "a1", "name": "Sunrise Clinic", "template_id": "clinic", "supported_languages": ["hi", "en"]},
        agent_id="a1", user_id="u", storage_mode="local", history=history or [], turn_id="turn1234abcd",
        asr_previous_agent_text=prev_agent, conversation_id=kw.pop("conversation_id", "conv-7"),
        emit=lambda type_, **body: events.append((type_, body)), **kw)
    return run, events


def no_entities(monkeypatch):
    from services import query_understanding as qu
    monkeypatch.setattr(qu, "get_profile", lambda *a, **k: None)
    return qu


def test_turn_hints_listen_for_a_name_with_the_desks_words(monkeypatch):
    qu = no_entities(monkeypatch)
    hearing.add_correction("a1", "Dr. Call Karni", "Dr. Kulkarni", conversation_id="12")
    monkeypatch.setattr("services.caller_profiles.known_names", lambda cid: ["Sunita", "Rahul"], raising=False)
    from services import turn_pipeline
    run, events = fake_run("May I have your name?")
    hints = {}
    turn_pipeline.gnani_turn_hints(run, hints, qu)
    assert hints["expecting"] == "name" and hints["speaker_names"] == ["Sunita", "Rahul"]
    assert "saying their name" in hints["topic"] and "clinic" in hints["topic"] and "Sunrise Clinic" in hints["topic"]
    assert hints["keyterms"][:1] == ["Dr. Kulkarni"] and "Sunita" in hints["keyterms"]
    assert hints["substitution_map"] == [{"patterns": ["Dr. Call Karni"], "replacement": "Dr. Kulkarni"}]
    assert hints["request_id"] == "conv-7:turn1234"
    assert events[0][0] == "hearing" and events[0][1]["label"] == "Listening for: a name (Sunita, Rahul)"


def test_turn_hints_when_digits_yes_no_and_open(monkeypatch):
    qu = no_entities(monkeypatch)
    from services import turn_pipeline
    seen = {}
    for prev, kind, word in [("कब आना है?", "when", "सोमवार"), ("Your phone number please", "digits", "zero"),
                             ("Is that right?", "yes_no", "बरोबर"), ("How can I help?", "open", None)]:
        run, events = fake_run(prev)
        hints = {}
        turn_pipeline.gnani_turn_hints(run, hints, qu)
        seen[kind] = (hints, events)
        assert hints["expecting"] == kind
        assert word is None or word in hints["keyterms"]
    assert seen["when"][1][0][1]["label"] == "Listening for: a day or time"
    assert seen["digits"][1][0][1]["label"] == "Listening for: digits"
    assert seen["yes_no"][1][0][1]["label"] == "Listening for: yes or no"
    assert seen["open"][1] == []                                                  # nothing to say for open speech


def test_the_last_agent_row_of_the_history_is_used_and_missing_known_names_is_fine(monkeypatch):
    qu = no_entities(monkeypatch)
    from services import turn_pipeline
    monkeypatch.delattr("services.caller_profiles.known_names", raising=False)
    run, _ = fake_run(None, history=[{"role": "user", "text_content": "hi"},
                                     {"role": "agent", "text_content": "तुमचे नाव सांगा"}])
    hints = {}
    turn_pipeline.gnani_turn_hints(run, hints, qu)
    assert hints["expecting"] == "name" and hints["speaker_names"] == [] and hints["request_id"] == "conv-7:2"
    run, _ = fake_run("Your name?", conversation_id=None)
    hints = {}
    turn_pipeline.gnani_turn_hints(run, hints, qu)
    assert hints["request_id"] == "turn1234"


def test_a_broken_known_names_never_fails_a_turn(monkeypatch):
    qu = no_entities(monkeypatch)
    from services import turn_pipeline

    def boom(cid):
        raise RuntimeError("store down")
    monkeypatch.setattr("services.caller_profiles.known_names", boom, raising=False)
    hints = {}
    turn_pipeline.gnani_turn_hints(fake_run("Your name?")[0], hints, qu)
    assert hints["speaker_names"] == [] and hints["expecting"] == "name"


def test_keyterms_stay_within_two_hundred_with_the_environment_ones(monkeypatch):
    qu = no_entities(monkeypatch)
    ents = [types.SimpleNamespace(text=f"Entity{i}", kind="place", count=1) for i in range(300)]
    monkeypatch.setattr(qu, "get_profile", lambda *a, **k: types.SimpleNamespace(lexicon=types.SimpleNamespace(entities=ents), prompt=None))
    monkeypatch.setenv("GNANI_KEYTERMS", "OPD, MRI")
    from services import turn_pipeline
    hearing.add_vocabulary("a1", "Shivajinagar")
    hints = {}
    turn_pipeline.gnani_turn_hints(fake_run("Which day?")[0], hints, qu)
    merged = gnani.merge_keyterms(gnani.env_keyterms(), hints["keyterms"])
    assert len(merged) == 200 and merged[2] == "Shivajinagar" and "Monday" in merged


def test_events_for_a_rule_that_probably_ran(monkeypatch):
    rules = [{"id": "H1", "heard": "Dr. Call Karni", "meant": "Dr. Kulkarni", "learnt_from": {"conversation_id": "12", "turn": 1}},
             {"id": "H2", "heard": "o p d", "meant": "OPD", "learnt_from": "owner"}]
    assert [r["id"] for r in hearing.probably_applied(rules, "I want dr. kulkarni at the OPD")] == ["H1", "H2"]
    assert hearing.probably_applied(rules, "Dr. Call Karni dr. kulkarni") == []         # heard is still there: not applied
    assert hearing.probably_applied(rules, "hello") == []
    assert hearing.corrected_label(rules[0]) == (
        "Probably heard 'Dr. Call Karni', corrected to 'Dr. Kulkarni' (rule learnt from call #12)")
    assert hearing.corrected_label(rules[1]).endswith("(rule you added)")


def test_speech_to_text_sends_the_hints_records_the_ids_and_emits_the_rule_event(monkeypatch, api):
    qu = no_entities(monkeypatch)
    from services import asr, turn_pipeline
    hearing.add_correction("a1", "Dr. Call Karni", "Dr. Kulkarni", conversation_id="12", turn=1)

    def reply(request):
        return httpx.Response(200, json={"text": "Dr. Kulkarni se milna hai", "detected_language_code": "hi-IN",
                                         "processing_time": 0.4, "request_id": request.headers["x-api-request-id"]})
    monkeypatch.setattr(gc, "_client", lambda: httpx.Client(transport=httpx.MockTransport(lambda r: (api.append(r), reply(r))[1])))
    run, events = fake_run("Which doctor would you like?")
    hints = {}
    turn_pipeline.gnani_turn_hints(run, hints, qu)
    from tests.test_gnani_providers import wav_bytes
    token = qu.ASR_CTX.set(hints)
    try:
        text = asyncio.run(asr.speech_to_text(wav_bytes(), "turn", None))
    finally:
        qu.ASR_CTX.reset(token)
    assert text == "Dr. Kulkarni se milna hai"
    f = fields(api[-1])
    assert api[-1].headers["x-api-request-id"] == "conv-7:turn1234" and f["enable_substitution"] == "true"
    assert json.loads(f["substitution_map"])[0]["replacement"] == "Dr. Kulkarni"
    assert hints["detected_language_code"] == "hi-IN" and hints["request_id"] == "conv-7:turn1234"
    assert hints["processing_time"] == 0.4 and hints["detected_language"] == "hi" and hints["rules_applied"] == ["H1"]
    corrected = [b for t, b in events if b.get("kind") == "corrected"]
    assert corrected and corrected[0]["probable"] is True and corrected[0]["label"].startswith("Probably heard 'Dr. Call Karni'")
    assert hearing.list_rules("a1")[0]["uses"] == 1 and hearing.list_rules("a1")[0]["last_used_at"]


def test_hearing_events_go_through_the_call_log():
    call_events.reset()
    ev = call_events.emit("a1", "c1", "t1", "hearing", kind="listening", label="Listening for: a name")
    assert ev["type"] == "hearing" and "hearing" in call_events.EVENT_TYPES
    assert call_events.since("a1")[0]["label"] == "Listening for: a name"


# ------------------------------------------------------------------------------------------ Marathi numbers (6)

def test_marathi_replies_always_name_marathi_never_auto():
    assert gnani.tts_language_code("mr") == "mr-IN" and gnani.tts_language_code("mr-IN") == "mr-IN"
    assert gnani.tts_language_code("marathi") == "mr-IN" and gnani.tts_language_code("Marathi") == "mr-IN"
    assert gnani.tts_language_code("hi") == "hi-IN" and gnani.tts_language_code("hinglish") == "hi-en"
    assert gnani.tts_language_code("fr") == "auto" and gnani.tts_language_code("") == "auto"
    # an unknown language with Devanagari text is never "auto": Marathi words decide, otherwise Hindi
    assert gnani.tts_language_code("auto", "तुमची भेट उद्या सकाळी आहे") == "mr-IN"
    assert gnani.tts_language_code("auto", "आपकी अपॉइंटमेंट कल है") == "hi-IN"
    assert gnani.tts_language_code("auto", "Your appointment is tomorrow") == "auto"


def test_the_timbre_request_for_a_marathi_reply_carries_mr_in(monkeypatch):
    sent = []
    monkeypatch.setattr(gnani.client, "synthesize", lambda text, **kw: (sent.append(kw), b"\x00\x00" * 80)[1])
    for lang in ("mr", "hi", "hinglish"):
        gnani.synth("तुमची भेट १२ तारखेला आहे", lang, phone=True)
    assert [kw["language"] for kw in sent] == ["mr-IN", "hi-IN", "hi-en"]

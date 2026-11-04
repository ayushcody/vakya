"""Agent templates, with embeddings faked and no language model."""
import hashlib
import time
from pathlib import Path

import numpy as np
import pytest

LOCAL = {"storage-mode": "local"}
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "agents"


class FakeEmbedding:
    loaded = True

    def encode(self, texts, *a, **k):
        out = []
        for t in texts:
            v = np.zeros(64, dtype=np.float32)
            for w in t.lower().split():
                v[int(hashlib.md5(w.strip(".,?!").encode()).hexdigest(), 16) % 64] += 1.0
            out.append(v)
        return np.stack(out)


@pytest.fixture()
def env(app_client, monkeypatch, tmp_path):
    from core.runtime import runtime as backend
    from services import llm
    from services.local_store import LocalStore
    from routers import templates as tpl_router

    monkeypatch.setattr(backend, "local_db", LocalStore(str(tmp_path)))
    monkeypatch.setattr(backend, "LOCAL_AUDIO_DIR", str(tmp_path))
    monkeypatch.setattr(backend, "LOCAL_VOICES_FILE", str(tmp_path / "voices.json"))
    monkeypatch.setattr(backend, "embedding_model", FakeEmbedding())
    if hasattr(backend.limiter, "reset"):
        backend.limiter.reset()

    calls = []

    def fake_chat(messages, **kw):  # any call here means a template used the LLM
        calls.append(messages)
        raise AssertionError("no LLM call expected")

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(backend, "chat_calls", calls, raising=False)
    return backend


def _raw(tid):
    import json
    return json.loads((TEMPLATES_DIR / f"{tid}.json").read_text(encoding="utf-8"))


IDS = ["clinic", "hotel", "salon", "restaurant", "office", "institute", "service_centre", "fitness"]
ACCENTS = {"violet", "orange", "blue", "green", "rose", "amber", "teal", "slate"}


def test_list_templates(app_client, env):
    r = app_client.get("/api/agent-templates", headers=LOCAL)
    assert r.status_code == 200
    templates = r.json()["templates"]
    # eight kinds of front desk, in the order of docs/planning/PLAN_FRONT_DESKS_AND_SCHEDULES.md section 2
    assert [t["id"] for t in templates] == IDS
    for t in templates:
        assert set(t) == {"id", "name", "type_label", "tagline", "illustration", "accent", "persona", "role", "languages",
                          "greeting", "document", "suggested_questions", "sample_pdf", "audience", "org_label",
                          "org_placeholder", "personalise", "schedules"}
        assert t["illustration"] == t["id"] and t["accent"] in ACCENTS
        assert t["type_label"] and t["tagline"] and not t["tagline"].endswith("\n")
        p = t["persona"]
        assert p["tone"] and len(p["traits"]) == 3 and set(p["sample_line"]) == {"en", "hi"}
        assert all(p["sample_line"].values())
        assert t["audience"].startswith("For ") and t["org_label"]
        assert "{org}" in t["greeting"] and "{org}" in _raw(t["id"])["system_instructions"]
        assert {"en", "hi"} <= set(t["languages"])
        doc = TEMPLATES_DIR / t["document"]
        text = doc.read_text(encoding="utf-8")
        assert doc.is_file() and "{org}" in text and "Sample details" in text
        assert len(t["suggested_questions"]) >= 3 and t["schedules"]
        for spec in t["schedules"]:
            assert spec["kind"] in ("slots", "nights") and spec["name"] and spec["capacity"] >= 1


def test_the_default_schedules_match_the_plan(app_client, env):
    by_id = {t["id"]: t for t in app_client.get("/api/agent-templates", headers=LOCAL).json()["templates"]}

    def only(tid):
        [spec] = by_id[tid]["schedules"]
        return spec
    clinic = only("clinic")
    assert (clinic["name"], clinic["slot_minutes"], clinic["capacity"]) == ("Doctor consultations", 15, 1)
    assert clinic["week"]["mon"] == [{"start": "10:00", "end": "13:00"}, {"start": "17:00", "end": "20:00"}]
    assert clinic["week"]["sat"] == clinic["week"]["mon"] and clinic["week"]["sun"] == []
    hotel = only("hotel")
    assert (hotel["kind"], hotel["capacity"], hotel["check_in"], hotel["check_out"]) == ("nights", 10, "12:00", "11:00")
    salon = only("salon")
    assert (salon["slot_minutes"], salon["capacity"], salon["week"]["mon"], salon["week"]["tue"]) == \
        (30, 2, [], [{"start": "10:00", "end": "20:00"}])
    restaurant = only("restaurant")
    assert restaurant["capacity"] == 8 and all(len(restaurant["week"][d]) == 2 for d in restaurant["week"])
    assert restaurant["week"]["sun"][0] == {"start": "12:00", "end": "15:30"}
    office = only("office")
    assert office["week"]["fri"] == [{"start": "10:00", "end": "13:30"}, {"start": "14:30", "end": "18:00"}]
    assert office["week"]["sat"] == []
    assert only("institute")["week"]["sat"] == [{"start": "10:00", "end": "17:00"}]
    assert (only("service_centre")["slot_minutes"], only("service_centre")["capacity"]) == (60, 3)
    fitness = only("fitness")
    assert fitness["capacity"] == 5 and fitness["week"]["sun"] == [{"start": "06:00", "end": "10:00"},
                                                                    {"start": "17:00", "end": "21:00"}]


def test_every_default_schedule_is_valid(app_client, env):
    from services import schedules
    for t in app_client.get("/api/agent-templates", headers=LOCAL).json()["templates"]:
        for spec in t["schedules"]:
            schedules.normalise_schedule({**spec, "accent": t["accent"]})


def test_no_template_invents_a_business_or_a_number(app_client, env):
    import re
    for tid in IDS:
        raw = _raw(tid)
        text = (TEMPLATES_DIR / raw["document"]).read_text(encoding="utf-8") + json_text(raw)
        assert not re.search(r"\b[6-9]\d{9}\b", text) and not re.search(r"\b\d{3,4}[- ]\d{4}[- ]\d{4}\b", text)
        assert not re.search(r"\b[A-Z]{5}\d{4}[A-Z]\b", text) and not re.search(r"\b\d{4}\s?\d{4}\s?\d{4}\b", text)
        for old in ("Sunrise", "Pune", "Joshi", "Kulkarni", "Lakeside"):
            assert old not in text, (tid, old)


def json_text(raw: dict) -> str:
    import json
    return json.dumps(raw, ensure_ascii=False)


def test_create_from_template_local(app_client, env):
    r = app_client.post("/api/agents/from-template", headers=LOCAL, json={"template_id": "clinic"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert set(a) == {"id", "name", "greeting", "system_instructions", "supported_languages", "voice_id",
                      "disclose_ai", "tools", "template_id", "created_at", "document_count", "schedules"}
    assert a["name"] == "Clinic front desk" and a["supported_languages"] == ["en", "hi", "mr"]
    assert "our clinic" in a["greeting"] and "{" not in a["greeting"] + a["system_instructions"]
    assert a["tools"] == ["schedules"] and a["template_id"] == "clinic"
    assert a["document_count"] >= 1 and a["voice_id"] is None
    assert env.chat_calls == []
    [s] = a["schedules"]
    assert s["agent_id"] == a["id"] and s["name"] == "Doctor consultations" and s["accent"] == "teal"
    assert s["summary"]["hours_text"] == "Mon to Sat, 10:00 to 13:00 and 17:00 to 20:00"
    got = app_client.get(f"/api/agents/{a['id']}", headers=LOCAL).json()
    assert got["document_count"] == a["document_count"] and got["template_id"] == "clinic"
    docs = app_client.get(f"/api/agents/{a['id']}/documents", headers=LOCAL).json()["documents"]
    assert docs[0]["filename"] == "Sample - clinic.md" and docs[0]["chunk_count"] >= 1
    listed = app_client.get(f"/api/schedules?agent_id={a['id']}", headers=LOCAL).json()["schedules"]
    assert [x["id"] for x in listed] == [s["id"]]


def test_a_hotel_gets_a_nightly_schedule(app_client, env):
    a = app_client.post("/api/agents/from-template", headers=LOCAL, json={"template_id": "hotel"}).json()
    [s] = a["schedules"]
    assert s["kind"] == "nights" and s["capacity"] == 10 and (s["check_in"], s["check_out"]) == ("12:00", "11:00")
    assert s["accent"] == "blue" and s["summary"]["today_free"] == 10


def test_deleting_the_agent_removes_its_schedules(app_client, env):
    a = app_client.post("/api/agents/from-template", headers=LOCAL, json={"template_id": "salon"}).json()
    assert app_client.delete(f"/api/agents/{a['id']}", headers=LOCAL).status_code == 200
    assert app_client.get(f"/api/schedules?agent_id={a['id']}", headers=LOCAL).json()["schedules"] == []


def _post(app_client, **body):
    return app_client.post("/api/agents/from-template", headers=LOCAL, json={"template_id": "clinic", **body})


def test_personalised_clinic(app_client, env):
    r = _post(app_client, org_name="  Lotus Care Clinic ", city="Nashik",
              details="Open 8 to 5. Callers ask about vaccines.", languages=["en", "mr"])
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["name"] == "Lotus Care Clinic Reception"
    assert "Lotus Care Clinic" in a["greeting"] and "{" not in a["greeting"]
    assert "Lotus Care Clinic in Nashik" in a["system_instructions"] and "{" not in a["system_instructions"]
    assert a["supported_languages"] == ["en", "mr"] and a["tools"] == ["schedules"]
    docs = app_client.get(f"/api/agents/{a['id']}/documents", headers=LOCAL).json()["documents"]
    assert sorted(d["filename"] for d in docs) == ["About Lotus Care Clinic.md", "Sample - clinic.md"]
    chunks = " ".join(c["chunk_text"] for c in env.local_db.select("document_chunks", lambda r: True))
    assert "Lotus Care Clinic is a family clinic" in chunks and "Nashik" in chunks and "{org}" not in chunks
    assert env.chat_calls == []


def test_personalised_without_city_drops_the_city(app_client, env):
    a = _post(app_client, org_name="Lotus Care Clinic").json()
    assert "of Lotus Care Clinic." in a["system_instructions"] and " in {city}" not in a["system_instructions"]
    assert "{" not in a["system_instructions"]
    docs = app_client.get(f"/api/agents/{a['id']}/documents", headers=LOCAL).json()["documents"]
    assert [d["filename"] for d in docs] == ["Sample - clinic.md"]  # no About file without details
    chunks = " ".join(c["chunk_text"] for c in env.local_db.select("document_chunks", lambda r: True))
    assert "{city}" not in chunks and "Location:" not in chunks


def test_sample_document_is_labelled_and_optional(app_client, env):
    a = _post(app_client, org_name="Lotus Care Clinic", include_sample=False).json()
    assert a["document_count"] == 0
    b = _post(app_client, org_name="Lotus Care Clinic").json()
    chunks = env.local_db.select("document_chunks", lambda r: True)
    assert any("[SAMPLE DOCUMENT" in c["chunk_text"] for c in chunks)
    assert b["document_count"] == 1


@pytest.mark.parametrize("tid", IDS)
def test_every_template_personalises(app_client, env, tid):
    r = app_client.post("/api/agents/from-template", headers=LOCAL,
                        json={"template_id": tid, "org_name": "Acme Place", "city": "Nagpur", "include_sample": False})
    assert r.status_code == 200, r.text
    a = r.json()
    assert "Acme Place" in a["name"] and a["template_id"] == tid
    assert "Acme Place" in a["greeting"] and "Acme Place in Nagpur" in a["system_instructions"]
    assert "{" not in a["greeting"] + a["system_instructions"]
    assert "scheduling tools" in a["system_instructions"] and "clearly says yes" in a["system_instructions"]
    assert len(a["schedules"]) == len(_raw(tid)["schedules"]) and a["tools"] == ["schedules"]


@pytest.mark.parametrize("tid", IDS)
def test_every_template_without_a_name_reads_naturally(app_client, env, tid):
    a = app_client.post("/api/agents/from-template", headers=LOCAL, json={"template_id": tid}).json()
    fallback = _raw(tid)["personalise"]["fallback_org"]
    assert fallback in a["greeting"] and a["name"] == _raw(tid)["name"]
    assert "{" not in a["greeting"] + a["system_instructions"]
    assert a["document_count"] == 1


@pytest.mark.parametrize("body", [
    {"org_name": "   "}, {"org_name": "x" * 81}, {"org_name": "A", "city": "c" * 61},
    {"org_name": "A", "details": "d" * 1001}, {"org_name": "A", "languages": []},
    {"org_name": "A", "languages": ["english"]}, {"city": "Pune"},
])
def test_bad_overrides_are_rejected_before_anything_is_created(app_client, env, body):
    r = _post(app_client, **body)
    assert r.status_code == 422 and r.json()["code"] == "INVALID_TEMPLATE_INPUT", r.text
    assert env.local_db.select("agents", lambda r: True) == []
    assert app_client.get("/api/schedules", headers=LOCAL).json()["schedules"] == []


def test_unknown_template_404(app_client, env):
    r = app_client.post("/api/agents/from-template", headers=LOCAL, json={"template_id": "nope"})
    assert r.status_code == 404 and r.json()["code"] == "TEMPLATE_NOT_FOUND"


@pytest.mark.parametrize("old", ["clinic_receptionist", "office_front_desk", "college_admissions", "emi_reminder",
                                 "school_fee_reminder", "farmer_helpline", "hindi_tutor", "document_qa"])
def test_removed_templates_are_gone(app_client, env, old):
    r = app_client.post("/api/agents/from-template", headers=LOCAL, json={"template_id": old})
    assert r.status_code == 404 and r.json()["code"] == "TEMPLATE_NOT_FOUND"
    assert sorted(p.stem for p in TEMPLATES_DIR.glob("*.json")) == sorted(IDS)


# ----------------------------------------------------------------------------------------- template_id on agents

def test_template_id_is_stored_returned_and_changeable(app_client, env):
    a = _post(app_client).json()
    assert a["template_id"] == "clinic"
    assert app_client.get(f"/api/agents/{a['id']}", headers=LOCAL).json()["template_id"] == "clinic"
    listed = app_client.get("/api/agents", headers=LOCAL).json()["agents"]
    assert [x["template_id"] for x in listed] == ["clinic"]
    r = app_client.put(f"/api/agents/{a['id']}", headers=LOCAL, json={"template_id": "hotel"})
    assert r.status_code == 200 and r.json()["template_id"] == "hotel"
    r = app_client.put(f"/api/agents/{a['id']}", headers=LOCAL, json={"name": "Renamed"})   # left out: unchanged
    assert r.json()["template_id"] == "hotel" and r.json()["name"] == "Renamed"
    r = app_client.put(f"/api/agents/{a['id']}", headers=LOCAL, json={"template_id": None})   # null clears it
    assert r.status_code == 200 and r.json()["template_id"] is None
    bad = app_client.put(f"/api/agents/{a['id']}", headers=LOCAL, json={"template_id": "spaceship"})
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_TEMPLATE_ID"


def test_agents_without_a_template_read_as_null(app_client, env):
    row = env.local_db.insert("agents", {"user_id": "local-user", "project_id": "local_project", "name": "Old",
                                         "system_instructions": "x", "greeting": "", "supported_languages": ["en"]})
    assert app_client.get(f"/api/agents/{row['id']}", headers=LOCAL).json()["template_id"] is None

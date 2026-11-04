"""One-tick consent (CONSENT_MODE=tick, the default) and the strict mode kept next to it.

Models are faked as in test_consent.py, so this runs on CPU."""
import asyncio
import glob
import hashlib
from pathlib import Path

import pytest

from services import consent_record, provenance
from tests.test_compliance import _generate_with, env, isolate  # noqa: F401  (fixtures)
from tests.test_consent import LOCAL, backend_mod  # noqa: F401  (backend_mod is a fixture)
from tests.test_provenance import fake_seal  # noqa: F401  (fake_seal is a fixture)


@pytest.fixture(autouse=True)
def tick_mode(monkeypatch):
    """backend_mod sets strict for the old tests; these tests choose their own mode."""
    monkeypatch.setenv("CONSENT_MODE", "tick")


@pytest.fixture()
def no_checks(env, monkeypatch):  # noqa: F811
    """Tick mode must never listen to a recording. (backend_mod sets strict, so set tick after it.)"""
    monkeypatch.setenv("CONSENT_MODE", "tick")

    async def boom(*a, **k):
        raise AssertionError("tick mode must not run the sentence check")

    monkeypatch.setattr(env, "verify_consent", boom)
    return env


def tick_clone(client, ref_wav, ticked="true", name="Test", consent_name=None):
    data = {"display_name": name, "language": "en"}
    if ticked is not None:
        data["consent_given"] = ticked
    if consent_name is not None:
        data["consent_name"] = consent_name
    return client.post("/api/voices/clone", headers=LOCAL, data=data,
                       files={"reference_audio": ("ref.wav", ref_wav.read_bytes(), "audio/wav")})


def nothing_saved(env):
    files = [f for f in glob.glob(str(Path(env.LOCAL_AUDIO_DIR) / "*")) if Path(f).name != "local_db"]
    return env.get_local_voices() == [] and not files


# ---- mode ---------------------------------------------------------------------------------------------------------

def test_the_mode_defaults_to_tick_and_ignores_unknown_values(monkeypatch):
    monkeypatch.delenv("CONSENT_MODE", raising=False)
    assert consent_record.consent_mode() == "tick"
    for value, want in (("strict", "strict"), (" STRICT ", "strict"), ("tick", "tick"), ("", "tick"), ("lax", "tick")):
        monkeypatch.setenv("CONSENT_MODE", value)
        assert consent_record.consent_mode() == want


def test_the_mode_endpoint_tells_the_wizard_which_flow_to_show(app_client, monkeypatch):
    r = app_client.get("/api/consent/mode", headers=LOCAL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["consent_mode"] == "tick" and body["statement"] == consent_record.TICK_STATEMENT
    assert body["retention_days"] == 365 and body["notice_version"] == consent_record.NOTICE_VERSION
    monkeypatch.setenv("CONSENT_MODE", "strict")
    assert app_client.get("/api/consent/mode", headers=LOCAL).json()["consent_mode"] == "strict"


# ---- tick mode ----------------------------------------------------------------------------------------------------

def test_tick_clone_needs_no_recording_or_sentence(app_client, no_checks, ref_wav):
    r = tick_clone(app_client, ref_wav, consent_name="Priya Sharma")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["consent"]["verified"] is True and body["consent"]["method"] == "tick"
    rec = body["consent_record"]
    assert rec["method"] == "tick" and rec["consented_by"] == "Priya Sharma"
    assert rec["consent_statement"] == consent_record.TICK_STATEMENT
    assert rec["purpose"] == "Create an AI version of this voice for use in Vakya by the owner of this Vakya studio."
    assert rec["challenge_sentence"] is None and rec["challenge_code"] is None and rec["challenge_id"] is None
    assert rec["transcript_match_score"] is None
    assert rec["consent_id"] == body["consent_id"] and rec["consented_at"] and rec["retain_until"]
    assert rec["data_stored"] == list(consent_record.DATA_STORED_TICK)
    stored = Path(no_checks.LOCAL_AUDIO_DIR) / f"{body['id']}.wav"
    assert rec["sample_sha256"] == hashlib.sha256(
        no_checks.voice_file_read(str(stored), body["id"], "wav")).hexdigest()
    assert not glob.glob(str(Path(no_checks.LOCAL_AUDIO_DIR) / "consent_*"))  # no consent recording exists


def test_tick_record_is_served_stored_and_marks_the_voice_consented(app_client, no_checks, ref_wav, monkeypatch):
    monkeypatch.setenv("VAKYA_OPERATOR_NAME", "Acme Clinic")
    body = tick_clone(app_client, ref_wav).json()  # no name given: the operator is recorded
    row = [v for v in app_client.get("/api/voices", headers=LOCAL).json() if v["id"] == body["id"]][0]
    assert row["consent_verified"] is True and row["consent_id"] == body["consent_id"]
    rec = app_client.get(f"/api/voices/{body['id']}/consent-record", headers=LOCAL).json()
    assert rec["method"] == "tick" and rec["consented_by"] == "Acme Clinic" and rec["legacy"] is False
    assert rec["purpose"].endswith("for use in Vakya by Acme Clinic.")


def test_receipts_of_a_tick_voice_say_consent_is_active(app_client, no_checks, ref_wav):
    body = tick_clone(app_client, ref_wav).json()
    gen = _generate_with(no_checks, body["id"], name="Test")
    receipt = provenance.get_receipt(gen["receipt_id"])
    assert receipt["consent_id"] == body["consent_id"] and receipt["consent_verified"] is True
    assert provenance.consent_status(receipt) == "active"
    assert app_client.get(f"/api/receipts/{gen['receipt_id']}").json()["voice_consent_status"] == "active"


def test_tick_without_the_box_is_refused_and_saves_nothing(app_client, no_checks, ref_wav):
    for ticked in (None, "", "false", "no", "0"):
        r = tick_clone(app_client, ref_wav, ticked=ticked)
        assert r.status_code == 400, (ticked, r.text)
        assert r.json()["code"] == "CONSENT_REQUIRED"
    assert nothing_saved(no_checks)


@pytest.mark.parametrize("value", ["true", "True", "1", "on", "yes"])
def test_tick_accepts_the_usual_form_values_for_true(app_client, no_checks, ref_wav, value):
    no_checks.limiter.reset()  # clone is limited to 5/minute
    assert tick_clone(app_client, ref_wav, ticked=value).status_code == 200


def test_a_strict_style_request_without_the_box_is_not_enough_in_tick_mode(app_client, no_checks, ref_wav):
    wav = ref_wav.read_bytes()
    r = app_client.post("/api/voices/clone", headers=LOCAL,
                        data={"display_name": "T", "language": "en", "consent_confirmed": "true"},
                        files={"reference_audio": ("ref.wav", wav, "audio/wav"),
                               "consent_audio": ("consent.wav", wav, "audio/wav")})
    assert r.status_code == 400 and r.json()["code"] == "CONSENT_REQUIRED"
    assert nothing_saved(no_checks)


def test_withdrawing_a_tick_voice_deletes_it_and_marks_its_receipts(app_client, no_checks, ref_wav):
    body = tick_clone(app_client, ref_wav).json()
    gen = _generate_with(no_checks, body["id"], name="Test")
    r = app_client.delete(f"/api/voices/{body['id']}", headers=LOCAL)
    assert r.status_code == 200 and r.json()["withdrawn_at"]
    assert not glob.glob(str(Path(no_checks.LOCAL_AUDIO_DIR) / f"{body['id']}.*"))
    assert app_client.get(f"/api/voices/{body['id']}/consent-record", headers=LOCAL).status_code == 404
    assert app_client.get(f"/api/receipts/{gen['receipt_id']}").json()["voice_withdrawn_at"]


def test_retention_applies_to_tick_records(app_client, no_checks, ref_wav):
    body = tick_clone(app_client, ref_wav).json()
    voices = [dict(v) for v in no_checks.get_local_voices() if v["id"] == body["id"]]
    assert consent_record.expired_voices(voices) == []
    voices[0]["consent_record"] = {**voices[0]["consent_record"], "consented_at": "2020-01-01T00:00:00+00:00"}
    assert [v["id"] for v in consent_record.expired_voices(voices)] == [body["id"]]


def test_cloud_tick_consent_writes_the_record_and_a_consents_row(env, monkeypatch):  # noqa: F811
    calls = {"uploads": [], "rows": []}

    async def fake_upload(path, data, content_type="audio/wav"):
        calls["uploads"].append((path, content_type))

    class Table:
        def insert(self, row):
            calls["rows"].append(row)
            return self

        def execute(self):
            return None

    monkeypatch.setattr(env, "sb_upload", fake_upload)
    monkeypatch.setattr(env, "supabase", type("S", (), {"table": lambda self, n: Table()})())
    cid = "5f0c2a52-5a1e-4a52-8f3e-0d9d3b6a1c11"
    assert asyncio.run(env.record_tick_consent("u1", "cloud", cid, {"voice_id": "v"})) == cid
    assert calls["uploads"] == [(f"u1/consents/{cid}.json", "application/json")]
    assert calls["rows"] == [{"id": cid, "user_id": "u1", "challenge_text": consent_record.TICK_STATEMENT,
                              "audio_storage_path": f"u1/consents/{cid}.json"}]
    monkeypatch.setattr(env, "supabase", None)  # a failing write gives None, like the strict flow
    assert asyncio.run(env.record_tick_consent("u1", "cloud", cid, {})) is None


# ---- strict mode is unchanged -------------------------------------------------------------------------------------

def test_strict_mode_still_refuses_a_tick_only_request(app_client, backend_mod, ref_wav, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CONSENT_MODE", "strict")
    r = tick_clone(app_client, ref_wav)  # consent_given is ignored: a spoken recording is needed
    assert r.status_code == 400 and r.json()["code"] == "CONSENT_REQUIRED"
    assert "spoken consent recording" in r.json()["error"]


def test_strict_records_say_method_challenge(app_client, env, ref_wav, monkeypatch):  # noqa: F811
    from tests.test_compliance import clone

    monkeypatch.setenv("CONSENT_MODE", "strict")
    _, r = clone(app_client, ref_wav, monkeypatch=monkeypatch, backend=env)
    assert r.status_code == 200, r.text
    rec = r.json()["consent_record"]
    assert rec["method"] == "challenge" and rec["consented_by"] is None and rec["challenge_sentence"]
    assert rec["data_stored"] == list(consent_record.DATA_STORED)

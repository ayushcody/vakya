"""Every engine has a licence row, /api/engines names the licences, and receipts name the engine's licence
(services/licences.py)."""
from core.runtime import runtime as backend
from services import licences


def test_every_registered_engine_has_a_licence_row():
    for key in backend.ENGINE_REGISTRY:
        assert licences.licence_of(key), key
        assert licences.paid_use_ok(key), key


def test_engine_list_names_licences(app_client):
    rows = {e["id"]: e for e in app_client.get("/api/engines").json()}
    assert set(rows) == set(backend.ENGINE_REGISTRY)
    assert rows["timbre"]["licence"] == "Gnani AI API terms" and rows["timbre"]["paid_use"] is True


def test_health_lists_the_engines(app_client):
    body = app_client.get("/health").json()
    assert set(body["engines"]) == set(backend.ENGINE_REGISTRY)


def test_receipts_name_the_engine_licence(tmp_path, monkeypatch):
    from services import provenance

    monkeypatch.setattr(provenance, "_data_dir", str(tmp_path), raising=False)
    receipt = provenance.create_receipt(kind="generation", subject_id="g1", engine="timbre", voice_id=None,
                                        voice_name=None, consent_id=None, consent_verified=None, text="hello",
                                        audio_bytes=b"RIFF....", user_id="local-user")
    assert receipt["engine_licence"] == "Gnani AI API terms"
    assert "engine_licence" not in provenance.SIGNED_FIELDS

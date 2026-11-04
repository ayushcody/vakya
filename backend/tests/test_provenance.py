"""Provenance: a label, a signed receipt and a verify endpoint for every generated clip. A clip is
identified by its receipt (an exact file match) and its label."""
from tests.voice_fakes import fake_timbre
import asyncio
import base64
import io
import os
import uuid

import numpy as np
import pytest
import soundfile as sf

from services import provenance
from test_offline_agents import _turn, _turn_setup, env, make_agent  # noqa: F401  (env is a fixture)

SR = 24000


def _tone(seconds=2.0, sr=SR, freq=440.0, amp=0.3):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _wav(x, sr=SR) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, x, sr, format="WAV")
    return buf.getvalue()


@pytest.fixture()
def fake_seal(monkeypatch):
    """The receipt secret and no cloud lookups."""
    monkeypatch.setenv("VAKYA_RECEIPT_SECRET", "unit-test-secret")
    monkeypatch.setattr(provenance, "_get_supabase", lambda: None)


@pytest.fixture()
def backend_mod(app_client, monkeypatch, tmp_path, fake_seal):
    from core.runtime import runtime as backend

    monkeypatch.setattr(backend, "LOCAL_AUDIO_DIR", str(tmp_path))
    monkeypatch.setattr(backend, "supabase", None)
    backend.limiter.reset()
    return backend


class _Clip(dict):
    """A clip made for the tests: the labelled WAV (`wav_bytes`) plus its id and receipt id."""
    wav_bytes: bytes = b""


def _generate(backend, x=None, **kw):
    """Label and receipt one clip the way every spoken reply is (kind "generation": a plain clip receipt)."""
    x = _tone() if x is None else x
    clip_id = str(uuid.uuid4())
    voice_id = kw.get("voice_id")
    consent_id, consent_verified, _name = asyncio.run(backend.voice_consent(voice_id, "local", "local-user"))
    wav, receipt = provenance.apply_provenance(
        kind="generation", subject_id=clip_id, wav_bytes=_wav(x), text=kw.get("text", "hello there"), engine="timbre",
        voice_id=voice_id, voice_name="af_heart", consent_id=consent_id, consent_verified=consent_verified,
        user_id="local-user")
    clip = _Clip(id=clip_id, receipt_id=receipt["receipt_id"])
    clip.wav_bytes = wav
    return clip


def _verify(client, data: bytes, name="clip.wav"):
    return client.post("/api/verify", files={"file": (name, data, "audio/wav")})


# ---- receipts ---------------------------------------------------------------------------------------------------

def test_a_generation_gets_a_label_and_a_signed_receipt(backend_mod, tmp_path):
    original = _wav(_tone())
    gen = _generate(backend_mod)
    marked = gen.wav_bytes
    assert marked == provenance.label_wav(original, gen["receipt_id"]) and marked != original   # the samples are untouched

    receipt = provenance.get_receipt(gen["receipt_id"])
    assert receipt["kind"] == "generation" and receipt["generation_id"] == gen["id"]
    assert receipt["engine"] == "timbre" and receipt["voice_name"] == "af_heart"
    assert receipt["text_sha256"] == provenance.sha256_hex(b"hello there")
    assert receipt["audio_sha256"] == provenance.sha256_hex(marked)          # hash of the labelled file
    assert provenance.signature_valid(receipt)


def test_a_cloned_voice_receipt_carries_its_consent(backend_mod, monkeypatch):
    async def lookup(voice_id, storage_mode, user_id=None):
        return {"id": voice_id, "display_name": "Asha", "consent_id": "consent-1", "consent_verified": True}

    monkeypatch.setattr(backend_mod, "lookup_voice", lookup)
    gen = _generate(backend_mod, voice_id="voice-1")
    receipt = provenance.get_receipt(gen["receipt_id"])
    assert receipt["voice_id"] == "voice-1"
    assert receipt["consent_id"] == "consent-1" and receipt["consent_verified"] is True


def test_a_tampered_receipt_fails_its_signature(backend_mod):
    receipt = provenance.get_receipt(_generate(backend_mod)["receipt_id"])
    assert provenance.signature_valid(receipt)
    for field, value in (("consent_verified", True), ("voice_name", "Someone Else"), ("engine", "other"),
                         ("audio_sha256", "0" * 64)):
        assert not provenance.signature_valid({**receipt, field: value}), field
    assert not provenance.signature_valid({**receipt, "signature": "0" * 64})
    assert not provenance.signature_valid({k: v for k, v in receipt.items() if k != "signature"})


def test_a_different_secret_does_not_validate_old_receipts(backend_mod, monkeypatch):
    receipt = provenance.get_receipt(_generate(backend_mod)["receipt_id"])
    monkeypatch.setenv("VAKYA_RECEIPT_SECRET", "another-secret")
    assert not provenance.signature_valid(receipt)


def test_the_secret_is_generated_once_and_kept(monkeypatch, tmp_path):
    monkeypatch.delenv("VAKYA_RECEIPT_SECRET", raising=False)
    monkeypatch.setattr(provenance, "_data_dir", str(tmp_path))
    monkeypatch.setattr(provenance, "_secret_cache", None)
    first = provenance._secret()
    assert (tmp_path / "receipt_secret").read_text().strip() == first.decode()
    monkeypatch.setattr(provenance, "_secret_cache", None)
    assert provenance._secret() == first
    if os.name != "nt":
        assert oct((tmp_path / "receipt_secret").stat().st_mode & 0o777) == "0o600"


def test_an_agent_reply_gets_a_receipt(app_client, env, fake_seal, monkeypatch, ref_wav):
    _turn_setup(env, monkeypatch)
    fake_timbre(monkeypatch, lambda model, text, lang="en": _tone(1.0))
    a = make_agent(app_client)
    body = _turn(app_client, a["id"], ref_wav.read_bytes())
    receipt = provenance.get_receipt(body["receipt_id"])
    assert receipt["kind"] == "agent_reply" and receipt["turn_id"] and receipt["generation_id"] is None
    assert receipt["engine"] == "timbre" and receipt["consent_id"] is None
    assert receipt["text_sha256"] == provenance.sha256_hex(body["agent_text"].encode("utf-8"))
    assert provenance.signature_valid(receipt)

    served = app_client.get(body["agent_audio_url"], headers={"storage-mode": "local"}).content
    assert provenance.sha256_hex(served) == receipt["audio_sha256"]           # the stored reply is the labelled one


# ---- /api/verify ------------------------------------------------------------------------------------------------

def test_verify_an_exact_copy_is_made_with_vakya(app_client, backend_mod):
    gen = _generate(backend_mod)
    r = _verify(app_client, gen.wav_bytes)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "made_with_vakya"
    assert body["match"]["exact"] is True and body["match"]["signature_valid"] is True
    receipt = body["match"]["receipt"]
    assert receipt["receipt_id"] == gen["receipt_id"] and receipt["voice_name"] == "af_heart"
    assert "user_id" not in receipt and receipt["signature_valid"] is True


def test_verify_unknown_audio_is_not_detected(app_client, backend_mod):
    body = _verify(app_client, _wav(_tone(freq=330.0))).json()
    assert body["verdict"] == "not_detected"
    assert body["match"] == {"exact": False, "receipt": None, "signature_valid": False}


def test_verify_rejects_bad_uploads(app_client, backend_mod):
    r = app_client.post("/api/verify", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 415
    r = app_client.post("/api/verify", files={"file": ("clip.wav", b"not audio at all", "audio/wav")})
    assert r.status_code == 415
    r = app_client.post("/api/verify", files={"file": ("clip.wav", b"", "audio/wav")})
    assert r.status_code == 400


def test_verify_needs_no_sign_in(app_client, backend_mod, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "secret")
    r = _verify(app_client, _wav(_tone()))                           # no Authorization header at all
    assert r.status_code == 200


def test_get_receipt_is_public_and_reports_the_signature(app_client, backend_mod):
    gen = _generate(backend_mod)
    r = app_client.get(f"/api/receipts/{gen['receipt_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["signature_valid"] is True and body["generation_id"] == gen["id"] and "user_id" not in body
    assert app_client.get("/api/receipts/00000000-0000-0000-0000-000000000000").status_code == 404
    assert app_client.get("/api/receipts/bad%20id").status_code == 422


# ---- the real model ---------------------------------------------------------------------------------------------

# ---- Ed25519 receipts and the signed withdrawal log -----------------------------------------------------------------

def test_a_receipt_has_an_ed25519_signature_anyone_can_check(app_client, backend_mod):
    """Verified from the public key alone: no HMAC secret, no server call."""
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    gen = _generate(backend_mod)
    body = app_client.get(f"/api/receipts/{gen['receipt_id']}").json()
    key = app_client.get("/.well-known/vakya-receipt-key").json()
    assert key["alg"] == "Ed25519" and key["key_id"] == body["key_id"]
    assert body["signature_ed25519_valid"] is True
    import json
    canonical = json.dumps({k: body.get(k) for k in key["signed_fields"]}, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    Ed25519PublicKey.from_public_bytes(base64.b64decode(key["public_key"])).verify(
        base64.b64decode(body["signature_ed25519"]), canonical)  # raises if invalid


def test_a_tampered_receipt_fails_its_ed25519_signature(backend_mod):
    receipt = provenance.get_receipt(_generate(backend_mod)["receipt_id"])
    assert provenance.signature_ed25519_valid(receipt) is True
    for field, value in (("consent_verified", True), ("voice_name", "Someone Else"), ("audio_sha256", "0" * 64)):
        assert provenance.signature_ed25519_valid({**receipt, field: value}) is False, field
    assert provenance.signature_ed25519_valid({k: v for k, v in receipt.items() if k != "signature_ed25519"}) is None


def test_another_key_does_not_validate_a_receipt(backend_mod, monkeypatch):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    receipt = provenance.get_receipt(_generate(backend_mod)["receipt_id"])
    other = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    assert provenance.signature_ed25519_valid(receipt, base64.b64encode(other).decode()) is False


def test_the_signing_key_is_generated_once_and_kept(monkeypatch, tmp_path):
    monkeypatch.delenv("VAKYA_RECEIPT_SIGNING_KEY", raising=False)
    monkeypatch.setattr(provenance, "_data_dir", str(tmp_path))
    monkeypatch.setattr(provenance, "_signing_cache", None)
    first = provenance.public_key_b64()
    monkeypatch.setattr(provenance, "_signing_cache", None)
    assert provenance.public_key_b64() == first
    if os.name != "nt":
        assert oct((tmp_path / "receipt_ed25519.key").stat().st_mode & 0o777) == "0o600"


def test_a_receipt_made_before_ed25519_still_verifies_by_hmac(backend_mod):
    receipt = provenance.get_receipt(_generate(backend_mod)["receipt_id"])
    old = {k: v for k, v in receipt.items() if k not in ("signature_ed25519", "key_id")}
    pub = provenance.public_receipt(old)
    assert pub["signature_valid"] is True and pub["signature_ed25519_valid"] is None


def _withdraw(voice_id, reason="withdrawn"):
    return provenance.record_withdrawal(voice_id, reason)


def test_withdrawals_are_hash_chained_and_signed(app_client, backend_mod):
    first, second = _withdraw("voice-a"), _withdraw("voice-b", "retention_expired")
    assert first["prev"] == provenance.LOG_GENESIS and second["prev"] == first["row_hash"]
    assert provenance.withdrawal_row_valid(first) and provenance.withdrawal_row_valid(second)
    log = app_client.get("/api/withdrawals").json()
    assert log["check"]["ok"] is True and log["check"]["signed_rows"] == 2 and log["check"]["head"] == second["row_hash"]
    assert [r["voice_id"] for r in log["rows"]] == ["voice-a", "voice-b"]


def test_an_edited_deleted_or_reordered_withdrawal_row_breaks_the_chain(backend_mod):
    rows = [_withdraw("v1"), _withdraw("v2"), _withdraw("v3")]
    assert provenance.verify_withdrawal_log(rows)["ok"] is True
    edited = [rows[0], {**rows[1], "withdrawn_at": "2020-01-01T00:00:00"}, rows[2]]
    assert provenance.verify_withdrawal_log(edited)["broken_at"] == 1
    assert provenance.verify_withdrawal_log([rows[0], rows[2]])["broken_at"] == 1       # a deleted row
    assert provenance.verify_withdrawal_log([rows[1], rows[0], rows[2]])["broken_at"] == 0  # reordered
    assert provenance.verify_withdrawal_log([rows[0], {**rows[1], "sig": rows[2]["sig"]}, rows[2]])["broken_at"] == 1


def test_legacy_unsigned_withdrawal_rows_still_work_and_are_flagged(backend_mod, tmp_path):
    import json
    (tmp_path / "voice_withdrawals.jsonl").write_text(
        json.dumps({"voice_id": "old", "withdrawn_at": "2026-10-01T00:00:00", "reason": "withdrawn"}) + "\n")
    provenance.configure(data_dir=str(tmp_path))
    assert provenance.withdrawal_for("old")["withdrawn_at"] == "2026-10-01T00:00:00"
    new = _withdraw("new")
    assert new["prev"] == provenance.LOG_GENESIS  # the chain starts at the first signed row
    check = provenance.verify_withdrawal_log()
    assert check["ok"] is True and check["legacy_rows"] == 1 and check["signed_rows"] == 1


def test_a_receipt_of_a_withdrawn_voice_reports_a_signed_withdrawal(backend_mod):
    receipt = provenance.get_receipt(_generate(backend_mod)["receipt_id"])
    assert provenance.public_receipt(receipt)["voice_withdrawal_signed"] is None  # no voice, not withdrawn
    receipt = {**receipt, "voice_id": "voice-x"}
    row = _withdraw("voice-x")
    pub = provenance.public_receipt(receipt)
    assert pub["voice_withdrawn_at"] == row["withdrawn_at"]
    assert pub["voice_withdrawal_signed"] is True and pub["voice_withdrawal_row_hash"] == row["row_hash"]


# ---- receipt spec, SDK and consent status -----------------------------------------------------------------------

def _sdk():
    import os
    import sys
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "sdk", "verify")
    if path not in sys.path:
        sys.path.insert(0, path)
    import vakya_verify
    return vakya_verify


def test_the_sdk_verifies_a_receipt_made_by_the_backend_without_the_server(backend_mod):
    sdk = _sdk()
    gen = _generate(backend_mod)
    receipt = provenance.get_receipt(gen["receipt_id"])
    pub = provenance.public_key_b64()
    assert sdk.verify_receipt(receipt, pub) is True
    assert sdk.key_id(pub) == provenance.key_id(pub)
    assert sdk.verify_audio_sha256(receipt, gen.wav_bytes) is True
    assert sdk.verify_audio_sha256(receipt, gen.wav_bytes + b"x") is False
    assert sdk.verify_receipt({**receipt, "voice_name": "someone else"}, pub) is False   # a field was edited
    assert sdk.verify_receipt({**receipt, "version": 2}, pub) is False                    # unknown version
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    other = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    assert sdk.verify_receipt(receipt, other) is False


def test_the_sdk_checks_the_withdrawal_log_like_the_server(backend_mod):
    sdk = _sdk()
    rows = [_withdraw("v1"), _withdraw("v2"), _withdraw("v3")]
    pub = provenance.public_key_b64()
    assert sdk.verify_withdrawal_log(rows, pub)["ok"] is True
    assert sdk.verify_withdrawal_log([rows[0], rows[2]], pub)["broken_at"] == 1
    assert sdk.verify_withdrawal_log([rows[0], {**rows[1], "reason": "retention_expired"}, rows[2]], pub)["broken_at"] == 1
    assert sdk.voice_withdrawn({"voice_id": "v2"}, rows)["voice_id"] == "v2"
    assert sdk.voice_withdrawn({"voice_id": "nobody"}, rows) is None


def test_the_javascript_sdk_agrees_with_python(backend_mod, tmp_path):
    import json
    import os
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    gen = _generate(backend_mod)
    receipt = provenance.get_receipt(gen["receipt_id"])
    rows = [_withdraw("v1"), _withdraw("v2")]
    pub = provenance.public_key_b64()
    case = {"receipt": receipt, "bad": {**receipt, "engine": "edited"}, "rows": rows,
            "badrows": [rows[1], rows[0]], "pub": pub}
    (tmp_path / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    sdk_file = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))) / "sdk" / "verify" / "verify.mjs"
    case_path = (tmp_path / "case.json").as_posix()
    script = (
        "import {verifyReceipt,verifyWithdrawalLog} from '%s';"
        "import fs from 'node:fs';const c=JSON.parse(fs.readFileSync('%s','utf8'));"
        "console.log(JSON.stringify([verifyReceipt(c.receipt,c.pub),verifyReceipt(c.bad,c.pub),"
        "verifyWithdrawalLog(c.rows,c.pub).ok,verifyWithdrawalLog(c.badrows,c.pub).ok]));"
    ) % (sdk_file.as_uri(), case_path)
    (tmp_path / "run.mjs").write_text(script, encoding="utf-8")
    out = subprocess.run([node, str(tmp_path / "run.mjs")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [True, False, True, False]


def test_consent_status_says_what_the_consent_behind_a_receipt_is(backend_mod):
    preset = provenance.get_receipt(_generate(backend_mod)["receipt_id"])
    assert provenance.public_receipt(preset)["voice_consent_status"] == "preset_voice"
    cloned = {**preset, "voice_id": "vc-1", "consent_id": "c-1", "consent_verified": True}
    assert provenance.public_receipt(cloned)["voice_consent_status"] == "active"
    assert provenance.public_receipt({**cloned, "consent_verified": False})["voice_consent_status"] == "unverified"
    _withdraw("vc-1")
    assert provenance.public_receipt(cloned)["voice_consent_status"] == "withdrawn"
    _withdraw("vc-2", "retention_expired")
    assert provenance.public_receipt({**cloned, "voice_id": "vc-2"})["voice_consent_status"] == "retention_ended"


def test_the_public_key_endpoint_lists_keys_and_the_receipt_version(app_client):
    body = app_client.get("/.well-known/vakya-receipt-key").json()
    assert body["receipt_version"] == 1 and body["alg"] == "Ed25519"
    assert body["keys"] == [{"key_id": body["key_id"], "public_key": body["public_key"], "status": "current"}]

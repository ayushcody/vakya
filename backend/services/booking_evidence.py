"""Proof of booking: what the caller said, what the agent read back, and the caller's yes, kept and sealed.

When the agent books a time on a call, this module collects the evidence of how the booking came about and seals it:

* for each field of the booking (name, phone number, date, time, schedule) the caller's utterance that supplied it
  (its turn id, transcript and a short audio clip),
* the agent's read-back turn (transcript and the reply audio as it was delivered),
* the caller's confirming "yes" turn (transcript and audio).

Each item is hashed (SHA-256 of the transcript and of the audio file); the hashes of all items make a manifest, and the
manifest's hash is the `text_sha256` of a signed receipt of kind "booking" (services/provenance.py: HMAC and Ed25519, so
`/verify`, `sdk/verify` and anyone with the public key can check it). Changing one byte of a clip or one letter of a
transcript changes its hash and `verify()` fails.

How the audio gets here: while a call runs, the turn pipeline keeps each caller utterance for UTTERANCE_TTL_MIN minutes
(default 360) as `utterances/<turn id>.wav` with a small JSON sidecar, only for a front desk that has schedules and only
in local storage. The clip is whatever the line delivered: 16 kHz for a browser web call (decoded from the recording),
8 kHz for a phone call (the PCM the carrier sends, passed through unchanged): `commit_utterance` keeps the sample rate it
is given. Utterances that never become part of a booking are deleted when their time is up. A booking copies the clips it
needs into its own folder `booking_evidence/<evidence id>/`, where they are kept for BOOKING_EVIDENCE_RETENTION_DAYS (default:
CALLER_PROFILE_RETENTION_DAYS, 180, the retention of the caller's other details); `purge_expired()` deletes the clips and
transcripts then and keeps only the hashes, so the receipt stays checkable against a copy somebody holds.

The evidence id is the id of the booking receipt. Public view: transcripts redacted (the name and the number replaced) and
no audio, unless the owner made the evidence public (`set_public`)."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from core import state
    from services import call_events, provenance
except ImportError:  # pragma: no cover
    from backend.core import state
    from backend.services import call_events, provenance

log = logging.getLogger("vakya.evidence")

VERSION = 1
FIELDS = ("name", "phone", "date", "time", "schedule")
FIELD_LABELS = {"name": "Name", "phone": "Phone number", "date": "Date", "time": "Time", "schedule": "Service"}
MAX_CLIP_S = 60.0
_lock = threading.RLock()
_REG: Dict[str, List[dict]] = {}      # conversation id -> the caller utterances kept so far, oldest first
_REG_MAX = 256


# ------------------------------------------------------------------------------------------------ settings and paths

def enabled() -> bool:
    return os.getenv("BOOKING_EVIDENCE", "on").strip().lower() not in ("off", "0", "false", "no")


def retention_days() -> int:
    for name in ("BOOKING_EVIDENCE_RETENTION_DAYS", "CALLER_PROFILE_RETENTION_DAYS"):
        try:
            value = int(float(os.getenv(name, "") or 0))
        except ValueError:
            continue
        if value > 0:
            return value
    return 180


def utterance_ttl_s() -> float:
    try:
        return max(60.0, float(os.getenv("UTTERANCE_TTL_MIN", "360") or 360) * 60.0)
    except ValueError:
        return 360 * 60.0


def _root() -> str:
    return os.getenv("VAKYA_BOOKING_EVIDENCE_DIR", "").strip() or os.path.join(state.LOCAL_AUDIO_DIR, "booking_evidence")


def _utterances() -> str:
    return os.getenv("VAKYA_UTTERANCES_DIR", "").strip() or os.path.join(state.LOCAL_AUDIO_DIR, "utterances")


_ID = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def _dir(evidence_id: str) -> Optional[str]:
    return os.path.join(_root(), evidence_id) if isinstance(evidence_id, str) and _ID.match(evidence_id) else None


def wants_audio(run: Any) -> bool:
    """Keep this turn's caller audio for a possible booking: evidence is on, the call is in local storage, and the front
    desk has schedules (or the appointments tool) to book into."""
    if not enabled() or not getattr(run, "local", False):
        return False
    agent = getattr(run, "agent", None) or {}
    try:
        from services import schedule_tools  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import schedule_tools  # noqa: PLC0415
    names = [str(n) for n in (agent.get("tools") or [])]
    return "schedules" in names or "appointments" in names or schedule_tools.has_schedules(agent.get("id"))


# ------------------------------------------------------------------------------------------------ hashing

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def transcript_hash(text: Optional[str]) -> str:
    return sha256_hex((text or "").encode("utf-8"))


def item_hash(item: dict) -> str:
    """The hash of one evidence item: what it is, which turn it comes from, and the hashes of its words and its sound."""
    audio = item.get("audio") or {}
    return sha256_hex(canon({"id": item["id"], "role": item["role"], "field": item.get("field"), "turn_id": item.get("turn_id"),
                             "speaker": item.get("speaker"), "transcript_sha256": item["transcript_sha256"],
                             "audio_sha256": audio.get("sha256")}))


def manifest_hash(booking: dict, items: List[dict]) -> str:
    return sha256_hex(canon({"version": VERSION, "booking_id": booking.get("booking_id"), "schedule_id": booking.get("schedule_id"),
                             "start": booking.get("start"), "items": [{"id": i["id"], "item_sha256": item_hash(i)} for i in items]}))


def _audio_root(items: List[dict]) -> bytes:
    return "\n".join(sorted((i.get("audio") or {}).get("sha256") or "" for i in items)).encode("ascii")


# ------------------------------------------------------------------------------------------------ audio

def _decode(data: bytes) -> Optional[Tuple[np.ndarray, int]]:
    """Mono float32 samples and their sample rate: a WAV keeps its own rate (8 kHz from the phone, 16 kHz from the page);
    any other recording (WebM, M4A) is decoded at 16 kHz. None when it cannot be read."""
    if not data:
        return None
    try:
        import soundfile as sf  # noqa: PLC0415
        audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return audio.astype(np.float32), int(sr)
    except Exception:  # noqa: BLE001 - not a WAV: let ffmpeg decode it
        pass
    try:
        try:
            from services import audio_decode  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            from backend.services import audio_decode  # noqa: PLC0415
        os.makedirs(_utterances(), exist_ok=True)
        fd, path = tempfile.mkstemp(dir=_utterances(), suffix=".bin")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            return audio_decode.decode_file(path, 16000), 16000
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception as e:  # noqa: BLE001
        log.info("utterance audio could not be decoded for the evidence: %s", e)
        return None


def _wav_bytes(samples: np.ndarray, sr: int) -> bytes:
    import soundfile as sf  # noqa: PLC0415
    buf = io.BytesIO()
    sf.write(buf, np.clip(samples, -1.0, 1.0), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _write_atomic(path: str, data: bytes) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _write_json(path: str, obj: Any) -> None:
    _write_atomic(path, json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"))


# ------------------------------------------------------------------------------------------------ utterances

def commit_utterance(run: Any, turn_id: str) -> Optional[dict]:
    """Blocking (call it with asyncio.to_thread). Keep the caller's words and, when there is a recording, the clip of this
    turn once the turn is committed; the record joins the conversation's list. Never raises."""
    try:
        if not wants_audio(run) or not getattr(run, "conversation_id", None):
            return None
        data = getattr(run, "utterance_bytes", None)
        if getattr(run, "id_removed", False):
            data = None     # the sound of this utterance has an identity number in it: only the cleaned words are kept
        record: Dict[str, Any] = {
            "turn_id": turn_id, "conversation_id": run.conversation_id, "agent_id": run.agent_id, "user_id": run.user_id,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "text": call_events.redact_ids(run.user_text),
            "language": run.language, "typed": not data and not getattr(run, "id_removed", False), "found": sorted((getattr(run, "caller_found", None) or {}).keys()),
            "audio": None, **({"audio_withheld": "id_number"} if getattr(run, "id_removed", False) else {})}
        decoded = _decode(data) if data else None
        os.makedirs(_utterances(), exist_ok=True)
        if decoded is not None:
            samples, sr = decoded
            samples = samples[: int(MAX_CLIP_S * sr)]
            wav = _wav_bytes(samples, sr)
            _write_atomic(os.path.join(_utterances(), f"{turn_id}.wav"), wav)
            record["audio"] = {"file": f"{turn_id}.wav", "sha256": sha256_hex(wav), "sr": sr,
                               "duration_s": round(len(samples) / float(sr), 2)}
        _write_json(os.path.join(_utterances(), f"{turn_id}.json"), record)
        with _lock:
            rows = _REG.setdefault(run.conversation_id, [])
            rows.append(record)
            while len(_REG) > _REG_MAX:
                _REG.pop(next(iter(_REG)))
        return record
    except Exception as e:  # noqa: BLE001
        log.warning("could not keep the caller's utterance for the evidence: %r", e)
        return None


def _records(conversation_id: str) -> List[dict]:
    """The kept utterances of a conversation, oldest first (from memory, else from the sidecar files)."""
    with _lock:
        rows = list(_REG.get(conversation_id) or [])
    if rows:
        return rows
    out = []
    try:
        for name in os.listdir(_utterances()):
            if name.endswith(".json"):
                try:
                    with open(os.path.join(_utterances(), name), "r", encoding="utf-8") as f:
                        row = json.load(f)
                except (OSError, ValueError):
                    continue
                if row.get("conversation_id") == conversation_id:
                    out.append(row)
    except OSError:
        pass
    out.sort(key=lambda r: r.get("at") or "")
    return out


# ------------------------------------------------------------------------------------------------ choosing the evidence

def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text or "").translate(str.maketrans("०१२३४५६७८९", "0123456789")))


def _tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[\wऀ-ॿ]+", str(text or "").casefold()) if len(t) >= 3]


def _reference_now(booking: dict) -> datetime:
    """The clock the caller's relative words ("Thursday", "kal") are read against: when the booking was made."""
    try:
        return datetime.fromisoformat(str(booking.get("created_at"))).replace(tzinfo=None)
    except (TypeError, ValueError):
        return datetime.now()


def _resolved(text: str, now: datetime):
    try:
        from services import when  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import when  # noqa: PLC0415
    return when.resolve_request(text, now)


def _source_for(field: str, booking: dict, records: List[dict]) -> Tuple[Optional[dict], Optional[str]]:
    """(the caller's utterance that supplied `field`, how we know): "text" (the words resolve to the booked value: the latest
    utterance whose date, time, name or phone IS the booked one), "heard" (the caller-details reader took the value from that
    utterance and the words give no different one), "inferred" (the first thing the caller asked), or (None, None).

    A caller who asks "Is Thursday 6 pm free?" and then says "7 pm then, I am Priya Sharma" booked 19:00 on the strength of
    the second utterance, not the first: the time is taken from the latest utterance whose time equals the booked time (the
    date may come from the earlier "Thursday")."""
    if field in ("date", "time"):
        now = _reference_now(booking)
        wanted = booking.get(field)
        unreadable = []
        for r in reversed(records):
            text = str(r.get("text") or "")
            asked = _resolved(text, now)
            value = asked.date.isoformat() if field == "date" and asked.date else \
                asked.time.strftime("%H:%M") if field == "time" and asked.time else None
            if value is not None and value == wanted:
                return r, "text"
            if value is None and field in (r.get("found") or []):
                unreadable.append(r)        # the details reader took it from here but the words do not resolve
        if field == "date" and wanted:
            # the caller named the weekday and the agent worked out which one: the latest utterance that names that weekday
            try:
                weekday = datetime.fromisoformat(str(wanted)).weekday()
            except ValueError:
                weekday = None
            for r in reversed(records):
                asked = _resolved(str(r.get("text") or ""), now)
                if weekday is not None and asked.date is not None and asked.date.weekday() == weekday:
                    return r, "inferred"
        if unreadable:
            return unreadable[0], "heard"
        return None, None
    if field == "name":
        wanted = _tokens(booking.get("name"))
        for r in reversed(records):
            if wanted and all(t in str(r.get("text") or "").casefold() for t in wanted[:2]):
                return r, "text"
        for r in reversed(records):
            if "name" in (r.get("found") or []):
                return r, "heard"
    if field == "phone":
        try:
            from services import spoken_digits  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            from backend.services import spoken_digits  # noqa: PLC0415
        phone = _digits(booking.get("phone"))
        tail = phone[-4:]
        for r in reversed(records):
            text = str(r.get("text") or "")
            if phone and spoken_digits.find_mobile(text) == phone[-10:]:
                return r, "text"
            if tail and tail in _digits(text):
                return r, "text"
        for r in reversed(records):
            if "phone" in (r.get("found") or []):
                return r, "heard"
    if field == "schedule":
        try:
            from services import schedule_tools  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            from backend.services import schedule_tools  # noqa: PLC0415
        wanted = schedule_tools._tokens(booking.get("schedule_name") or "")   # noqa: SLF001
        for r in records:
            if wanted & schedule_tools._tokens(r.get("text") or ""):   # noqa: SLF001
                return r, "text"
        if records:
            return records[0], "inferred"
    return None, None


def _readback_turn(run: Any, booking: dict, current_turn_id: str) -> Optional[dict]:
    """The agent's turn that read the booking back: the latest earlier agent turn of this conversation that names the day
    and time and ends in a question (the same rule the tool loop used to allow the booking)."""
    try:
        from services import tools as tools_mod  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import tools as tools_mod  # noqa: PLC0415
    conv = run.conversation_id
    rows = state.local_db.select_last("conversation_turns", lambda r: r.get("conversation_id") == conv, 40)
    d, t = tools_mod._iso_date(booking.get("date")), tools_mod._hhmm(booking.get("time"))   # noqa: SLF001
    for r in reversed(rows):
        if r.get("role") == "agent" and r.get("id") != current_turn_id:
            if tools_mod._slot_mentioned(r.get("text_content") or "", d, t, booking.get("name"), booking.get("phone"),   # noqa: SLF001
                                         booking.get("nights")):
                return r
            return None   # the latest earlier agent turn is the one that must have read it back
    return None


def _copy_audio(src: str, dst: str) -> Optional[dict]:
    try:
        with open(src, "rb") as f:
            data = f.read()
        _write_atomic(dst, data)
        info: Dict[str, Any] = {"file": os.path.basename(dst), "sha256": sha256_hex(data)}
        try:
            import soundfile as sf  # noqa: PLC0415
            meta = sf.info(io.BytesIO(data))
            info.update(sr=int(meta.samplerate), duration_s=round(meta.frames / float(meta.samplerate), 2))
        except Exception:  # noqa: BLE001
            pass
        return info
    except OSError:
        return None


# ------------------------------------------------------------------------------------------------ sealing

def seal_booking(run: Any, booking: dict, turn_id: str, reply_receipt_id: Optional[str] = None) -> Optional[dict]:
    """Blocking. Collect and seal the evidence of `booking` (the stored row), made in the current turn `turn_id`.
    Returns {"evidence_id", "receipt_id", "items", "complete"} or None. Never raises."""
    try:
        if not wants_audio(run) or not booking.get("booking_id"):
            return None
        evidence_id = ""
        records = _records(run.conversation_id)
        confirm = next((r for r in reversed(records) if r.get("turn_id") == turn_id), None)
        if confirm is None:
            return None
        receipt_id = provenance_new_id()
        evidence_id = receipt_id
        folder = _dir(evidence_id)
        os.makedirs(folder, exist_ok=True)
        items: List[dict] = []
        used: Dict[str, str] = {}      # utterance turn id -> the item id that holds its clip (a clip is stored once)

        def utterance_item(role: str, field: Optional[str], r: dict, basis: Optional[str]) -> dict:
            item_id = f"i{len(items) + 1}"
            item = {"id": item_id, "role": role, "field": field, "speaker": "caller", "turn_id": r["turn_id"], "at": r.get("at"),
                    "transcript": r.get("text") or "", "transcript_sha256": transcript_hash(r.get("text")),
                    "language": r.get("language"), "basis": basis, "typed": bool(r.get("typed")), "audio": None}
            src = (r.get("audio") or {}).get("file")
            if src:
                if r["turn_id"] in used:
                    item["audio"] = dict(next(i for i in items if i["id"] == used[r["turn_id"]])["audio"])
                else:
                    info = _copy_audio(os.path.join(_utterances(), src), os.path.join(folder, f"{item_id}.wav"))
                    if info:
                        item["audio"] = info
                        used[r["turn_id"]] = item_id
            return item

        for field in FIELDS:
            r, basis = _source_for(field, booking, records)
            if r is not None:
                items.append(utterance_item("field", field, r, basis))
            else:
                items.append({"id": f"i{len(items) + 1}", "role": "field", "field": field, "speaker": "caller", "turn_id": None,
                              "at": None, "transcript": "", "transcript_sha256": transcript_hash(""), "language": None,
                              "basis": None, "typed": False, "audio": None})
        readback = _readback_turn(run, booking, turn_id)
        if readback is not None:
            item_id = f"i{len(items) + 1}"
            audio = _copy_audio(os.path.join(state.LOCAL_AUDIO_DIR, f"turn_{readback['id']}.wav"),
                                os.path.join(folder, f"{item_id}.wav"))
            receipt = _receipt_of_turn(readback["id"])
            items.append({"id": item_id, "role": "readback", "field": None, "speaker": "agent", "turn_id": readback["id"],
                          "at": readback.get("created_at"), "transcript": readback.get("text_content") or "",
                          "transcript_sha256": transcript_hash(readback.get("text_content")), "language": readback.get("language"),
                          "basis": None, "typed": False, "audio": audio, "reply_receipt_id": (receipt or {}).get("receipt_id")})
        items.append(utterance_item("confirm", None, confirm, None))
        items[-1]["reply_receipt_id"] = reply_receipt_id

        for it in items:
            it["item_sha256"] = item_hash(it)
        digest = manifest_hash(booking, items)
        manifest = {"version": VERSION, "evidence_id": evidence_id, "receipt_id": receipt_id, "booking_id": booking["booking_id"],
                    "agent_id": run.agent_id, "conversation_id": run.conversation_id, "schedule_id": booking.get("schedule_id"),
                    "schedule_name": booking.get("schedule_name"), "start": booking.get("start"), "date": booking.get("date"),
                    "time": booking.get("time"), "manifest_sha256": digest,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "items": items}
        complete = all(i["transcript"] for i in items if i["role"] != "field" or i["field"] in ("name", "phone", "date", "time")) \
            and any(i["role"] == "readback" for i in items)
        receipt = provenance.create_receipt(
            kind="booking", subject_id=turn_id, engine=None, voice_id=None, voice_name=None, consent_id=None,
            consent_verified=None, text=json.dumps({"manifest_sha256": digest, "booking_id": booking["booking_id"]}, sort_keys=True),
            audio_bytes=_audio_root(items), user_id=run.user_id, cloud=False, receipt_id=receipt_id)
        if not receipt:
            shutil.rmtree(folder, ignore_errors=True)
            return None
        _write_json(os.path.join(folder, "manifest.json"), manifest)
        _write_json(os.path.join(folder, "meta.json"), {
            "evidence_id": evidence_id, "receipt_id": receipt_id, "booking_id": booking["booking_id"], "user_id": run.user_id,
            "agent_id": run.agent_id, "public": False, "created_at": manifest["created_at"], "complete": complete,
            "redact": {"name": booking.get("name"), "phone": booking.get("phone")}})
        _write_json(os.path.join(folder, "hashes.json"), {"manifest_sha256": digest, "booking_id": booking["booking_id"],
                    "schedule_id": booking.get("schedule_id"), "start": booking.get("start"),
                    "items": [{"id": i["id"], "role": i["role"], "field": i["field"], "turn_id": i["turn_id"], "speaker": i["speaker"],
                               "transcript_sha256": i["transcript_sha256"], "audio_sha256": (i.get("audio") or {}).get("sha256"),
                               "item_sha256": i["item_sha256"]} for i in items]})
        return {"evidence_id": evidence_id, "receipt_id": receipt_id, "complete": complete,
                "items": len(items), "manifest_sha256": digest}
    except Exception as e:  # noqa: BLE001
        log.warning("could not seal the evidence of booking %s: %r", booking.get("booking_id"), e)
        return None


def provenance_new_id() -> str:
    import uuid  # noqa: PLC0415
    return str(uuid.uuid4())


def _receipt_of_turn(turn_id: str) -> Optional[dict]:
    try:
        rows = provenance._store.find(turn_id=turn_id)   # noqa: SLF001
        return rows[0] if rows else None
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------------------------------ reading

def _load(evidence_id: str) -> Tuple[Optional[dict], Optional[dict], Optional[dict]]:
    """(manifest or None when its recordings were deleted, hashes, meta) of an evidence folder."""
    folder = _dir(evidence_id)
    if not folder or not os.path.isdir(folder):
        return None, None, None

    def read(name: str) -> Optional[dict]:
        try:
            with open(os.path.join(folder, name), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    return read("manifest.json"), read("hashes.json"), read("meta.json")


def exists(evidence_id: str) -> bool:
    return _load(evidence_id)[2] is not None


def owner_of(evidence_id: str) -> Optional[str]:
    meta = _load(evidence_id)[2]
    return meta.get("user_id") if meta else None


def verify(evidence_id: str) -> dict:
    """Recompute every hash from what is stored and compare: each item's transcript and audio, the manifest, and the signed
    receipt that covers the manifest. {"ok", "receipt_ok", "manifest_ok", "items": [{id, transcript_ok, audio_ok, ok}],
    "deleted", "reason"}. After the retention period the recordings are gone: `deleted` is true and only the receipt and the
    hashes file are checked."""
    manifest, hashes, meta = _load(evidence_id)
    if meta is None or hashes is None:
        return {"ok": False, "receipt_ok": False, "manifest_ok": False, "items": [], "deleted": False, "reason": "not_found"}
    receipt = provenance.get_receipt(meta["receipt_id"])
    receipt_ok = bool(receipt) and provenance.signature_valid(receipt) and provenance.signature_ed25519_valid(receipt) is not False
    out: Dict[str, Any] = {"ok": False, "receipt_ok": receipt_ok, "manifest_ok": False, "items": [], "deleted": manifest is None,
                           "reason": None}
    expected = json.dumps({"manifest_sha256": hashes["manifest_sha256"], "booking_id": hashes["booking_id"]}, sort_keys=True)
    covered = bool(receipt) and receipt.get("text_sha256") == sha256_hex(expected.encode("utf-8"))
    if manifest is None:
        out.update(manifest_ok=covered, ok=receipt_ok and covered, reason=None if receipt_ok and covered else "receipt_mismatch")
        return out
    folder = _dir(evidence_id)
    items_ok = True
    for it in manifest["items"]:
        t_ok = transcript_hash(it.get("transcript")) == it.get("transcript_sha256")
        a_ok = True
        if it.get("audio"):
            try:
                with open(os.path.join(folder, it["audio"]["file"]), "rb") as f:
                    a_ok = sha256_hex(f.read()) == it["audio"]["sha256"]
            except OSError:
                a_ok = False
        h_ok = item_hash(it) == it.get("item_sha256")
        ok = t_ok and a_ok and h_ok
        items_ok = items_ok and ok
        out["items"].append({"id": it["id"], "transcript_ok": t_ok, "audio_ok": a_ok, "ok": ok})
    booking = {"booking_id": manifest["booking_id"], "schedule_id": manifest.get("schedule_id"), "start": manifest.get("start")}
    recomputed = manifest_hash(booking, manifest["items"])
    out["manifest_ok"] = recomputed == manifest.get("manifest_sha256") == hashes.get("manifest_sha256") and covered
    out["ok"] = bool(items_ok and out["manifest_ok"] and receipt_ok)
    if not out["ok"]:
        out["reason"] = "tampered" if not (items_ok and out["manifest_ok"]) else "receipt_invalid"
    return out


_REDACTED = "▒▒▒"


def redact(text: str, terms: Dict[str, Any]) -> str:
    """Hide the caller's name and any long number in a transcript."""
    out = str(text or "")
    for word in _tokens(terms.get("name") or ""):
        out = re.sub(re.escape(word), _REDACTED, out, flags=re.IGNORECASE)
    tail = _digits(terms.get("phone") or "")[-4:]
    if len(tail) == 4:      # the last digits the agent read back ("ending 3 2 1 0") are the number too
        out = re.sub(r"(?<!\d)" + r"[\s\-]?".join(tail) + r"(?!\d)", "••••", out)
    return re.sub(r"(?:\d[\s\-]?){6,}", "•••• ", out).strip()


_PLACEHOLDER = {"name": "(the caller said their name)", "phone": "(the caller said their phone number)"}


def _item_view(it: dict, *, public_view_full: bool, terms: Dict[str, Any], audio_ok: bool) -> dict:
    text = call_events.mask_numbers(it.get("transcript") or "")   # a spoken phone number never leaves the server in full
    if not public_view_full:
        text = _PLACEHOLDER.get(it.get("field") or "", None) if it.get("field") in _PLACEHOLDER and text else redact(text, terms)
    audio = it.get("audio")
    return {"id": it["id"], "role": it["role"], "field": it.get("field"), "label": FIELD_LABELS.get(it.get("field") or "", None),
            "speaker": it["speaker"], "turn_id": it.get("turn_id"), "at": it.get("at"), "transcript": text,
            "language": it.get("language"), "basis": it.get("basis"), "typed": bool(it.get("typed")),
            "transcript_sha256": it["transcript_sha256"], "audio_sha256": (audio or {}).get("sha256"),
            "item_sha256": it.get("item_sha256"), "has_audio": bool(audio), "audio_available": bool(audio) and audio_ok,
            "duration_s": (audio or {}).get("duration_s"), "sample_rate": (audio or {}).get("sr"),
            "reply_receipt_id": it.get("reply_receipt_id")}


def _view(evidence_id: str, *, full: bool, audio_ok: bool) -> Optional[dict]:
    manifest, hashes, meta = _load(evidence_id)
    if meta is None or hashes is None:
        return None
    terms = meta.get("redact") or {}
    checked = verify(evidence_id)
    out: Dict[str, Any] = {
        "evidence_id": evidence_id, "receipt_id": meta["receipt_id"], "booking_id": meta["booking_id"], "public": bool(meta.get("public")),
        "created_at": meta.get("created_at"), "complete": bool(meta.get("complete")), "deleted": manifest is None,
        "retention_days": retention_days(), "manifest_sha256": hashes["manifest_sha256"], "verified": checked["ok"],
        "verification": {k: checked[k] for k in ("receipt_ok", "manifest_ok", "reason")}}
    if manifest is not None:
        out.update(schedule_name=manifest.get("schedule_name"), start=manifest.get("start"), date=manifest.get("date"),
                   time=manifest.get("time"), conversation_id=manifest.get("conversation_id"),
                   items=[_item_view(i, public_view_full=full, terms=terms, audio_ok=audio_ok) for i in manifest["items"]])
    else:   # recordings and transcripts deleted at the end of the retention period: only the hashes are left
        out.update(schedule_name=None, start=hashes.get("start"), items=[
            {"id": h["id"], "role": h["role"], "field": h.get("field"), "label": FIELD_LABELS.get(h.get("field") or "", None),
             "speaker": h["speaker"], "turn_id": h.get("turn_id"), "at": None, "transcript": "", "language": None, "basis": None,
             "typed": False, "transcript_sha256": h["transcript_sha256"], "audio_sha256": h.get("audio_sha256"),
             "item_sha256": h.get("item_sha256"), "has_audio": bool(h.get("audio_audio_sha256") or h.get("audio_sha256")),
             "audio_available": False, "duration_s": None, "sample_rate": None, "reply_receipt_id": None}
            for h in hashes["items"]])
    return out


def owner_view(evidence_id: str) -> Optional[dict]:
    """The full evidence for the owner of the front desk: transcripts as said, audio playable."""
    return _view(evidence_id, full=True, audio_ok=True)


def public_view(evidence_id: str) -> Optional[dict]:
    """What anyone with the receipt sees: transcripts redacted and no audio, unless the owner made the evidence public."""
    meta = _load(evidence_id)[2]
    if meta is None:
        return None
    public = bool(meta.get("public"))
    out = _view(evidence_id, full=public, audio_ok=public)
    if out is not None:
        out["public"] = public
    return out


def audio_path(evidence_id: str, item_id: str, *, public: bool) -> Optional[str]:
    """The clip file of an item, or None. `public=True` (no owner): only when the owner made the evidence public."""
    manifest, _hashes, meta = _load(evidence_id)
    if manifest is None or meta is None or (public and not meta.get("public")):
        return None
    item = next((i for i in manifest["items"] if i["id"] == item_id), None)
    if not item or not item.get("audio"):
        return None
    path = os.path.join(_dir(evidence_id) or "", item["audio"]["file"])
    return path if os.path.isfile(path) else None


def set_public(evidence_id: str, value: bool) -> bool:
    folder = _dir(evidence_id)
    meta = _load(evidence_id)[2]
    if not folder or meta is None:
        return False
    meta["public"] = bool(value)
    _write_json(os.path.join(folder, "meta.json"), meta)
    return True


# ------------------------------------------------------------------------------------------------ retention

def purge_expired(now: Optional[datetime] = None) -> Dict[str, int]:
    """Delete the clips and transcripts of evidence older than the retention period (the hashes and the receipt stay), and
    kept utterances older than their time. Run at start-up. Never raises."""
    now = now or datetime.now(timezone.utc)
    removed = {"evidence": 0, "utterances": 0}
    try:
        cutoff = now - timedelta(days=retention_days())
        for name in os.listdir(_root()) if os.path.isdir(_root()) else []:
            manifest, _h, meta = _load(name)
            if manifest is None or meta is None:
                continue
            try:
                made = datetime.fromisoformat(str(meta.get("created_at")))
            except ValueError:
                continue
            if made < cutoff:
                folder = _dir(name)
                for entry in os.listdir(folder):
                    if entry not in ("hashes.json", "meta.json"):
                        os.unlink(os.path.join(folder, entry))
                meta["redact"] = {}
                meta["public"] = False
                meta["deleted_at"] = now.isoformat(timespec="seconds")
                _write_json(os.path.join(folder, "meta.json"), meta)
                removed["evidence"] += 1
        limit = time.time() - utterance_ttl_s()
        for name in os.listdir(_utterances()) if os.path.isdir(_utterances()) else []:
            path = os.path.join(_utterances(), name)
            if os.path.isfile(path) and os.path.getmtime(path) < limit:
                os.unlink(path)
                removed["utterances"] += 1
    except Exception as e:  # noqa: BLE001
        log.warning("evidence retention run failed: %r", e)
    return removed


def forget() -> None:
    """Drop the in-memory list of utterances (tests)."""
    with _lock:
        _REG.clear()

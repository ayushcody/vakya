"""Shared pytest fixtures. Nothing here reaches Gnani or Gemini."""
import math
import os
import struct
import sys
import wave
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent
# Tests never read the developer's real backend/.env or frontend/.env.local: core/state.py and core/offline.py skip their
# load_dotenv calls when this flag is set, so the suite gives the same answers on every machine. Set before any import
# of the app.
os.environ["VAKYA_SKIP_DOTENV"] = "1"
FIXTURES_DIR = BACKEND_DIR / "fixtures"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def _gnani_off_by_default(monkeypatch):
    """Gnani AI (services/gnani.py) goes live with GNANI_API_KEY; the suite never reads the developer's environment, and
    no test reaches api.vachana.ai (tests of Gnani use fakes and set their own variables)."""
    for name in ("GNANI_API_KEY", "GNANI_BASE_URL", "GNANI_STT_MODEL", "GNANI_TTS_MODEL",
                 "GNANI_STT_FORMAT", "GNANI_VOICES", "GNANI_KEYTERMS", "GNANI_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _gnani_gateway_clean(monkeypatch, tmp_path):
    """The Gnani gateway (services/gnani_gateway.py) keeps a cache, breakers and a queue in memory and the usage ledger is a
    file: every test gets a fresh gateway, a ledger under tmp_path, no waiting on backoff and no budget or rate overrides."""
    from services import gnani_gateway, gnani_hold_lines, usage_ledger
    monkeypatch.setenv("VAKYA_USAGE_FILE", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("VAKYA_HOLD_LINES_DIR", str(tmp_path / "hold_lines"))
    monkeypatch.setenv("GNANI_PREPARE_HOLD_LINES", "off")
    for name in ("GNANI_CREDIT_BUDGET", "GNANI_CREDIT_RESERVE_PCT", "GNANI_RUPEES_PER_CREDIT", "GNANI_DEFAULT_LANE",
                 "GNANI_CACHE_MB", "GNANI_MAX_RETRIES", "GNANI_BREAKER_FAILS", "GNANI_STREAM_HOLDS_SLOT",
                 "GNANI_FIX_ACRONYMS", "GNANI_LIVE_DEADLINE_S", "GNANI_QUEUED_DEADLINE_S", "GNANI_BENCH_DEADLINE_S",
                 "GNANI_QUEUE_WAIT_LIVE_S", "GNANI_QUEUE_WAIT_DESK_S", "GNANI_HOLD_LINE_LANGUAGES", "GNANI_RATE_STT_REST_PER_S", "GNANI_RATE_STT_STREAM_PER_S",
                 "GNANI_RATE_TTS_PER_1K_CHARS", "GNANI_RATE_CLONE_PER_REQUEST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(gnani_gateway, "_sleep", lambda s: None)
    gnani_gateway.GATEWAY.reset()
    usage_ledger.LEDGER.reset_memory()
    gnani_hold_lines.reset()
    yield
    gnani_gateway.GATEWAY.reset()
    usage_ledger.LEDGER.reset_memory()
    gnani_hold_lines.reset()


@pytest.fixture(autouse=True)
def _no_gemini_by_default(monkeypatch):
    """No test calls Gemini with the developer's real key: the language-model and embedding keys are empty (a test that needs
    them sets its own, with a fake HTTP layer) and the on-disk embedding cache is off."""
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "GEMINI_EMBED_MODEL", "GEMINI_EMBED_DIM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMBED_DISK_CACHE", "off")
    from services import gemini as _gemini
    _gemini._reset_for_tests()
    yield
    _gemini._reset_for_tests()


@pytest.fixture(autouse=True)
def _grounding_extras_off_by_default(monkeypatch, tmp_path):
    """Grounded-answer extras that would change what other tests' faked models say (services/grounding.py,
    services/fact_sheet.py) are off here; tests/test_grounding.py and tests/test_fact_sheet.py turn them on. The fact
    sheet would also call the faked model from a background thread and write under backend/data; the number guard would
    replace a faked reply whose numbers are in no document; the confidence gate would skip the faked model."""
    monkeypatch.setenv("FACT_SHEET", "off")
    monkeypatch.setenv("FACT_SHEET_DIR", str(tmp_path / "fact_sheets"))
    monkeypatch.setenv("ANSWER_GUARD", "off")
    monkeypatch.setenv("CONFIDENCE_GATE", "off")


@pytest.fixture(autouse=True)
def _caller_memory_private_and_offline(monkeypatch, tmp_path):
    """The caller details (services/caller_memory.py) go to a file under tmp_path, never backend/data, and the language model
    that reads them is never called: tests of it (tests/test_caller_memory.py) fake the call and turn it on."""
    from services import caller_memory
    monkeypatch.setenv("VAKYA_CALLER_DETAILS_FILE", str(tmp_path / "caller_details.json"))
    monkeypatch.setenv("CALLER_MEMORY_LLM", "off")
    caller_memory.forget()
    yield
    caller_memory.forget()


@pytest.fixture(autouse=True)
def _caller_profiles_private_and_instant(monkeypatch, tmp_path):
    """Caller profiles (services/caller_profiles.py) go to a file under tmp_path, never backend/data, and a finished call
    writes its profile at once (the production delay lets the last words reach the store first)."""
    from services import caller_profiles
    monkeypatch.setenv("VAKYA_CALLER_PROFILES_FILE", str(tmp_path / "caller_profiles.json"))
    monkeypatch.setenv("CALLER_PROFILE_END_DELAY_S", "0")
    caller_profiles.forget_context()
    caller_profiles.configure(supabase=None)
    yield
    caller_profiles.forget_context()
    caller_profiles.configure(supabase=None)


@pytest.fixture(autouse=True)
def _tool_readback_off_by_default(monkeypatch):
    """Committing tools (book_appointment) answers NOT DONE until the agent has read the slot back and
    asked (services/tools.py READBACK_TOOLS). The loop and endpoint tests script a model that books in one step, so the
    rule is off here; tests/test_tools_readback.py turns it on."""
    monkeypatch.setenv("TOOLS_READBACK", "off")


@pytest.fixture(autouse=True)
def _voice_vault_off_by_default(monkeypatch, tmp_path):
    """Tests never touch the real Keychain or write keys under backend/data. The vault is off, so tests that write
    plain voice files and read them back keep working; tests/test_voice_vault.py turns it on, with a fixed key."""
    import base64
    from services import voice_vault
    monkeypatch.setenv("VOICE_VAULT", "off")
    monkeypatch.setenv("VAKYA_VAULT_KEY", base64.b64encode(b"k" * 32).decode("ascii"))
    monkeypatch.setenv("VOICE_VAULT_DIR", str(tmp_path / "voice_keys"))
    monkeypatch.setenv("VOICE_VAULT_KEYCHAIN", "off")
    voice_vault.reset()
    yield
    voice_vault.reset()


def write_sine_wav(path: Path, seconds: float = 5.0, sr: int = 24000, freq: float = 220.0):
    n = int(seconds * sr)
    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / sr) * (0.6 + 0.4 * math.sin(2 * math.pi * 3 * i / sr))))
        for i in range(n)
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(frames)
    return path


@pytest.fixture(scope="session")
def ref_wav() -> Path:
    """5 second, 24 kHz mono 16-bit WAV reference clip, generated on demand."""
    FIXTURES_DIR.mkdir(exist_ok=True)
    p = FIXTURES_DIR / "ref_5s_24k.wav"
    if not p.exists():
        write_sine_wav(p)
    return p


@pytest.fixture(scope="session")
def rag_txt() -> Path:
    FIXTURES_DIR.mkdir(exist_ok=True)
    p = FIXTURES_DIR / "rag_sample.txt"
    if not p.exists():
        p.write_text(
            "Vakya is a voice intelligence platform. " * 40
            + "The refund window is thirty days from purchase. " * 40,
            encoding="utf-8",
        )
    return p


@pytest.fixture(scope="session")
def app_client():
    """FastAPI TestClient for the real app, or skip if the app cannot import on this machine."""
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    try:
        import backend  # noqa: WPS433
    except BaseException as exc:  # heavy deps / model downloads unavailable
        pytest.skip(f"backend app not importable here: {type(exc).__name__}: {exc}")
    return TestClient(backend.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _schedules_and_bookings_private(monkeypatch, tmp_path):
    """The schedules (services/schedules.py) and the appointments store they book into live in files under tmp_path, never
    backend/data. A test that needs its own file sets the variable again."""
    monkeypatch.setenv("VAKYA_SCHEDULES_FILE", str(tmp_path / "schedules" / "schedules.json"))
    monkeypatch.setenv("VAKYA_APPOINTMENTS_FILE", str(tmp_path / "mcp_default" / "appointments.json"))


@pytest.fixture(autouse=True)
def _hearing_private(monkeypatch, tmp_path):
    """What a desk has learnt to hear (services/hearing.py) lives in a file under tmp_path, never backend/data."""
    monkeypatch.setenv("VAKYA_HEARING_FILE", str(tmp_path / "hearing" / "hearing.json"))


@pytest.fixture(autouse=True)
def _no_real_calendar(monkeypatch):
    """No test books on the developer's real Google Calendar: a test that needs one fakes services/gcal itself."""
    monkeypatch.delenv("GOOGLE_CALENDAR_ID", raising=False)


@pytest.fixture(autouse=True)
def _no_cloud_keys(monkeypatch):
    """Tests never call the language model with the developer's real key from backend/.env; a test that needs it sets its own."""
    try:
        from services import llm
        llm.last_failures.clear()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _isolated_receipts(monkeypatch, tmp_path_factory):
    """Receipts (and their signing secret) written during a test go to a temp dir, never to backend/data."""
    from services import provenance

    monkeypatch.setattr(provenance, "_data_dir", str(tmp_path_factory.mktemp("provenance")))
    monkeypatch.setattr(provenance, "_secret_cache", None)
    provenance._store.reset()
    yield
    provenance._store.reset()


@pytest.fixture(autouse=True)
def _isolated_canned_reply_cache():
    """No clarification audio cached by another test is served to this one."""
    try:
        from services import canned_reply
        canned_reply.audio_cache.clear()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _no_real_whatsapp(monkeypatch, tmp_path):
    """No test sends a WhatsApp message: the access token and number id are unset (the outbox then only builds previews), the
    outbox file is under tmp_path, never backend/data, and the one network exit (services/whatsapp._http) raises if reached."""
    from services import booking_notify, whatsapp
    for name in ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_API_VERSION", "WHATSAPP_TEMPLATE_CONFIRMED",
                 "WHATSAPP_TEMPLATE_MOVED", "WHATSAPP_TEMPLATE_CANCELLED", "WHATSAPP_CONFIRMATIONS", "WHATSAPP_PREVIEW",
                 "WHATSAPP_SEND_DELAY_S", "WHATSAPP_BACKSTOP_S"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VAKYA_WHATSAPP_FILE", str(tmp_path / "whatsapp" / "outbox.json"))

    def refuse(*a, **k):
        raise AssertionError("a test reached the real WhatsApp API: fake services.whatsapp._http")
    monkeypatch.setattr(whatsapp, "_http", refuse)
    booking_notify.reset()
    yield
    booking_notify.reset()

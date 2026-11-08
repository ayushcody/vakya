"""Process-wide state and configuration: the environment, the Supabase client, the local data directories and the hosted
embedder.

Everything here is read through this module (`state.supabase`, `state.local_db`, ...) and never copied into another
module's globals, so the tests can replace a value in one place and every caller sees it.
"""

try:  # first: offline mode switches the hosted services off before anything reaches for them
    from core import offline
except ImportError:  # pragma: no cover - imported as backend.core.state
    from backend.core import offline

import os
import subprocess

import logging
from typing import Any

from dotenv import load_dotenv

try:
    from supabase import create_client, Client
except Exception:  # noqa: BLE001
    create_client, Client = None, Any

try:
    from core.errors import AppError
    from services import provenance, retrieval
    from services.local_store import LocalStore
except ImportError:  # pragma: no cover
    from backend.core.errors import AppError
    from backend.services import provenance, retrieval
    from backend.services.local_store import LocalStore

log = logging.getLogger("vakya")

APP_VERSION = "2.0.0"

# ============================================================================
# This machine
# ============================================================================

def platform_info() -> dict:
    """Which machine this is (operating system, version, architecture). Nothing runs on it but the API: speech comes from
    Gnani and the hosted language model."""
    import platform as _platform
    system = _platform.system()
    os_name = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(system, system or "Unknown")
    os_version = _platform.mac_ver()[0] if system == "Darwin" else _platform.release()
    return {"os": os_name, "os_version": os_version, "arch": _platform.machine()}


# ============================================================================
# Environment and Supabase
# ============================================================================

# Load environment variables (not in the test suite: conftest.py sets VAKYA_SKIP_DOTENV)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.abspath(os.path.join(BACKEND_DIR, "../frontend/.env.local"))
if not os.getenv("VAKYA_SKIP_DOTENV"):
    load_dotenv(env_path)
    load_dotenv(os.path.join(BACKEND_DIR, ".env"))

# Initialize Supabase
SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_ANON_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY") or SUPABASE_KEY
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
supabase = None
if offline.enabled():
    print("Offline mode: Supabase, Gnani, the language model, Calendar and phone calls are switched off.")
elif SUPABASE_URL and SUPABASE_KEY and create_client is not None:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as _e:  # noqa: BLE001
        print(f"WARNING: Could not initialise Supabase client: {_e}")
else:
    print("WARNING: Supabase URL/Key not set (or supabase package missing). Using local storage fallbacks.")

AUDIO_BUCKET = "audio-assets"
# The bucket is private, so clients get time-limited signed links to audio (default one hour).
SIGNED_URL_EXPIRES_S = max(60, int(os.getenv("SIGNED_URL_EXPIRES_S", "3600") or 3600))


class LazyEmbeddingModel:
    """The hosted embedder (services/retrieval.py), reached over HTTP on first use. `encode(texts,
    kind="passage" | "query")` returns unit-length float32 vectors, or raises retrieval.EmbeddingUnavailable when there
    is no embeddings key or the embeddings service cannot be reached (callers then search by keyword or store the text without a vector)."""

    @property
    def loaded(self) -> bool:
        return retrieval.embeddings_available()

    def encode(self, texts, **kwargs):
        return retrieval.encode_texts(texts, **kwargs)


embedding_model = LazyEmbeddingModel()


# ============================================================================
# Local storage (fallback / "storage-mode: local")
# ============================================================================

LOCAL_DATA_DIR = os.path.join(BACKEND_DIR, "data")
# Knowledge is embedded by the hosted embedder (EMBEDDINGS_MODEL; EMBEDDINGS_DIM, 768 by default),
# which is multilingual: a Hindi question finds an English document and the reverse. Local chunks record the model and
# dimension they were embedded with and are re-embedded on their next search when either differs (see
# refresh_local_embeddings); with no key they are searched by BM25 keyword match.
EMBEDDING_MODEL = retrieval.embedding_model_name()
LOCAL_AUDIO_DIR = os.path.join(LOCAL_DATA_DIR, "audio")
LOCAL_VOICES_FILE = os.path.join(LOCAL_DATA_DIR, "voices.json")

os.makedirs(LOCAL_AUDIO_DIR, exist_ok=True)

# Agents, knowledge, conversations and evaluations when Supabase is absent or storage-mode is "local".
local_db = LocalStore(LOCAL_DATA_DIR)

provenance.configure(lambda: supabase, LOCAL_DATA_DIR)

PLATFORM = platform_info()

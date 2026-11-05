"""Knowledge retrieval building blocks: the hosted embedder (Gemini embeddings), a Hindi/English/Hinglish normaliser and
tokenizer, BM25, score fusion, question-row -> passage mapping and a retrieval confidence rating.

Two ways to search, one ranking function:
  * hybrid   a Gemini key is set: each passage and each question has a vector; the dense cosine is fused with BM25.
  * keyword  no key, a quota error, a network error: no vector, so BM25 over the same passages ranks them alone. The
             search never fails and never answers "nothing" only because the embedder is away.

Pipeline (see rank_rows, LocalStore.match_chunks and knowledge.retrieve_context):
  dense top-20 U BM25 top-20 (passages and question rows) = candidates
     -> scores: dense cosine (when the rows have comparable vectors), BM25
     -> weighted fusion of normalised scores (RETRIEVAL_FUSION=rrf: Reciprocal Rank Fusion)
     -> question rows stand for their parent passage (best score wins) -> assess_confidence -> top k

The embedder (encode_texts / encode_query) calls Google's REST API with httpx: models/{model}:batchEmbedContents, at most
EMBED_BATCH_SIZE texts per request, retries with backoff on 429 / 5xx, every vector scaled to unit length here (so
cosine == dot product whatever the model does), and a cache keyed by a hash of model + dimension + kind + text: in
memory (LRU) and in a small SQLite file under backend/data (never required). Nothing is imported or loaded at module
level except httpx and numpy.
"""
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
from collections import Counter, OrderedDict
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx
import numpy as np

try:
    from services import gemini
except ImportError:  # pragma: no cover - imported as backend.services.retrieval
    from backend.services import gemini

log = logging.getLogger(__name__)

RRF_K = 60


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def hybrid_enabled() -> bool:
    """HYBRID_SEARCH=off: dense order only (BM25 still ranks when there are no vectors at all)."""
    return (os.getenv("HYBRID_SEARCH", "on") or "on").strip().lower() not in ("off", "0", "false", "no")


# ------------------------------------------------------------------------------------------------ the embedder

EMBED_BATCH_SIZE = 100            # texts per batchEmbedContents request (the API accepts up to 100)
EMBED_MAX_ATTEMPTS = 4            # a document or a bulk re-embed: 429 and 5xx are retried with backoff
EMBED_QUERY_ATTEMPTS = 2          # a caller's question is waiting: one retry, then the keyword search answers
EMBED_TIMEOUT_S = 30.0
EMBED_QUERY_TIMEOUT_S = 5.0
EMBED_BACKOFF_BASE_S = 0.5
EMBED_BACKOFF_MAX_S = 8.0
EMBED_COOLDOWN_S = 30.0           # after a failed call the embedder is not tried again for this long (keyword search)
EMBED_CACHE_SIZE = 4096           # vectors kept in memory
EMBED_DISK_CACHE_ROWS = 20000     # vectors kept in the SQLite file (float16: 1.5 KB each at 768 dimensions)

# gemini-embedding-2 has no task type: the text itself carries the task (docs: ai.google.dev/gemini-api/docs/embeddings).
QUERY_PREFIX = "task: search result | query: {text}"
PASSAGE_PREFIX = "title: {title} | text: {text}"
LEGACY_TASK_TYPES = {"query": "RETRIEVAL_QUERY", "passage": "RETRIEVAL_DOCUMENT"}   # gemini-embedding-001


class EmbeddingUnavailable(RuntimeError):
    """The embedder cannot answer now: no Gemini key, a quota or network error, or a reply that is not a vector. Search
    goes on by keyword; documents are stored without a vector and embedded later."""


def embedding_model_name() -> str:
    """The id stored with every vector (`embed_model`): GEMINI_EMBED_MODEL, else gemini-embedding-2."""
    return gemini.embed_model()


def embedding_dim() -> int:
    """The vector size asked for (`outputDimensionality`): GEMINI_EMBED_DIM, else 768."""
    return gemini.embed_dim()


def embeddings_configured() -> bool:
    return gemini.embeddings_configured()


_embed_lock = threading.Lock()
_embed_cache: "OrderedDict[str, np.ndarray]" = OrderedDict()
_embed_down_until = 0.0            # monotonic time before which the embedder is not tried (it failed just now)
_embed_last_error: Optional[str] = None
_disk_lock = threading.Lock()
_disk_db: Optional[sqlite3.Connection] = None
_disk_path: Optional[str] = None
_disk_failed = False


def embedder_status() -> dict:
    """{"provider": "gemini", "model", "dim", "configured", "reason", "mode"}: `mode` is "hybrid" when searches use
    vectors and BM25 together, "keyword" when BM25 alone answers (no key, or the embedder failed a moment ago)."""
    configured = embeddings_configured()
    cooling = configured and time.monotonic() < _embed_down_until
    reason = None
    if not configured:
        reason = "Needs a Gemini key"
    elif cooling:
        reason = "Gemini embeddings are unreachable just now: searching by keyword" + (
            f" ({_embed_last_error})" if _embed_last_error else "")
    return {"provider": "gemini", "model": embedding_model_name(), "dim": embedding_dim(), "configured": configured,
            "reason": reason, "mode": "hybrid" if configured and not cooling else "keyword"}


def embeddings_available() -> bool:
    """A key is set and the embedder did not just fail: encode_texts is worth calling."""
    return embedder_status()["mode"] == "hybrid"


def reset_embedder_state() -> None:
    """Forget the memory cache and the failure cool-down (tests, and after a key change)."""
    global _embed_down_until, _embed_last_error
    with _embed_lock:
        _embed_cache.clear()
        _embed_down_until = 0.0
        _embed_last_error = None


def _is_legacy_model(model: str) -> bool:
    return "embedding-001" in model


def _cache_key(model: str, dim: int, kind: str, text: str) -> str:
    return hashlib.sha256(f"{model}\x1f{dim}\x1f{kind}\x1f{text}".encode("utf-8")).hexdigest()


def _prepare_input(text: str, kind: str, title: Optional[str], model: str) -> str:
    """What is sent for one text. gemini-embedding-2: prefixed (query / passage with a title, "none" when there is no
    title); gemini-embedding-001: the text as it is (the task type is a request field)."""
    text = (text or "").strip() or "."
    if _is_legacy_model(model):
        return text
    if kind == "query":
        return QUERY_PREFIX.format(text=text)
    return PASSAGE_PREFIX.format(title=(title or "").strip() or "none", text=text)


def _unit(vec: Sequence[float]) -> np.ndarray:
    v = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


# -- the on-disk cache (SQLite, stdlib): a convenience, any failure here is ignored

def _disk_file() -> Optional[str]:
    if (os.getenv("EMBED_DISK_CACHE") or "on").strip().lower() in ("off", "0", "false", "no"):
        return None
    return (os.getenv("VAKYA_EMBED_CACHE_FILE") or "").strip() or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "embed_cache.sqlite")


def _disk() -> Optional[sqlite3.Connection]:
    global _disk_db, _disk_path, _disk_failed
    path = _disk_file()
    if path is None:
        return None
    if _disk_db is not None and _disk_path == path:
        return _disk_db
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if _disk_db is not None:
            _disk_db.close()
        _disk_db = sqlite3.connect(path, timeout=2.0, check_same_thread=False)
        _disk_db.execute("create table if not exists vec (k text primary key, v blob not null)")
        _disk_db.commit()
        _disk_path, _disk_failed = path, False
        return _disk_db
    except Exception as e:  # noqa: BLE001 - a read-only disk: the memory cache still works
        if not _disk_failed:
            log.info("embedding disk cache unavailable (%s)", e)
        _disk_db, _disk_path, _disk_failed = None, path, True
        return None


def _disk_get(keys: List[str]) -> Dict[str, np.ndarray]:
    out: Dict[str, np.ndarray] = {}
    with _disk_lock:
        db = _disk()
        if db is None or not keys:
            return out
        try:
            for i in range(0, len(keys), 500):
                part = keys[i:i + 500]
                marks = ",".join("?" * len(part))
                for k, blob in db.execute(f"select k, v from vec where k in ({marks})", part):
                    out[k] = _unit(np.frombuffer(blob, dtype=np.float16).astype(np.float32))
        except Exception as e:  # noqa: BLE001
            log.info("embedding disk cache read failed (%s)", e)
    return out


def _disk_put(items: Dict[str, np.ndarray]) -> None:
    with _disk_lock:
        db = _disk()
        if db is None or not items:
            return
        try:
            db.executemany("insert or replace into vec (k, v) values (?, ?)",
                           [(k, v.astype(np.float16).tobytes()) for k, v in items.items()])
            (n,) = db.execute("select count(*) from vec").fetchone()
            if n > EMBED_DISK_CACHE_ROWS:
                db.execute("delete from vec where rowid in (select rowid from vec order by rowid limit ?)",
                           (n - EMBED_DISK_CACHE_ROWS,))
            db.commit()
        except Exception as e:  # noqa: BLE001
            log.info("embedding disk cache write failed (%s)", e)


# -- the request

def _http_post(url: str, headers: dict, body: dict, timeout: float):
    """One POST (a seam the tests replace)."""
    return httpx.post(url, headers=headers, json=body, timeout=timeout)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _retry_delay(attempt: int, response) -> float:
    delay = min(EMBED_BACKOFF_MAX_S, EMBED_BACKOFF_BASE_S * (2 ** attempt))
    try:
        after = float(response.headers.get("retry-after")) if response is not None else 0.0
    except (TypeError, ValueError):
        after = 0.0
    return min(EMBED_BACKOFF_MAX_S, max(delay, after))


def _request_batch(inputs: List[str], kind: str, model: str, dim: int, key: str, attempts: int,
                   timeout: float) -> List[np.ndarray]:
    url = f"{gemini.EMBED_ENDPOINT}/models/{model}:batchEmbedContents"
    reqs = []
    for text in inputs:
        item: Dict[str, Any] = {"model": f"models/{model}", "content": {"parts": [{"text": text}]},
                                "outputDimensionality": dim}
        if _is_legacy_model(model):
            item["taskType"] = LEGACY_TASK_TYPES[kind]
        reqs.append(item)
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    last = "no attempt"
    switches = 0
    attempt = 0
    while attempt < max(1, attempts):
        response = None
        try:
            response = _http_post(url, headers, {"requests": reqs}, timeout)
            status = response.status_code
            if status == 200:
                vectors = [e.get("values") for e in (response.json().get("embeddings") or [])]
                if len(vectors) != len(inputs) or any(not v for v in vectors):
                    raise EmbeddingUnavailable(f"Gemini returned {len(vectors)} vectors for {len(inputs)} texts")
                return [_unit(v) for v in vectors]
            last = f"HTTP {status}"
            if gemini.is_key_error(status):
                try:
                    text = str(getattr(response, "text", "") or "") or json.dumps(response.json())
                except Exception:  # noqa: BLE001
                    text = ""
                nxt = gemini.next_key_after_failure(headers["x-goog-api-key"], status, text) \
                    if switches < len(gemini.keys()) else None
                if nxt:                     # another key takes over at once; this does not use up a retry
                    headers = {**headers, "x-goog-api-key": nxt}
                    switches += 1
                    continue
            if status not in (408, 429, 500, 502, 503, 504):
                try:
                    last += ": " + str(response.json().get("error", {}).get("message", ""))[:120]
                except Exception:  # noqa: BLE001
                    pass
                raise EmbeddingUnavailable(last)
        except EmbeddingUnavailable:
            raise
        except (httpx.HTTPError, ValueError) as e:      # timeouts, connection errors, a body that is not JSON
            last = f"{type(e).__name__}"
        if attempt + 1 < attempts:
            _sleep(_retry_delay(attempt, response))
        attempt += 1
    raise EmbeddingUnavailable(f"Gemini embeddings failed after {attempts} attempt(s): {last}")


def encode_texts(texts: Sequence[str], *, kind: str = "passage", titles: Optional[Sequence[Optional[str]]] = None,
                 **_ignored) -> np.ndarray:
    """Unit-length float32 embeddings of `texts`, shape (len(texts), embedding_dim()). kind "passage" (default) or
    "query" picks the prefix / task type; `titles` are the passage titles (none: "none"). Other keyword arguments of
    the removed local encoder (batch_size, normalize_embeddings, ...) are accepted and ignored.

    Cached by sha256(model + dimension + kind + text): a text seen before costs no request. Raises EmbeddingUnavailable
    when there is no key or Gemini cannot be reached (the caller falls back to keyword search or stores the text
    without a vector)."""
    global _embed_down_until, _embed_last_error
    texts = [str(t) if t is not None else "" for t in texts]
    dim = embedding_dim()
    if not texts:
        return np.zeros((0, dim), dtype=np.float32)
    kind = "query" if kind == "query" else "passage"
    model = embedding_model_name()
    titles = list(titles) if titles is not None else [None] * len(texts)
    inputs = [_prepare_input(t, kind, titles[i] if i < len(titles) else None, model) for i, t in enumerate(texts)]
    keys = [_cache_key(model, dim, kind, x) for x in inputs]

    found: Dict[str, np.ndarray] = {}
    with _embed_lock:
        for k in keys:
            v = _embed_cache.get(k)
            if v is not None:
                _embed_cache.move_to_end(k)
                found[k] = v
    missing = [k for k in dict.fromkeys(keys) if k not in found]
    if missing:
        for k, v in _disk_get(missing).items():
            if v.shape[0] == dim:
                found[k] = v
        missing = [k for k in missing if k not in found]
    if missing:
        key = gemini.embed_key()
        if not key:
            raise EmbeddingUnavailable("Needs a Gemini key")
        if time.monotonic() < _embed_down_until:
            raise EmbeddingUnavailable(_embed_last_error or "Gemini embeddings are unreachable just now")
        first = {k: x for k, x in zip(keys, inputs)}
        attempts = EMBED_QUERY_ATTEMPTS if kind == "query" else EMBED_MAX_ATTEMPTS
        timeout = EMBED_QUERY_TIMEOUT_S if kind == "query" else EMBED_TIMEOUT_S
        size = max(1, min(EMBED_BATCH_SIZE, _int_env("EMBED_BATCH_SIZE", EMBED_BATCH_SIZE)))
        fresh: Dict[str, np.ndarray] = {}
        try:
            for i in range(0, len(missing), size):
                part = missing[i:i + size]
                for k, v in zip(part, _request_batch([first[k] for k in part], kind, model, dim, gemini.embed_key() or key, attempts, timeout)):
                    if v.shape[0] != dim:
                        raise EmbeddingUnavailable(f"Gemini returned {v.shape[0]}-dimensional vectors, not {dim}")
                    fresh[k] = v
        except EmbeddingUnavailable as e:
            if "Needs a Gemini key" not in str(e):
                _embed_down_until = time.monotonic() + _float_env("EMBED_COOLDOWN_S", EMBED_COOLDOWN_S)
                _embed_last_error = str(e)[:160]
                log.warning("Gemini embeddings failed: %s (keyword search until it is back)", e)
            if fresh:                       # the batches that did arrive are worth keeping
                _remember(fresh)
            raise
        _embed_down_until = 0.0
        _embed_last_error = None
        _remember(fresh)
        found.update(fresh)
    return np.stack([found[k] for k in keys]).astype(np.float32, copy=False)


def _remember(vectors: Dict[str, np.ndarray]) -> None:
    with _embed_lock:
        for k, v in vectors.items():
            _embed_cache[k] = v
            _embed_cache.move_to_end(k)
        while len(_embed_cache) > EMBED_CACHE_SIZE:
            _embed_cache.popitem(last=False)
    _disk_put(vectors)


def encode_query(text: str) -> np.ndarray:
    """The unit vector of one question (shape (dim,)). Raises EmbeddingUnavailable like encode_texts."""
    return encode_texts([text], kind="query")[0]


# ------------------------------------------------------------------------------------------------ tokenizer

# The ONE place where Hindi (Devanagari), Hinglish (Roman Hindi) and English text is normalised for matching (BM25, the
# confidence keyword overlap; other modules import `tokenize` / `normalise_text`).
#
# Every word ends up as a Roman "key": a Devanagari word is transliterated (कितना -> kitna), every Roman word is folded
# the same way (ee -> i, oo -> u, ph -> f, w -> v, z -> j, doubled letters collapsed, English plural -s dropped), and
# a curated table maps the frequent spelling variants of one word, in both scripts, to one key (kb/kab/कब, doctor/dr/
# dactor/डॉक्टर, fees/phees/फीस/शुल्क). For a handful of words a caller says in Hindi about an English document (days,
# fee, doctor, timings, medicine...) the key is the English word, so "ravivar ko" finds "Sunday". Numbers: any Indian
# script digits become ASCII, 45,000 == 45000, a phone number written in groups also gives its digits joined, and
# ₹ / Rs / rupees / रुपये are one word. Both sides (questions and passages) go through exactly this code.

# Devanagari: letters, vowel signs, virama (U+0900-U+097F) except the danda marks U+0964/U+0965 and the digits
# U+0966-U+096F (digits are mapped to ASCII below so "४००" and "400" match).
_DEVANAGARI = r"ऀ-ॣ॰-ॿ"
_TOKEN_RE = re.compile(rf"[{_DEVANAGARI}]+|[a-z]+|[0-9]+")
_DEVA_RE = re.compile(rf"[{_DEVANAGARI}]")
# Decimal digits of the Indian scripts (Devanagari, Bengali, Gurmukhi, Gujarati, Odia, Tamil, Telugu, Kannada,
# Malayalam) -> ASCII. Kept as _DEVA_DIGITS for older callers.
_INDIC_DIGIT_BASES = (0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66)
_DEVA_DIGITS = {base + i: ord("0") + i for base in _INDIC_DIGIT_BASES for i in range(10)}
_ZERO_WIDTH_RE = re.compile("[​-‍⁠﻿­]")      # ZWJ/ZWNJ inside Devanagari words, soft hyphen
_LATIN_ACCENT_RE = re.compile("[À-ɏḀ-ỿ̀-ͯ]")
_COMBINING_RE = re.compile("[̀-ͯ]")                       # Latin combining accents only (not Devanagari signs)
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d\d)")                       # 45,000 / 1,20,000 -> 45000 / 120000
_RUPEE_RE = re.compile("[₹₨]")                                   # ₹ and the old "Rs" sign
_PHONE_RE = re.compile(r"\d+(?:[ \-]\d+)+")                          # 020-5550-1299, 98200 12345

_NUKTA, _CHANDRABINDU, _ANUSVARA = "़", "ँ", "ं"
_DEVA_PRE = str.maketrans({"ॉ": "ो", "ऑ": "ओ",   # candra O (डॉक्टर, ऑफिस) -> plain o
                           "ॅ": "े", "ऍ": "ए"})   # candra E -> e


def _collapse_repeats(word: str) -> str:
    """'kitnaa' -> 'kitna', 'accha' -> 'acha', 'feee' -> 'fe': a run of the same letter becomes one letter."""
    out: List[str] = []
    for ch in word:
        if not out or out[-1] != ch or ch.isdigit():
            out.append(ch)
    return "".join(out)


_VOWELS = set("aeiou")
_DIGRAPHS = (("kh", "K"), ("gh", "G"), ("chh", "Q"), ("ch", "C"), ("jh", "J"), ("th", "T"), ("dh", "D"), ("ph", "P"),
             ("bh", "B"), ("sh", "S"))


@lru_cache(maxsize=50000)
def _transliterate_devanagari(word: str) -> str:
    """A Devanagari word as a Roman 'Hinglish-like' spelling (before folding), so Hinglish questions can match Hindi
    text and vice versa: long vowels are written like short ones (ई -> i, ऊ -> u, आ -> a), the silent word-final and
    interior schwa is dropped (समय -> samay, कितना -> kitna), a word-final nasal is dropped (नहीं -> nahi)."""
    deva = word.replace(_NUKTA, "").replace(_CHANDRABINDU, _ANUSVARA).translate(_DEVA_PRE)
    if len(deva) > 1 and deva.endswith(_ANUSVARA):
        deva = deva[:-1]                                  # nasalised final vowel: नहीं -> नही, हैं -> है, बच्चों -> बच्चो
    try:
        from indic_transliteration import sanscript  # noqa: PLC0415 - optional dependency
        roman = sanscript.transliterate(deva, sanscript.DEVANAGARI, sanscript.ITRANS)
    except Exception:  # noqa: BLE001 - without the package the Devanagari token is used as it is
        return deva
    roman = roman.replace("j~n", "gy").replace("GY", "gy")            # ज्ञ: gyan
    roman = re.sub(r"[^A-Za-z]", "", roman)              # nasal marks (.N), stray signs, unconverted letters
    if roman.endswith("a") and len(roman) > 2:           # word-final inherent 'a' is silent in Hindi: bhojana -> bhojan
        roman = roman[:-1]
    roman = (roman.replace("RRi", "ri").replace("A", "a").replace("I", "i").replace("U", "u").replace("M", "n")
             .replace("x", "ksh").lower())
    # Interior schwa deletion: V C a C V -> V C C V (kitana -> kitna, baiThate -> baithte). Aspirated consonants and
    # sh are one sound written with two letters: they are folded to one placeholder letter while the rule runs.
    for dg, ph in _DIGRAPHS:
        roman = roman.replace(dg, ph)
    chars = list(roman)
    keep: List[str] = []
    for i, ch in enumerate(chars):
        if (ch == "a" and 2 <= i < len(chars) - 2 and chars[i - 1] not in _VOWELS and chars[i - 2] in _VOWELS
                and chars[i + 1] not in _VOWELS and chars[i + 2] in _VOWELS):
            continue
        keep.append(ch)
    roman = "".join(keep)
    for dg, ph in _DIGRAPHS:
        roman = roman.replace(ph, dg)
    return roman


def _fold_roman(word: str) -> str:
    """The spelling-insensitive key of one Roman word (English, Hinglish, or a transliterated Hindi word)."""
    if word.isdigit():
        return word
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]                                  # fees -> fee, doctors -> doctor
    word = (word.replace("ph", "f").replace("w", "v").replace("z", "j").replace("q", "k")
            .replace("ee", "i").replace("oo", "u"))
    word = re.sub(r"n(?=b|p)", "m", word)                # sanbandh / sambandh, chanpa / champa
    return _collapse_repeats(word)


# Spelling variants -> one key. Canonical word first (written naturally; folded when the table is built), then its
# variants in Roman and Devanagari. Where the canonical is English, a Hindi or Hinglish word finds the English
# document word. Keep entries unambiguous: a variant must not also be a common, different English or Hindi word.
_VARIANTS: Dict[str, Sequence[str]] = {
    # question and function words (mostly stopwords, unified so the stopword list catches every spelling)
    "hai": ("he", "h", "hain", "hein", "hay", "hy", "hei", "hae", "है", "हैं"),
    "nahi": ("nahin", "nai", "nhi", "nahe", "nahee", "nehi", "nahi", "नहीं", "नही", "ना"),
    "kya": ("kia", "kyaa", "kiya", "क्या", "किया"),
    "kyon": ("kyo", "kyu", "kyun", "kyoon", "क्यों", "क्यूं", "क्यूँ"),
    "kab": ("kb", "kabb", "कब"),
    "kitna": ("kitni", "kitne", "kitnaa", "kitana", "ktna", "kitney", "kitnee", "कितना", "कितनी", "कितने"),
    "kaise": ("kese", "kaisa", "kaisi", "kaisay", "kayse", "kaise", "कैसे", "कैसा", "कैसी"),
    "kahan": ("kaha", "kahaan", "kahaa", "kidhar", "किधर", "कहाँ", "कहां", "कहा"),
    "kaun": ("kon", "koun", "kaunsa", "konsa", "kaunsi", "konsi", "kaunse", "konse", "कौन", "कौनसा", "कौनसी", "कौनसे"),
    "ka": ("ke", "ki", "ko", "का", "की", "के", "को"),
    "mein": ("men", "me", "mai", "mei", "में", "मैं"),
    "aap": ("ap", "aapka", "aapki", "aapke", "apka", "apki", "apke", "आप", "आपका", "आपकी", "आपके"),
    "mujhe": ("muje", "mujhey", "mujhko", "mereko", "mujhe", "मुझे", "मुझको"),
    "chahiye": ("chaiye", "chahie", "chahiyee", "chahye", "chahiya", "चाहिए", "चाहिये"),
    "bataiye": ("batao", "bataye", "batayein", "batayen", "bataiyee", "batana", "बताइए", "बताइये", "बताओ", "बताएं",
                "बताये", "बताना"),
    "hota": ("hoti", "hote", "hoga", "hogi", "honge", "hona", "होता", "होती", "होते", "होगा", "होगी", "होंगे", "होना"),
    "hun": ("hoon", "hu", "hoo", "हूँ", "हूं", "हू"),
    "baje": ("bje", "बजे"),
    # money
    "rupee": ("rupees", "rs", "inr", "rupaye", "rupye", "rupay", "rupaya", "rupiya", "rupiye", "rupe", "रुपये", "रुपए",
              "रुपया", "रुपयों", "रु"),
    "money": ("paisa", "paise", "paisha", "pese", "पैसा", "पैसे"),
    "fee": ("fees", "phees", "phee", "fis", "shulk", "फीस", "फ़ीस", "शुल्क"),
    "price": ("keemat", "kimat", "daam", "mulya", "कीमत", "क़ीमत", "दाम", "मूल्य"),
    "free": ("muft", "nishulk", "मुफ्त", "मुफ़्त", "निःशुल्क", "निशुल्क"),
    "discount": ("chhoot", "chhut", "छूट"),
    "payment": ("bhugtan", "भुगतान"),
    # people, places, services (English loanwords first: their Devanagari spelling transliterates differently)
    "doctor": ("dr", "doktar", "daktar", "dactor", "dactar", "docter", "doktor", "daktor", "doctr", "doctoron",
               "डॉक्टर", "डाक्टर", "डॉक्टरों", "डाक्टरों"),
    "clinic": ("klinik", "clinik", "kilinik", "क्लिनिक", "क्लीनिक", "davakhana", "दवाखाना"),
    "hospital": ("aspatal", "haspatal", "hospitl", "aspataal", "अस्पताल", "हॉस्पिटल", "हास्पिटल"),
    "medicine": ("dawai", "davai", "dava", "dawa", "davaiyan", "dawaiyan", "aushadhi", "दवा", "दवाई", "दवाइयां",
                 "दवाइयाँ", "औषधि"),
    "pharmacy": ("chemist", "farmesi", "pharmesi", "फार्मेसी", "फ़ार्मेसी", "केमिस्ट"),
    "appointment": ("apointment", "appoinment", "apoinment", "appointmant", "अपॉइंटमेंट", "अपोइंटमेंट", "अपाइंटमेंट"),
    "emergency": ("imarjensi", "imrjensi", "emarjensi", "emergancy", "emarjency", "इमरजेंसी", "इमर्जेंसी",
                  "aapatkal", "आपातकाल"),
    "ambulance": ("embulens", "ambulans", "embulance", "एम्बुलेंस", "एंबुलेंस"),
    "dentist": ("dental", "teeth", "tooth", "dant", "daant", "दांत", "दाँत", "डेंटिस्ट", "डेंटल"),
    "test": ("janch", "jaanch", "जांच", "जाँच", "टेस्ट"),
    "report": ("riport", "रिपोर्ट"),
    "blood": ("khoon", "khun", "rakt", "खून", "ख़ून", "रक्त"),
    "eye": ("aankh", "ankh", "aankhon", "आंख", "आँख", "आंखों", "आँखों"),
    "child": ("children", "kid", "kids", "bachcha", "bachche", "bachchon", "bacche", "bachhe", "baccha", "bachon",
              "बच्चा", "बच्चे", "बच्चों", "बच्ची"),
    "lab": ("laib", "लैब", "prayogshala", "प्रयोगशाला"),
    "college": ("kolej", "kalej", "कॉलेज", "कालेज", "mahavidyalay", "महाविद्यालय"),
    "course": ("kors", "कोर्स", "pathyakram", "पाठ्यक्रम"),
    "hostel": ("hostal", "हॉस्टल", "होस्टल", "chhatravas", "छात्रावास"),
    "admission": ("admisan", "dakhila", "daakhila", "pravesh", "प्रवेश", "दाखिला", "एडमिशन"),
    "exam": ("exams", "examination", "pariksha", "imtihan", "imtehaan", "परीक्षा", "इम्तिहान"),
    "result": ("parinam", "natija", "nateeja", "परिणाम", "नतीजा", "रिजल्ट"),
    "scholarship": ("scolarship", "skolarship", "chhatravritti", "छात्रवृत्ति", "स्कॉलरशिप"),
    "office": ("ofis", "daftar", "karyalay", "दफ्तर", "दफ़्तर", "कार्यालय", "ऑफिस", "ऑफ़िस"),
    "meeting": ("baithak", "mulakat", "mulaqat", "बैठक", "मुलाकात", "मुलाक़ात", "मीटिंग"),
    "holiday": ("chhutti", "chutti", "छुट्टी"),
    "document": ("dastavez", "dastavej", "दस्तावेज़", "दस्तावेज"),
    "phone": ("fon", "फोन", "फ़ोन"),
    "number": ("nambar", "numbar", "nanbar", "नंबर", "नम्बर"),
    "name": ("naam", "नाम"),
    "available": ("milega", "milegi", "milenge", "milta", "milti", "milte", "uplabdh", "उपलब्ध", "मिलेगा", "मिलेगी",
                  "मिलेंगे", "मिलता", "मिलती", "मिलते"),
    "open": ("khula", "khuli", "khule", "khulta", "khulti", "khulte", "khulna", "khulega", "khulegi", "khulenge",
             "opan", "खुला", "खुली", "खुले", "खुलता", "खुलती", "खुलते", "खुलना", "खुलेगा", "खुलेगी", "ओपन"),
    "closed": ("band", "बंद"),
    "cancel": ("cancellation", "kainsil", "radd", "रद्द", "कैंसिल"),
    # time
    "time": ("timing", "timings", "samay", "samai", "taim", "tym", "टाइम", "समय", "टाइमिंग"),
    "hour": ("ghanta", "ghante", "ghanton", "घंटा", "घंटे", "घंटों"),
    "today": ("aaj", "आज"),
    "now": ("abhi", "अभी"),
    "day": ("din", "dino", "dinon", "दिन", "दिनों"),
    "week": ("hafta", "hafte", "saptah", "हफ्ता", "हफ़्ता", "हफ्ते", "हफ़्ते", "सप्ताह"),
    "month": ("mahina", "mahine", "mahino", "महीना", "महीने", "महीनों"),
    "year": ("saal", "varsh", "साल", "वर्ष"),
    "morning": ("subah", "subha", "सुबह"),
    "evening": ("shaam", "sham", "शाम"),
    "night": ("raat", "रात"),
    "afternoon": ("dopahar", "dopehar", "दोपहर"),
    "sunday": ("ravivar", "raviwar", "itvar", "itwar", "इतवार", "रविवार"),
    "monday": ("somvar", "somwar", "सोमवार"),
    "tuesday": ("mangalvar", "mangalwar", "मंगलवार"),
    "wednesday": ("budhvar", "budhwar", "बुधवार"),
    "thursday": ("guruvar", "guruwar", "brihaspativar", "गुरुवार", "बृहस्पतिवार"),
    "friday": ("shukravar", "shukrawar", "शुक्रवार"),
    "saturday": ("shanivar", "shaniwar", "शनिवार"),
}


def _raw_key(word: str) -> str:
    """Script conversion + folding, without the variant table."""
    if _DEVA_RE.search(word):
        word = _transliterate_devanagari(word)
        if _DEVA_RE.search(word):                         # indic_transliteration missing: keep the Devanagari word
            return word.replace(_NUKTA, "").replace(_CHANDRABINDU, _ANUSVARA)
    return _fold_roman(word)


@lru_cache(maxsize=1)
def _variant_table() -> Dict[str, str]:
    """folded variant -> folded canonical. Built once (lazily: it transliterates the Devanagari entries)."""
    table: Dict[str, str] = {}
    for canonical, variants in _VARIANTS.items():
        canon = _raw_key(canonical.lower())
        for v in (canonical, *variants):
            key = _raw_key(v.lower())
            if key and key != canon:
                table.setdefault(key, canon)              # the first entry wins a clash (tests check there is none)
    return table


@lru_cache(maxsize=100000)
def normalise_token(token: str) -> str:
    """Canonical key of one lowercase token (Latin, digits or Devanagari): the same key for the spelling variants of a
    word in both scripts (kitni / kitne / कितनी -> kitna)."""
    if not token:
        return ""
    if token.isdigit():
        return token
    key = _raw_key(token)
    return _variant_table().get(key, key)


def _prepare(text: str) -> str:
    """Unicode clean-up shared by every tokenizer call: NFKC, zero-width characters out, Latin accents stripped
    (Pāṇḍey -> Pandey), Indian-script digits to ASCII, thousands separators out, ₹ -> 'rupee', lowercase."""
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    if _LATIN_ACCENT_RE.search(text):
        text = unicodedata.normalize("NFC", _COMBINING_RE.sub("", unicodedata.normalize("NFD", text)))
    text = text.translate(_DEVA_DIGITS)
    text = _THOUSANDS_RE.sub("", text)
    text = _RUPEE_RE.sub(" rupee ", text)
    return text.lower()


# Function words dropped before BM25 (both scripts). They carry no topic and, in short chunks, only add noise.
_STOPWORDS = frozenset("""
a an the of in on at to for and or is are was were be been it this that these those with by from as i me my we you your
do does did can could will would should what when where which who how why not no yes please tell about there any
hai ka ka mein se par ko ne bhi aur ya to toh ye yeh wo vo kya kab kaise kitna kyo koi kaun kahan aap mujhe chahiye
bataiye hota hun hum ham tha thi the raha rahi rahe sakta sakti sakte
है हैं था थे थी का की के को में से पर ने भी और या तो यह ये वह वो क्या कब कैसे कितना कितने कितनी क्यों कोई एक
कौन कहाँ कहां आप मुझे चाहिए बताइए होता हूँ हम मैं रहा रही रहे सकता सकती सकते
""".split())


@lru_cache(maxsize=1)
def _stopwords() -> frozenset:
    """The stopword list in normalised form (so 'है' and 'hai', 'का' and 'ka' are the same stopword)."""
    return frozenset(normalise_token(w) for w in _STOPWORDS)


def _phone_joins(text: str) -> List[str]:
    """A number written in groups ('020-5550-1299', '98200 12345') also as one token, as a caller may say or type it
    without the separators."""
    out = []
    for m in _PHONE_RE.finditer(text):
        joined = re.sub(r"[ \-]", "", m.group(0))
        if len(joined) >= 7:
            out.append(joined)
    return out


@lru_cache(maxsize=20000)
def _tokenize_cached(text: str, drop_stopwords: bool) -> tuple:
    text = _prepare(text)
    tokens = [normalise_token(t) for t in _TOKEN_RE.findall(text)]
    tokens = [t for t in tokens if t] + _phone_joins(text)
    if drop_stopwords:
        stop = _stopwords()
        kept = [t for t in tokens if t not in stop]
        return tuple(kept or tokens)    # a question made only of function words keeps them
    return tuple(tokens)


def tokenize(text: str, drop_stopwords: bool = True) -> List[str]:
    """Normalised tokens of a text (see the notes above this section): punctuation removed, digits of Indian scripts
    mapped to ASCII, Devanagari words transliterated to Roman, Roman words folded, spelling variants in both scripts
    unified. Stopwords (both scripts) are dropped unless that would leave nothing."""
    return list(_tokenize_cached(text or "", drop_stopwords))


def content_tokens(text: str) -> List[str]:
    """Normalised tokens without stopwords, even when that leaves nothing (for keyword-overlap checks)."""
    stop = _stopwords()
    return [t for t in _tokenize_cached(text or "", False) if t not in stop]


def normalise_text(text: str) -> str:
    """The whole text as space-separated normalised keys (stopwords kept): two texts that differ only in script,
    Hinglish spelling, digits or ₹/Rs give the same string. For other modules that compare spoken text (contract 5)."""
    return " ".join(_tokenize_cached(text or "", False))


# ------------------------------------------------------------------------------------------------ BM25

class BM25:
    """Okapi BM25 (k1=1.5, b=0.75) over pre-tokenised documents. Pure Python, no dependencies."""

    def __init__(self, docs: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(d) for d in docs]
        self.lengths = [len(d) for d in docs]
        self.n = len(docs)
        self.avgdl = (sum(self.lengths) / self.n) if self.n else 0.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        self.idf = {t: math.log(1.0 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query_tokens: Iterable[str]) -> List[float]:
        q = [t for t in dict.fromkeys(query_tokens) if t in self.idf]
        out = [0.0] * self.n
        if not q or not self.avgdl:
            return out
        for i, tf in enumerate(self.docs):
            norm = self.k1 * (1.0 - self.b + self.b * self.lengths[i] / self.avgdl)
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1.0) / (f + norm)
            out[i] = s
        return out


_bm25_cache: "OrderedDict[tuple, BM25]" = OrderedDict()
_bm25_lock = threading.Lock()
_BM25_CACHE_SIZE = 16


def bm25_index(texts: Sequence[str]) -> BM25:
    """BM25 over `texts`, built once per distinct list of texts (an agent's passages are the same on every turn, so the
    index is not rebuilt per question; a changed document gives a new list and a new index)."""
    key = tuple(texts)
    with _bm25_lock:
        bm = _bm25_cache.get(key)
        if bm is not None:
            _bm25_cache.move_to_end(key)
            return bm
    bm = BM25([tokenize(t) for t in key])
    with _bm25_lock:
        _bm25_cache[key] = bm
        while len(_bm25_cache) > _BM25_CACHE_SIZE:
            _bm25_cache.popitem(last=False)
    return bm


def bm25_ranking(query: str, texts: Sequence[str]) -> List[int]:
    """Indices of `texts` that share at least one token with the query, best first (empty when nothing matches)."""
    scores = bm25_index(texts).scores(tokenize(query))
    order = [i for i in sorted(range(len(texts)), key=lambda i: -scores[i]) if scores[i] > 0]
    return order


# ------------------------------------------------------------------------------------------------ fusion

def rrf_fuse(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> List[int]:
    """Reciprocal Rank Fusion: score(d) = sum over rankings of 1 / (k + rank(d)), ranks starting at 1. Returns the ids
    best first; ties keep the order of first appearance (so the first ranking, the dense one, wins a tie)."""
    scores = rrf_scores(rankings, k)
    first_seen: Dict[int, int] = {}
    for ranking in rankings:
        for doc in ranking:
            first_seen.setdefault(doc, len(first_seen))
    return sorted(scores, key=lambda d: (-scores[d], first_seen[d]))


def rrf_scores(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> Dict[int, float]:
    scores: Dict[int, float] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, start=1):
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (k + rank)
    return scores


def dense_ranking(similarities: Sequence[float]) -> List[int]:
    return sorted(range(len(similarities)), key=lambda i: -similarities[i])


# Weighted score fusion (Bruch, Gai & Ingber, "An Analysis of Fusion Functions for Hybrid Retrieval", TOIS 2023: a
# convex combination of normalised scores beats RRF in and out of domain). Each signal is scaled to 0..1 over the
# candidates (theoretical min-max: score / best score, the paper's TM2C2; RETRIEVAL_NORM=minmax for observed min-max),
# then fused = sum(w_s * norm_s) / sum(active w_s). Dense 0.8 : BM25 0.2 (BM25 keeps exact numbers, names and Hinglish
# spellings that a vector blurs). When a signal has no value at all (no vectors: keyword search) it drops out and BM25
# alone orders the passages. The weights were tuned on the previous local embedder's bench sets, not on Gemini's: run
# bench/retrieval_eval.py with a key before changing them.
DEFAULT_FUSION_WEIGHTS: Dict[str, float] = {"dense": 0.8, "bm25": 0.2}
SIGNALS = ("dense", "bm25")
CANDIDATES_PER_SIGNAL = 20        # candidates = union of the dense top-N and the BM25 top-N


def fusion_mode() -> str:
    """RETRIEVAL_FUSION=weighted (default) | rrf (Reciprocal Rank Fusion, k=60)."""
    mode = (os.getenv("RETRIEVAL_FUSION") or "weighted").strip().lower()
    return mode if mode in ("weighted", "rrf") else "weighted"


def fusion_weights() -> Dict[str, float]:
    """RETRIEVAL_WEIGHTS="dense:0.8,bm25:0.2" (any subset; missing ones keep the default, 0 switches a signal off).
    Malformed entries are ignored."""
    weights = dict(DEFAULT_FUSION_WEIGHTS)
    for part in (os.getenv("RETRIEVAL_WEIGHTS") or "").split(","):
        name, _, value = part.partition(":")
        name = name.strip().lower()
        try:
            if name in weights:
                weights[name] = max(0.0, float(value))
        except ValueError:
            continue
    return weights


def candidates_per_signal() -> int:
    return max(1, _int_env("RETRIEVAL_CANDIDATES", CANDIDATES_PER_SIGNAL))


def minmax(values: Sequence[Optional[float]]) -> List[float]:
    """Min-max normalisation to 0..1; a missing value counts as 0; all-equal present values become 1."""
    present = [v for v in values if v is not None]
    if not present:
        return [0.0] * len(values)
    lo, hi = min(present), max(present)
    if hi - lo < 1e-12:
        return [1.0 if v is not None else 0.0 for v in values]
    return [((v - lo) / (hi - lo)) if v is not None else 0.0 for v in values]


def tmm(values: Sequence[Optional[float]], floor: float = 0.0) -> List[float]:
    """Theoretical-min-max normalisation (Bruch et al.'s TM2C2): (v - floor) / (max - floor), with a fixed lower bound
    instead of the observed minimum, so a small difference between two close candidates is not stretched to 0 vs 1.
    Every score fused here is >= 0 for a relevant text (cosine, BM25), so the floor is 0."""
    present = [v for v in values if v is not None]
    if not present:
        return [0.0] * len(values)
    hi = max(present)
    if hi - floor < 1e-12:
        return [0.0] * len(values)
    return [max(0.0, (v - floor) / (hi - floor)) if v is not None else 0.0 for v in values]


def score_norm() -> str:
    """RETRIEVAL_NORM=tmm (default) | minmax: how each signal is scaled to 0..1 before the weighted sum."""
    mode = (os.getenv("RETRIEVAL_NORM") or "tmm").strip().lower()
    return mode if mode in ("tmm", "minmax") else "tmm"


def weighted_fuse(signals: Dict[str, Sequence[Optional[float]]], weights: Dict[str, float]) -> List[float]:
    """Convex combination of normalised signals (RETRIEVAL_NORM: tmm by default, or minmax). A signal with no value at
    all, or weight 0, is left out and the remaining weights are renormalised, so the result is always in 0..1."""
    n = max((len(v) for v in signals.values()), default=0)
    active = [s for s, vals in signals.items() if weights.get(s, 0.0) > 0 and any(v is not None for v in vals)]
    total = sum(weights[s] for s in active)
    out = [0.0] * n
    if not active or total <= 0:
        return out
    norm = minmax if score_norm() == "minmax" else tmm
    for s in active:
        w = weights[s] / total
        for i, v in enumerate(norm(signals[s])):
            out[i] += w * v
    return out


# -- passage rows: question-index rows (contract 3) and headers (contract 4)

def row_text(row: dict) -> str:
    return str(row.get("chunk_text") or row.get("content") or "")


def passage_text(row: dict) -> str:
    """The text of a passage as it is matched and shown: `header + "\\n" + text` when the row has a header."""
    header = str(row.get("header") or "").strip()
    text = row_text(row)
    return f"{header}\n{text}" if header else text


def is_question_row(row: dict) -> bool:
    return str(row.get("kind") or "passage").lower() == "question"


def _parents(rows: Sequence[dict]) -> List[Optional[int]]:
    """For each row, the index of the passage it stands for: itself for a passage, its parent passage for a question
    row (None when the parent is not among the rows: such a question can never be returned)."""
    by_id = {r.get("id"): i for i, r in enumerate(rows) if not is_question_row(r) and r.get("id") is not None}
    return [by_id.get(r.get("parent_chunk_id")) if is_question_row(r) else i for i, r in enumerate(rows)]


def rank_rows(query: str, rows: Sequence[dict], similarities: Sequence[Optional[float]], *,
              info: Optional[dict] = None) -> List[dict]:
    """Rank knowledge rows for a question. Returns one entry per PASSAGE, best first:
    {"index": i (into rows), "score": fused 0..1, "similarity": best dense cosine of the passage or its questions,
     "signals": {"dense", "bm25"} of its best-scoring row, "via": "passage" | "question"}.

    `similarities[i]` is the dense cosine of row i, or None when it has no comparable embedding (no vector yet, another
    dimension, or the embedder is away: all None). Such a row takes part through BM25 only, so vectors of different
    dimensions are never compared, and with no vector at all the whole ranking is the BM25 keyword search. Question
    rows (kind="question") are scored like passages but stand for their parent passage (best score wins); they are
    never returned themselves.
    HYBRID_SEARCH=off: dense order only (BM25 only when no row has a vector). RETRIEVAL_FUSION=rrf: Reciprocal Rank
    Fusion (k=60) of the full dense and BM25 rankings. `info`, when given, is filled with what the fusion did (the
    signals used, the agreement of the dense and BM25 tops, `mode`: "hybrid" | "dense" | "keyword") for the confidence
    assessment."""
    n = len(rows)
    info = info if info is not None else {}
    if not n:
        info.update({"fusion": None, "signals": [], "n_candidates": 0, "mode": "keyword"})
        return []
    parent = _parents(rows)
    dense_order = sorted((i for i in range(n) if similarities[i] is not None and parent[i] is not None),
                         key=lambda i: -similarities[i])  # type: ignore[operator]

    def best_dense(p_rows: List[int]) -> Optional[float]:
        vals = [similarities[i] for i in p_rows if similarities[i] is not None]
        return max(vals) if vals else None  # type: ignore[type-var]

    members: Dict[int, List[int]] = {}
    for i, p in enumerate(parent):
        if p is not None:
            members.setdefault(p, []).append(i)

    if dense_order and (not hybrid_enabled() or not (query or "").strip()):
        out, seen = [], set()
        for i in dense_order:
            p = parent[i]
            if p in seen:
                continue
            seen.add(p)
            out.append({"index": p, "score": max(0.0, float(similarities[i])), "similarity": best_dense(members[p]),  # type: ignore[arg-type]
                        "signals": {"dense": similarities[i]}, "via": "question" if p != i else "passage"})
        info.update({"fusion": "dense", "signals": ["dense"], "n_candidates": len(out), "agree": None, "mode": "dense"})
        return out

    texts = [passage_text(r) if not is_question_row(r) else row_text(r) for r in rows]
    bm = bm25_index(texts).scores(tokenize(query))
    bm_order = sorted((i for i in range(n) if bm[i] > 0 and parent[i] is not None), key=lambda i: -bm[i])
    dense_top = parent[dense_order[0]] if dense_order else None
    bm_top = parent[bm_order[0]] if bm_order else None
    info.update({"dense_top": dense_top, "bm25_top": bm_top, "mode": "hybrid" if dense_order else "keyword",
                 "agree": (dense_top == bm_top) if (dense_top is not None and bm_top is not None) else None})

    cap = candidates_per_signal()
    cand_parents: List[int] = []
    for i in dense_order[:cap] + bm_order[:cap]:
        if parent[i] not in cand_parents:
            cand_parents.append(parent[i])  # type: ignore[arg-type]
    cand_rows = [i for p in cand_parents for i in members[p]]

    signals: Dict[str, List[Optional[float]]] = {"dense": [similarities[i] for i in cand_rows],
                                                 "bm25": [bm[i] for i in cand_rows]}
    weights = fusion_weights()
    mode = fusion_mode()
    if mode == "rrf":
        rankings = [r for r in (dense_order, bm_order) if r]
        rr = rrf_scores(rankings)
        max_possible = max(1, len(rankings)) / (RRF_K + 1)
        ordered = rrf_fuse(rankings)
        row_score = {i: rr[i] / max_possible for i in ordered}
        scored_rows = ordered
    else:
        fused = weighted_fuse(signals, weights)
        row_score = {i: fused[j] for j, i in enumerate(cand_rows)}
        scored_rows = cand_rows

    best: Dict[int, int] = {}            # parent -> its best-scoring row
    for i in scored_rows:
        p = parent[i]
        if p is None:
            continue
        if p not in best or row_score[i] > row_score[best[p]]:
            best[p] = i
    first_seen = {p: k for k, p in enumerate(dict.fromkeys(parent[i] for i in scored_rows if parent[i] is not None))}
    ranked = sorted(best, key=lambda p: (-row_score[best[p]], -(best_dense(members[p]) or -2.0), first_seen[p]))

    def entry(p: int, score: float) -> dict:
        i = best.get(p, p)
        sig = {"dense": similarities[i], "bm25": round(bm[i], 4)}
        return {"index": p, "score": round(float(score), 4), "similarity": best_dense(members[p]), "signals": sig,
                "via": "question" if i != p else "passage"}

    out = [entry(p, row_score[best[p]]) for p in ranked]
    # Passages outside the candidates follow (a caller may ask for the full ranking): dense order, then the rest.
    seen = set(ranked)
    for i in dense_order:
        p = parent[i]
        if p not in seen:
            seen.add(p)
            out.append(entry(p, 0.0))
    for p in sorted(members):
        if p not in seen and mode == "weighted" and any(bm[i] > 0 for i in members[p]):
            seen.add(p)
            out.append(entry(p, 0.0))
    used = [s for s in SIGNALS if (mode == "rrf" or weights.get(s, 0) > 0) and any(v is not None for v in signals[s])
            and (s != "bm25" or bm_order)]
    info.update({"fusion": mode, "signals": used, "n_candidates": len(cand_parents)})
    return out


def hybrid_order(query: str, texts: Sequence[str], similarities: Sequence[Optional[float]]) -> List[int]:
    """Fused order of `texts`. `similarities[i]` is the dense cosine of chunk i, or None when that chunk has no usable
    embedding (different dimension): such a chunk takes part through BM25 only, so mixed dimensions are never
    compared. Same fusion as rank_rows (RETRIEVAL_FUSION, RETRIEVAL_WEIGHTS)."""
    return [e["index"] for e in rank_rows(query, [{"chunk_text": t} for t in texts], similarities)]


# ------------------------------------------------------------------------------------------------ confidence

# How sure retrieval is that the passages answer the question (contract 2, read by services/grounding.py). Inputs:
# the dense cosine of the best passage, the fused top score and the gap to the second passage, the share of the
# question's content words found in the best passage (after the Hinglish/Devanagari normaliser), and whether the dense
# and BM25 rankings agree on the best passage. Without any vector (keyword search) only the last three exist: "high"
# needs half the question's content words in the best passage, "low" fewer than 15%.
# The dense thresholds below were calibrated on the previous local embedder (unrelated text 0.3-0.45, an answering
# passage 0.55-0.8). Gemini's cosines sit higher, so they are shifted up: treat them as a starting point and calibrate
# with bench/retrieval_eval.py (it prints the dense scores of answerable and unanswerable questions). Every threshold
# has an env override (RETRIEVAL_CONF_DENSE_HIGH=...).
CONF_DENSE_HIGH = 0.68      # best passage this close in meaning: high, when another signal agrees
CONF_DENSE_LOW = 0.52       # below this the passage is probably about something else
CONF_DENSE_NONE = 0.40      # below this, with no shared keyword and no BM25 match at all: nothing relevant
CONF_GAP_CLEAR = 0.15       # fused top1 - top2: a clear winner
CONF_OVERLAP_HIGH = 0.5     # half the question's content words are in the best passage
CONF_OVERLAP_LOW = 0.15


def _conf_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(f"RETRIEVAL_{name}") or default)
    except ValueError:
        return default


def confidence_thresholds() -> Dict[str, float]:
    """The thresholds in force (RETRIEVAL_CONF_DENSE_HIGH=0.62 etc. override the constants above)."""
    names = ("CONF_DENSE_HIGH", "CONF_DENSE_LOW", "CONF_DENSE_NONE", "CONF_GAP_CLEAR", "CONF_OVERLAP_HIGH",
             "CONF_OVERLAP_LOW")
    return {n.lower(): _conf_env(n, globals()[n]) for n in names}


def keyword_overlap(query: str, passage: str) -> Optional[float]:
    """Share of the question's content words (normalised, stopwords out) that occur in the passage; None when the
    question has no content word."""
    q = set(content_tokens(query))
    if not q:
        return None
    return len(q & set(content_tokens(passage))) / len(q)


def assess_confidence(chunks: Sequence[Any], query: str, info: Optional[dict] = None) -> dict:
    """{"level": "high"|"medium"|"low"|"none", "top": fused score of the best passage, "gap": top1 - top2,
    "reason": short text, plus "dense", "overlap", "agree"}.

    `chunks` are the ranked passage rows (dicts with `score`, `similarity`, `chunk_text`/`header`); plain strings are
    accepted (only the keyword overlap is then known). `info` is what rank_rows reported (dense/BM25 agreement). With no
    dense score on the best passage (keyword search) the rating comes from the keyword overlap alone. Never raises."""
    t = confidence_thresholds()
    rows = [c if isinstance(c, dict) else {"chunk_text": str(c)} for c in (chunks or [])]
    if not rows:
        return {"level": "none", "top": 0.0, "gap": 0.0, "reason": "no passages", "dense": None, "overlap": None,
                "agree": None}
    try:
        scores = [r.get("score") for r in rows]
        top = float(scores[0]) if isinstance(scores[0], (int, float)) else None
        second = float(scores[1]) if len(scores) > 1 and isinstance(scores[1], (int, float)) else 0.0
        gap = (top - second) if top is not None else 0.0
        dense = rows[0].get("similarity")
        dense = float(dense) if isinstance(dense, (int, float)) else None
        overlap = keyword_overlap(query, passage_text(rows[0]))
        agree = (info or {}).get("agree")
        ov = overlap if overlap is not None else 0.0
        reasons = []
        if dense is None:
            level = "high" if ov >= t["conf_overlap_high"] else "medium" if ov >= t["conf_overlap_low"] else "low"
            reasons.append(f"keyword overlap {ov:.2f} only (no vector score)")
        elif dense < t["conf_dense_none"] and ov == 0 and agree is None:
            level = "none"
            reasons.append(f"dense {dense:.2f} < {t['conf_dense_none']}, no shared keyword, no BM25 match")
        elif dense < t["conf_dense_low"] and ov < t["conf_overlap_low"] and not agree:
            level = "low"
            reasons.append(f"dense {dense:.2f} < {t['conf_dense_low']}, overlap {ov:.2f}")
        elif dense >= t["conf_dense_high"] and (agree or gap >= t["conf_gap_clear"] or ov >= t["conf_overlap_high"]):
            level = "high"
            reasons.append(f"dense {dense:.2f}" + (", dense and BM25 agree" if agree else "")
                           + (f", gap {gap:.2f}" if gap >= t["conf_gap_clear"] else "")
                           + (f", overlap {ov:.2f}" if ov >= t["conf_overlap_high"] else ""))
        else:
            level = "medium"
            reasons.append(f"dense {dense:.2f}")
            reasons.append(f"gap {gap:.2f}, overlap {ov:.2f}")
        return {"level": level, "top": round(top, 4) if top is not None else (round(dense, 4) if dense else 0.0),
                "gap": round(gap, 4), "reason": "; ".join(reasons),
                "dense": round(dense, 4) if dense is not None else None,
                "overlap": round(overlap, 3) if overlap is not None else None, "agree": agree}
    except Exception as e:  # noqa: BLE001 - a confidence bug must never break a turn
        log.warning("assess_confidence failed: %s", e)
        return {"level": "medium", "top": 0.0, "gap": 0.0, "reason": "assessment failed"}

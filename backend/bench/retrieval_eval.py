"""Knowledge-retrieval evaluation: BM25 keyword search alone, hosted embeddings alone, and the two fused.

It imports services/retrieval.py directly (the same tokenizer, BM25 and fusion the API uses) and never calls the running
API. With EMBEDDINGS_API_KEY set (backend/.env or the environment) the passages and the questions are embedded through
the embeddings service (services/retrieval.encode_texts: one batched request per 100 texts, cached on disk, so a second run is free).
Without a key only the BM25 row is scored: that is the search an agent falls back to with no key.

Data: bench/data/retrieval_questions.json (19 questions about the clinic template document, in English, Hindi in
Devanagari and Hinglish in Roman letters). A question is a HIT at k when one of the top-k chunks contains one of its
`gold` strings. The documents are split into small chunks (about 40 words, never across a heading), because the
production chunker (500 words) turns each of these short templates into a single chunk and every system would score 100%.

    backend/.venv/bin/python backend/bench/retrieval_eval.py                       # BM25, embeddings, fused
    backend/.venv/bin/python backend/bench/retrieval_eval.py --quick
    backend/.venv/bin/python backend/bench/retrieval_eval.py --chunker production   # the app's 120-word chunks

Writes bench/results/retrieval_eval-<timestamp>.json (git-ignored) with every ranking and the timings.
"""
import argparse
import datetime as dt
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List

import numpy as np

BENCH_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BENCH_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv  # noqa: E402

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:  # pragma: no cover
    pass

from services import chunking, retrieval  # noqa: E402

DATA = BENCH_DIR / "data" / "retrieval_questions.json"
RESULTS_DIR = BENCH_DIR / "results"
KS = (1, 3)


def chunk_markdown(text: str, max_words: int = 40) -> List[str]:
    """Split by `#` headings, then pack lines into chunks of up to `max_words` words (a line is never split); every chunk
    starts with its heading, which is how a section title reaches the embedding."""
    chunks: List[str] = []
    heading, buf, words = "", [], 0

    def flush():
        nonlocal buf, words
        if buf:
            chunks.append((heading + "\n" if heading else "") + "\n".join(buf))
        buf, words = [], 0

    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            flush()
            heading = line.lstrip("# ").strip()
            continue
        n = len(line.split())
        if buf and words + n > max_words:
            flush()
        buf.append(line)
        words += n
    flush()
    return chunks


def load_corpus(max_words: int, chunker: str = "markdown", overlap: int = chunking.DEFAULT_OVERLAP_WORDS):
    """`chunker` markdown: the 40-word packer above (no overlap). production: services/chunking.py, the splitter an
    uploaded document goes through (about 120 words, 20 words of overlap, never across a heading)."""
    spec = json.loads(DATA.read_text(encoding="utf-8"))
    cut = (lambda text: chunking.chunk_text(text, max_words, overlap)) if chunker == "production" \
        else (lambda text: chunk_markdown(text, max_words))
    docs = {name: cut((BACKEND_DIR / rel).read_text(encoding="utf-8")) for name, rel in spec["docs"].items()}
    problems = []
    for i, q in enumerate(spec["questions"]):
        for g in q["gold"]:
            if not any(g in c for c in docs[q["doc"]]):
                problems.append(f"question {i} ({q['q']!r}): gold {g!r} is in no chunk")
    if problems:
        raise SystemExit("gold strings missing from the chunks:\n" + "\n".join(problems))
    return spec["questions"], docs


def rss_mb() -> float:
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except Exception:  # noqa: BLE001
        return float("nan")


def is_hit(chunks: List[str], order: List[int], gold: List[str], k: int) -> bool:
    return any(g in chunks[i] for i in order[:k] for g in gold)


def evaluate(questions, docs, timings: Dict[str, dict]):
    """Returns ({system name: [ranking per question]}, [top dense cosine per question]). The embeddings systems need a key."""
    systems: Dict[str, List[List[int]]] = {"BM25 only": [retrieval.bm25_ranking(q["q"], docs[q["doc"]]) for q in questions]}
    top_dense: List[float] = []
    status = retrieval.embedder_status()
    timings["embedder"] = {k: status[k] for k in ("provider", "model", "dim", "mode", "reason")}
    if not retrieval.embeddings_configured():
        print("no EMBEDDINGS_API_KEY: scoring BM25 only (the keyword search an agent falls back to)")
        return systems, top_dense
    try:
        t = time.perf_counter()
        doc_vecs = {d: retrieval.encode_texts(chunks, kind="passage") for d, chunks in docs.items()}
        timings["embedder"]["passages_s"] = round(time.perf_counter() - t, 2)
        qtimes = []
        qvecs = []
        for q in questions:
            t = time.perf_counter()
            qvecs.append(retrieval.encode_query(q["q"]))
            qtimes.append((time.perf_counter() - t) * 1000)
    except retrieval.EmbeddingUnavailable as e:
        print(f"hosted embeddings unavailable ({e}): scoring BM25 only")
        return systems, top_dense
    timings["embedder"]["query_ms_median"] = round(statistics.median(qtimes), 1)
    timings["embedder"]["query_ms_p95"] = round(float(np.percentile(qtimes, 95)), 1)
    systems["Embeddings dense"], systems["Embeddings + BM25 (RRF)"], systems["Embeddings + BM25 (weighted)"] = [], [], []
    for q, qv in zip(questions, qvecs):
        chunks = docs[q["doc"]]
        sims = [float(x) for x in doc_vecs[q["doc"]] @ qv]
        dense = retrieval.dense_ranking(sims)
        systems["Embeddings dense"].append(dense)
        systems["Embeddings + BM25 (RRF)"].append(retrieval.rrf_fuse([dense, retrieval.bm25_ranking(q["q"], chunks)]))
        ranked = retrieval.rank_rows(q["q"], [{"chunk_text": c} for c in chunks], sims)
        systems["Embeddings + BM25 (weighted)"].append([e["index"] for e in ranked])
        top_dense.append(max(sims))
    return systems, top_dense


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chunk-words", type=int, default=None, help="words per chunk (default 40; 120 with --chunker production)")
    ap.add_argument("--chunker", choices=["markdown", "production"], default="markdown",
                    help="markdown: 40-word line packer; production: the app's splitter (services/chunking.py)")
    ap.add_argument("--quick", action="store_true", help="first 9 questions only (smoke test)")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    if args.chunk_words is None:
        args.chunk_words = chunking.DEFAULT_CHUNK_WORDS if args.chunker == "production" else 40
    questions, docs = load_corpus(args.chunk_words, args.chunker)
    if args.quick:
        questions = questions[:9]
    print(f"{len(questions)} questions, {sum(len(c) for c in docs.values())} chunks "
          f"({', '.join(f'{d}: {len(c)}' for d, c in docs.items())}), embedder: {retrieval.embedder_status()['mode']}")

    timings: Dict[str, dict] = {}
    results, top_dense = evaluate(questions, docs, timings)

    langs = ["en", "hi", "hinglish"]
    header = f"{'system':32s}" + "".join(f"{'hit@' + str(k):>8s}" for k in KS) + "   " + \
             "  ".join(f"{lang + ' @1/@3':>13s}" for lang in langs)
    print("\n" + header)
    rows_out = {}
    for name, orders in results.items():
        total = {k: sum(is_hit(docs[q["doc"]], o, q["gold"], k) for q, o in zip(questions, orders)) for k in KS}
        by_lang = {}
        for lang in langs:
            idx = [i for i, q in enumerate(questions) if q["lang"] == lang]
            by_lang[lang] = {k: sum(is_hit(docs[questions[i]["doc"]], orders[i], questions[i]["gold"], k) for i in idx)
                             for k in KS} | {"n": len(idx)}
        n = len(questions)
        rows_out[name] = {"hit@1": total[1] / n, "hit@3": total[3] / n, "by_lang": by_lang}
        print(f"{name:32s}" + "".join(f"{total[k] / n:8.1%}" for k in KS) + "   " + "  ".join(
            f"{by_lang[l][1]:>4d}/{by_lang[l][3]:>2d} of {by_lang[l]['n']:<2d}" for l in langs))

    print("\ntimings:")
    for key, t in timings.items():
        print(f"  {key}: " + ", ".join(f"{k}={v}" for k, v in t.items()))
    if top_dense:       # for calibrating the confidence thresholds (services/retrieval.py CONF_DENSE_*)
        pct = np.percentile(top_dense, [5, 25, 50, 75, 95])
        print("top dense cosine of the best passage per question (p5/p25/p50/p75/p95): " + " / ".join(f"{v:.2f}" for v in pct))

    if not args.no_save:
        RESULTS_DIR.mkdir(exist_ok=True)
        out = RESULTS_DIR / f"retrieval_eval-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        misses = {name: [questions[i]["q"] for i, o in enumerate(orders)
                         if not is_hit(docs[questions[i]["doc"]], o, questions[i]["gold"], 3)]
                  for name, orders in results.items()}
        out.write_text(json.dumps({"settings": vars(args),
                                   "questions": len(questions), "results": rows_out, "timings": timings,
                                   "missed_at_3": misses}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()

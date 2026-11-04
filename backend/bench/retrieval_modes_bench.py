"""Retrieval fusion modes: BM25 keyword search alone, Gemini dense alone, RRF and weighted score fusion, the
Hinglish/Devanagari normaliser, and the retrieval confidence rating.

Self-contained: it never calls the running API and never touches backend/data. It splits the test documents the way the
app does, embeds passages and questions through Gemini when GEMINI_API_KEY is set (services/retrieval.encode_texts:
batched, cached on disk, so a second run costs nothing), and ranks with services/retrieval.rank_rows (the code
LocalStore.match_chunks and the hosted path call). Without a key only the keyword systems run: that is the search an
agent falls back to when embeddings are unavailable.

Data:
  bench/data/retrieval_questions.json   19 questions about the clinic document (en / hi / Hinglish); a hit is a
                                        passage containing one of the `gold` strings.
  bench/data/doc_qa_questions.json      (when present) hand-written questions about four documents; a hit at k is ALL
                                        `evidence` strings inside the top k (as in doc_qa_eval.py); MRR uses the first
                                        passage with any of them. Its unanswerable questions calibrate the confidence.

Systems (each on the same passages and query vectors):
  bm25              keyword search only (no vectors: what runs with no key)
  dense             vectors only (HYBRID_SEARCH=off)
  rrf               dense + BM25, Reciprocal Rank Fusion (RETRIEVAL_FUSION=rrf)
  weighted (default) dense + BM25, weighted fusion (DEFAULT_FUSION_WEIGHTS), theoretical min-max
  weighted-minmax   the same with observed min-max normalisation
  bm25 weight grid  weighted dense+BM25 with BM25 at 0.1 .. 0.6

    backend/.venv/bin/python -I backend/bench/retrieval_modes_bench.py
    backend/.venv/bin/python -I backend/bench/retrieval_modes_bench.py --latency-only   # synthetic 50-page corpus, no key

Writes bench/results/retrieval_modes-<stamp>.json (git-ignored).
"""
import argparse
import datetime as dt
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List

import numpy as np

BENCH_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BENCH_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:  # pragma: no cover
    pass

from services import chunking, retrieval  # noqa: E402

RESULTS_DIR = BENCH_DIR / "results"
RQ = BENCH_DIR / "data" / "retrieval_questions.json"
DQ = BENCH_DIR / "data" / "doc_qa_questions.json"
LANGS = ("en", "hi", "hinglish")


def ws(s: str) -> str:
    return " ".join(s.split())


def pct(xs: List[float], q: float) -> float:
    return float(np.percentile(xs, q)) if xs else float("nan")


# ----------------------------------------------------------------------------------------------- corpora

def passages_of(text: str, filename: str, chunker: str) -> List[dict]:
    if chunker == "structured":
        ps = chunking.chunk_document(text, chunking.guess_title(text, filename))
        return [{"chunk_text": p["content"], "header": p.get("header") or "", "kind": "passage"} for p in ps]
    if chunker == "small40":
        sys.path.insert(0, str(BENCH_DIR))
        from retrieval_eval import chunk_markdown  # noqa: PLC0415
        return [{"chunk_text": c, "header": "", "kind": "passage"} for c in chunk_markdown(text, 40)]
    return [{"chunk_text": c, "header": "", "kind": "passage"} for c in chunking.chunk_text(text)]


def load_sets(chunker_rq: str) -> List[dict]:
    """[{name, docs: {key: [rows]}, questions: [...], hit: "any"|"all"}]"""
    sets = []
    spec = json.loads(RQ.read_text(encoding="utf-8"))
    docs = {k: passages_of((BACKEND_DIR / rel).read_text(encoding="utf-8"), rel, chunker_rq)
            for k, rel in spec["docs"].items()}
    qs = [{"id": f"rq-{i}", "doc": q["doc"], "lang": q["lang"], "q": q["q"], "gold": q["gold"], "answerable": True}
          for i, q in enumerate(spec["questions"])]
    sets.append({"name": f"retrieval_questions ({chunker_rq})", "docs": docs, "questions": qs, "hit": "any"})
    if DQ.exists():
        sys.path.insert(0, str(BENCH_DIR))
        import doc_qa_eval  # noqa: PLC0415 - read-only use of its document loader
        dspec = doc_qa_eval.load_spec()
        texts = doc_qa_eval.doc_texts(dspec)
        ddocs = {k: passages_of(texts[k], dspec["docs"][k]["source"], "structured") for k in texts}
        dqs = [{"id": q["id"], "doc": q["doc"], "lang": q["lang"], "q": q["q"], "type": q["type"],
                "gold": q.get("evidence") or [], "answerable": q["type"] != "unanswerable"} for q in dspec["questions"]]
        sets.append({"name": "doc_qa_questions (structured)", "docs": ddocs, "questions": dqs, "hit": "all"})
    for s in sets:
        for key, rows in s["docs"].items():
            for i, r in enumerate(rows):
                r["id"] = f"{key}-{i}"
    return sets


def ranks(rows: List[dict], order: List[int], gold: List[str], mode: str):
    """(rank where the hit condition is met, rank of the first passage with any gold string), 1-based or None."""
    golds = [ws(g) for g in gold]
    seen, first_any = set(), None
    for r, i in enumerate(order, start=1):
        text = ws(retrieval.passage_text(rows[i]))
        found = {g for g in golds if g in text}
        if found and first_any is None:
            first_any = r
            if mode == "any":
                return r, r
        seen |= found
        if mode == "all" and len(seen) == len(golds):
            return r, first_any
    return None, first_any


def metrics(results: List[dict]) -> dict:
    n = len(results)
    if not n:
        return {"n": 0}
    out = {"n": n}
    for k in (1, 3):
        out[f"hit@{k}"] = sum(1 for r in results if r["rank"] is not None and r["rank"] <= k) / n
    out["mrr"] = sum(1.0 / r["first"] for r in results if r["first"]) / n
    return out


# ----------------------------------------------------------------------------------------------- encoding

def encode_passages(texts: List[str]):
    """Gemini passage vectors (one batched request per 100 texts), or None when embeddings are unavailable."""
    try:
        return list(retrieval.encode_texts(texts, kind="passage"))
    except retrieval.EmbeddingUnavailable as e:
        print(f"  Gemini embeddings unavailable ({e}): keyword systems only")
        return None


# ----------------------------------------------------------------------------------------------- systems

class Env:
    """Temporarily set environment variables."""

    def __init__(self, **kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def systems(has_dense: bool) -> Dict[str, dict]:
    """name -> {env, dense}: `dense` False scores without any vector."""
    out: Dict[str, dict] = {"bm25": {"env": {"RETRIEVAL_FUSION": "weighted", "RETRIEVAL_WEIGHTS": None}, "dense": False}}
    if not has_dense:
        return out
    out.update({
        "dense": {"env": {"HYBRID_SEARCH": "off"}, "dense": True},
        "rrf": {"env": {"RETRIEVAL_FUSION": "rrf"}, "dense": True},
        "weighted (default)": {"env": {"RETRIEVAL_FUSION": "weighted", "RETRIEVAL_WEIGHTS": None}, "dense": True},
        "weighted-minmax": {"env": {"RETRIEVAL_FUSION": "weighted", "RETRIEVAL_WEIGHTS": None,
                                    "RETRIEVAL_NORM": "minmax"}, "dense": True},
    })
    for w in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
        out[f"grid bm25={w}"] = {"env": {"RETRIEVAL_FUSION": "weighted", "RETRIEVAL_WEIGHTS": f"dense:{1 - w:.2f},bm25:{w}"},
                                 "dense": True}
    return out


# ----------------------------------------------------------------------------------------------- synthetic latency

def synthetic_latency(pages: int = 50, words_per_page: int = 500, runs: int = 60, dim: int = 768) -> dict:
    """rank_rows on a synthetic `pages`-page corpus (120-word passages, random vectors): the fusion and scoring work a live
    turn adds, with vectors and with BM25 alone. No network."""
    rng = random.Random(0)
    vocab = [f"w{i}" for i in range(3000)] + "fee doctor clinic sunday monday time open closed rupee hospital".split()
    n_pass = pages * words_per_page // 120
    rows = [{"id": f"p{i}", "chunk_text": " ".join(rng.choice(vocab) for _ in range(120)), "header": f"Doc > S{i % 12}",
             "kind": "passage"} for i in range(n_pass)]
    nrng = np.random.default_rng(0)
    emb = nrng.normal(size=(n_pass, dim)).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    out = {"passages": n_pass}
    for label, with_vectors in (("dense+bm25", True), ("bm25 only", False)):
        times = []
        for i in range(runs):
            q = emb[i % n_pass] + nrng.normal(size=dim).astype(np.float32) * 0.5
            q /= np.linalg.norm(q)
            sims = (emb @ q).tolist() if with_vectors else [None] * n_pass
            query = f"doctor fee kitni hai sunday {rows[i % n_pass]['chunk_text'].split()[3]}"
            t = time.perf_counter()
            info: dict = {}
            ranked = retrieval.rank_rows(query, rows, sims, info=info)
            retrieval.assess_confidence([{**rows[e["index"]], **e} for e in ranked[:3]], query, info)
            times.append((time.perf_counter() - t) * 1000)
        times = times[3:]
        out[label] = {"ms_median": round(statistics.median(times), 2), "ms_p95": round(pct(times, 95), 2),
                      "signals": info.get("signals")}
    return out


# ----------------------------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--latency-only", action="store_true", help="only the synthetic 50-page latency test (no key needed)")
    ap.add_argument("--rq-chunker", choices=["structured", "small40", "legacy"], default="small40",
                    help="passages for retrieval_questions.json (default small40: the app's structured chunker makes "
                         "only 7-8 passages per template, which every system ranks perfectly at 3)")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()
    out: dict = {"settings": vars(args), "sets": {}}

    print("synthetic 50-page corpus (fusion + scoring work per query):")
    out["synthetic_latency"] = synthetic_latency()
    print("  " + json.dumps(out["synthetic_latency"]))
    if args.latency_only:
        return save(out, args)

    status = retrieval.embedder_status()
    out["embedder"] = status
    print(f"\nembedder: {status['provider']} {status['model']} ({status['dim']} dims), mode {status['mode']}"
          + (f" - {status['reason']}" if status["reason"] else ""))

    sets = load_sets(args.rq_chunker)
    all_q = [q for s in sets for q in s["questions"]]
    qvec: Dict[str, np.ndarray] = {}
    if status["configured"]:
        t_q = []
        try:
            for q in all_q:
                t = time.perf_counter()
                qvec[q["id"]] = retrieval.encode_query(q["q"])
                t_q.append((time.perf_counter() - t) * 1000)
            out["query_encode_ms"] = {"median": round(statistics.median(t_q), 1), "p95": round(pct(t_q, 95), 1)}
            print("query embedding ms (cache hits included): " + json.dumps(out["query_encode_ms"]))
        except retrieval.EmbeddingUnavailable as e:
            print(f"  Gemini embeddings unavailable ({e}): keyword systems only")
            qvec = {}

    for s in sets:
        name = s["name"]
        all_rows = [r for rows in s["docs"].values() for r in rows]
        texts = [chunking.embedding_text(r["header"], r["chunk_text"]) for r in all_rows]
        dense = encode_passages(texts) if qvec else None
        has_dense = dense is not None and bool(qvec)
        for r, d in zip(all_rows, dense or [None] * len(all_rows)):
            r["_vec"] = d
        print(f"\n== {name}: {len(s['questions'])} questions, {len(all_rows)} passages "
              f"({', '.join(f'{k}: {len(v)}' for k, v in s['docs'].items())})")
        answerable = [q for q in s["questions"] if q["answerable"]]
        res_set: dict = {"passages": len(all_rows), "systems": {}}
        for sname, d in systems(has_dense).items():
            results, times = [], []
            for q in answerable:
                rows = s["docs"][q["doc"]]
                sims = [float(np.dot(r["_vec"], qvec[q["id"]])) for r in rows] if d["dense"] else [None] * len(rows)
                t = time.perf_counter()
                with Env(**d["env"]):
                    ranked = retrieval.rank_rows(q["q"], rows, sims)
                order = [e["index"] for e in ranked]
                times.append((time.perf_counter() - t) * 1000)
                rank, first = ranks(rows, order, q["gold"], s["hit"])
                results.append({"id": q["id"], "lang": q["lang"], "rank": rank, "first": first})
            m = metrics(results)
            m["by_lang"] = {lang: metrics([r for r in results if r["lang"] == lang]) for lang in LANGS}
            m["rank_ms_median"] = round(statistics.median(times), 2)
            m["rank_ms_p95"] = round(pct(times, 95), 2)
            m["misses_at_3"] = [r["id"] for r in results if r["rank"] is None or r["rank"] > 3]
            res_set["systems"][sname] = m
            bl = "  ".join(f"{lang} {m['by_lang'][lang].get('hit@1', 0):.0%}/{m['by_lang'][lang].get('hit@3', 0):.0%}"
                           for lang in LANGS if m["by_lang"][lang]["n"])
            print(f"  {sname:26s} hit@1 {m['hit@1']:6.1%}  hit@3 {m['hit@3']:6.1%}  MRR {m['mrr']:.3f}  "
                  f"rank {m['rank_ms_median']:5.2f} ms   [{bl}]")

        # confidence calibration on the default system: answerable (hit / miss at 3) vs unanswerable
        table: Dict[str, Dict[str, int]] = {}
        feats = []
        for q in s["questions"]:
            rows = s["docs"][q["doc"]]
            sims = [float(np.dot(r["_vec"], qvec[q["id"]])) for r in rows] if has_dense else [None] * len(rows)
            info: dict = {}
            ranked = retrieval.rank_rows(q["q"], rows, sims, info=info)
            top = [{**rows[e["index"]], **e} for e in ranked[:3]]
            conf = retrieval.assess_confidence(top, q["q"], info)
            rank, _ = ranks(rows, [e["index"] for e in ranked], q["gold"], s["hit"]) if q["answerable"] else (None, None)
            bucket = "unanswerable" if not q["answerable"] else ("hit@3" if rank and rank <= 3 else "miss@3")
            table.setdefault(bucket, {}).setdefault(conf["level"], 0)
            table[bucket][conf["level"]] += 1
            feats.append({"id": q["id"], "bucket": bucket, "lang": q["lang"], **{k: conf.get(k) for k in
                                                                              ("level", "top", "gap", "dense", "overlap", "agree")}})
        res_set["confidence"] = table
        res_set["confidence_features"] = feats
        print("  confidence level by bucket: " + json.dumps(table))
        for b in ("hit@3", "miss@3", "unanswerable"):
            ds = [f["dense"] for f in feats if f["bucket"] == b and f["dense"] is not None]
            if ds:
                print(f"    {b:12s} top dense: min {min(ds):.3f}  p10 {pct(ds, 10):.3f}  median {statistics.median(ds):.3f}"
                      f"  p90 {pct(ds, 90):.3f}  max {max(ds):.3f}  (n={len(ds)})")
        out["sets"][name] = res_set
    save(out, args)


def save(out: dict, args) -> None:
    if args.no_save:
        return
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"retrieval_modes-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()

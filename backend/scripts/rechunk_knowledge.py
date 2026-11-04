#!/usr/bin/env python
"""Re-chunk stored knowledge documents (all agents, local storage).

    backend/.venv/bin/python backend/scripts/rechunk_knowledge.py --dry-run     # report only, no model, no writes
    backend/.venv/bin/python backend/scripts/rechunk_knowledge.py               # do it (backs the two tables up first)
    backend/.venv/bin/python backend/scripts/rechunk_knowledge.py --data-dir /path/to/copy-of-data --agent-id <id>
    backend/.venv/bin/python backend/scripts/rechunk_knowledge.py --with-structure --questions --source-dir docs/demo   # report
    backend/.venv/bin/python backend/scripts/rechunk_knowledge.py --with-structure --questions --source-dir docs/demo --apply

Three jobs, each switched on by its flag (the first runs when no flag is given):
    (default)          documents that still have the old 500-word passages are split into ~120-word passages
    --with-structure   every document is re-cut along its structure (headings, FAQ pairs, table rows, sentences) and gets
                       passage headers ("Title > Section"); the embedding text is header + content. Add --source-dir
                       <folder> to read the original files (matched by file name; .pdf .txt .md) instead of rebuilding the
                       text from the stored passages, which loses line breaks and so tables. --force redoes documents
                       that already have headers.
    --questions        writes the question index (2-3 questions per passage in English, Hindi and Hinglish, written by the
                       language model: needs EMBEDDINGS_API_KEY; paced, so it takes minutes) for documents that
                       have none; --redo-questions replaces existing ones. Combined with --with-structure the structure
                       is done first.
--with-structure and --questions are DRY RUNS unless --apply is given (no model, no writes; --questions reports passages and
model calls). The default job (old 500-word passages) writes unless --dry-run. A real run backs the two tables up first.

Safe to run again: a document whose passages are already the new size / have headers / have questions is skipped. Stop the app first (or at least do not
upload while it runs): the app and this script write the same JSON files. Documents are re-embedded with Embeddings
(EMBEDDINGS_API_KEY, read from backend/.env). Without a key the new passages are stored without vectors and found by keyword
search until a later search embeds them. Hosted (Supabase) documents are not touched.
Exit code 0 when nothing failed, 1 when a document failed (it keeps its old passages), 2 on a bad data dir.
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv  # noqa: E402

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:  # pragma: no cover
    pass

from services import restructure, retrieval  # noqa: E402
from services.local_store import LocalStore  # noqa: E402
from services.rechunk import rechunk_documents  # noqa: E402

TABLES_TO_BACK_UP = ("documents", "document_chunks")


def backup_tables(data_dir: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(data_dir, f"rechunk-backup-{stamp}")
    os.makedirs(dest, exist_ok=True)
    for t in TABLES_TO_BACK_UP:
        src = os.path.join(data_dir, "local_db", f"{t}.json")
        if os.path.exists(src):
            shutil.copy2(src, dest)
    return dest


def run(data_dir: str, *, agent_id=None, dry_run=False, backup=True, model_name=None, encode=None, out=print,
        with_structure=False, questions=False, source_dir=None, force=False, redo_questions=False, chat=None,
        pacer=None, sleep=None) -> dict:
    """The whole job; `encode` is injectable for tests (default: Embeddings embeddings, or None per call when they are
    unavailable, which stores the passages without vectors).
    `chat`, `pacer` and `sleep` are test hooks for the question index (default: the language model, real pacing)."""
    if not os.path.isdir(os.path.join(data_dir, "local_db")):
        raise SystemExit(f"no local_db folder under {data_dir}")
    model_name = model_name or retrieval.embedding_model_name()
    store = LocalStore(data_dir)
    if encode is None:
        def encode(texts):
            try:
                return retrieval.encode_texts(texts)
            except retrieval.EmbeddingUnavailable as e:
                out(f"  no embeddings ({e}): storing the passages without vectors")
                return None
    if not dry_run and backup:
        out(f"backup: {backup_tables(data_dir)}")

    if not with_structure and not questions:
        def show(d):
            out(f"  {'would split' if d['dry_run'] else 'split'} {d['filename']!r} (agent {d['agent_id']}): "
                f"{d['chunks_before']} -> {d['chunks_after']} passages")

        r = rechunk_documents(store, encode, model_name, agent_id=agent_id, dry_run=dry_run, on_document=show)
        verb = "would be re-chunked" if dry_run else "re-chunked"
        out(f"{r['documents']} document(s) {verb} ({r['chunks_before']} -> {r['chunks_after']} passages), "
            f"{r['skipped']} already fine, {r['failed']} failed")
        for e in r["errors"]:
            out(f"  FAILED {e['filename']!r} ({e['document_id']}): {e['error']}")
        return r

    result: dict = {"failed": 0}
    if with_structure:
        def show_s(d):
            src = "original file" if d["from_source"] else "stored passages"
            out(f"  {'would re-cut' if d['dry_run'] else 're-cut'} {d['filename']!r} (agent {d['agent_id']}) from {src}: "
                f"{d['chunks_before']} -> {d['chunks_after']} passages")

        r = restructure.restructure_documents(store, encode, model_name, agent_id=agent_id, dry_run=dry_run,
                                              source_dir=source_dir, force=force, on_document=show_s)
        verb = "would be re-cut" if dry_run else "re-cut"
        out(f"structure: {r['documents']} document(s) {verb} ({r['chunks_before']} -> {r['chunks_after']} passages, "
            f"{r['from_source']} from original files), {r['skipped']} already structured, {r['failed']} failed")
        for e in r["errors"]:
            out(f"  FAILED {e['filename']!r} ({e['document_id']}): {e['error']}")
        result["structure"] = r
        result["failed"] += r["failed"]
    if questions:
        if dry_run and with_structure:
            out("questions: (dry run) counted on the passages as they are now; re-cutting changes the numbers")

        def show_q(d):
            if d["dry_run"]:
                out(f"  would index {d['filename']!r}: {d['passages']} passages, about {d['calls']} model calls")
            else:
                out(f"  indexed {d['filename']!r}: {d['questions']} questions ({d['status']})")

        kw = {}
        if pacer is not None:
            kw["pacer"] = pacer
        if sleep is not None:
            kw["sleep"] = sleep
        r = restructure.index_questions_for_documents(store, encode, model_name, agent_id=agent_id, dry_run=dry_run,
                                                      redo=redo_questions, chat=chat, on_document=show_q, **kw)
        verb = "would be indexed" if dry_run else "indexed"
        out(f"questions: {r['documents']} document(s) {verb} ({r['passages']} passages, {r['questions']} questions, "
            f"about {r['calls']} model calls), {r['skipped']} skipped, {r['failed']} failed; {r['status']}")
        for e in r["errors"]:
            out(f"  FAILED {e['filename']!r} ({e['document_id']}): {e['error']}")
        result["questions"] = r
        result["failed"] += r["failed"]
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(BACKEND_DIR / "data"), help="folder that holds local_db/ (default backend/data)")
    ap.add_argument("--agent-id", help="only this agent's documents")
    ap.add_argument("--dry-run", action="store_true", help="show what would change; load no model, write nothing")
    ap.add_argument("--no-backup", action="store_true", help="do not copy documents.json and document_chunks.json first")
    ap.add_argument("--with-structure", action="store_true",
                    help="re-cut every document along its headings, FAQ pairs and table rows, with passage headers")
    ap.add_argument("--source-dir", help="with --with-structure: a folder with the original files (matched by file name)")
    ap.add_argument("--force", action="store_true", help="with --with-structure: also redo documents that already have headers")
    ap.add_argument("--questions", action="store_true",
                    help="write the question index (language model, needs EMBEDDINGS_API_KEY) for documents that have none")
    ap.add_argument("--redo-questions", action="store_true", help="with --questions: replace existing question rows")
    ap.add_argument("--apply", action="store_true",
                    help="--with-structure and --questions only report (dry run) unless this is given")
    a = ap.parse_args(argv)
    new_modes = a.with_structure or a.questions
    dry_run = a.dry_run or (new_modes and not a.apply)
    if new_modes and dry_run and not a.dry_run:
        print("dry run: nothing is written (add --apply to do it)")
    try:
        r = run(a.data_dir, agent_id=a.agent_id, dry_run=dry_run, backup=not a.no_backup,
                with_structure=a.with_structure, questions=a.questions, source_dir=a.source_dir, force=a.force,
                redo_questions=a.redo_questions)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    return 1 if r["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())

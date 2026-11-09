# Vakya measurement suite

Scripts that measure the running system over HTTP (and a few that run the app in one process), and a report writer. Speech is
Gnani only (Prisma listens, Timbre speaks, Gnani clones voices) and the language model is hosted, so every script that
speaks or listens spends Gnani credits, on the gateway's `bench` lane (lowest priority; it never starves a live call). Each
script writes raw per-item results to `backend/bench/results/<name>-<timestamp>.json` (git-ignored) and prints a short
summary. Each result file records the git commit, the machine (CPU, RAM, platform), `/api/health`, the settings used and
every raw measurement.

## Before you run

```
./vakya start                      # from the repo root; the API must be on http://127.0.0.1:8020
./vakya doctor                     # GNANI_API_KEY and a embeddings key must be set in backend/.env
export VAKYA_API=http://127.0.0.1:8020   # optional; the default, or pass --api
```

Run everything with `backend/.venv/bin/python` (it needs `requests`, `numpy`, `soundfile`, `psutil`, `jiwer`;
`jiwer` is in `backend/requirements-dev.txt`). Every request sends `storage-mode: local`. Every script has `--quick`,
a tiny version for checking that it runs; quick numbers mean nothing and are flagged as such in the report.

| Script | What it measures | Full run |
| --- | --- | --- |
| `latency.py` | Spoken-turn latency: a spoken question in, the full reply audio ready (`--stream`: time to first audio) | `backend/.venv/bin/python backend/bench/latency.py` |
| `phone_rehearsal.py` | A whole phone call driven through the real phone code: with `--audio` the caller's lines are spoken by Timbre, degraded to 8 kHz mu-law and streamed as Vobiz media frames in real time (recognition, end of speech, speculative start, tools and speech synthesis all run), and each turn is timed from the end of the caller's speech to the first audio and to the first real words (not a filler); without it the lines go in as text. `--cancel-created` cancels the bookings the call made | `backend/.venv/bin/python backend/bench/phone_rehearsal.py --agent <id> --caller +91... --audio --cancel-created` |
| `phone_latency.py` | The phone WebSocket, without a phone call: start to greeting, end of speech to first reply byte | `backend/.venv/bin/python backend/bench/phone_latency.py --agent <id>` |
| `soak.py` | Stability: 45 minutes of mixed requests, memory trend, crashes | `backend/.venv/bin/python backend/bench/soak.py` |
| `leak_hunt.py` | The API process under a mixed load with the Gnani calls and the language model faked (no credits spent): RSS and unique-set-size slope, `--tracemalloc` shows what grew | `backend/.venv/bin/python backend/bench/leak_hunt.py --minutes 12` |
| `stress_report.py` | The acoustic-stress report card: booking completion under 8 kHz, noise and codecs, through Gnani Prisma | `backend/.venv/bin/python backend/bench/stress_report.py --conditions all` |
| `gnani_smoke.py` | One call to each Gnani endpoint (voices, Prisma, Timbre, voice clone), printing the reply shapes | `backend/.venv/bin/python backend/bench/gnani_smoke.py` |
| `suite.py` | The cross-machine run: talk, phone, phone with a cloned voice, retrieval, tests; one JSON and one Markdown table | `backend/.venv/bin/python backend/bench/suite.py --label mac-m4 --tests` |
| `prompt_shape.py` | The shape of the chat messages across turns (prefix reuse); no network | `backend/.venv/bin/python backend/bench/prompt_shape.py` |
| `report.py` | Writes `docs/RESULTS.md` from the newest result of each kind | `backend/.venv/bin/python backend/bench/report.py` |

The retrieval and document-question scripts (`retrieval_eval.py`, `retrieval_modes_bench.py`, `doc_qa_eval.py`) measure
knowledge search on `data/retrieval_questions.json` and `data/doc_qa_questions.json`; see their own docstrings.

## What each one does

**latency.py** creates an agent from the `clinic` template (or uses `--agent-id`; an agent it creates is deleted at the
end unless `--keep-agent`). It renders 30 short questions (15 English, 15 Hindi, alternating) to WAV with Gnani Timbre
(`POST /api/voices/generate`, not timed). After two warm-up turns it posts each one to `/api/conversations/{agent}/turn`, a
new conversation each time (or `--same-conversation`). It records client wall time (request start to JSON received), the
server's `total_ms` and per-stage `timings`, engine, LLM provider, fallback flag, what the server heard and what it
replied. The summary gives p50 and p95 overall and per language, stage p50s and the share within 3 s. This is speech in to
full reply audio ready: nothing is streamed, so it is not time to first audio. Options: `-n`, `--warmup`.

With `--stream` the same questions go to `/api/conversations/{agent}/turn/stream` (server-sent events) and the result
is written to `latency_stream-*.json`. The client clock records when the first `sentence` event arrives (time to
first audio), when the first sentence of the answer itself arrives, and when `done` arrives (the whole reply ready), next
to the server's `first_audio_ms`. Agents say they are an AI at the start of a call, and that disclosure is its own first
sentence, so first audio is usually the disclosure; "first answer" skips it. `--no-disclosure` turns the disclosure
off on the agent the script creates, so the first sentence is the answer. `report.py` writes this as the table
"Time to first audio (streaming)" and keeps the non-streaming table.

**soak.py** loops a weighted mix (Timbre English 30, Timbre Hindi 20, spoken turn 20, transcription 15, and with
`--voice-id` cloned-voice generation 15) with random 0.5 to 4 s pauses for `--minutes` (default 45). Every 60 s it
samples the API process RSS, its child processes and `/api/health`. It counts requests, failures by
status code or exception, restarts (API unreachable and then back, or its process id changed) and latency p50 and
p95 per request type. The target is 0 crashes.

**report.py** takes the newest result file of each kind and writes `docs/RESULTS.md`: the machine, date and commit, one
table per measurement with its method, and reference points. Anything without a result file says "not measured".

## Acoustic-stress report card

**stress_report.py** measures what a bad line does to a booking, not only to the word error. It speaks 24 invented clinic
booking utterances (8 Hindi, 8 Marathi, 8 Hinglish; `data/stress_tasks.json`, every name, day, time, amount and phone
number with accepted spellings in both scripts) with the Gnani Timbre voice that `GNANI_VOICES` lists for the language,
degrades each clip with a fixed seed (`tel8k` 300 to 3400 Hz, `noise_10db`, `noise_5db`, `noise_0db` with babble and street
noise at a measured SNR, `g711` mu-law, `amr` when ffmpeg has `libopencore_amrnb`, `tel8k+noise_5db`), recognises it through
Gnani Prisma (`GNANI_STT_MODEL`, `GNANI_STT_FORMAT`) and scores it with `services/asr_scoring.py`: entity hit rate, task
completion (name AND date AND time heard), folded WER over the accepted reference texts and median latency.

    cd backend
    .venv/bin/python bench/stress_report.py --conditions all [--quick] [--keyterms]

`--quick` runs 6 items per language; `--keyterms` runs each condition without and with a vocabulary pooled over the whole
task set (Prisma's `keyterms` field). It needs `GNANI_API_KEY` and no running API server. A full run is 24 clips spoken
once and 24 x 7 recognitions (double with `--keyterms`). The result file is `results/stress_report-<date>.json` and the
Markdown table and the worst five transcripts per condition are printed. The clean audio is synthetic speech, not real
callers. Tests: `tests/test_stress_report.py`, `tests/test_asr_scoring_entities.py` (nothing is sent to Gnani).

## Caveats

- Numbers depend on the network to Gnani and the hosted models and on the hosted models' load at the time. The health block in each
  result file says which language model answered.
- Close other apps. The machine's load average is recorded, but background work still changes the numbers. Run
  long measurements on an idle machine.
- The API rate-limits per route and per client address (transcribe 10 per minute, voices/generate 10, voices/clone 5). The
  scripts pace themselves under these limits and wait out a 429; the waiting is never counted in a latency number.
- Gnani answers one request at a time per key through the gateway, so two measurement scripts run together slow each other down.
- Memory (RSS) on macOS drops when the system compresses or swaps pages under pressure, so the soak's memory trend
  is only meaningful on a machine with free RAM. Each sample also records system memory use and swap.
- The soak talks to one API process on this machine (found by its listening port) to read memory; with a remote
  `--api` it skips the memory samples.
- Do not edit code or restart the API while a measurement runs: a restart is counted as a crash by the soak.

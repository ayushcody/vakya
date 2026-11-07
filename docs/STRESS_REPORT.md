# Acoustic-stress report card

How well does a voice receptionist hear a booking when the line is bad? `backend/bench/stress_report.py` answers with one table: the same 24 booking utterances (Hindi, Marathi, Hinglish), played clean and through telephone-band, noisy and codec conditions, recognised by Gnani Prisma, and scored on **what a booking needs** (name, day, time) rather than only on word errors.

Ideas it follows: TRACE, "Voice Agents under Acoustic Stress" (arXiv 2609.29452); codec factors for Hindi ASR (2606.09335); Voice of India's accepted-spelling scoring (2604.19151); entity hit rate from the TTS-STT Flywheel paper (2605.03073).

> **First Gnani run: 6 Nov 2026, Prisma v3.0 (codemix), 24 clips per condition** (full table in
> `backend/bench/results/stress_report-2026-11-06.json`, published on `/report-card`).
>
> | Condition | Task completion, plain | with keyterms | Entity hit rate, plain |
> |---|---:|---:|---:|
> | clean | 100% | 100% | 100% |
> | 8 kHz telephone band | 100% | 100% | 100% |
> | G.711 codec | 100% | 100% | 100% |
> | noise 10 dB | 100% | 100% | 99% |
> | noise 5 dB | 100% | 100% | 97% |
> | noise 0 dB | 96% | 100% | 96% |
> | 8 kHz + noise 5 dB | 95% (n=21) | 100% | 99% |
>
> Median recognition time 0.8 s per clip in every condition. Three clips of the plain 8 kHz + noise run were refused by
> Gnani's rate limit and are left out (n=21), not counted as passes. AMR was skipped (no encoder in this ffmpeg).
> Gnani's edge firewall blocked one Marathi clip (`mr-06`) outright; the client now treats that as one blocked request
> and sends it again at 98% volume, which goes through (`backend/services/gnani_client.py`).
>
> Read it with two cautions: the audio is **synthetic speech** (Gnani Timbre), not real callers, and the degradations are
> simulated, not recorded. Each condition is 24 clips, so one clip is about 4 points: differences of one or two clips are
> noise. The shape is the finding: the telephone band and the codec cost nothing, and only 0 dB noise (noise as loud as
> the speech) starts to lose fields, which the clinic's own words (keyterms) win back.

## Run it

```
./vakya stress --conditions all --provider gnani --keyterms
```

or, from `backend/`, `.venv/bin/python bench/stress_report.py --conditions all --provider gnani --keyterms [--quick]`.

- Needs `GNANI_API_KEY` in `backend/.env`. The clean audio is rendered with Gnani Timbre voices, so a run spends credits for 24 renderings and 24 recognitions per condition and per keyterm setting. The run goes through the Gnani gateway as benchmark work, which holds back from the credit reserve kept for live calls (`docs/GNANI.md`).
- `--keyterms` runs every condition twice, without and with a vocabulary pooled over the whole task set (every name, weekday and amount spelling, no phone numbers or times). It is what an agent's documents would give; it is not each clip's own answer. Prisma gets it as `keyterms`.
- `--quick` is 6 items per language. The result file is `backend/bench/results/stress_report-<date>.json` (every hypothesis, every score, the SNR applied per clip, the skipped conditions); the audio is under `backend/bench/results/stress_audio/<condition>/` (git-ignored).

## The task set

`backend/bench/data/stress_tasks.json`: 24 invented utterances for a clinic receptionist, 8 Hindi (Devanagari), 8 Marathi (Devanagari) and 8 Hinglish (Roman and Devanagari mixed). Each has a person's name, a weekday or date and a time; nine also have an amount in hazaar or lakh and six a ten-digit phone number (spoken digit by digit). No real people or numbers. Every entity carries accepted spellings in both scripts (`["प्रिया", "priya", "priyaa"]`). The score uses the accepted spellings, so a correct word in either script counts.

## Conditions

| Condition | What is done | SNR applied |
|---|---|---|
| `clean` | The rendered audio, resampled to 16 kHz | none |
| `tel8k` | Butterworth band-pass 300 to 3400 Hz, carried at 8 kHz, back to 16 kHz | none |
| `noise_10db`, `noise_5db`, `noise_0db` | clean plus noise: equal power of babble (4 other task utterances, looped, random shifts) and coloured street noise (pink hiss plus a low rumble), fixed seed | measured 10.0, 5.0, 0.0 dB (whole-clip power ratio, per clip) |
| `g711` | resample to 8 kHz, mu-law encode and decode, back to 16 kHz | none |
| `amr` | ffmpeg `libopencore_amrnb`, 8 kHz, 12.2 kbit/s | skipped when the local ffmpeg has no such encoder; the report says so |
| `tel8k+noise_5db` | noise at 5 dB first, then the telephone band | 5.0 dB at the source; about 7 dB after the band-pass, because the noise has more energy outside the band than the speech |

## How a clip is scored

- **Task completion:** the name, the day and the time were all found, in any accepted spelling. This is the headline.
- **Entity hit rate:** the share of entities found (name, date, time, and amount and phone where the clip has them), also per kind, so the table shows *which part of the booking was lost*.
- **Folded word error:** word error with spelling folded across scripts, so it is a lower bound on the real error.
- **Median recognition time** of one clip, on the machine that ran it.

## The results file

`backend/bench/results/stress_report-<date>.json` is the full record. The web pages read a smaller file, `frontend/src/components/report/reportCardResults.json`, which holds `{ "run": null }` until the run exists. To publish the run, replace it with the following (without the comments):

```json
{
  "run": {
    "date": "2026-11-xx",
    "model": "prisma-v3.0",
    "tasks": 24,
    "commit": "<git commit>",
    "rows": [
      {
        "condition": "clean",          // clean, tel8k, g711, noise_10db, noise_5db, noise_0db, tel8k+noise_5db
        "language": "all",             // all, hi, mr, hi-en (the pages read "all")
        "keyterms": false,             // one row for each setting
        "n": 24,
        "taskCompletion": 0,           // percent
        "wer": 0.0,
        "latencyS": 0.0,
        "hitByKind": { "name": 0, "date": 0, "time": 0, "amount": 0, "phone": 0 }
      }
    ]
  }
}
```

Both `/report-card` and the desk's Report card page, and the landing teaser, read that one file; no number is typed anywhere else. Rows that are missing show "pending" and a field with no clips shows "n/a".

## What to look for when it runs

- Whether the telephone band and G.711 cost little, and how far noise pulls a booking down.
- Which field goes first: dates, names, times or ten-digit phone numbers. A phone number heard whole is the case the read-back exists for: the front desk reads every number back and commits only on a spoken yes, whatever the table says.
- What keyterms buy, compared with the plain run.

## Limits to state alongside any result

- One synthetic speaker per language, no hesitations, no real room.
- Marathi is spoken by whatever Timbre voice reads it; nobody who speaks Marathi has judged those clips.
- Simulated noise and codecs are not recorded ones; AMR may be skipped.
- 24 clips per condition: read the shape, not single cells. A noisier condition scoring above a cleaner one is sampling noise at this size.

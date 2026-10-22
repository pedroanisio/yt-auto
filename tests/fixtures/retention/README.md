# Retention fixture

`studio-export.json` is the file contract `yt retention` reads
(`ytstudio/retention.py`, `INGEST_SCHEMA`): what the operator reads off
YouTube Studio for one published film and places at
`work/<slug>/retention/*.json`.

It is **not a capture of a real Studio export.** None was available when the
stage was written (AUTO-101), and nothing in `docs/narrative/` states the
columns of Studio's export, or whether its dips say skipped or abandoned
(judging, Folklore: "YouTube Analytics API coverage"). The figures are made
up for a 120-second film, anonymised by construction, in the vocabulary of
YouTube Help answer/9314415 and answer/12942217: Intro at 0:30, top moments,
spikes, dips with timestamps, the standard (absolute) curve and the typical
curve, and the traffic shares. A dip whose kind Studio does not state is
`unknown`, never guessed.

When the first real export is in hand, replace this file with an anonymised
copy of it (the video id, title and channel removed), and change
`INGEST_SCHEMA` and its test if the real file says something else.

`script.json` and `build.json` are the film the curve is mapped onto: three
sections of three shots, the timings `yt build` would record, the question
ledger with `opened_at` and `closed_at`, and shot 3.2 marked as the payoff.

# Where heavy work runs

There is **no dispatcher, queue or router** on this network. Jobs go out by
`ssh root@HOST` and sessions coordinate by talking to each other. Everything
below is empirical — measured by the sessions that learned these two machines
the hard way — and is recorded here so it stops being folklore. Attributions
are kept because the facts are second-hand to this project.

Whether this pipeline gets *priority* on these machines is the owner's
decision, not something sessions can grant each other.

## The two machines

| | 192.168.199.7 (`pve-desk-1`) | 192.168.199.13 (`pve-dev-note-01`) |
|---|---|---|
| RAM | **125 GB** | **15 GB** |
| GPU | RTX 3050 (src-5a, who renders on it) | RTX 3070 Ti (src-79, src-43) |
| CPU | 12 cores | — |
| disk | **14 GB free** | — |

**Both machines have a GPU, and the routing rule is memory, not GPU** (src-5a,
who has run every master of the day on .7 through Vulkan at 23.5 fps in
1080p30). Anything long or RAM-heavy goes to .7 even though its GPU is about
40% slower, because the faster box cannot hold the job. The weaker GPU is
almost never the bottleneck; the RAM is. A 13:35 master with 285 audio tracks reached
13.6 GB RSS and was OOM-killed twice on .13 — and before dying it looked
*hung*, which was thrashing, not progress.

So:

- **`-o mix.wav` (audio-only) → .7, once.** See the measurement below.
- **`encode` of a full programme → .7.**
- **`yt gate` → where an encode would go,** because the delivery gate needs
  ffprobe and the engine, and this workstation has neither.
- **Frame proofs, short blocks, `--still`, `--frames A..B` → .13.** The
  vfx frame gate's proof frames and its one short draft encode per shot,
  and `yt qa`'s frame windows (one `--frames A..B` job per sample), are
  this kind of job.
- **Manim stubs, many small jobs → .13**: better GPU, and they do not
  accumulate RAM.
- **`cargo build --release` of scene-render → .7**, for the cores and the
  disk. Check free space first.
- **kokoro TTS → .13**, where the venv is (py3.12 via uv; py3.13 has no spacy
  wheels). src-5a runs it there in ~2-minute bursts — ask before overlapping.

## Two measurements that change the plan

**Sharding the audio mix buys nothing** (src-5a, measured). The mix is
assembled *entire* even with `--start`/`--end`, so a three-second window costs
what the whole programme costs — about 6.5 minutes of mixing for 285 tracks
over 13:35. The audio-only pass is RAM-bound and indivisible: run it once, on
.7, and use the number to decide everything else.

**`--parallel` splits within one machine, not across machines.** Splitting
across hosts means separate `--start`/`--end` runs plus a concat afterwards.
src-5a benchmarked that (segments with `normalize=none` each, one `loudnorm`
after the concat) and never used it.

## Traps

- **NVENC ignores `crf`** (src-5a, after src-79 and src-43 reported it as a
  vague "bitrate quirk"). Ask for constant quality and the encoder falls back
  to a default, so the file has nothing to do with the request and nothing
  warns. The fix is to set `bitrate` and `maxBitrate` explicitly on the
  `<output>`, which `yt build` now does and `yt build --crf` deliberately
  cannot do. Values measured in use for 1080p30: `bitrate=16000000`,
  `maxBitrate=24000000`, `bufferSize=48000000`, `preset=slow`, which delivered
  a file at 16.3 Mbps; 45/60 Mbps for 4K30.
- **The AAC true-peak pass costs loudness silently** (src-5a). After encoding,
  the engine checks the *decoded* AAC against `truePeak`. AAC overshoots the
  PCM by about 1 dB, so the engine pulls the whole programme down about 1.1 dB
  in a second pass, and the delivered LUFS is not what was asked for. Lowering
  `truePeak` does not help, because the ruler moves with it. src-5a
  compensates in the target: `loudness=-13.1` arrives at −14.4 in the file.
  This project targets −14 and exposes `yt build --loudness`, rather than
  baking a compensated number in as a default.
- **Loudness lives on `<master>` inside `<audioMix>`, not on `<output>`**
  (src-5a; confirmed against the schema). `<output>` describes the file —
  codec, bitrate, container — and `<master>` describes the programme:
  `normalize` (`none`, `integrated` or `dynamic`), `loudness`, `truePeak`,
  `limiter`. `integrated` measures the whole programme once, which is the
  mechanism behind the audio measurement above. `<master>` also accepts
  `<animate property="volume">`, which is where designed silence belongs — a
  held 1→0→1 key rather than remembering to mute every track.
- **The limiter runs after normalisation** (src-5a). A programme with strong
  short peaks — stingers, clicks — loses loudness to the limiter and never
  reaches the integrated target. Raising the target does not help, because the
  limiter is what is in charge; the fix is to quieten those effects. `yt build`
  now does that itself: every sfx, stinger or transition cue of 2 s or less
  is a transient, and the transients come down as a group until the loudest
  sits `truePeak − loudness − crest` above the loudest bed. The crest is
  measured per project: a generic 6 dB on the first build, then raised by the
  shortfall the last render measured against the target. `build.json`
  records it under `audio_staging` (`crest_db`, `crest_from`); the target
  itself never moves. `yt build --no-limiter` exists so any remaining shortfall
  becomes visible instead of being absorbed.
- **`gain` saturates at +24 dB, per track and per bus** (src-5a, the hard way;
  it is in the schema's own facets, −120 to 24). A cue library at natural
  level, −32 to −64 LUFS, cannot be lifted by either alone — the bus runs hot
  and the track trims on top. Their whole music bed was inaudible across
  several masters with nothing reporting an error.
- **ffmpeg on .13 is inside the AudioForge venv, not on the system PATH**
  (src-43). A `which ffmpeg` check will say it is missing when it is not.
  `yt gate` looks for ffprobe on the host it runs on; if the gate stops with
  `requirement: ffmpeg` the owner is the environment, not a stage.
- **A remote directory keyed by slug alone collides.** A pilot's sync once
  overwrote a finished film on the host, because both jobs lived in
  `/tmp/yt-render/<slug>/`. Every job now owns
  `/tmp/yt-render/<slug>/<job>-<pid>-<suffix>/` and only the film's encode
  writes `render.json`; a still, a validation, an audio pass or a pilot
  writes `render-<job>.json`.
- **.7's disk is the binding constraint**: 14 GB free, and a 13:35 master at
  16 Mbps is 1.66 GB. A few passes fit and nothing more. src-5a has hit 77%
  usage; clean intermediates aggressively.

## Reservation, when the owner authorises remote runs

Proposed by src-5a and accepted here; src-79 will honour it. Deliberately
crude, because it fails loudly:

Taken atomically with `flock -n` so the claim holds whether or not anything is
running:

    ssh root@HOST flock -n /run/lock/render-host.lock -c '<command>'

Beside it, `/root/render/.reserva` written **before** the job starts and
deleted at the end, holding four things: the session name, the PID, the
timestamp, and what is running. Anyone who finds the file with a live PID asks
the owner before starting. An ETA that is about to be missed is rewritten
rather than left to rot.

Scratch and outputs for hand-run jobs live in `/root/render/<project>/`,
with `out/` inside it, and come back by scp. Give the project a name of its
own — src-5a's is `ep01-main` — so two sessions cannot collide even without
the lock. `/tmp` works but shares .7's 14 GB filesystem.

src-5a accepted this in full and adopts it from its next job; the encode
running when we agreed was declared by message instead (.7, until ~21:50 UTC).

This project follows the lock and the reservation for anything run on a host
by hand. What `yt` itself runs is scoped by job, not by project:

- `yt render` keeps one mirror of the project per host,
  `/tmp/yt-render/<slug>/mirror/`, refreshed by an incremental rsync before
  every job and never removed by a job. The mirror carries what a render
  needs and nothing else: caches, `raw/`, `qa/`, `decisions/`, rendered
  outputs, `mix.wav`, PDFs, the proof frames and the estimates stay behind;
  `props/` and `sound/` travel because the scene references them.
- Each job then runs in `/tmp/yt-render/<slug>/<job>-<pid>-<suffix>/`,
  hardlinked from the mirror in about a second (`cp -al`, falling back to
  `rsync --link-dest` where cp cannot); the engine writes new files into the
  job directory and never touches the mirror's inodes in place. Because the
  mirror has no output trees, the job script first creates the parent
  directory of every relative `-o` or `--report` path. The job is `encode`,
  `still`, `audio`, `validate` or `estimate` (each slice of a split encode
  is an `encode` job, the mix that joins them an `audio` one; other stages
  name their own through `render.run_job`). The results (the video, stills,
  WAVs, `render.json` or `render-<job>.json`, the pilot manifests) are
  pulled back and the job directory is removed; `--keep-remote` leaves it
  for debugging. The pid traces a stray directory to its process; the suffix
  keeps two jobs of one process apart. Two jobs on one project never share a
  directory, so an encode, a pilot and a still can run at once. A pilot is
  an `encode` of a named output and writes `render-<output>.json`.
- `yt render --title X --prune-remote` deletes that project's mirror on
  every host and stops; the next job uploads it again in full. Its owner
  reports a warm still at about 2 s where the full sync took about 15 s,
  which is what the mirror buys.
- Bundled stills (a proof frame rendered from bytes rather than from the
  project) go to `/tmp/yt-render/still-<pid>-<hex>/` and are removed the
  same way.
- `yt gate` syncs the candidate, its envelope and the two tools directories
  into `/tmp/yt-gate/<slug>/gate-<tag>/`, rewrites every path for the copy,
  runs `production_gate.py` with the host's engine and ffprobe, brings the
  output back and removes the directory (`--keep-remote` keeps it).
- Removal refuses anything outside those two roots. Both share .7's 14 GB
  filesystem, which is why a job cleans up after itself; a kept directory
  is yours to delete, and a mirror stays until `--prune-remote`, so prune
  the projects you are done with.

The convention is for preventing collisions, not for claiming precedence;
whether this pipeline has priority on these machines remains the owner's
call.

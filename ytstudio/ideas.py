#!/usr/bin/env python3
"""Generate YouTube video ideas (title, genre, description) with Claude.

Built on claude_engine: schema-enforced output, generated in deduplicated
batches -- in parallel -- so a long list stays varied instead of drifting into
fifty variants of one idea.

    yt ideas                                   # 50 ideas, mixed genres
    yt ideas --niche "home automation"         # ideas for one channel
    yt ideas --inspo "Cool Worlds,Asianometry" # borrow a channel's voice
    yt ideas --rich --rank --count 20          # hooks, keywords, ranked
    yt ideas --dedupe-from ideas.json          # keep a backlog fresh
    yt ideas --store backlog.json --count 30   # grow one shared backlog
    yt ideas --json ideas.json --csv ideas.csv --md ideas.md

`--inspo` accepts any channel name; the ones in CHANNEL_STYLES get a hand-written
brief, anything else is passed through for the model to characterise itself.

Every run also feeds a store file (`<work>/ideas.store.json` by default, see
`--store` and `--no-store`): titles already in it are excluded up front, and
every unique idea the run produced -- including any generated past `--count` --
is appended. So repeated runs accumulate one growing, duplicate-free list.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, fields
from datetime import date
from pathlib import Path

from claude_engine import ClaudeEngine, ClaudeError, EngineConfig, Usage

from . import workspace
from .core import Idea, _key, _merge_usage, compact, wrap
from .core import write_json as dump_json

#: Each batch is nudged towards a slice of this pool, which is what keeps a
#: fifty-item list from collapsing into one format repeated fifty times.
GENRE_POOL = (
    "tutorial", "explainer", "video essay", "documentary", "review",
    "challenge", "vlog", "listicle", "case study", "interview",
    "reaction", "build log", "news breakdown", "comparison", "shorts",
)

#: Hand-written voice briefs. Keys are matched loosely (case and punctuation
#: are ignored), so "3blue1brown", "3Blue1Brown" and "3 blue 1 brown" all hit.
CHANNEL_STYLES = {
    "pbsspacetime": "graduate-level physics and cosmology, unafraid of real "
                    "math, builds on a running series of prior episodes",
    "3blue1brown": "mathematics through animated visual intuition; a single "
                   "'aha' geometric reframing carries the whole video",
    "veritasium": "a counter-intuitive claim, tested on camera, with the "
                  "viewer's wrong assumption named out loud first",
    "mentourpilot": "aviation incidents reconstructed minute by minute by a "
                    "working pilot; procedure and human factors, never blame",
    "realengineering": "one engineering problem, its constraints quantified, "
                       "narrated calmly over precise 3D animation",
    "megaprojects": "the biggest thing ever built in some category: why it was "
                    "attempted, what it cost, whether it was worth it",
    "astrum": "a single solar-system object or mission, told through real "
              "imagery and what the data actually showed",
    "historyoftheuniverse": "cinematic deep-time narration across billions of "
                            "years, atmospheric and unhurried",
    "coolworlds": "an astronomer's slow, literary essay on one deep question, "
                  "rigorous about what we cannot know",
    "asianometry": "dense industrial and semiconductor history, dry delivery, "
                   "research-paper density with no visual flourish",
    "fireship": "100 seconds of dense, fast, joke-punctuated developer "
                "explanation, zero filler",
    "bytebytego": "system design and backend architecture in clean animated "
                  "diagrams, interview-oriented",
    "theprimetimeagen": "opinionated live developer commentary on code, tools "
                        "and industry drama, reacting as it reads",
    "quantasciencechannel": "a frontier research result explained through the "
                            "researchers' own framing, journalistically careful",
    "welchlabs": "the history of an idea in math or physics, narrated through "
                 "the original struggle to find it",
    "scienceclicen": "general relativity and field theory made geometric "
                     "through flawless custom animation",
    "dialectphilosophy": "the conceptual foundations of physics, patiently "
                         "dismantling what everyone thinks the theory says",
    "vox": "a news-pegged explainer with a strong thesis, archival footage and "
           "annotated graphics",
    "wendoverproductions": "logistics and infrastructure, following the money "
                           "and the routing decisions nobody notices",
    "lemmino": "immaculate long-form investigation, minimal narration, "
               "evidence laid out so the viewer draws the conclusion",
    "practicalengineering": "civil infrastructure explained with a backyard "
                            "scale model demonstrating the failure mode",
    "technologyconnections": "obsessive consumer-technology archaeology; why "
                             "the boring design choice was actually correct",
    "posymusic": "quiet, wordless craft: tiny details of sound and image "
                 "noticed and shown, no narration",
    "tantacrul": "music software and design criticism with theatrical comic "
                 "timing and a genuine expert's eye",
    "defunctland": "exhaustively researched institutional history of something "
                   "nobody thought deserved it",
    "kurzgesagt": "a big question answered with cheerful motion graphics, "
                  "numbers made tangible, ending on a reframe",
}

SYSTEM = (
    "You are a YouTube content strategist. You produce specific, searchable "
    "video concepts -- never generic filler. Titles are click-worthy but "
    "honest, at most 80 characters. Descriptions are one or two sentences "
    "stating the hook and what the viewer walks away with."
)


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #

def idea_schema(rich: bool) -> dict:
    """Output shape for a batch. `rich` adds production-planning fields."""
    props = {
        "title": {"type": "string"},
        "genre": {"type": "string"},
        "description": {"type": "string"},
    }
    if rich:
        props |= {
            "hook": {"type": "string"},
            "length": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}},
        }
    return {
        "type": "object",
        "properties": {
            "ideas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": props,
                    "required": list(props),
                },
            }
        },
        "required": ["ideas"],
    }


RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "ranked": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "score": {"type": "number"},
                    "why": {"type": "string"},
                },
                "required": ["n", "score", "why"],
            },
        }
    },
    "required": ["ranked"],
}


# --------------------------------------------------------------------------- #
# prompting
# --------------------------------------------------------------------------- #

def style_brief(inspo: list[str]) -> str:
    """Turn channel names into a voice brief, curated where we have one."""
    known = [f"- {n}: {CHANNEL_STYLES[_key(n)]}" for n in inspo if _key(n) in CHANNEL_STYLES]
    unknown = [n for n in inspo if _key(n) not in CHANNEL_STYLES]
    parts = []
    if known:
        parts.append("Match the sensibility of these channels:\n" + "\n".join(known))
    if unknown:
        parts.append(
            "Also match the sensibility of these channels -- recall what their "
            f"videos are actually like and write to it: {', '.join(unknown)}."
        )
    if len(inspo) > 1:
        parts.append("Blend them rather than alternating between them.")
    return "\n\n".join(parts)


def build_prompt(n: int, *, niche: str | None, genres: tuple[str, ...],
                 used: list[str], inspo: list[str], rich: bool) -> str:
    topic = f"the niche: {niche}" if niche else "a broad general-interest channel"
    parts = [
        f"Generate {n} YouTube video ideas for {topic}.",
        f"Use these genres, spread roughly evenly: {', '.join(genres)}.",
    ]
    if inspo:
        parts.append(style_brief(inspo))
    fields_wanted = ["a title", "a genre (one of the ones listed)",
                     "a one-or-two-sentence description"]
    if rich:
        fields_wanted += [
            "a hook (the first 10 seconds, verbatim)",
            "a length (e.g. '8-12 min' or '45 sec')",
            "3-6 search keywords people would actually type",
        ]
    parts.append("Each idea needs " + ", ".join(fields_wanted) + ".")
    if used:
        listed = "\n".join(f"- {t}" for t in used[-60:])
        parts.append(
            "These titles already exist -- do not repeat them, and avoid the "
            f"same angle:\n{listed}"
        )
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #

def _plan(count: int, batch: int, genres: tuple[str, ...],
          per_batch: int = 3) -> list[tuple[int, tuple[str, ...]]]:
    """Split `count` into batches, each handed its own slice of the genre pool."""
    plan, cursor, left = [], 0, count
    while left > 0:
        want = min(batch, left)
        slice_ = tuple(genres[(cursor + i) % len(genres)] for i in range(per_batch))
        plan.append((want, slice_))
        cursor += per_batch
        left -= want
    return plan


class Generator:
    """Collects unique ideas, running batches concurrently.

    Ideas beyond the requested count are kept in `spare` rather than thrown
    away -- they were already paid for, and the store has room for them.
    """

    def __init__(self, config: EngineConfig, *, niche: str | None = None,
                 inspo: list[str] | None = None, rich: bool = False,
                 genres: tuple[str, ...] = GENRE_POOL, jobs: int = 3,
                 exclude: list[str] | None = None, verbose: bool = True) -> None:
        self.config = config
        self.niche = niche
        self.inspo = inspo or []
        self.rich = rich
        self.genres = genres
        self.jobs = max(1, jobs)
        self.verbose = verbose
        self.usage = Usage()
        self.ideas: list[Idea] = []
        self.spare: list[Idea] = []
        self.seen: set[str] = {_key(t) for t in (exclude or [])}
        self.excluded = list(exclude or [])

    # -- internals ---------------------------------------------------------

    def _engine(self) -> ClaudeEngine:
        """A fresh engine per worker: threads must not share usage counters."""
        return ClaudeEngine(EngineConfig(**asdict(self.config)))

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def _batch(self, want: int, genres: tuple[str, ...], used: list[str]) -> list[Idea]:
        engine = self._engine()
        try:
            data = engine.ask_json(
                build_prompt(want, niche=self.niche, genres=genres,
                             used=used, inspo=self.inspo, rich=self.rich),
                idea_schema(self.rich),
                system=SYSTEM,
            )
        finally:
            _merge_usage(self.usage, engine.usage)
        return [Idea.from_raw(raw) for raw in data.get("ideas", [])]

    def _absorb(self, ideas: list[Idea], limit: int) -> int:
        fresh = 0
        for idea in ideas:
            if not idea.title or _key(idea.title) in self.seen:
                continue
            self.seen.add(_key(idea.title))
            (self.ideas if len(self.ideas) < limit else self.spare).append(idea)
            fresh += 1
        return fresh

    @property
    def all_ideas(self) -> list[Idea]:
        """Every unique idea generated, overflow included."""
        return self.ideas + self.spare

    # -- public ------------------------------------------------------------

    def run(self, count: int, batch: int) -> list[Idea]:
        plan = _plan(count, batch, self.genres)
        self._say(f"generating {count} in {len(plan)} batch(es), {self.jobs} at a time")

        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            # Parallel batches cannot see each other's titles, so they are kept
            # apart by genre slice instead; the top-up pass below fixes overlap.
            futures = [pool.submit(self._batch, want, genres, self.excluded)
                       for want, genres in plan]
            for future in futures:
                try:
                    self._absorb(future.result(), count)
                except ClaudeError as exc:
                    self._say(f"  batch failed: {exc}")

        cursor, stalls = 0, 0
        while len(self.ideas) < count and stalls < 3:
            want = count - len(self.ideas)
            genres = tuple(self.genres[(cursor + i) % len(self.genres)] for i in range(3))
            cursor += 3
            self._say(f"  top-up: {want} more ({', '.join(genres)})")
            try:
                got = self._batch(want, genres,
                                  self.excluded + [i.title for i in self.ideas])
            except ClaudeError as exc:
                self._say(f"  top-up failed: {exc}")
                break
            stalls = 0 if self._absorb(got, count) else stalls + 1

        return self.ideas


def rank(engine: ClaudeEngine, ideas: list[Idea], *, niche: str | None = None
         ) -> list[Idea]:
    """Score every idea 1-10 on effort-to-audience value, best first."""
    listing = "\n".join(f"{n}. [{i.genre}] {i.title} -- {i.description}"
                        for n, i in enumerate(ideas, 1))
    context = f" for a channel about {niche}" if niche else ""
    data = engine.ask_json(
        f"Score each of these {len(ideas)} video ideas{context} from 1-10 on "
        "effort-to-value: likely watch-time and search demand against how hard "
        "it is to make well. Be discriminating -- use the full range, and say "
        f"in one short clause why.\n\n{listing}",
        RANK_SCHEMA,
        system=SYSTEM,
    )
    for row in data.get("ranked", []):
        index = int(row.get("n", 0)) - 1
        if 0 <= index < len(ideas):
            ideas[index].score = row.get("score")
            ideas[index].why = str(row.get("why", "")).strip()
    return sorted(ideas, key=lambda i: (i.score is None, -(i.score or 0)))


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def print_ideas(ideas: list[Idea]) -> None:
    for n, idea in enumerate(ideas, 1):
        score = f"  ({idea.score:g}/10)" if idea.score is not None else ""
        print(f"\n{n:>3}. {idea.title}{score}")
        print(f"     [{idea.genre}]")
        print(wrap(idea.description, "     "))
        if idea.hook:
            print(wrap(f'hook: "{idea.hook}"', "     "))
        if idea.length or idea.keywords:
            bits = [b for b in (idea.length, ", ".join(idea.keywords)) if b]
            print(wrap(" | ".join(bits), "     "))
        if idea.why:
            print(wrap(f"why: {idea.why}", "     "))


def write_json(path: str, ideas: list[Idea]) -> None:
    dump_json(path, [compact(i) for i in ideas])


def write_csv(path: str, ideas: list[Idea]) -> None:
    cols = [f.name for f in fields(Idea)
            if any(getattr(i, f.name) not in ("", [], None) for i in ideas)]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for idea in ideas:
            row = asdict(idea)
            row["keywords"] = ", ".join(idea.keywords)
            writer.writerow({c: row[c] for c in cols})


def write_md(path: str, ideas: list[Idea], *, niche: str | None,
             inspo: list[str]) -> None:
    lines = [f"# Video ideas{f' — {niche}' if niche else ''}", ""]
    if inspo:
        lines += [f"*In the spirit of: {', '.join(inspo)}*", ""]
    for n, idea in enumerate(ideas, 1):
        score = f" — **{idea.score:g}/10**" if idea.score is not None else ""
        lines.append(f"## {n}. {idea.title}{score}")
        lines.append(f"`{idea.genre}`" + (f" · {idea.length}" if idea.length else ""))
        lines += ["", idea.description]
        if idea.hook:
            lines += ["", f"> {idea.hook}"]
        if idea.keywords:
            lines += ["", "Keywords: " + ", ".join(f"`{k}`" for k in idea.keywords)]
        if idea.why:
            lines += ["", f"*{idea.why}*"]
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def read_store(path: str) -> list[dict] | None:
    """Rows already in the store; None if unreadable, so it is never clobbered."""
    file = Path(path)
    if not file.exists():
        return []
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: cannot read store {path} ({exc}); leaving it alone",
              file=sys.stderr)
        return None
    rows = data.get("ideas", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        print(f"warning: store {path} is not a list of ideas; leaving it alone",
              file=sys.stderr)
        return None
    return [r for r in rows if isinstance(r, dict) and r.get("title")]


def update_store(path: str, rows: list[dict], ideas: list[Idea], *,
                 niche: str | None = None, inspo: list[str] | None = None) -> int:
    """Append the ideas whose titles are not in `rows` yet. Returns how many."""
    seen = {_key(r["title"]) for r in rows}
    stamp = date.today().isoformat()
    added: list[dict] = []
    for idea in ideas:
        if not idea.title or _key(idea.title) in seen:
            continue
        seen.add(_key(idea.title))
        row = compact(idea) | {"added": stamp}
        if niche:
            row["niche"] = niche
        if inspo:
            row["inspo"] = list(inspo)
        added.append(row)
    if not added:
        return 0
    file = Path(path)
    if file.parent != Path(""):
        file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text(json.dumps(rows + added, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(file)  # one write or none: a crash cannot truncate the backlog
    return len(added)


def load_titles(paths: list[str]) -> list[str]:
    """Titles from earlier runs (JSON array, {"ideas": [...]}, or one per line)."""
    titles: list[str] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            print(f"warning: {raw} not found, ignoring", file=sys.stderr)
            continue
        text = path.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            titles += [line.strip() for line in text.splitlines() if line.strip()]
            continue
        rows = data.get("ideas", []) if isinstance(data, dict) else data
        titles += [r["title"] for r in rows
                   if isinstance(r, dict) and r.get("title")]
    return titles


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "ideas", parents=list(parents), help="generate video ideas",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--count", type=int, default=50, help="how many ideas (default 50)")
    p.add_argument("--batch", type=int, default=10, help="ideas per call (default 10)")
    p.add_argument("--jobs", type=int, default=3, help="batches in parallel (default 3)")
    p.add_argument("--niche", help="channel topic; omit for general interest")
    p.add_argument("--inspo", default="",
                   help="comma-separated channel names to borrow voice from")
    p.add_argument("--genres", default="",
                   help=f"comma-separated genre pool (default: {len(GENRE_POOL)} mixed)")
    p.add_argument("--rich", action="store_true",
                   help="also generate hook, length and search keywords")
    p.add_argument("--rank", action="store_true",
                   help="score ideas 1-10 on effort-to-value and sort best first")
    p.add_argument("--dedupe-from", action="append", default=[], metavar="FILE",
                   help="exclude titles from an earlier run (repeatable)")
    p.add_argument("--store", metavar="FILE",
                   help=f"the running store of every unique idea "
                        f"(default <work>/{workspace.STORE_FILE})")
    p.add_argument("--no-store", action="store_true",
                   help="neither read nor update the store")
    p.add_argument("--json", dest="json_out", metavar="FILE",
                   help=f"where to write the ideas (default <work>/{workspace.IDEAS_FILE})")
    p.add_argument("--csv", dest="csv_out", help="also write CSV here")
    p.add_argument("--md", dest="md_out", help="also write Markdown here")
    p.add_argument("--list-styles", action="store_true",
                   help="print the channels with a hand-written voice brief")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    if args.list_styles:
        for name, brief in sorted(CHANNEL_STYLES.items()):
            print(f"{name:<24} {brief}")
        return 0

    work = workspace.work_dir(args)
    split = lambda s: [p.strip() for p in s.split(",") if p.strip()]  # noqa: E731
    inspo = split(args.inspo)
    genres = tuple(split(args.genres)) or GENRE_POOL

    store_path = None if args.no_store else (args.store or str(work / workspace.STORE_FILE))
    stored = read_store(store_path) if store_path else None
    exclude = load_titles(args.dedupe_from)
    if stored:
        exclude += [r["title"] for r in stored]
        if not args.quiet:
            print(f"store: {len(stored)} idea(s) in {store_path} to avoid",
                  file=sys.stderr)

    config = EngineConfig(tools=(), model=args.model)
    generator = Generator(
        config, niche=args.niche, inspo=inspo, rich=args.rich, genres=genres,
        jobs=args.jobs, exclude=exclude, verbose=not args.quiet,
    )

    try:
        ideas = generator.run(args.count, args.batch)
    except ClaudeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not ideas:
        print("error: no ideas generated", file=sys.stderr)
        return 1

    usage = generator.usage
    if args.rank:
        if not args.quiet:
            print("ranking...", file=sys.stderr)
        ranker = ClaudeEngine(config)
        try:
            ideas = rank(ranker, ideas, niche=args.niche)
        except ClaudeError as exc:
            print(f"warning: ranking failed ({exc}); leaving order as generated",
                  file=sys.stderr)
        _merge_usage(usage, ranker.usage)

    print_ideas(ideas)

    json_out = args.json_out or str(work / workspace.IDEAS_FILE)
    write_json(json_out, ideas)
    print(f"\nwrote {json_out}", file=sys.stderr)
    if args.csv_out:
        write_csv(args.csv_out, ideas)
        print(f"wrote {args.csv_out}", file=sys.stderr)
    if args.md_out:
        write_md(args.md_out, ideas, niche=args.niche, inspo=inspo)
        print(f"wrote {args.md_out}", file=sys.stderr)

    if stored is not None and store_path:
        # Ranking reorders and scores `ideas`; spares never went through it.
        added = update_store(store_path, stored, ideas + generator.spare,
                             niche=args.niche, inspo=inspo)
        print(f"store: +{added} new, {len(stored) + added} total in {store_path}",
              file=sys.stderr)

    print(f"\n{len(ideas)}/{args.count} ideas | {usage}", file=sys.stderr)
    return 0 if len(ideas) == args.count else 1

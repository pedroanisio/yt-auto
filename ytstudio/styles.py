#!/usr/bin/env python3
"""Pick the visual styles that can actually carry one video's message.

Takes an idea from `yt ideas` (or typed in directly), works out what the video
has to make a viewer understand, and ranks concrete visual treatments against
it: what each one shows better than the alternatives, the shots to cut for this
specific video, what it costs to make, and where it falls apart. With a
`yt research` dossier in the project the styles are judged against the footage
you can actually license.

    yt styles --title "How EUV light is made"
    yt styles --from ideas.json --index 3 --rich
    yt styles --from ideas.json --title "lithography" --count 7
    yt styles --title "..." --solo --no-face --tools "blender,davinci"
    yt styles --from ideas.json --index 1 \
        --dossier research/how-euv-light-is-made --md styles.md
    yt styles --title "..." --deliberate full --count 4
    cat one-idea.json | yt styles - --json styles.json

STYLE_LIBRARY is the candidate pool handed to the model. `--only` narrows it to
what you can produce, `--avoid` drops treatments you are sick of, `--add` puts
something else in the running, and any name you pass that is not in the library
is passed through for the model to characterise itself.

By default the choice is one call: every style is picked, scored and justified
in a single pass, which means nothing outside the chosen few is ever scored and
nothing argues back. `--deliberate` buys that back, as comma-separated stages:

    sweep        score the whole pool first (one cheap call), then detail only
                 the shortlist -- so the pick is the top of a scored sweep
    independent  score each candidate in its own call, in parallel, so no
                 style is judged in the shadow of the one named before it
    critique     after the choice, attack every pick, revise the scores, and
                 name what was wrongly left out
    full         sweep + critique

`independent` is one call per candidate -- 29 on the default pool -- so pair it
with `--only` or `--jobs`.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from claude_engine import ClaudeEngine, ClaudeError, EngineConfig, Usage
from .core import Idea, _key, _merge_usage, compact, wrap
from .core import write_json as dump_json
from . import harness, house, workspace
from .assess import stack_brief

#: Deliberation stages, as accepted by --deliberate. See the module docstring.
DELIBERATION = ("sweep", "independent", "critique", "full")

#: The candidate pool. One line each, because the brief is what stops the model
#: from answering "motion graphics" to every question ever asked.
STYLE_LIBRARY = {
    "3d animation": "camera moves through an accurate 3D model of the thing; "
                    "scale, mechanism and interior cutaways",
    "2d motion graphics": "flat vector shapes and labels animating on a clean "
                          "background; relationships, flows and steps",
    "whiteboard / hand drawn": "a drawing appearing under the narration; "
                               "reasoning unfolding at the speed of thought",
    "data visualisation": "charts built live on screen, axes narrated; "
                          "magnitudes, trends and comparisons",
    "archival montage": "period film, stills, documents and newspapers cut to "
                        "narration; history and institutional memory",
    "talking head + b-roll": "a person on camera, cutaways over the claims; "
                             "authority, opinion and direct address",
    "screen recording": "the real software or interface, cursor and all; "
                        "anything a viewer will reproduce themselves",
    "tabletop demonstration": "hands and objects on a lit surface, macro "
                              "lenses; physical cause and effect at human scale",
    "scale model": "a built physical rig that fails on camera the way the real "
                   "thing does; intuition you cannot get from a diagram",
    "lab / field demonstration": "the actual measurement being taken, with "
                                 "instruments visible; evidence, not assertion",
    "stop motion": "objects, paper or clay moved frame by frame; craft, "
                   "charm and transformation over time",
    "kinetic typography": "the words themselves as the image, timed to the "
                          "voice; quotes, definitions and rhetoric",
    "isometric diagram": "a clean 2.5D cutaway built up layer by layer; "
                         "systems, plants, buildings and networks",
    "map driven": "camera over real maps and satellite imagery, routes drawn; "
                  "geography, logistics and spread",
    "timeline driven": "a scrubbing horizontal timeline as the spine of the "
                       "edit; sequence, causality and deep time",
    "comic / graphic novel": "illustrated panels with limited animation; "
                             "reconstructions nobody filmed",
    "chalkboard derivation": "symbols written out and worked through; proofs "
                             "and derivations where the steps are the point",
    "photoreal simulation": "physically-based simulation footage (fluid, "
                            "orbital, structural); behaviour you cannot shoot",
    "voxel animation": "blocky volumetric models assembled and dismantled a "
                       "cube at a time; volumes, growth and 3D data read as "
                       "countable units",
    "satellite and GIS": "real remote-sensing imagery, time series of one "
                         "place; change at planetary scale",
    "photogrammetry": "scanned real objects and sites, flown through freely; "
                      "places and artefacts you cannot visit",
    "cad exploded view": "engineering models pulled apart in space, parts "
                         "labelled; assembly, tolerance and design intent",
    "signal / instrument readout": "oscilloscope traces, spectrograms, sensor "
                                   "dashboards; anything invisible but measured",
    "found footage essay": "other people's uploads, clips and streams cut "
                           "together; culture, internet history and discourse",
    "minimalist infographic": "one number, typeset enormous, held on screen; "
                              "a single statistic you want remembered",
    "slow cinematic photography": "long lenses, slow pushes, almost no "
                                  "narration; atmosphere, awe and scale",
    "puppet / character host": "a recurring drawn or built character explains; "
                               "warmth, humour and a young audience",
    "reenactment": "staged scenes with actors and period detail; decisions, "
                   "rooms and conversations that were never filmed",
    "split screen comparison": "two treatments held side by side on one "
                               "timeline; A/B claims and before/after",
}

SYSTEM = (
    "You are an art director for factual YouTube: documentary, explainer and "
    "video-essay work. You choose visual treatments by what they let a viewer "
    "understand, not by what looks expensive. You are specific -- you name the "
    "actual shots for the actual video in front of you -- and you are honest "
    "about production cost, the skills required, and the ways a style fails. "
    "You never recommend a treatment the stated constraints rule out. "
    "Everything is produced on one stack: the only renderer is scene-render, "
    "with the sr-core schema and SDK; there is no Blender, After Effects or "
    "Manim in the loop, no camera crew and no stock subscription unless the "
    "constraints say so. Judge every treatment by what that stack renders, "
    "approximates or cannot do, as the assessment states it, and name "
    "scene-render constructs -- never other software -- as the tools."
)


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #

def style_schema(rich: bool) -> dict:
    """Output shape. `rich` adds the art-direction and production detail."""
    props = {
        "name": {"type": "string"},
        "fit": {"type": "number"},
        "why": {"type": "string"},
        "shows_well": {"type": "string"},
        "shots": {"type": "array", "items": {"type": "string"}},
        "effort": {"type": "string", "enum": ["low", "medium", "high"]},
        "pitfalls": {"type": "string"},
    }
    if rich:
        props |= {
            "tools": {"type": "array", "items": {"type": "string"}},
            "time_estimate": {"type": "string"},
            "palette": {"type": "string"},
            "typography": {"type": "string"},
            "motion": {"type": "string"},
            "sound": {"type": "string"},
            "references": {"type": "array", "items": {"type": "string"}},
        }
    schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "demands": {"type": "array", "items": {"type": "string"}},
            "styles": {
                "type": "array",
                "items": {"type": "object", "properties": props,
                          "required": list(props)},
            },
            "combination": {"type": "string"},
            "avoid": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"},
                                   "why": {"type": "string"}},
                    "required": ["name", "why"],
                },
            },
        },
        "required": ["message", "demands", "styles", "combination", "avoid"],
    }
    if rich:
        schema["properties"]["thumbnail"] = {"type": "string"}
        schema["required"].append("thumbnail")
    return schema


#: Stage one: every candidate scored, with nothing else asked for.
SWEEP_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "fit": {"type": "number"},
                    "note": {"type": "string"},
                },
                "required": ["name", "fit", "note"],
            },
        }
    },
    "required": ["scores"],
}

#: The same, for one candidate judged on its own.
SCORE_SCHEMA = {
    "type": "object",
    "properties": {"fit": {"type": "number"}, "note": {"type": "string"}},
    "required": ["fit", "note"],
}

#: The adversarial pass: one objection per pick, a revised score, and the
#: styles the choice skipped over.
CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "attack": {"type": "string"},
                    "holds": {"type": "boolean"},
                    "fit": {"type": "number"},
                },
                "required": ["name", "attack", "holds", "fit"],
            },
        },
        "overlooked": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"},
                               "why": {"type": "string"}},
                "required": ["name", "why"],
            },
        },
    },
    "required": ["verdicts", "overlooked"],
}


@dataclass
class Style:
    name: str
    fit: float = 0.0
    why: str = ""
    shows_well: str = ""
    shots: list[str] = field(default_factory=list)
    effort: str = ""
    pitfalls: str = ""
    tools: list[str] = field(default_factory=list)
    time_estimate: str = ""
    palette: str = ""
    typography: str = ""
    motion: str = ""
    sound: str = ""
    references: list[str] = field(default_factory=list)
    attack: str = ""                 # the critique stage's best objection
    holds: bool | None = None        # whether the pick survived it
    fit_before: float | None = None  # the score it had before the critique

    @classmethod
    def from_raw(cls, raw: dict) -> Style:
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in raw.items() if k in known and v is not None}
        for key in ("name", "why", "shows_well", "effort", "pitfalls",
                    "time_estimate", "palette", "typography", "motion", "sound"):
            if key in clean:
                clean[key] = str(clean[key]).strip()
        if "fit" in clean:
            try:
                clean["fit"] = float(clean["fit"])
            except (TypeError, ValueError):
                clean.pop("fit")
        return cls(**clean)


# --------------------------------------------------------------------------- #
# prompting
# --------------------------------------------------------------------------- #

#: Names are matched loosely, so "3D Animation", "3d-animation" and
#: "3d animation" all find the same brief.
_BY_KEY = {_key(name): (name, brief) for name, brief in STYLE_LIBRARY.items()}


def candidates(only: list[str], avoid: list[str],
               extra: list[str] | None = None) -> list[tuple[str, str]]:
    """The pool as (name, brief) pairs.

    `only` replaces the library, `extra` appends to whatever is left, and
    either may name a style the library has never heard of -- which then
    reaches the model as a bare name for it to characterise itself.
    """
    dropped = {_key(a) for a in avoid}
    if only:
        pairs = [_BY_KEY.get(_key(n), (n, "")) for n in only
                 if _key(n) not in dropped]
    else:
        pairs = [(name, brief) for name, brief in STYLE_LIBRARY.items()
                 if _key(name) not in dropped]
    seen = {_key(name) for name, _ in pairs}
    for name in extra or []:
        key = _key(name)
        if key in dropped or key in seen:
            continue
        seen.add(key)
        pairs.append(_BY_KEY.get(key, (name, "")))
    return pairs


def pool_lines(pairs: list[tuple[str, str]]) -> list[str]:
    return [f"- {name}: {brief}" if brief else f"- {name}"
            for name, brief in pairs]


def constraint_lines(args: argparse.Namespace) -> list[str]:
    bits = []
    if args.solo:
        bits.append("One person, no crew and no presenter but them.")
    if args.no_face:
        bits.append("Nobody appears on camera; hands are fine, faces are not.")
    if args.vertical:
        bits.append("Vertical 9:16, watched on a phone, often muted.")
    if args.money:
        bits.append({
            "shoestring": "Budget is near zero: free tools, free archives, "
                          "things already in the room.",
            "modest": "Budget is modest: a stock subscription and a few paid "
                      "assets, nothing bespoke.",
            "funded": "Budget is real: bespoke animation or a shoot is "
                      "affordable if it earns its place.",
        }[args.money])
    if args.tools:
        bits.append("Software and kit already on hand: " + ", ".join(args.tools) + ".")
    if args.time:
        bits.append(f"Time available to make it: {args.time}.")
    return bits


def dossier_context(path: str, idea: Idea, *, limit: int = 20) -> str:
    """What the research already found, so styles are judged against real footage."""
    root = Path(path)
    file = root if root.is_file() else root / "dossier.json"
    if not file.exists():
        print(f"warning: no dossier.json at {path}, ignoring", file=sys.stderr)
        return ""
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: cannot read {file} ({exc}), ignoring", file=sys.stderr)
        return ""

    researched = str((data.get("idea") or {}).get("title", ""))
    if researched and _key(researched) != _key(idea.title):
        print(f"warning: {file} researched {researched!r}, not this idea; "
              "using it anyway", file=sys.stderr)

    parts = []
    if data.get("verdict"):
        parts.append(f"Research verdict: {data['verdict']}")
    numbers = [f"- {c.get('number') or '?'}: {c.get('claim', '')}"
               for c in data.get("claims", []) if c.get("number")][:limit]
    if numbers:
        parts.append("Quantified claims the visuals have to carry:\n"
                     + "\n".join(numbers))
    assets = [f"- [{a.get('kind', '?')}] {a.get('title', '')} "
              f"({a.get('licence', 'licence unknown')})"
              for a in data.get("assets", [])][:limit]
    if assets:
        parts.append("Footage and imagery already cleared or found:\n"
                     + "\n".join(assets))
    if data.get("misconceptions"):
        parts.append("Misconceptions the video must visibly correct:\n"
                     + "\n".join(f"- {m}" for m in data["misconceptions"][:8]))
    return "\n\n".join(parts)


def situation(constraints: list[str], context: str, stack: str = "") -> list[str]:
    """The constraint, research and stack blocks every stage is judged against."""
    parts = []
    if constraints:
        parts.append("Hard production constraints -- a style that breaks one "
                     "of these is disqualified, not discounted:\n"
                     + "\n".join(f"- {c}" for c in constraints))
    if context:
        parts.append("What the research for this video already established:\n\n"
                     + context)
    if stack:
        parts.append("What the production stack makes of this video, from its "
                     "assessment -- a treatment that needs what it cannot do is "
                     "disqualified; one that leans on what it renders well is "
                     "favoured:\n\n" + stack)
    return parts


def build_prompt(idea: Idea, *, count: int, pool: list[str], rich: bool,
                 constraints: list[str], context: str, house: str = "",
                 stack: str = "") -> str:
    parts = [
        idea.block(),
        "First state, in one sentence, what a viewer has to walk away "
        "understanding -- the message, not the topic. Then list the visual "
        "demands that message makes: the things that must be seen for it to "
        "land (a mechanism in motion, a magnitude, a place, a timeline, a "
        "person's judgement, an invisible signal).",
        f"Then choose the {count} visual styles that serve those demands best, "
        "from this pool:\n" + "\n".join(pool),
        "Score each one 1-10 on fit for THIS video -- use the full range, and "
        "do not score a style highly just because it is popular. Order them "
        "best first.",
    ]
    if house:
        parts.append(house)
    parts += situation(constraints, context, stack)

    wanted = [
        "why it serves this video's message specifically",
        "what it shows better than the alternatives",
        "3-5 concrete shots or sequences from THIS video, not generic examples",
        "production effort (low, medium or high)",
        "the pitfall that ruins it, and the tell that it is going wrong",
    ]
    if rich:
        wanted += [
            "the tools that actually produce it -- scene-render constructs "
            "(elements, materials, lights, cameras, particles, post) and this "
            "pipeline's own stages, never other software",
            "a realistic time estimate for one video",
            "a palette direction, in words plus hex codes",
            "a typographic direction (typeface character, not a brand name only)",
            "a motion and pacing note: cut rhythm, camera, transitions",
            "a sound design note",
            "2-4 channels or specific videos that execute it well",
        ]
    parts.append("For each style give " + "; ".join(wanted) + ".")
    parts.append(
        "Then say how to combine them: one primary treatment that carries the "
        "runtime, and the accents that earn a cut away from it. Finally, name "
        "the styles to avoid for this video and why -- including the obvious "
        "choice everyone would reach for, if it is wrong here."
    )
    if rich:
        parts.append("Also give one thumbnail and title-card direction that "
                     "reads at 120 px wide and matches the primary treatment.")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# deliberation
# --------------------------------------------------------------------------- #

class Director:
    """Chooses the treatment, deliberating first if asked to.

    With no stages this is one call: the model picks, scores and justifies in a
    single pass. `sweep` or `independent` put a scored pool in front of that
    choice; `critique` puts an adversary after it.
    """

    def __init__(self, idea: Idea, *, model: str = "sonnet", count: int = 5,
                 rich: bool = False, constraints: list[str] | None = None,
                 context: str = "", modes: set[str] | None = None,
                 keep: int = 0, jobs: int = 3, house: str = "",
                 house_digest: dict | None = None, stack: str = "",
                 verbose: bool = True, root: Path | str | None = None) -> None:
        self.idea = idea
        self.root = root            # the project, for its open revision notes
        self.model = model
        self.count = count
        self.rich = rich
        self.constraints = constraints or []
        self.context = context
        self.stack = stack
        self.modes = modes or set()
        self.keep = keep
        self.jobs = max(1, jobs)
        self.house = house
        self.house_digest = house_digest or {}
        self.verbose = verbose
        self.usage = Usage()

    # -- plumbing ----------------------------------------------------------

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def _ask(self, prompt: str, schema: dict) -> dict:
        """One call on its own engine: threads must not share usage counters."""
        engine = ClaudeEngine(EngineConfig(tools=(), model=self.model))
        try:
            return engine.ask_json(prompt, schema, system=harness.with_feedback(
                SYSTEM, self.root, "styles"))
        finally:
            _merge_usage(self.usage, engine.usage)

    def _preamble(self, *extra: str) -> str:
        return "\n\n".join([self.idea.block(),
                            *([self.house] if self.house else []),
                            *situation(self.constraints, self.context, self.stack),
                            *extra])

    # -- stage one: score the pool -----------------------------------------

    _SCORING = (
        "Score on how well the treatment would communicate what this video "
        "has to make a viewer understand -- not on how impressive it looks, "
        "how modern it is, or how popular it is on YouTube. Use the full "
        "range: most styles are a poor fit for any given video and belong "
        "below 5. No shots and no production detail yet."
    )

    def _sweep(self, pairs: list[tuple[str, str]]) -> list[dict]:
        data = self._ask(
            self._preamble(
                f"Score every one of these {len(pairs)} candidate visual "
                f"styles from 1-10 for this video.\n\n{self._SCORING}\n\n"
                "Score every entry, invent none, and give one short clause of "
                "reasoning each.\n\n" + "\n".join(pool_lines(pairs))),
            SWEEP_SCHEMA)
        return data.get("scores", [])

    def _independent(self, pairs: list[tuple[str, str]]) -> list[dict]:
        def one(pair: tuple[str, str]) -> dict:
            name, brief = pair
            line = f"- {name}: {brief}" if brief else f"- {name}"
            data = self._ask(
                self._preamble(
                    f"Candidate visual style:\n{line}\n\n"
                    f"Score this one style from 1-10 for this video.\n\n"
                    f"{self._SCORING}\n\nJudge it on its own merits against "
                    "this video. You are not comparing it with other styles "
                    "and you are not choosing a winner -- a poor fit scores "
                    "low even if nothing better exists. One short clause of "
                    "reasoning."),
                SCORE_SCHEMA)
            return {"name": name, "fit": data.get("fit", 0),
                    "note": data.get("note", "")}

        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            out = []
            for pair, future in zip(pairs, [pool.submit(one, p) for p in pairs]):
                try:
                    out.append(future.result())
                except ClaudeError as exc:
                    self._say(f"  {pair[0]}: scoring failed ({exc})")
            return out

    def shortlist(self, pairs: list[tuple[str, str]]
                  ) -> tuple[list[tuple[str, str]], list[dict]]:
        """Score the whole pool, then hand only the survivors to the choice."""
        independent = "independent" in self.modes
        keep = min(self.keep or max(self.count + 2, self.count * 2), len(pairs))
        label = "independent" if independent else "sweep"
        self._say(f"[{label}] scoring {len(pairs)} candidate(s)"
                  + (f", {self.jobs} at a time" if independent else " in one call")
                  + f", keeping {keep}")
        scores = self._independent(pairs) if independent else self._sweep(pairs)
        if not scores:
            self._say(f"[{label}] no scores came back; keeping the whole pool")
            return pairs, []

        # Keep only rows that name a real candidate, under the pool's own
        # spelling: a sweep that invents or repeats a name scores nothing.
        by_key = {_key(name): (name, brief) for name, brief in pairs}
        ranked, seen = [], set()
        for row in sorted(scores, key=lambda r: -float(r.get("fit") or 0)):
            key = _key(str(row.get("name", "")))
            if key not in by_key or key in seen:
                continue
            seen.add(key)
            ranked.append(row | {"name": by_key[key][0]})
            self._say(f"  {float(row.get('fit') or 0):>4.1f}  {by_key[key][0]}"
                      f" -- {str(row.get('note', ''))[:70]}")
        survivors = [by_key[_key(r["name"])] for r in ranked[:keep]]
        if len(survivors) < self.count:
            # A name the sweep invented or mangled is not worth failing over.
            survivors += [p for p in pairs if p not in survivors][
                :self.count - len(survivors)]
        return survivors, ranked

    # -- the choice --------------------------------------------------------

    def choose(self, pairs: list[tuple[str, str]]) -> dict:
        count = min(self.count, len(pairs))
        self._say(f"[choose] {len(pairs)} candidate(s), picking {count}")
        return self._ask(
            build_prompt(self.idea, count=count, pool=pool_lines(pairs),
                         rich=self.rich, constraints=self.constraints,
                         context=self.context, house=self.house, stack=self.stack),
            style_schema(self.rich))

    # -- stage three: argue with it ----------------------------------------

    def critique(self, data: dict, styles: list[Style],
                 rejected: list[tuple[str, str]]) -> list[dict]:
        """Attack every pick, revise the scores in place, report the misses."""
        self._say(f"[critique] attacking {len(styles)} pick(s)")
        plan = "\n\n".join(
            f"### {s.name} (scored {s.fit:g}/10, {s.effort} effort)\n"
            f"Why: {s.why}\nShows: {s.shows_well}\n"
            + "\n".join(f"- shot: {shot}" for shot in s.shots)
            + (f"\nStated risk: {s.pitfalls}" if s.pitfalls else "")
            for s in styles)
        extra = ["\n".join(pool_lines(rejected))] if rejected else []
        try:
            verdicts = self._ask(
                self._preamble(
                    f"Stated message: {data.get('message', '')}",
                    "Visual demands:\n"
                    + "\n".join(f"- {d}" for d in data.get("demands", [])),
                    f"A director chose these treatments:\n\n{plan}",
                    "Attack this plan. For each treatment name the single "
                    "strongest objection: a demand it does not actually "
                    "serve, a shot that cannot be produced under the "
                    "constraints, the way it will bore or mislead the viewer, "
                    "or a cost it hides. Then say whether the choice still "
                    "holds, and give the score you would put on it having "
                    "made the objection -- leave the score alone when the "
                    "objection is weak, and do not demote everything to look "
                    "rigorous.",
                    *(["Styles that were available and not chosen. Name any "
                       "that was wrongly left out and what it would do "
                       "better, or return none:\n" + extra[0]] if extra else
                      ["Return an empty overlooked list."])),
                CRITIQUE_SCHEMA)
        except ClaudeError as exc:
            self._say(f"[critique] failed ({exc}); keeping the choice as made")
            return []

        by_key = {_key(s.name): s for s in styles}
        for row in verdicts.get("verdicts", []):
            style = by_key.get(_key(str(row.get("name", ""))))
            if style is None:
                continue
            style.attack = str(row.get("attack", "")).strip()
            style.holds = bool(row.get("holds"))
            try:
                revised = float(row.get("fit"))
            except (TypeError, ValueError):
                continue
            if revised != style.fit:
                style.fit_before, style.fit = style.fit, revised
        return verdicts.get("overlooked", [])

    # -- run ---------------------------------------------------------------

    def run(self, pairs: list[tuple[str, str]]) -> tuple[dict, list[Style]]:
        scores: list[dict] = []
        pool = pairs
        if self.modes & {"sweep", "independent"}:
            pool, scores = self.shortlist(pairs)

        data = self.choose(pool)
        data["house"] = self.house_digest
        styles = [Style.from_raw(raw) for raw in data.get("styles", [])]

        overlooked: list[dict] = []
        if "critique" in self.modes and styles:
            chosen = {_key(s.name) for s in styles}
            overlooked = self.critique(
                data, styles, [p for p in pairs if _key(p[0]) not in chosen])

        # The count is a prompt instruction, not a schema constraint, so hold
        # it here -- after any critique has had its say on the order.
        styles.sort(key=lambda s: -s.fit)
        styles = styles[:min(self.count, len(pool))]
        if self.modes:
            data["deliberation"] = {"stages": sorted(self.modes),
                                    "pool_scores": scores,
                                    "overlooked": overlooked}
        return data, styles


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def print_styles(idea: Idea, data: dict, styles: list[Style]) -> None:
    print(f"\n{idea.title}")
    print(f"[{idea.genre}]" + (f" · {idea.length}" if idea.length else ""))
    print(wrap(f"message: {data.get('message', '')}", "  "))
    for demand in data.get("demands", []):
        print(wrap(f"- {demand}", "  "))

    for n, style in enumerate(styles, 1):
        score = f"{style.fit:g}/10"
        if style.fit_before is not None:
            score += f", was {style.fit_before:g}"
        print(f"\n{n:>2}. {style.name}  ({score}"
              + (f", {style.effort} effort)" if style.effort else ")"))
        print(wrap(style.why, "    "))
        print(wrap(f"shows: {style.shows_well}", "    "))
        for shot in style.shots:
            print(wrap(f"· {shot}", "      "))
        if style.tools or style.time_estimate:
            bits = [b for b in (", ".join(style.tools), style.time_estimate) if b]
            print(wrap(" | ".join(bits), "    "))
        for label, value in (("palette", style.palette),
                             ("type", style.typography),
                             ("motion", style.motion),
                             ("sound", style.sound)):
            if value:
                print(wrap(f"{label}: {value}", "    "))
        if style.references:
            print(wrap("like: " + ", ".join(style.references), "    "))
        if style.pitfalls:
            print(wrap(f"risk: {style.pitfalls}", "    "))
        if style.attack:
            print(wrap(f"{'holds' if style.holds else 'challenged'}: "
                        f"{style.attack}", "    "))

    if data.get("combination"):
        print("\n  how to combine")
        print(wrap(data["combination"], "    "))
    if data.get("thumbnail"):
        print("\n  thumbnail")
        print(wrap(data["thumbnail"], "    "))
    if data.get("avoid"):
        print("\n  avoid")
        for row in data["avoid"]:
            print(wrap(f"· {row.get('name', '')}: {row.get('why', '')}", "    "))

    overlooked = (data.get("deliberation") or {}).get("overlooked") or []
    if overlooked:
        print("\n  worth reconsidering")
        for row in overlooked:
            print(wrap(f"· {row.get('name', '')}: {row.get('why', '')}", "    "))


def write_json(path: str, idea: Idea, data: dict, styles: list[Style]) -> None:
    doc = {"idea": compact(idea),
           "message": data.get("message", ""),
           "demands": data.get("demands", []),
           "styles": [compact(s) for s in styles],
           "combination": data.get("combination", ""),
           "avoid": data.get("avoid", [])}
    if data.get("thumbnail"):
        doc["thumbnail"] = data["thumbnail"]
    if data.get("deliberation"):
        doc["deliberation"] = data["deliberation"]
    dump_json(path, doc)


def write_csv(path: str, styles: list[Style]) -> None:
    cols = [f.name for f in fields(Style)
            if any(getattr(s, f.name) not in ("", [], None) for s in styles)]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for style in styles:
            row = asdict(style)
            for key in ("shots", "tools", "references"):
                row[key] = " | ".join(row[key])
            writer.writerow({c: row[c] for c in cols})


def write_md(path: str, idea: Idea, data: dict, styles: list[Style]) -> None:
    md = [f"# Visual direction — {idea.title}", "",
          f"`{idea.genre}`" + (f" · {idea.length}" if idea.length else ""), "",
          f"**Message.** {data.get('message', '')}", ""]
    if data.get("demands"):
        md += ["**What has to be seen**", ""]
        md += [f"- {d}" for d in data["demands"]] + [""]

    md += ["| # | style | fit | effort | shows |", "|---|---|---|---|---|"]
    md += [f"| {n} | {s.name} | {s.fit:g}/10 | {s.effort} | "
           f"{s.shows_well.replace('|', '-')} |"
           for n, s in enumerate(styles, 1)]
    md.append("")

    for n, style in enumerate(styles, 1):
        md += [f"## {n}. {style.name} — **{style.fit:g}/10**", "",
               f"`{style.effort} effort`"
               + (f" · {style.time_estimate}" if style.time_estimate else ""), "",
               style.why, ""]
        if style.shots:
            md += ["**Shots**", ""] + [f"- {s}" for s in style.shots] + [""]
        for label, value in (("Palette", style.palette),
                             ("Type", style.typography),
                             ("Motion", style.motion),
                             ("Sound", style.sound)):
            if value:
                md.append(f"- **{label}.** {value}")
        if style.tools:
            md.append("- **Tools.** " + ", ".join(f"`{t}`" for t in style.tools))
        if style.references:
            md.append("- **Like.** " + ", ".join(style.references))
        if style.pitfalls:
            md.append(f"- **Risk.** {style.pitfalls}")
        if style.attack:
            verdict = "Holds" if style.holds else "Challenged"
            was = (f" (scored {style.fit_before:g} before the critique)"
                   if style.fit_before is not None else "")
            md.append(f"- **{verdict}.** {style.attack}{was}")
        md.append("")

    if data.get("combination"):
        md += ["## How to combine them", "", data["combination"], ""]
    if data.get("thumbnail"):
        md += ["## Thumbnail and title card", "", data["thumbnail"], ""]
    if data.get("avoid"):
        md += ["## Avoid", ""]
        md += [f"- **{r.get('name', '')}** — {r.get('why', '')}"
               for r in data["avoid"]] + [""]

    deliberation = data.get("deliberation") or {}
    if deliberation.get("overlooked"):
        md += ["## Worth reconsidering", ""]
        md += [f"- **{r.get('name', '')}** — {r.get('why', '')}"
               for r in deliberation["overlooked"]] + [""]
    if deliberation.get("pool_scores"):
        md += ["## The whole pool, scored", "",
               f"*Deliberation: {', '.join(deliberation.get('stages', []))}.*", "",
               "| style | fit | read |", "|---|---|---|"]
        md += [f"| {r.get('name', '')} | {float(r.get('fit') or 0):g} | "
               f"{str(r.get('note', '')).replace('|', '-')} |"
               for r in deliberation["pool_scores"]] + [""]
    Path(path).write_text("\n".join(md), encoding="utf-8")


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "styles", parents=list(parents), help="rank visual treatments for one idea",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--count", type=int, default=5,
                   help="how many styles to rank (default 5)")
    p.add_argument("--only", default="", metavar="NAMES",
                   help="comma-separated styles to choose among")
    p.add_argument("--avoid", default="", metavar="NAMES",
                   help="comma-separated styles to leave out")
    p.add_argument("--add", default="", metavar="NAMES",
                   help="comma-separated styles to add to the pool, library or "
                        "not (unlike --only, this extends it)")
    p.add_argument("--dossier", metavar="DIR",
                   help="research to judge styles against (default: the project's)")
    p.add_argument("--rich", action="store_true",
                   help="also get palette, type, motion, sound, tools, refs")
    p.add_argument("--deliberate", default="", metavar="STAGES",
                   help="comma-separated: sweep (score the whole pool first), "
                        "independent (score each candidate in its own call), "
                        "critique (attack the choice afterwards), full "
                        "(sweep + critique)")
    p.add_argument("--shortlist", type=int, default=0, metavar="N",
                   help="candidates the sweep keeps (default count x2)")
    p.add_argument("--jobs", type=int, default=3,
                   help="parallel calls when scoring independently (default 3)")
    p.add_argument("--solo", action="store_true", help="one person, no crew")
    p.add_argument("--no-face", action="store_true", dest="no_face",
                   help="nobody on camera")
    p.add_argument("--vertical", action="store_true",
                   help="vertical, phone-first, often muted")
    p.add_argument("--money", choices=["shoestring", "modest", "funded"],
                   help="budget level")
    p.add_argument("--tools", default="", metavar="LIST",
                   help="comma-separated software and kit on hand")
    p.add_argument("--time", metavar="SPAN",
                   help="time available, e.g. '1 weekend' or '3 weeks'")
    p.add_argument("--json", dest="json_out", help="where to write JSON")
    p.add_argument("--csv", dest="csv_out", help="also write CSV here")
    p.add_argument("--md", dest="md_out", help="where to write Markdown")
    p.add_argument("--list-styles", action="store_true",
                   help="print the style library and exit")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    if args.list_styles:
        for name, brief in STYLE_LIBRARY.items():
            print(f"{name:<28} {brief}")
        return 0

    split = lambda s: [p.strip() for p in s.split(",") if p.strip()]  # noqa: E731
    args.tools = split(args.tools)
    idea, project = workspace.resolve(args, inputs=("dossier",))
    pairs = candidates(split(args.only), split(args.avoid), split(args.add))
    if not pairs:
        print("error: --only and --avoid leave no styles to choose from",
              file=sys.stderr)
        return 1
    if args.count > len(pairs):
        print(f"warning: only {len(pairs)} candidate style(s) in the pool",
              file=sys.stderr)
        args.count = len(pairs)

    modes = set(split(args.deliberate))
    if unknown := modes - set(DELIBERATION):
        print(f"error: unknown deliberation stage(s) {', '.join(sorted(unknown))}; "
              f"pick from {', '.join(DELIBERATION)}", file=sys.stderr)
        return 1
    if "full" in modes:
        modes = (modes - {"full"}) | {"sweep", "critique"}
    if {"sweep", "independent"} <= modes:
        print("warning: independent scoring supersedes sweep", file=sys.stderr)
        modes.discard("sweep")

    dossier = project.source("dossier", args.dossier)
    if not args.quiet:
        print(f"directing: {idea.title}\n  -> {project.root}", file=sys.stderr)

    rules, digest = house.for_args(args, project, "styles")
    if rules and not args.quiet:
        print(f"standards: {', '.join(digest['sources'])}", file=sys.stderr)
    stack = stack_brief(project.root, work=workspace.work_dir(args), idea=idea)
    if not args.quiet:
        print("stack: " + ("judged against this video's `yt assess` verdict" if stack
                           else "no `yt assess` verdict for this video; judged against "
                                "the stack alone"), file=sys.stderr)
    director = Director(
        idea, house=rules, house_digest=digest, stack=stack, root=project.root,
        model=args.model, count=args.count, rich=args.rich,
        constraints=constraint_lines(args), modes=modes, keep=args.shortlist,
        jobs=args.jobs, verbose=not args.quiet,
        context=dossier_context(dossier, idea) if dossier else "")
    try:
        data, styles = director.run(pairs)
    except ClaudeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not styles:
        print("error: no styles returned", file=sys.stderr)
        return 1

    print_styles(idea, data, styles)

    json_out = project.out("styles", args.json_out)
    md_out = project.out("styles_md", args.md_out)
    write_json(json_out, idea, data, styles)
    write_md(md_out, idea, data, styles)
    print(f"\nwrote {json_out}\nwrote {md_out}", file=sys.stderr)
    if args.csv_out:
        write_csv(args.csv_out, styles)
        print(f"wrote {args.csv_out}", file=sys.stderr)

    print(f"\n{len(styles)} style(s) | {director.usage}", file=sys.stderr)
    return 0

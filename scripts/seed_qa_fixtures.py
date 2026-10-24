#!/usr/bin/env python3
"""Draw the QA judge's fixture films: tests/fixtures/qa/<case>/ (AUTO-156).

    python scripts/seed_qa_fixtures.py --out tests/fixtures/qa
    python scripts/seed_qa_fixtures.py --out tests/fixtures/qa --base <captured case>

Each case is exactly what `qa.score_evidence` reads (build.json,
script.json, voice.json, samples.json, frames/ and frames/burst/) and an
expected.json saying what the judge must give. `python -m ytstudio.qa
calibrate` reads them.

`good` is the base every seeded case is made from. Without --base it is a
drawn stand-in (labelled "seeded", so the set cannot pass on it: see
qa.calibrate); with --base it is a case `python -m ytstudio.qa
capture-fixture` cut from one of the owner's own films and the owner
labelled (`label: owner`), copied as it is. `good-b` is the base with every
other sample. The seeded cases each change one thing and say what must fall:

- slide-drift: every frame a title-and-bullets slide of the shot's own
  narration, filling the frame and drifting slowly (a Ken Burns move over
  a slide is motion no frozen check sees), with the shot's caption box
  where the base has it;
- wall-of-text: a paragraph over the stage;
- band-violation: the super drawn in the middle band and the caption in
  the top one, the picture under them the base's;
- illegible-type: the overlay boxes' type redrawn at 9 px, in low
  contrast, the picture around them the base's;
- placeholder-moving: one shot's frames a grey placeholder card drifting
  over the picture, with build.placeholders left empty;
- tells-not-shows: three shots' narration rewritten to describe a process
  the frames never show, frames untouched;
- generic-identity (AUTO-146): every frame the same subject as stock
  explainer clip art (a flat two-tone backdrop, one rounded card, a sun
  disc and a comet icon), identical from shot to shot but for a slow
  slide, with the base's super and caption drawn again where they were,
  the words untouched: competent and generic, a look that could be moved
  onto any other topic, so editorial_identity must fall.

From the stand-in, band-violation and illegible-type redraw the picture
without its overlays (`picture`) and draw the overlays where the case
says, so nothing but the overlay boxes differs from good. A captured film's
overlays cannot be found in its pixels: from one, band-violation draws the
super and caption again in the wrong bands over the picture (its own
overlays stay), and illegible-type washes the two bands whole.

Deterministic: no clock, no unseeded random, PNGs written by PIL with
fixed settings, JSON as ytstudio.core.write_json writes it. Run twice, the
trees are byte-identical (tests/test_qa_calibrate.py checks the committed
tree against a fresh run). Uses the fonts under ytstudio/fonts, so nothing
is downloaded.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat  # noqa: E402

from ytstudio import qa  # noqa: E402
from ytstudio.core import read_json, write_json  # noqa: E402

FONTS = ROOT / "ytstudio" / "fonts" / "Inter"
W, H = qa.SHEET_TILE
#: The bands the prompt names: supers in the top 21%, captions in the bottom 24%.
TOP, BOTTOM = round(H * 0.21), H - round(H * 0.24)
#: Stand-in frames are stored with this many colours: small enough that a
#: case stays under qa.FIXTURE_CASE_BYTES.
COLOURS = 192

TITLE = "Why a comet's tail runs ahead of it"
SECTIONS = [
    ("The tail", [("1.1", "hook", "This comet is leaving the Sun, and its tail is in front.",
                   "TAIL AHEAD"),
                  ("1.2", "context", "Dust and gas stream off the head as it warms.",
                   "DUST AND GAS"),
                  ("1.3", "context", "So what is pushing them the other way?",
                   "WHAT PUSHES?")]),
    ("The push", [("2.1", "rehook", "Sunlight presses on the dust, but only gently.",
                   "LIGHT PRESSURE"),
                  ("2.2", "reveal", "The gas is pushed by something faster: the solar wind.",
                   "SOLAR WIND"),
                  ("2.3", "context", "It blows straight out from the Sun at every point.",
                   "RADIAL FLOW")]),
    ("The wind", [("3.1", "context", "So the tail always points away from the Sun.",
                   "AWAY FROM THE SUN"),
                  ("3.2", "payoff", "The tail is showing you the wind.", "THE WIND, SHOWN")]),
]
#: tells-not-shows: what three shots say instead. Each line points the
#: viewer at a concrete process ("watch", "see", "now") that the frames,
#: left as they are, never show, so the fault is between the line and the
#: picture, not in the line alone.
TOLD = {
    "2.1": "Watch single dust grains glint as sunlight knocks them loose from the crust.",
    "2.2": "See the ions glow blue and spiral along the Sun's magnetic field lines.",
    "2.3": "Now the field lines snap and reconnect, and a knot of plasma breaks off the tail.",
}
#: What each told line claims to show, which no frame of its shot draws.
TOLD_CLAIMS = {"2.1": "dust grains", "2.2": "magnetic field lines", "2.3": "knot of plasma"}
SHOT_SECONDS, FPS = 5.0, 30


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"Inter-{name}.ttf"), size)


# --------------------------------------------------------------------------- #
# the stand-in film
# --------------------------------------------------------------------------- #

def stand_in() -> tuple[dict, dict, dict]:
    """build.json, script.json and voice.json of the drawn stand-in."""
    shots, script_sections, takes, at = [], [], [], 0.0
    for heading, rows in SECTIONS:
        script_shots = []
        for label, function, line, term in rows:
            shots.append({"label": label, "at": at, "seconds": SHOT_SECONDS,
                          "asset": "effect", "section": heading, "function": function})
            script_shots.append({"label": label, "narration": line,
                                 "visual": f"the comet, {term.lower()}",
                                 "function": function, "shows": term.lower(),
                                 "seconds": SHOT_SECONDS})
            takes.append({"label": label, "text": line, "seconds": 4.0})
            at += SHOT_SECONDS
        script_sections.append({"heading": heading, "shots": script_shots})
    script_sections[0].update(opens=["Q1"], closes=[], rehook="", escalation="the tail leads")
    script_sections[1].update(opens=["Q2"], closes=[], rehook="Q1",
                              escalation="light is too weak to do it")
    script_sections[2].update(opens=[], closes=["Q1", "Q2"], rehook="",
                              escalation="the wind is everywhere")
    build = {"title": TITLE,
             "scene": {"duration": at, "fps": FPS, "width": 1920, "height": 1080},
             "shots": shots, "placeholders": [], "findings": [], "audio_tracks": 2}
    script = {
        "idea": {"title": TITLE},
        "train": "A comet's tail must point somewhere: what pushes it?",
        "promise": "why the tail runs ahead of the comet",
        "payoff": "the solar wind, shown bending the tail",
        "stakes": "the same wind can put a power grid out",
        "viewer_stake": "your lights",
        "questions": [{"id": "Q1", "text": "What pushes the tail?", "opened_in": 1,
                       "closed_in": 3, "left_open_why": ""},
                      {"id": "Q2", "text": "Why is light not enough?", "opened_in": 2,
                       "closed_in": 3, "left_open_why": ""}],
        "ending": {"payoff_line": "The tail is showing you the wind.",
                   "callback": "the tail in front", "next_film": "the heliopause"},
        "sections": script_sections,
    }
    voice = {"takes": takes}
    return build, script, voice


def terms() -> dict[str, str]:
    return {label: term for _, rows in SECTIONS for label, _, _, term in rows}


#: The overlays' type: the super's and the caption's.
SUPER_FONT, CAPTION_FONT = ("SemiBold", 22), ("Medium", 15)
OVERLAY_FILL, SUPER_INK, CAPTION_INK = (12, 14, 24), (236, 236, 236), (232, 232, 226)


def overlay_boxes(heading: str | None, term: str, placement: str = "good") -> dict:
    """Where a frame's overlays sit, as {"super": box (a section's start
    only), "caption": box}, each [x0, y0, x1, y1] in the tile. "good" is
    the house's: the super in the top band, the caption in the bottom one.
    "violation" is band-violation's: the super in the middle band, the
    caption in the top one."""
    out = {}
    if heading:
        width = font(*SUPER_FONT).getlength(heading.upper())
        y0 = 12 if placement == "good" else H // 2 - 17
        out["super"] = [16, y0, 16 + width + 24, y0 + 34]
    width = font(*CAPTION_FONT).getlength(term)
    y0 = BOTTOM + 18 if placement == "good" else 12
    out["caption"] = [16, y0, 16 + width + 20, y0 + 28]
    return out


def draw_overlays(im: Image.Image, heading: str | None, term: str,
                  placement: str = "good") -> Image.Image:
    """The super (at a section's start) and the caption, drawn where
    `overlay_boxes` puts them."""
    px = ImageDraw.Draw(im)
    boxes = overlay_boxes(heading, term, placement)
    if "super" in boxes:
        x0, y0, x1, y1 = boxes["super"]
        px.rectangle([x0, y0, x1, y1], fill=OVERLAY_FILL)
        px.text((x0 + 12, y0 + 3), heading.upper(), font=font(*SUPER_FONT), fill=SUPER_INK)
    x0, y0, x1, y1 = boxes["caption"]
    px.rectangle([x0, y0, x1, y1], fill=OVERLAY_FILL)
    px.text((x0 + 10, y0 + 5), term, font=font(*CAPTION_FONT), fill=CAPTION_INK)
    return im


def draw_good(t: float, shot: dict, index: int, heading: str | None, term: str) -> Image.Image:
    """One frame of the stand-in at film time `t`: the picture, a super at
    a section's start and a lower-third term."""
    return draw_overlays(picture(t, shot, index), heading, term)


def picture(t: float, shot: dict, index: int) -> Image.Image:
    """The stand-in's picture at film time `t`, without its overlays: a
    graded sky, the Sun's glow at the left, the comet crossing with its
    tail pointed away from the Sun."""
    hue = [(18, 26, 58), (14, 40, 52), (36, 22, 56)][index // 3 % 3]
    im = Image.new("RGB", (W, H))
    px = ImageDraw.Draw(im)
    for y in range(H):
        k = y / (H - 1)
        px.line([(0, y), (W, y)], fill=tuple(int(c + 46 * k) for c in hue))
    rng = random.Random(f"stars-{shot['label']}")
    for _ in range(48):
        x, y, b = rng.randrange(W), rng.randrange(H), rng.randrange(120, 200)
        px.point((x, y), fill=(b, b, b))
    for r, c in ((96, (90, 60, 30)), (72, (150, 100, 40)), (50, (210, 150, 60)),
                 (34, (236, 196, 110))):
        px.ellipse([-r - 30, H // 2 - r, r - 30, H // 2 + r], fill=c)
    local = (t - float(shot["at"])) / float(shot["seconds"])
    cx = int(round(150 + 230 * local))
    cy = 92 + (index * 37) % 96
    tail = 60 + 6 * (index % 4)
    px.polygon([(cx, cy - 7), (cx + tail, cy - 22), (cx + tail + 20, cy), (cx + tail, cy + 22),
                (cx, cy + 7)], fill=(120, 150, 175))
    px.polygon([(cx, cy - 4), (cx + tail, cy - 10), (cx + tail, cy + 10), (cx, cy + 4)],
               fill=(170, 195, 215))
    px.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], fill=(238, 232, 210))
    return im


def frames_of(row: dict) -> list[tuple[str, float]]:
    """Every file a sample names, with the film time it shows."""
    burst = row.get("burst") or {}
    return [(row["frame"], float(row["t"]))] + list(zip(burst.get("files") or [],
                                                        burst.get("times") or []))


def save(im: Image.Image, path: Path, like: Image.Image | None = None) -> None:
    """A frame as the fixture stores it: quantized when its base frame was
    (a stand-in), RGB as the base was otherwise, so a seeded case never
    differs from its base by how it was stored."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if like is None or like.mode == "P":
        im = im.convert("RGB").quantize(colors=COLOURS, method=Image.Quantize.MEDIANCUT,
                                        dither=Image.Dither.NONE)
    else:
        im = im.convert(like.mode)
    im.save(path, format="PNG", optimize=True)


def write_case(out: Path, build: dict, script: dict, voice: dict | None, samples: list[dict],
               expected: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "build.json", build)
    write_json(out / "script.json", script)
    if voice is not None:
        write_json(out / "voice.json", voice)
    write_json(out / "samples.json", samples)
    write_json(out / "expected.json", expected)


GOOD = {"label": "seeded", "judge_only": False, "verdict": "READY", "under_bar": [],
        "over_bar": list(qa.SIDE_CATEGORIES), "must_find": []}


def draw_stand_in(out: Path) -> None:
    build, script, voice = stand_in()
    samples = qa.fixture_samples(qa.sample(build, limit=qa.FIXTURE_SAMPLES, voice=voice),
                                 build)
    shots = {s["label"]: (n, s) for n, s in enumerate(build["shots"])}
    starts = {sec["shots"][0]["label"]: sec["heading"] for sec in script["sections"]}
    named = terms()
    for row in samples:
        n, shot = shots[row["label"]]
        for name, t in frames_of(row):
            save(draw_good(t, shot, n, starts.get(row["label"]), named[row["label"]]),
                 out / "frames" / name)
    write_case(out, build, script, voice, samples, GOOD)


# --------------------------------------------------------------------------- #
# the seeded cases, each from the base
# --------------------------------------------------------------------------- #

def load(case: Path) -> dict:
    return qa.load_case(case)


def shot_of(build: dict, label: str) -> dict:
    return next(s for s in build["shots"] if str(s.get("label")) == str(label))


def section_starts(build: dict) -> set[str]:
    seen, out = set(), set()
    for s in sorted(build["shots"], key=lambda s: float(s["at"])):
        if s.get("section") not in seen:
            seen.add(s.get("section"))
            out.add(str(s["label"]))
    return out


def band_text(script: dict, label: str) -> str:
    """The words a shot's lower third carries: the stand-in's term, else
    the first words of what the shot shows or says."""
    named = terms()
    if label in named:
        return named[label]
    for sec in script.get("sections") or []:
        for shot in sec.get("shots") or []:
            if str(shot.get("label")) == label:
                words = str(shot.get("shows") or shot.get("narration") or "").split()
                return " ".join(words[:4]).upper()
    return label


def heading_of(build: dict, label: str) -> str:
    return str(shot_of(build, label).get("section") or "")


def transform(base: dict, out: Path, change, rows: list[dict] | None = None) -> None:
    """Every frame of `rows` (the base's samples) through `change(im, row,
    t, shot)`; frames it returns None for are copied as they are."""
    for row in rows if rows is not None else base["samples"]:
        shot = shot_of(base["build"], row["label"])
        for name, t in frames_of(row):
            src = base["dir"] / "frames" / name
            with Image.open(src) as im:
                im.load()
                mode = im.copy()
            changed = change(mode.convert("RGB"), row, t, shot)
            if changed is None:
                (out / "frames" / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, out / "frames" / name)
            else:
                save(changed, out / "frames" / name, like=mode)


def scaled(y: int, im: Image.Image) -> int:
    return round(y * im.height / H)


def narration_of(script: dict, label: str) -> str:
    for sec in script.get("sections") or []:
        for shot in sec.get("shots") or []:
            if str(shot.get("label")) == str(label):
                return str(shot.get("narration") or "")
    return ""


def slide_bullets(line: str, most: int = 3, words: int = 5) -> list[str]:
    """A narrated line as a slide's bullets: its clauses (split at
    punctuation and at "but", "as" and "so"), each cut into even pieces of
    at most `words` words, the first `most` of them."""
    clauses = [c.split() for c in re.split(r"[,:;.?!]+|\s(?:but|as|so)\s", line)]
    out = []
    for clause in (c for c in clauses if c):
        pieces = -(-len(clause) // words)
        size = -(-len(clause) // pieces)
        out += [" ".join(clause[k:k + size]) for k in range(0, len(clause), size)]
    return [b[:1].upper() + b[1:] for b in out[:most]]


def slide_drift(base: dict, out: Path) -> None:
    fps = qa._fps(base["build"])

    def change(im, row, t, shot):
        w, h = im.size
        label = str(row["label"])
        drift = round(0.6 * (round(t * fps) - round(float(shot["at"]) * fps)))
        slide = Image.new("RGB", (w + 120, h))
        px = ImageDraw.Draw(slide)
        for y in range(h):
            v = int(34 + 30 * y / (h - 1))
            px.line([(0, y), (w + 120, y)], fill=(v, v + 4, v + 12))
        heading = heading_of(base["build"], label) or "Overview"
        px.text((104, scaled(64, im)), heading, font=font("Bold", 24), fill=(232, 232, 232))
        bullets = slide_bullets(narration_of(base["script"], label)) or [
            band_text(base["script"], label).title()]
        for k, line in enumerate(bullets):
            px.text((116, scaled(104 + 26 * k, im)), f"- {line}", font=font("Regular", 16),
                    fill=(206, 210, 216))
        px.rectangle([w - 130, scaled(70, im), w - 10, scaled(180, im)],
                     outline=(170, 176, 186), width=2)
        frame = slide.crop((drift, 0, drift + w, h))
        # the shot's caption where the base has it, still: only the slide drifts
        return draw_overlays(frame, None, band_text(base["script"], label))
    transform(base, out, change)


def wall_of_text(base: dict, out: Path) -> None:
    said = " ".join(t.get("text", "") for t in (base["voice"] or {}).get("takes") or []) or \
        " ".join(sh.get("narration", "") for sec in base["script"].get("sections") or []
                 for sh in sec.get("shots") or [])
    f = font("Regular", 11)

    def change(im, row, t, shot):
        w, h = im.size
        panel = Image.new("RGBA", im.size, (0, 0, 0, 0))
        px = ImageDraw.Draw(panel)
        top, bottom = scaled(TOP + 4, im), scaled(BOTTOM - 4, im)
        px.rectangle([20, top, w - 20, bottom], fill=(10, 12, 20, 120))
        y, line = top + 6, ""
        for word in (said + " " + said).split():
            if px.textlength(line + " " + word, font=f) > w - 56:
                px.text((28, y), line.strip(), font=f, fill=(222, 222, 222, 255))
                y, line = y + 13, ""
                if y > bottom - 16:
                    break
            line += " " + word
        return Image.alpha_composite(im.convert("RGBA"), panel).convert("RGB")
    transform(base, out, change)


def stand_in_base(base: dict) -> bool:
    """Whether the base is the drawn stand-in (its picture can be drawn
    again without overlays) rather than a captured, owner-labelled film."""
    return (base["expected"] or {}).get("label") != "owner" and \
        base["build"].get("title") == TITLE


def overlays_of(base: dict, label: str) -> tuple[str | None, str]:
    """The super (a section's start only) and caption text of a shot."""
    heading = heading_of(base["build"], label) if label in section_starts(base["build"]) \
        else None
    return heading, band_text(base["script"], label)


def bare(base: dict, t: float, shot: dict) -> Image.Image:
    """The stand-in's picture at `t` for `shot`, without its overlays."""
    index = next(n for n, s in enumerate(base["build"]["shots"]) if s is shot)
    return picture(t, shot, index)


def band_violation(base: dict, out: Path) -> None:
    drawn = stand_in_base(base)

    def change(im, row, t, shot):
        heading, term = overlays_of(base, str(row["label"]))
        frame = bare(base, t, shot) if drawn else im.copy()
        return draw_overlays(frame, heading, term, "violation")
    transform(base, out, change)


def illegible_type(base: dict, out: Path) -> None:
    drawn = stand_in_base(base)
    f = font("Regular", 9)

    def mean_of(im, box):
        return tuple(int(v) for v in ImageStat.Stat(
            im.crop(tuple(round(c) for c in box))).mean)

    def wash(im, box):
        region = im.crop(box)
        mean = tuple(int(v) for v in ImageStat.Stat(region).mean)
        soft = region.filter(ImageFilter.GaussianBlur(10))
        im.paste(Image.blend(soft, Image.new("RGB", region.size, mean), 0.5), box)
        return mean

    def change(im, row, t, shot):
        heading, term = overlays_of(base, str(row["label"]))
        if drawn:
            # each overlay box filled with the picture's own colour under
            # it, and its words at 9 px a shade off that: the rest of the
            # frame is the base's
            frame = bare(base, t, shot)
            px = ImageDraw.Draw(frame)
            for kind, box in overlay_boxes(heading, term).items():
                ground = mean_of(frame, box)
                px.rectangle(box, fill=ground)
                px.text((box[0] + 10, box[1] + 10), heading.upper() if kind == "super" else term,
                        font=f, fill=tuple(min(c + 16, 255) for c in ground))
            return frame
        w, h = im.size
        frame = im.copy()
        px = ImageDraw.Draw(frame)
        top = wash(frame, (0, 0, w, scaled(TOP, im)))
        bottom = wash(frame, (0, scaled(BOTTOM, im), w, h))
        if heading:
            px.text((28, scaled(24, im)), heading.upper(), font=f,
                    fill=tuple(min(c + 16, 255) for c in top))
        px.text((26, scaled(BOTTOM + 28, im)), term, font=f,
                fill=tuple(min(c + 16, 255) for c in bottom))
        return frame
    transform(base, out, change)


def placeholder_shot(base: dict) -> dict:
    """The shot the placeholder replaces: the one sampled nearest the middle
    of the film that is neither its hook nor its ending."""
    samples = base["samples"]
    middle = [r for r in samples if not qa._on_edge(r)] or samples
    mid = samples[len(samples) // 2]["t"]
    row = min(middle, key=lambda r: (abs(float(r["t"]) - float(mid)), float(r["t"])))
    return shot_of(base["build"], row["label"])


def placeholder_moving(base: dict, out: Path) -> dict:
    target = placeholder_shot(base)
    fps = qa._fps(base["build"])

    def change(im, row, t, shot):
        if shot is not target:
            return None
        w, h = im.size
        frame = im.copy()
        px = ImageDraw.Draw(frame)
        dx = round(1.2 * (round(t * fps) - round(float(shot["at"]) * fps)))
        x0, y0 = 30 + dx, scaled(TOP + 6, im)
        x1, y1 = x0 + w // 2, scaled(BOTTOM - 6, im)
        px.rectangle([x0, y0, x1, y1], fill=(128, 128, 128), outline=(200, 200, 200), width=2)
        px.line([(x0, y0), (x1, y1)], fill=(200, 200, 200), width=2)
        px.line([(x0, y1), (x1, y0)], fill=(200, 200, 200), width=2)
        f = font("Bold", 16)
        label = "PLACEHOLDER"
        tw = px.textlength(label, font=f)
        px.rectangle([(x0 + x1 - tw) / 2 - 6, (y0 + y1) / 2 - 12, (x0 + x1 + tw) / 2 + 6,
                      (y0 + y1) / 2 + 12], fill=(128, 128, 128))
        px.text(((x0 + x1 - tw) / 2, (y0 + y1) / 2 - 10), label, font=f, fill=(232, 232, 232))
        return frame
    transform(base, out, change)
    return target


def told(base: dict) -> list[str]:
    """The three shots tells-not-shows rewrites: the stand-in's middle
    section, else the three consecutive shots in the film's middle."""
    labels = [str(s["label"]) for s in sorted(base["build"]["shots"], key=lambda s: float(s["at"]))]
    if all(label in labels for label in TOLD):
        return list(TOLD)
    k = max(0, len(labels) // 2 - 1)
    return labels[k:k + 3]


def tells_not_shows(base: dict, out: Path) -> tuple[dict, dict | None, list[dict], list[str]]:
    """The base with three shots' lines rewritten (`TOLD`) in script.json,
    voice.json and the samples' `lines` (a captured pass carries each
    line's text, and the old words must not stand beside the new), and
    every frame the samples name copied as it is."""
    labels = told(base)
    lines = list(TOLD.values())
    rewrite = dict(zip(labels, lines))
    script = copy.deepcopy(base["script"])
    for sec in script.get("sections") or []:
        for shot in sec.get("shots") or []:
            if str(shot.get("label")) in rewrite:
                shot["narration"] = rewrite[str(shot["label"])]
    voice = copy.deepcopy(base["voice"])
    for take in (voice or {}).get("takes") or []:
        if str(take.get("label")) in rewrite:
            take["text"] = rewrite[str(take["label"])]
            take.pop("words", None)       # the word timings were the old line's
    samples = copy.deepcopy(base["samples"])
    for row in samples:
        label = str(row.get("label"))
        if label not in rewrite or not row.get("lines"):
            continue
        old = {qa._line_why(str(line.get("text", ""))) for line in row["lines"]}
        for line in row["lines"]:
            line["text"] = rewrite[label]
        new = qa._line_why(rewrite[label])
        why = [new if part in old else part for part in str(row.get("why", "")).split("; ")]
        row["why"] = "; ".join(dict.fromkeys(why))
    for row in base["samples"]:
        for name, _ in frames_of(row):
            dst = out / "frames" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(base["dir"] / "frames" / name, dst)
    return script, voice, samples, labels


def generic_identity(base: dict, out: Path) -> None:
    """The base's film as a stock explainer template: what it shows is the
    same (the Sun, a comet with its tail pointed away from it), but drawn
    as the clip art any explainer on any topic would use, in one card that
    is the same in every shot. Only the card slides, so every shot moves
    (no frozen check sees the fault). The overlays are drawn again where
    `overlays_of` puts them; a captured base's own are covered."""
    fps = qa._fps(base["build"])

    def change(im, row, t, shot):
        w, h = im.size
        frame = Image.new("RGB", (w, h), (232, 238, 246))
        px = ImageDraw.Draw(frame)
        px.rectangle([0, scaled(TOP, im), w, scaled(BOTTOM, im)], fill=(214, 224, 238))
        step = round(t * fps) - round(float(shot["at"]) * fps)
        x0 = round(w * 0.22) + round(0.6 * step)
        y0, y1 = scaled(TOP + 22, im), scaled(BOTTOM - 22, im)
        x1 = x0 + round(w * 0.46)
        px.rounded_rectangle([x0, y0, x1, y1], radius=14, fill=(244, 246, 250),
                             outline=(196, 208, 224), width=2)
        cy, r = (y0 + y1) // 2, max(10, (y1 - y0) // 6)
        px.ellipse([x0 + 24, cy - r, x0 + 24 + 2 * r, cy + r], fill=(250, 190, 60))
        hx = x1 - 3 * r
        px.ellipse([hx - r // 2, cy - r // 2, hx + r // 2, cy + r // 2], fill=(70, 110, 200))
        px.polygon([(hx + r // 2, cy - r // 3), (x1 - 14, cy - r), (x1 - 14, cy + r),
                    (hx + r // 2, cy + r // 3)], fill=(150, 176, 228))
        heading, term = overlays_of(base, str(row["label"]))
        return draw_overlays(frame, heading, term)
    transform(base, out, change)


def span(shot: dict) -> list[float]:
    return [round(float(shot["at"]), 3), round(float(shot["at"]) + float(shot["seconds"]), 3)]


def seeded(name: str, base: dict, out: Path) -> None:
    case = out / name
    build, script, voice, samples = base["build"], base["script"], base["voice"], base["samples"]
    expected = {"label": "seeded", "judge_only": True, "verdict": "NOT_READY",
                "under_bar": [], "over_bar": [], "must_find": [], "base": "good"}
    if name == "slide-drift":
        slide_drift(base, case)
        expected.update(under_bar=["animation_vfx", "cinematography"],
                        drop={"animation_vfx": 1.0, "cinematography": 1.0})
    elif name == "wall-of-text":
        wall_of_text(base, case)
        expected.update(under_bar=["graphics_overlays"], drop={"graphics_overlays": 1.0})
    elif name == "band-violation":
        band_violation(base, case)
        starts = sorted(section_starts(build), key=lambda l: float(shot_of(build, l)["at"]))
        expected.update(under_bar=["graphics_overlays"], drop={"graphics_overlays": 1.0},
                        must_find=[{"t_range": span(shot_of(build, label)),
                                    "owners": ["build", "vfx"]} for label in starts[:2]])
    elif name == "illegible-type":
        illegible_type(base, case)
        expected.update(under_bar=["graphics_overlays"], drop={"graphics_overlays": 1.0})
    elif name == "placeholder-moving":
        target = placeholder_moving(base, case)
        expected.update(under_bar=["visual_quality", "overall_production"],
                        drop={"overall_production": 1.0},
                        must_find=[{"t_range": span(target), "owners": ["vfx", "build"]}])
        build = copy.deepcopy(build)
        build["placeholders"] = []
    elif name == "tells-not-shows":
        script, voice, samples, labels = tells_not_shows(base, case)
        shots = [shot_of(build, label) for label in labels]
        expected.update(under_bar=["narrative_clarity"], drop={"narrative_clarity": 1.0},
                        must_find=[{"t_range": [span(shots[0])[0], span(shots[-1])[1]],
                                    "owners": ["script", "vfx"]}])
    elif name == "generic-identity":
        generic_identity(base, case)
        expected.update(under_bar=["editorial_identity"], drop={"editorial_identity": 1.0})
    write_case(case, build, script, voice, samples, expected)


SEEDED = ("slide-drift", "wall-of-text", "band-violation", "illegible-type",
          "placeholder-moving", "tells-not-shows", "generic-identity")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, help="the fixture tree to write")
    parser.add_argument("--base", help="a captured, owner-labelled case to seed from "
                                       "(default: draw the stand-in)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if args.base:
        src = Path(args.base).resolve()
        if src != (out / "good").resolve():
            if (out / "good").exists():
                shutil.rmtree(out / "good")
            shutil.copytree(src, out / "good")
    else:
        if (out / "good").exists():
            shutil.rmtree(out / "good")
        draw_stand_in(out / "good")
    for name in ("good-b", *SEEDED):
        if (out / name).exists():
            shutil.rmtree(out / name)
    base = load(out / "good")
    # good-b: the same film read through every other sample
    half = base["samples"][::2]
    for row in half:
        for name, _ in frames_of(row):
            dst = out / "good-b" / "frames" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(base["dir"] / "frames" / name, dst)
    write_case(out / "good-b", base["build"], base["script"], base["voice"], half,
               base["expected"])
    for name in SEEDED:
        seeded(name, base, out)
    print(json.dumps({"out": str(out), "cases": ["good", "good-b", *SEEDED]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

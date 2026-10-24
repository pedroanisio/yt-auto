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

- slide-drift: every frame a static slide with a slow drift (a Ken Burns
  move over a slide is motion no frozen check sees), the base's caption
  band kept;
- wall-of-text: a paragraph over the stage;
- band-violation: the super band and the caption band swapped;
- illegible-type: the bands' type redrawn at 9 px, in low contrast;
- placeholder-moving: one shot's frames a grey placeholder card drifting
  over the picture, with build.placeholders left empty;
- tells-not-shows: three shots' narration rewritten to tell, frames
  untouched.

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
#: tells-not-shows: what three shots say instead, telling the viewer what
#: the picture never demonstrates.
TOLD = {
    "2.1": "Scientists have worked out that this is very complicated, so take their word for it.",
    "2.2": "There is an invisible force here that is far too hard to show you.",
    "2.3": "Just remember that it is important and that it explains everything.",
}
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


def draw_good(t: float, shot: dict, index: int, heading: str | None, term: str) -> Image.Image:
    """One frame of the stand-in at film time `t`: a graded sky, the Sun's
    glow at the left, the comet crossing with its tail pointed away from
    the Sun, a super at a section's start and a lower-third term."""
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
    if heading:
        f = font("SemiBold", 22)
        width = px.textlength(heading.upper(), font=f)
        px.rectangle([16, 12, 16 + width + 24, 46], fill=(12, 14, 24))
        px.text((28, 15), heading.upper(), font=f, fill=(236, 236, 236))
    f = font("Medium", 15)
    width = px.textlength(term, font=f)
    px.rectangle([16, BOTTOM + 18, 16 + width + 20, BOTTOM + 46], fill=(12, 14, 24))
    px.text((26, BOTTOM + 23), term, font=f, fill=(232, 232, 226))
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


def slide_drift(base: dict, out: Path) -> None:
    fps = qa._fps(base["build"])

    def change(im, row, t, shot):
        w, h = im.size
        drift = round(0.6 * (round(t * fps) - round(float(shot["at"]) * fps)))
        slide = Image.new("RGB", (w + 120, h))
        px = ImageDraw.Draw(slide)
        for y in range(h):
            v = int(34 + 30 * y / (h - 1))
            px.line([(0, y), (w + 120, y)], fill=(v, v + 4, v + 12))
        heading = heading_of(base["build"], row["label"]) or "Overview"
        px.text((104, scaled(64, im)), heading, font=font("Bold", 24), fill=(232, 232, 232))
        words = band_text(base["script"], str(row["label"])).title()
        for k, line in enumerate((f"- {words}", "- Key facts on this slide",
                                  "- Read the points below")):
            px.text((116, scaled(104 + 26 * k, im)), line, font=font("Regular", 16),
                    fill=(206, 210, 216))
        px.rectangle([w - 130, scaled(70, im), w - 10, scaled(180, im)],
                     outline=(170, 176, 186), width=2)
        frame = slide.crop((drift, 0, drift + w, h))
        top = scaled(BOTTOM, im)
        frame.paste(im.crop((0, top, w, h)), (0, top))
        return frame
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


def band_violation(base: dict, out: Path) -> None:
    def change(im, row, t, shot):
        w, h = im.size
        band = scaled(H - BOTTOM, im)
        top, bottom = im.crop((0, 0, w, band)), im.crop((0, h - band, w, h))
        frame = im.copy()
        frame.paste(bottom, (0, 0))
        frame.paste(top, (0, h - band))
        return frame
    transform(base, out, change)


def illegible_type(base: dict, out: Path) -> None:
    starts = section_starts(base["build"])
    f = font("Regular", 9)

    def wash(im, box):
        region = im.crop(box)
        mean = tuple(int(v) for v in ImageStat.Stat(region).mean)
        soft = region.filter(ImageFilter.GaussianBlur(10))
        im.paste(Image.blend(soft, Image.new("RGB", region.size, mean), 0.5), box)
        return mean

    def change(im, row, t, shot):
        w, h = im.size
        frame = im.copy()
        px = ImageDraw.Draw(frame)
        top = wash(frame, (0, 0, w, scaled(TOP, im)))
        bottom = wash(frame, (0, scaled(BOTTOM, im), w, h))
        if str(row["label"]) in starts:
            px.text((28, scaled(24, im)), heading_of(base["build"], row["label"]).upper(),
                    font=f, fill=tuple(min(c + 16, 255) for c in top))
        px.text((26, scaled(BOTTOM + 28, im)), band_text(base["script"], str(row["label"])),
                font=f, fill=tuple(min(c + 16, 255) for c in bottom))
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


def tells_not_shows(base: dict, out: Path) -> tuple[dict, dict | None, list[str]]:
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
    for name in (base["dir"] / "frames").rglob("*.png"):
        dst = out / "frames" / name.relative_to(base["dir"] / "frames")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(name, dst)
    return script, voice, labels


def span(shot: dict) -> list[float]:
    return [round(float(shot["at"]), 3), round(float(shot["at"]) + float(shot["seconds"]), 3)]


def seeded(name: str, base: dict, out: Path) -> None:
    case = out / name
    build, script, voice = base["build"], base["script"], base["voice"]
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
        script, voice, labels = tells_not_shows(base, case)
        shots = [shot_of(build, label) for label in labels]
        expected.update(under_bar=["narrative_clarity"], drop={"narrative_clarity": 1.0},
                        must_find=[{"t_range": [span(shots[0])[0], span(shots[-1])[1]],
                                    "owners": ["script"]}])
    write_case(case, build, script, voice, base["samples"], expected)


SEEDED = ("slide-drift", "wall-of-text", "band-violation", "illegible-type",
          "placeholder-moving", "tells-not-shows")


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

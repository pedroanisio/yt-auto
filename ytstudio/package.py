"""Write and check the upload set: what an operator uploads to YouTube by hand.

A gated, QA'd video.mp4 is not yet an upload. `yt package` writes the rest
of it into `work/<slug>/package/` and checks it against YouTube's limits:

    yt package --title euv                       # write it, check it, say ready or not
    yt package --title euv --reviewer-model gpt-5.5   # an opus author's reviewer

    package/package.json     title, description, chapters, tags, thumbnail,
                             credits, sources, the digests judged, findings, verdict
    package/description.txt  the description, as it is pasted
    package/thumbnail.png    one frame of the film, rendered at 1280x720
    package/captions.vtt     a copy of narration.vtt
    package/UPLOAD.md        the checklist, in YouTube Studio's order

Code assembles everything that must be true: the chapter times from the
section markers in scene.xml, the credit lines from build.json's `credits`
(as the end-credits card prints them), the sources and their URLs from the
dossier claims the screenplay's shots cite, and the frames a thumbnail may
be taken from. A model only words the title, a two-sentence hook, one title
per chapter and the tags, and picks one of those frames ("package-draft",
--model); another family reads that wording against the narration
("package-review", --reviewer-model), and a blocking flag earns one revision
(--rounds). Every check that fails is an `error:` finding and the verdict is
"not ready", and the command exits non-zero.

Nothing is uploaded, scheduled or sent anywhere: the upload is the
operator's, in YouTube Studio, from UPLOAD.md. Not a stage `yt run` walks.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import shutil
import struct
import sys
from pathlib import Path
from xml.etree import ElementTree

from . import build as build_stage
from . import doctor, flags, house, render, reviewers, spend, workspace
from .core import _key, read_json, write_json
from .harness import Harness, HarnessError, brief
from .vfx import REVIEWER_MODEL

#: Where the upload set lives, inside the project.
PACKAGE_DIR = "package"

# YouTube's limits, as yt reads them. TO VERIFY against YouTube's current
# published rules before relying on them: each is a number on a help page,
# not something this code can measure.
TITLE_MAX = 100                    # TO VERIFY: characters in a title
DESCRIPTION_MAX = 5000             # TO VERIFY: characters in a description
TAGS_MAX = 500                     # TO VERIFY: characters of all tags together
CHAPTER_MIN_SECONDS = 10           # TO VERIFY: the shortest chapter shown
CHAPTER_MIN_COUNT = 3              # TO VERIFY: the fewest chapters shown
THUMB_WIDTH, THUMB_HEIGHT = 1280, 720      # TO VERIFY: the smallest custom thumbnail
THUMB_MAX_BYTES = 2 * 1024 * 1024  # TO VERIFY: the largest custom thumbnail

#: The most frames offered to the model as thumbnails.
THUMB_CANDIDATES_MAX = 24

#: What YouTube refuses in a description (TO VERIFY), and what is printed instead.
BRACKETS = {"<": "‹", ">": "›"}

JPEG_STEP = "export thumbnail.png as JPEG outside yt and upload that"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------- #
# what code reads from the film
# --------------------------------------------------------------------------- #

def _parse(scene_bytes: bytes):
    try:
        return ElementTree.fromstring(scene_bytes)
    except ElementTree.ParseError:
        return None


def credits_start(root) -> float | None:
    """When the end-credits card begins: the first marker build labelled
    "credits" (build.CREDITS_LABEL), else None. Never a section or a beat."""
    if root is None:
        return None
    times = []
    for marker in root.iter("marker"):
        if (marker.get("label") == build_stage.CREDITS_LABEL
                and marker.get("kind") not in ("section", "beat")):
            try:
                times.append(float(marker.get("time") or 0))
            except ValueError:
                continue
    return min(times) if times else None


def _sections(root) -> list[tuple[float, str]]:
    out = []
    for marker in root.iter("marker") if root is not None else ():
        if marker.get("kind") == "section":
            try:
                out.append((float(marker.get("time") or 0), marker.get("label", "")))
            except ValueError:
                continue
    return sorted(out)


def card_text(root) -> str:
    """Every line the end-credits cards print (build.CREDITS_ASSET text assets)."""
    if root is None:
        return ""
    return "\n".join(str(t.get("text") or "") for t in root.iter("text")
                     if str(t.get("id", "")).startswith(build_stage.CREDITS_ASSET))


def clock(seconds: float) -> str:
    """A description timestamp: m:ss, or h:mm:ss from an hour."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def chapters_from_scene(scene_bytes: bytes, duration: float) -> tuple[list[dict], list[str]]:
    """The chapters, from the document's `kind="section"` markers: (chapters,
    warnings), each chapter {at, title} in whole seconds, titled with the
    section's heading. The first is forced to 0:00; one shorter than
    CHAPTER_MIN_SECONDS merges into the one before it (the first, into the
    one after); the last ends where the credits card begins, else at
    `duration`. Fewer than CHAPTER_MIN_COUNT left is no chapters and a
    warning: YouTube would show none of them."""
    root = _parse(scene_bytes)
    end = credits_start(root)
    end = float(duration) if end is None else end
    chapters = [{"at": int(at), "title": label} for at, label in _sections(root) if at < end]
    if chapters:
        chapters[0]["at"] = 0
    while len(chapters) > 1:
        bounds = [c["at"] for c in chapters[1:]] + [end]
        short = next((n for n, (c, stop) in enumerate(zip(chapters, bounds))
                      if stop - c["at"] < CHAPTER_MIN_SECONDS), None)
        if short is None:
            break
        del chapters[short or 1]
    if len(chapters) < CHAPTER_MIN_COUNT:
        return [], [f"warning: chapters: the sections give {len(chapters)} chapter(s) of at "
                    f"least {CHAPTER_MIN_SECONDS} s before the credits, and YouTube shows "
                    f"chapters only from {CHAPTER_MIN_COUNT}; the description lists none"]
    return chapters, []


def _shots(script: dict):
    for section in (script or {}).get("sections") or []:
        for shot in (section or {}).get("shots") or []:
            if isinstance(shot, dict):
                yield shot


def sources_from_script(script: dict, dossier: dict) -> tuple[list[dict], list[str]]:
    """The sources the film cites, in the order its shots first cite them:
    (rows, warnings), each row {title, url}. A dossier claim is cited by a
    shot when its source_url is in the shot's `source`, or when its
    source_title's comparison key (core._key, as build.match_media matches)
    is a non-empty part of the source's. Rows are one per URL. A source no
    claim matches is a row with no URL and a warning naming its shots."""
    claims = [c for c in (dossier or {}).get("claims") or [] if isinstance(c, dict)]
    rows: list[dict] = []
    seen: set[str] = set()
    unmatched: dict[str, list[str]] = {}
    for shot in _shots(script):
        text = " ".join(str(shot.get("source") or "").split())
        if not text:
            continue
        hits = [c for c in claims
                if str(c.get("source_url") or "").strip()
                and str(c["source_url"]).strip() in text]
        if not hits:
            key = _key(text)
            hits = [c for c in claims if _key(str(c.get("source_title") or ""))
                    and _key(str(c["source_title"])) in key]
        if not hits:
            if text not in unmatched:
                unmatched[text] = []
                if ("text:" + _key(text)) not in seen:
                    seen.add("text:" + _key(text))
                    rows.append({"title": text, "url": ""})
            unmatched[text].append(str(shot.get("label") or "?"))
            continue
        for claim in hits:
            url = str(claim.get("source_url") or "").strip()
            title = " ".join(str(claim.get("source_title") or "").split()) or url
            key = url or "title:" + _key(title)
            if key not in seen:
                seen.add(key)
                rows.append({"title": title, "url": url})
    warnings = [f"warning: sources: shot {', '.join(labels)} cites {text!r}, which no dossier "
                "claim's source_url or source_title matches; it is listed without a link "
                "(name the claim's source in the screenplay, or add it to the research)"
                for text, labels in unmatched.items()]
    return rows, warnings


def thumbnail_candidates(scene_bytes: bytes, script: dict | None = None) -> list[dict]:
    """The frames a thumbnail may be taken from: every section marker's time
    and every beat's midpoint (build.shots_from_scene), rounded to 0.1 s,
    one each, none inside the credits card or past the film, at most
    THUMB_CANDIDATES_MAX spread evenly over it. Each is {at, label, section,
    visual}: the shot on screen, its section and the screenplay's `visual`
    for it, so a model can choose without seeing a frame."""
    root = _parse(scene_bytes)
    if root is None:
        return []
    project = root.find("project")
    try:
        duration = float((project.get("duration") if project is not None else 0) or 0)
    except ValueError:
        duration = 0.0
    stop = credits_start(root)
    stop = duration if stop is None else stop
    sections = _sections(root)
    beats = build_stage.shots_from_scene(scene_bytes)
    times = [at for at, _ in sections] + [b["at"] + b["seconds"] / 2 for b in beats]
    ats = sorted({round(t, 1) for t in times if 0 <= t < stop})
    if len(ats) > THUMB_CANDIDATES_MAX:
        last = len(ats) - 1
        ats = [ats[round(n * last / (THUMB_CANDIDATES_MAX - 1))]
               for n in range(THUMB_CANDIDATES_MAX)]
    visuals = {str(s.get("label")): str(s.get("visual") or "") for s in _shots(script or {})}
    out = []
    for at in ats:
        beat = next((b for b in reversed(beats) if b["at"] <= at + 1e-6), None)
        label = str(beat["label"]) if beat else ""
        section = next((name for when, name in reversed(sections) if when <= at + 1e-6), "")
        out.append({"at": at, "label": label, "section": section,
                    "visual": visuals.get(label, "")})
    return out


# --------------------------------------------------------------------------- #
# the description
# --------------------------------------------------------------------------- #

def bracketed(text: str) -> str:
    """`text` as the description prints it: angle brackets replaced."""
    for raw, shown in BRACKETS.items():
        text = text.replace(raw, shown)
    return text


def credit_source(row: dict) -> str:
    """Where a credit line is corrected: the file the build read it from."""
    src, ident = str(row.get("src") or ""), str(row.get("id") or "")
    if src.startswith("props/"):
        return "its credit in props.json"
    if ident.startswith("cue-asset-") or src.startswith("sound/"):
        return "the cue's credit in sound.json"
    return "the dossier asset's credit_line in dossier.json"


def assemble_description(hook: str, chapters: list[dict], sources: list[dict],
                         credits: list[dict]) -> tuple[str, list[str]]:
    """The description: the hook, then "Chapters" (m:ss title), "Sources"
    (title, then its URL) and "Credits" (each line verbatim), a block left
    out when it has nothing. Angle brackets, which YouTube refuses in a
    description, are printed as U+2039 and U+203A; each replacement is a
    warning naming the field to correct at its source: (text, warnings)."""
    warnings: list[str] = []

    def shown(text: str, what: str, fix: str) -> str:
        text = " ".join(str(text or "").split())
        if any(raw in text for raw in BRACKETS):
            warnings.append(f"warning: description: {what} {text!r} holds an angle bracket, "
                            f"which YouTube refuses; it is printed as ‹ ›: correct "
                            f"{fix}")
        return bracketed(text)

    blocks = []
    if str(hook or "").strip():
        blocks.append(shown(hook, "the hook", "the hook (yt package --fresh)"))
    if chapters:
        blocks.append("\n".join(["Chapters"] + [
            f"{clock(c['at'])} {shown(c['title'], 'chapter title', 'the chapter title')}"
            for c in chapters]))
    if sources:
        lines = ["Sources"]
        for row in sources:
            fix = ("the claim's source_title in dossier.json" if row.get("url")
                   else "the shot's source in script.json")
            title = shown(row.get("title", ""), "source", fix)
            url = shown(row.get("url", ""), "source URL", "the claim's source_url")
            lines.append(f"{title} — {url}" if url else title)
        blocks.append("\n".join(lines))
    if credits:
        blocks.append("\n".join(["Credits"] + [
            shown(row.get("credit", ""), "credit line", credit_source(row))
            for row in credits]))
    return "\n\n".join(blocks) + "\n", warnings


# --------------------------------------------------------------------------- #
# the thumbnail
# --------------------------------------------------------------------------- #

def png_size(data: bytes | None) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR chunk; None when it is not a PNG."""
    if not data or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR" or len(data) < 24:
        return None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def thumbnail_problems(data: bytes | None) -> list[str]:
    """The thumbnail against YouTube's limits (TO VERIFY): at least
    THUMB_WIDTH x THUMB_HEIGHT at 16:9, at most THUMB_MAX_BYTES. yt never
    re-encodes; an oversized frame is the operator's JPEG export."""
    if not data:
        return ["error: thumbnail: no thumbnail.png was rendered; yt package again on a "
                "machine that reaches a render host"]
    size = png_size(data)
    if size is None:
        return ["error: thumbnail: thumbnail.png is not a PNG"]
    out = []
    width, height = size
    if width < THUMB_WIDTH or height < THUMB_HEIGHT or width * 9 != height * 16:
        out.append(f"error: thumbnail: thumbnail.png is {width}x{height}; YouTube takes at "
                   f"least {THUMB_WIDTH}x{THUMB_HEIGHT} at 16:9")
    if len(data) > THUMB_MAX_BYTES:
        out.append(f"error: thumbnail: thumbnail.png is {len(data) / 1024 / 1024:.1f} MB and "
                   f"YouTube takes at most {THUMB_MAX_BYTES // (1024 * 1024)} MB: {JPEG_STEP}")
    return out


_PROJECT = re.compile(rb"<project\b[^>]*>")


def _project_size(root) -> tuple[int, int] | None:
    project = root.find("project") if root is not None else None
    try:
        return int(float(project.get("width"))), int(float(project.get("height")))
    except (AttributeError, TypeError, ValueError):
        return None


def at_thumbnail_size(scene_bytes: bytes) -> bytes:
    """The document with its <project> width and height rewritten to
    THUMB_WIDTH x THUMB_HEIGHT and every other byte as it was."""
    def resize(match: re.Match) -> bytes:
        tag = match.group(0)
        tag = re.sub(rb'\bwidth="[^"]*"', b'width="%d"' % THUMB_WIDTH, tag, count=1)
        return re.sub(rb'\bheight="[^"]*"', b'height="%d"' % THUMB_HEIGHT, tag, count=1)
    return _PROJECT.sub(resize, scene_bytes, count=1)


def render_thumbnail(scene_bytes: bytes, at: float, root: Path, *,
                     host: str | None = None) -> tuple[bytes | None, list[str]]:
    """One frame at `at`, rendered by the engine at final quality from a copy
    of the document sized THUMB_WIDTH x THUMB_HEIGHT (engine_render_still
    takes no size; the same 16:9 is rescaled, TO VERIFY on a render host):
    (png or None, findings). A build that is not 16:9 is refused unrendered."""
    size = _project_size(_parse(scene_bytes))
    if size is None or size[0] * 9 != size[1] * 16:
        built = f"{size[0]}x{size[1]}" if size else "of no readable size"
        return None, [f"error: thumbnail: thumbnail must be 16:9; the build is {built}"]
    doc = at_thumbnail_size(scene_bytes)
    text = doc.decode("utf-8", "replace")
    host = build_stage.look_host(text) if host is None else host
    try:
        data, message = render.engine_render_still(
            doc, at, host=host, quality="final", files=build_stage.shipped_with(text, root))
    except Exception as exc:          # a host that hung up, a timeout: the package says so
        return None, [f"error: thumbnail: the still at {at:g} s failed: {exc}"]
    if data is None:
        first = (str(message or "").splitlines() or ["no frame came back"])[0]
        return None, [f"error: thumbnail: the still at {at:g} s was not rendered: {first}"]
    return data, []


# --------------------------------------------------------------------------- #
# the check
# --------------------------------------------------------------------------- #

def _sha256(data) -> str:
    """A byte string's sha256, or a file's (read in blocks, as doctor does)."""
    if isinstance(data, (bytes, bytearray)):
        return hashlib.sha256(data).hexdigest()
    return doctor.scene_digest(Path(data))


def chapter_problems(chapters: list[dict], root, duration: float) -> list[str]:
    """Chapters as YouTube reads them (TO VERIFY): the first at 0:00, then
    ascending, each at least CHAPTER_MIN_SECONDS to the next or to the
    credits, at least CHAPTER_MIN_COUNT of them, the last before the end."""
    if not chapters:
        return []
    out = []
    try:
        ats = [float(c.get("at")) for c in chapters]
    except (TypeError, ValueError):
        return ["error: chapters: a chapter has no time"]
    end = credits_start(root)
    end = float(duration) if end is None else end
    if ats[0] != 0:
        out.append(f"error: chapters: the first chapter starts at {clock(ats[0])}, not 0:00")
    if len(chapters) < CHAPTER_MIN_COUNT:
        out.append(f"error: chapters: {len(chapters)} chapter(s); YouTube shows chapters only "
                   f"from {CHAPTER_MIN_COUNT}")
    for n, (at, stop) in enumerate(zip(ats, ats[1:] + [end])):
        if stop <= at:
            out.append(f"error: chapters: chapter {n + 1} at {clock(at)} does not start "
                       "before the next one or the end")
        elif stop - at < CHAPTER_MIN_SECONDS:
            out.append(f"error: chapters: chapter {n + 1} at {clock(at)} lasts "
                       f"{stop - at:g} s; YouTube needs at least {CHAPTER_MIN_SECONDS} s")
    return out


def check_package(pkg: dict, scene_bytes: bytes, video_bytes, duration: float, *,
                  build: dict | None = None, thumbnail: bytes | None = None) -> list[str]:
    """Everything an upload needs, as `error:` / `warning:` findings; any
    error makes the package "not ready". `video_bytes` is the video's bytes
    or its path; `build` is build.json, `thumbnail` the PNG's bytes."""
    out = []
    root = _parse(scene_bytes)
    title = str(pkg.get("title") or "")
    if not 1 <= len(title) <= TITLE_MAX:
        out.append(f"error: title: {len(title)} characters; YouTube takes 1 to {TITLE_MAX}")
    description = str(pkg.get("description") or "")
    if len(description) > DESCRIPTION_MAX:
        out.append(f"error: description: {len(description)} characters; YouTube takes at "
                   f"most {DESCRIPTION_MAX}")
    if any(raw in description for raw in BRACKETS):
        out.append("error: description: it holds a raw angle bracket (< or >), which YouTube "
                   "refuses; yt package again rather than editing description.txt by hand")
    tags = [str(t) for t in pkg.get("tags") or []]
    if len(",".join(tags)) > TAGS_MAX:
        out.append(f"error: tags: {len(','.join(tags))} characters together; YouTube takes "
                   f"at most {TAGS_MAX}")
    out += chapter_problems(list(pkg.get("chapters") or []), root, duration)
    printed = card_text(root)
    for row in pkg.get("credits") or []:
        line = str(row.get("credit") or "")
        if not row.get("required") or not line:
            continue
        if bracketed(line) not in description:
            out.append(f"error: credit: {line!r} ({row.get('src')}) is owed under "
                       f"{row.get('licence')} and missing from the description; yt package again")
        if line not in printed:
            out.append(f"error: credit: {line!r} ({row.get('src')}) is owed under "
                       f"{row.get('licence')} and missing from the credits card in scene.xml; "
                       "yt build again")
    out += thumbnail_problems(thumbnail)
    if pkg.get("scene_sha256") != _sha256(scene_bytes):
        out.append("error: digest: the package was made for another scene.xml than the one "
                   "now built; yt package again")
    if pkg.get("video_sha256") != _sha256(video_bytes):
        out.append("error: digest: the package was made for another video.mp4 than the one "
                   "now rendered; yt package again")
    if not (build or {}).get("publishable"):
        errors = len((build or {}).get("credit_errors") or [])
        out.append("error: build: build.json is not publishable"
                   + (f" ({errors} credit error(s))" if errors else "")
                   + "; fix its findings and yt build, render, gate and qa again")
    return out


# --------------------------------------------------------------------------- #
# the model's words
# --------------------------------------------------------------------------- #

DRAFT_ROLE = (
    "You word a finished documentary film's YouTube upload: its title, a hook "
    "paragraph of at most two sentences that opens the description, one title "
    "per chapter, and its tags; and you choose the thumbnail from the frames "
    "offered. Promise only what the narration delivers and state no claim the "
    "narration does not make. A chapter title names what the chapter is about "
    "without giving away its reveal. No clickbait, no capitals for shouting, no "
    "angle brackets. The times, the sources, the URLs and the credit lines are "
    "assembled by code and are not yours to write."
)

REVIEW_ROLE = (
    "You review the wording of a documentary film's YouTube upload against its "
    "narration, as an editor who will not let a package oversell. Flag only "
    "three things: a title or hook that promises what the narration never "
    "delivers, a claim the title, hook, chapter titles or tags state that the "
    "narration does not make, and a chapter title that gives a reveal away "
    "before the film reaches it. A flag that must change before upload is "
    "blocking; anything else is minor. No flags is a valid answer."
)

FIELDS = ("title", "hook", "chapter_titles", "tags")


def draft_schema(chapters: list[dict], candidates: list[dict]) -> dict:
    count = len(chapters)
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": (
                f"at most {TITLE_MAX} characters: what the film shows, as its narration "
                "delivers it")},
            "hook": {"type": "string", "description": (
                "at most two sentences opening the description: the question the film "
                "answers and why it matters, in the narration's own terms")},
            "chapter_titles": {"type": "array", "items": {"type": "string"},
                               "minItems": count, "maxItems": count,
                               "description": f"exactly {count}: one per chapter, in order"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": (
                f"search terms the film is about, {TAGS_MAX} characters at most together")},
            "thumbnail_at": {"type": "number", "enum": [c["at"] for c in candidates],
                             "description": "the time of the one offered frame to use"},
        },
        "required": ["title", "hook", "chapter_titles", "tags", "thumbnail_at"],
    }


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "flags": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": list(FIELDS)},
                "problem": {"type": "string", "description": (
                    "what is promised, claimed or given away, quoting the words")},
                "severity": {"type": "string", "enum": ["blocking", "minor"]},
                "fix": {"type": "string", "description": "the wording that would hold"}},
            "required": ["field", "problem", "severity", "fix"]}},
    },
    "required": ["flags"],
}


def film_brief(idea, script: dict, styles: dict, chapters: list[dict],
               candidates: list[dict]) -> dict:
    """What the author and the reviewer read: the promise, the story, the
    narration by shot, the chapters and the frames on offer."""
    return {
        "idea": {"title": idea.title, "promise": getattr(idea, "promise", "") or ""},
        "story": {k: script.get(k, "") for k in ("promise", "payoff", "train", "stakes")
                  if script.get(k)},
        "thumbnail_direction": str((styles or {}).get("thumbnail") or ""),
        "narration": [{"label": s.get("label", ""), "line": s.get("narration", "")}
                      for s in _shots(script) if s.get("narration")],
        "chapters": [{"at": clock(c["at"]), "section": c["title"]} for c in chapters],
        "thumbnail_candidates": candidates,
    }


def _refused(draft, chapters: list[dict], candidates: list[dict]) -> str:
    """Why code will not take a draft, or "": a thumbnail time it did not
    offer, or a chapter title count that is not the chapter count."""
    offered = [c["at"] for c in candidates]
    try:
        at = float(draft.get("thumbnail_at"))
    except (TypeError, ValueError):
        at = math.nan
    if not any(math.isclose(at, o, abs_tol=1e-6) for o in offered):
        return (f"thumbnail_at {draft.get('thumbnail_at')!r} is not one of the "
                f"{len(offered)} candidate times")
    titles = draft.get("chapter_titles")
    if not isinstance(titles, list) or len(titles) != len(chapters):
        got = len(titles) if isinstance(titles, list) else 0
        return f"{got} chapter title(s) for {len(chapters)} chapter(s)"
    return ""


def _draft(author: Harness, name: str, prompt: str, chapters, candidates) -> dict:
    answer = author.decide(name, prompt, draft_schema(chapters, candidates))
    if why := _refused(answer, chapters, candidates):
        author.forget(name)       # kept, it would be served again and refused again
        raise HarnessError(f"[package] {name}: refused: {why}")
    return answer


def _wording(answer: dict) -> dict:
    return {k: answer.get(k) for k in FIELDS}


def decide_package(author: Harness, reviewer: Harness, film: dict, chapters: list[dict],
                   candidates: list[dict], *, rounds: int = 1) -> dict:
    """The draft, its review and up to `rounds` revisions: {draft, flags,
    rounds, findings}. A blocking flag still standing after the last round is
    an `error:` finding; a minor one a `warning:`. An author that cannot
    answer raises HarnessError; a reviewer that cannot is an error finding,
    since the wording then went unread."""
    base = brief("The film", film)
    answer = _draft(author, "package-draft", "\n\n".join([
        base, "Word the upload and choose the thumbnail. Answer in the schema."]),
        chapters, candidates)

    def review(name: str) -> list[dict] | None:
        try:
            got = reviewer.decide(name, "\n\n".join([
                base, brief("The upload's wording", _wording(answer)),
                "Flag what the narration does not support. Answer in the schema."]),
                REVIEW_SCHEMA)
        except HarnessError as exc:
            findings.append(f"error: package-review: the review could not be asked ({exc}); "
                            "the wording is unread")
            return None
        return [f for f in got.get("flags") or [] if isinstance(f, dict)]

    findings: list[str] = []
    flagged = review("package-review")
    done = 0
    while flagged and done < rounds and any(f.get("severity") == "blocking" for f in flagged):
        done += 1
        blocking = [f for f in flagged if f.get("severity") == "blocking"]
        answer = _draft(author, f"package-revise-{done}", "\n\n".join([
            base, brief("Your draft", _wording(answer)),
            brief("The reviewer's blocking flags", blocking),
            "Revise the draft so every blocking flag is answered, changing nothing else. "
            "Answer in the schema."]), chapters, candidates)
        flagged = review(f"package-review-{done}")
    for flag in flagged or []:
        level = "error" if flag.get("severity") == "blocking" else "warning"
        findings.append(f"{level}: package-review: {flag.get('field')}: {flag.get('problem')}"
                        f" (fix: {flag.get('fix')})")
    return {"draft": answer, "flags": flagged or [], "rounds": done, "findings": findings}


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #

def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "package", parents=list(parents),
        help="write and check the YouTube upload set; nothing is uploaded",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    flags.reviewer_model(p, default=None, help=(
        f"the model that reads the wording against the narration (default "
        f"{REVIEWER_MODEL}; never --model's own family)"))
    flags.rounds(p, default=1, help="revisions a blocking review flag earns (default 1)")
    flags.fresh(p, help="ask the model again even if the questions did not change")
    p.set_defaults(run=run)
    return p


def upload_md(pkg: dict, project: workspace.Project) -> str:
    """The operator's checklist, in YouTube Studio's order."""
    thumb = pkg.get("thumbnail") or {}
    errors = [f for f in pkg.get("findings") or [] if str(f).startswith("error:")]
    lines = [
        f"# Upload: {pkg.get('title', '')}",
        "",
        f"Verdict: **{pkg.get('verdict')}**"
        + (f" ({len(errors)} error(s) below; do not upload)" if errors else ""),
        "",
        "yt uploads nothing. In YouTube Studio, Create > Upload videos, then:",
        "",
        f"1. File: `{project.path('video')}` (sha256 `{pkg.get('video_sha256', '')}`).",
        "2. Title: copy `title` from package.json:",
        "",
        f"   {pkg.get('title', '')}",
        "",
        "3. Description: paste description.txt whole. Its chapters, sources and "
        "credits are the film's; edit them at their source and run `yt package` again.",
        f"4. Thumbnail: upload thumbnail.png ({thumb.get('width')}x{thumb.get('height')}, "
        f"{thumb.get('bytes')} bytes, the frame at {thumb.get('at')} s). Over 2 MB: "
        f"{JPEG_STEP}.",
        "5. Playlists and Audience: the operator's choice (yt does not decide whether the "
        "film is made for kids).",
        "6. Show more > Tags: paste " + ", ".join(pkg.get("tags") or []),
        "7. Show more > Language and captions > Subtitles: upload captions.vtt.",
        "8. Video elements: end screens and cards are not made by yt.",
        "9. Checks, then Visibility: the operator's.",
    ]
    if pkg.get("findings"):
        lines += ["", "## Findings", ""] + [f"- {f}" for f in pkg["findings"]]
    return "\n".join(lines) + "\n"


def run(args) -> int:
    # A model never grades its own work: refused before anything is read or paid.
    reviewer_model = getattr(args, "reviewer_model", None) or REVIEWER_MODEL
    if reviewers.is_gateway(reviewer_model):
        print(f"error: --reviewer-model {reviewer_model}: the gateway reviewer pool is not "
              "wired to yt package; pass a claude or codex model", file=sys.stderr)
        return 2
    if own := flags.own_reviewer(args.model, reviewer_model):
        print(own, file=sys.stderr)
        if not getattr(args, "reviewer_model", None):
            print(f"  yt package's reviewer is {REVIEWER_MODEL} by default: pass "
                  f"--reviewer-model sonnet, or a codex model, with --model {args.model}",
                  file=sys.stderr)
        return 2
    idea, project = workspace.resolve(args)
    scene_file, video_file = project.path("scene"), project.path("video")
    if not scene_file.is_file():
        print(f"error: no scene.xml in {project.root}; run `yt build` first", file=sys.stderr)
        return 1
    if not video_file.is_file():
        print(f"error: no video.mp4 in {project.root}; run `yt render --quality final "
              "--strict` first", file=sys.stderr)
        return 1
    built = read_json(project.path("build"), warn=False) or {}
    scene = scene_file.read_bytes()
    size = _project_size(_parse(scene))
    duration = float((built.get("timeline") or {}).get("runtime")
                     or (built.get("scene") or {}).get("duration") or 0)
    script = read_json(project.path("script"), warn=False) or {}
    dossier = read_json(project.path("dossier"), warn=False) or {}
    styles = read_json(project.path("styles"), warn=False) or {}

    chapters, findings = chapters_from_scene(scene, duration)
    sources, warned = sources_from_script(script, dossier)
    findings += warned
    candidates = thumbnail_candidates(scene, script)
    if not candidates:
        print("error: scene.xml offers no frame to take a thumbnail from (no markers and "
              "no duration); run `yt build` again", file=sys.stderr)
        return 1
    credits = [r for r in built.get("credits") or [] if isinstance(r, dict)]

    rules, digest = house.for_args(args, project, "package")
    verbose = not args.quiet
    author = Harness(project.root, "package", model=args.model, role=DRAFT_ROLE,
                     house=rules, house_digest=digest, fresh=args.fresh, verbose=verbose)
    reader = Harness(project.root, "package", model=reviewer_model, role=REVIEW_ROLE,
                     house=rules, house_digest=digest, usage=author.usage,
                     decisions=author.decisions, lock=author.lock, fresh=args.fresh,
                     verbose=verbose)
    try:
        decided = decide_package(author, reader, film_brief(idea, script, styles, chapters,
                                                            candidates),
                                 chapters, candidates, rounds=max(0, int(args.rounds)))
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    draft = decided["draft"]
    findings += decided["findings"]
    titled = [{"at": c["at"], "title": " ".join(str(t or "").split()) or c["title"]}
              for c, t in zip(chapters, draft.get("chapter_titles") or [])]
    description, warned = assemble_description(str(draft.get("hook") or ""), titled,
                                                sources, credits)
    findings += warned
    tags = list(dict.fromkeys(" ".join(str(t).split()) for t in draft.get("tags") or []
                              if str(t).strip()))
    at = float(draft["thumbnail_at"])

    folder = project.root / PACKAGE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    png, rendered = render_thumbnail(scene, at, project.root)
    findings += rendered
    if png:
        (folder / "thumbnail.png").write_bytes(png)
    else:
        (folder / "thumbnail.png").unlink(missing_ok=True)
    captions = project.path("captions")
    if captions.is_file():
        shutil.copyfile(captions, folder / "captions.vtt")
    else:
        findings.append("warning: captions: no narration.vtt; the upload carries no "
                        "subtitles (yt voice writes it)")
    width, height = png_size(png) or (None, None)
    pkg = {
        "title": " ".join(str(draft.get("title") or "").split()),
        "description": description,
        "chapters": titled,
        "tags": tags,
        "thumbnail": {"file": "thumbnail.png" if png else "", "at": at, "width": width,
                      "height": height, "bytes": len(png) if png else 0,
                      "candidates": len(candidates)},
        "credits": credits,
        "sources": sources,
        "scene_sha256": _sha256(scene),
        "video_sha256": _sha256(video_file),
        "build": {"width": size[0] if size else None, "height": size[1] if size else None,
                  "runtime": duration, "publishable": bool(built.get("publishable"))},
    }
    findings += [f for f in check_package(pkg, scene, video_file, duration, build=built,
                                          thumbnail=png) if f not in findings]
    verdict = "not ready" if any(f.startswith("error:") for f in findings) else "ready"
    pkg.update(findings=findings, verdict=verdict,
               review={"model": reviewer_model, "rounds": decided["rounds"],
                       "flags": decided["flags"]},
               engine=author.report(), usage=spend.from_report(author.report()))
    (folder / "description.txt").write_text(description, encoding="utf-8")
    (folder / "UPLOAD.md").write_text(upload_md(pkg, project), encoding="utf-8")
    write_json(project.path("package"), pkg)

    print(f"\n{idea.title} — upload package")
    print(f"  title        {pkg['title']}")
    print(f"  chapters     {len(titled)}   sources {len(sources)}   credits {len(credits)}"
          f"   tags {len(tags)}")
    print(f"  thumbnail    {at:g} s, {width}x{height}" if png else "  thumbnail    none")
    for finding in findings:
        print(f"  {finding}")
    print(f"  verdict: {verdict}")
    print(f"wrote {folder}/ (nothing was uploaded; UPLOAD.md is the operator's checklist)",
          file=sys.stderr)
    return 0 if verdict == "ready" else 1

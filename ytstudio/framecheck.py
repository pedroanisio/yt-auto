"""Objective checks on a proof frame and on what the engine says about it.

A document that validates can still render nothing a viewer would accept: a
camera that frames no object, a window that clips to white, a room so dark
the hero is a rumour. The eyes that judge a frame are the model's (the
author looks at every PNG, and the review scores them); these are the checks
that need no eyes, so they run on every frame before anyone looks. Every
function here is pure: PNG bytes in, finding strings out, or the engine's
diagnostics text in, object ids out. `vfx.Workspace.frames` is the caller.

Findings follow the pipeline's convention: a string starting with `error`,
`warning` or `note`, naming the frame (`frame at 5s of world-open`).

The hero convention: an object whose id starts with `hero-` is one the
camera must never lose. The engine reports every object3D a camera never
frames; for a hero that is an error, for the set it is a count, and a set
mostly out of view across the frames is a camera pointing at nothing.
"""

from __future__ import annotations

import io
import re

FLAT_STD = 3.0        # luma standard deviation below which a frame is flat
DIM_STD = 8.0         # ... below which it is nearly flat (a warning)
CLIP_LUMA = 250       # a pixel at or above this luma is clipped
CLIP_SHARE = 0.20     # share of clipped pixels that counts as clipped exposure
DARK_MEAN = 28.0      # mean luma below which a frame with detail is too dark

#: Ids with this prefix are heroes: the camera must frame them at every proof frame.
HERO_PREFIX = "hero-"

#: The engine's off-camera diagnostic (code X-rs-scene-render-3D-OFF-CAMERA):
#: "object3D <include>/<id> is drawn from ... and is never inside the camera's view".
OFF_CAMERA = re.compile(r"object3D\s+(\S+)\s[^\n]*?is never inside the camera's view")


# --------------------------------------------------------------------------- #
# pixels
# --------------------------------------------------------------------------- #

def frame_stats(png: bytes) -> dict:
    """Mean luma, spread, the share of pixels at the dominant level, and the
    shares clipped (>= CLIP_LUMA) and near black, from a thumbnail of the PNG."""
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("L")
    im.thumbnail((320, 320))
    px = list(im.tobytes())
    n = len(px) or 1
    mean = sum(px) / n
    std = (sum((v - mean) ** 2 for v in px) / n) ** 0.5
    hist = im.histogram()
    peak = max(range(256), key=hist.__getitem__)
    near = sum(hist[max(0, peak - 4):peak + 5]) / n
    return {"mean": round(mean, 1), "std": round(std, 2), "peak": peak,
            "dominant_share": round(near, 3),
            "clipped": round(sum(hist[CLIP_LUMA:]) / n, 3),
            "black": round(sum(hist[:8]) / n, 3), "size": im.size}


def judge_frame(png: bytes, at: float, symbol: str, *, clip_share: float = CLIP_SHARE,
                dark_mean: float = DARK_MEAN) -> list[str]:
    """The tooling's verdict on one frame, what a person would see at once:
    a flat or black frame is an error; nearly flat, clipped exposure and a
    too-dark frame are warnings the author has to look at."""
    try:
        st = frame_stats(png)
    except Exception as exc:
        return [f"warning: frame at {at:g}s of {symbol} could not be analysed ({str(exc)[:80]})"]
    where = f"frame at {at:g}s of {symbol}"
    if st["std"] < FLAT_STD:
        tone = "black" if st["mean"] < 24 else "white" if st["mean"] > 232 else "flat"
        return [f"error: {where} is {tone} (luma {st['mean']:.0f}, spread {st['std']:.1f}): "
                "nothing is visible -- the camera does not frame the objects, the lights "
                "do not reach them, or the geometry is elsewhere; fix and look again"]
    found: list[str] = []
    if st["std"] < DIM_STD or st["dominant_share"] > 0.97:
        found.append(f"warning: {where} is nearly flat (luma {st['mean']:.0f}, spread "
                     f"{st['std']:.1f}, {st['dominant_share']:.0%} one level): check that the "
                     "shot's subject is actually in frame and lit")
    if st["clipped"] > clip_share:
        found.append(f"warning: {where} has clipped exposure ({st['clipped']:.0%} of pixels at "
                     f"luma >= {CLIP_LUMA}): lower the light or emissive strength behind "
                     "the subject, or expose for the subject and let the window roll off")
    if st["mean"] < dark_mean and not found:
        found.append(f"warning: {where} is too dark (mean luma {st['mean']:.0f}): the key "
                     "light does not reach the subject, or the exposure is set for the "
                     "highlight; lift the fill or the key")
    return found


# --------------------------------------------------------------------------- #
# the engine's words
# --------------------------------------------------------------------------- #

def never_in_view(diagnostics: str | list[str]) -> list[str]:
    """The object3D ids the engine reports as never inside the camera's view,
    without their include prefix (`shot/desk` -> `desk`), in order, once each."""
    text = diagnostics if isinstance(diagnostics, str) else "\n".join(diagnostics)
    out: list[str] = []
    for m in OFF_CAMERA.finditer(text):
        oid = m.group(1).rsplit("/", 1)[-1]
        if oid not in out:
            out.append(oid)
    return out


def is_hero(object_id: str) -> bool:
    """Whether an id names a hero under the `hero-` convention."""
    return str(object_id).startswith(HERO_PREFIX)


def symbol_objects(doc_text: str, symbol_id: str) -> list[str]:
    """The object3D ids declared inside one <symbol id=...> of a document's text."""
    pattern = re.compile(r'<symbol\b[^>]*\bid="' + re.escape(symbol_id) + r'"[^>]*(/?)>', re.S)
    m = pattern.search(doc_text)
    if not m:
        return []
    if m.group(1) == "/":
        return []
    end = doc_text.find("</symbol>", m.end())
    body = doc_text[m.end():end if end >= 0 else None]
    return [t for t in re.findall(r'<object3D\b[^>]*\bid="([^"]+)"', body)]


def report_findings(report: dict | None) -> list[str]:
    """The findings of an encode's JSON envelope (on each output, and on the
    result itself) as `severity: message` lines; diagnostics are not findings."""
    if not isinstance(report, dict):
        return []
    result = report.get("result") or {}
    rows: list[dict] = []
    for output in result.get("outputs") or []:
        rows += [f for f in (output.get("findings") or []) if isinstance(f, dict)]
    rows += [f for f in (result.get("findings") or []) if isinstance(f, dict)]
    return [f"{f.get('severity', 'warning')}: {f.get('message', '')}" for f in rows]


def view_findings(out_of_view: list[str], objects: list[str], *, symbol: str,
                  at: float, label: str = "") -> list[str]:
    """One frame's (or, with `label`, one shot's) verdict from the engine's
    off-camera ids: an error per hero the camera does not frame, and one
    warning counting the set objects out of view. Ids the symbol does not
    declare are ignored."""
    owned = set(objects)
    out = [o for o in out_of_view if o in owned]
    where = f"shot {label} ({at:g}s) of {symbol}" if label else f"frame at {at:g}s of {symbol}"
    found: list[str] = []
    for hero in [o for o in out if is_hero(o)]:
        found.append(f"error: {where}: hero `{hero}` is never inside the camera's view: "
                     "the camera must frame the hero at every shot -- move or aim the "
                     f"camera (target=\"{hero}\"), or move the hero into its view")
    set_out = [o for o in out if not is_hero(o)]
    set_all = [o for o in objects if not is_hero(o)]
    if set_out:
        # Information for the author; the verdict is never_framed's, over the
        # whole symbol, because an object the camera sees in one shot is fine.
        found.append(f"note: {where}: {len(set_out)} of {len(set_all)} set objects are "
                     f"out of the camera's view ({', '.join(set_out[:8])}"
                     + (", ..." if len(set_out) > 8 else "") + ")")
    return found


def never_framed(per_window: list[list[str]], objects: list[str], *,
                 symbol: str) -> list[str]:
    """The symbol's verdict across all its encode windows: an error naming
    every object3D never inside the camera's view in any of them, because
    the delivery gate fails closed on that same engine warning. The author
    culls the object or keys its visibility to the time the camera sees it."""
    if not per_window or not objects:
        return []
    never = set(objects)
    for ids in per_window:
        never &= set(ids)
    if not never:
        return []
    names = sorted(never)
    listing = ", ".join(names[:12]) + (", ..." if len(names) > 12 else "")
    nothing = (" The camera is pointing at nothing: re-aim it at the set and the hero "
               "(target=, yaw/pitch, position)." if len(never) * 2 > len(objects) else "")
    return [f"error: symbol {symbol}: {len(never)} of {len(objects)} object3D are never inside "
            f"the camera's view in any shot ({listing}); the delivery gate fails on this. "
            "Remove each one, or key it to exist only while the camera sees it "
            "(`visible`, `start`/`end`, or `condition` on the object3D), or move the "
            f"camera so it frames it.{nothing}"]

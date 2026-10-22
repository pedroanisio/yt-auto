"""The film against the authors' proof frames: the plan and the comparison.

The vfx gate keeps a proof frame of each authored world under
`vfx/vNN/frames/<section>__<world>__<T>s.png`. `yt build` and `yt qa` both
compare the composed film at the same moment with that frame: build before
the encode is paid for (`build.proof_check`), qa on the rendered film
(`qa.proof_frame_findings`). This is the one place that says which frame,
at which film time, and how far apart two frames may be.
"""

from __future__ import annotations

import glob
import re
from pathlib import Path

#: The film's stage band against the author's proof frame of the same world
#: moment, as the mean absolute luma difference (0-255) over rows 230..820 of
#: 1920x1080 on a 240-wide greyscale. Measured on the Euler film (2026-10-21):
#: the film still against its own proof frame differed by 0.12 (encode and
#: quality noise only); the same sparse 2D world between its opening state
#: and its latest proof frame, 24 s on, by 7.9 (what a world restarted at
#: t=0 under a later shot looks like); another world's frame by 24.6. So the
#: line sits well above the noise and under the quietest restart measured.
#: The film's frame is kept under qa/pass-NN/proof/ (qa) or build/proof/
#: (build) and the author's stays in vfx/vNN/frames/, so a reviewer can look
#: at both.
PROOF_DIFF = 4.0
PROOF_BAND = (230, 820)           # stage rows at PROOF_BAND_FRAME height
PROOF_BAND_FRAME = 1080
PROOF_WIDTH = 240                 # the greyscale both frames are reduced to
PROOF_DRIFT = 0.05                # plan and build starts further apart are reported
PROOF_LIMIT = 24                 # the most proof frames rendered from the film
_PROOF_NAME = re.compile(r"^(?P<t>\d+(?:\.\d+)?)s\.png$")


def proof_diff(proof, film) -> float | None:
    """Mean absolute luma difference of two frames' stage bands, each cut at
    the band's share of its own height and reduced to the same small
    greyscale; None when either file cannot be read. Either may be a path or
    the PNG bytes."""
    import io
    from PIL import Image, ImageChops, ImageStat
    bands = []
    for frame in (proof, film):
        try:
            with Image.open(io.BytesIO(frame) if isinstance(frame, bytes) else frame) as src:
                im = src.convert("L")
        except (OSError, ValueError):
            return None
        w, h = im.size
        top, bottom = (round(h * edge / PROOF_BAND_FRAME) for edge in PROOF_BAND)
        band = im.crop((0, top, w, max(bottom, top + 1)))
        height = max(1, round(PROOF_WIDTH * (PROOF_BAND[1] - PROOF_BAND[0]) / 1920))
        bands.append(band.resize((PROOF_WIDTH, height)))
    return float(ImageStat.Stat(ImageChops.difference(*bands)).mean[0])


def proof_plan(vfx: dict | None, home: Path, shots: list[dict]) -> list[dict]:
    """One row per authored world that has a proof frame the gate kept: the
    latest by time, mapped to film time through the build's placement (the
    `at` of the world's first shot) + T, falling back to `worlds[].start` + T
    when that shot is not placed. Rows read like sample rows so render_frames
    renders them; a row carries `start_drift` when plan and build differ,
    and `shots`, the labels the world draws."""
    rows: list[dict] = []
    for doc in (vfx or {}).get("authored") or []:
        file = str(doc.get("file") or "")
        if not file.endswith(".scene.xml"):
            continue
        folder = Path(home) / Path(file).parent / "frames"
        stem = Path(file).name[:-len(".scene.xml")]
        for world in doc.get("worlds") or []:
            prefix = f"{stem}__{world.get('id')}__"
            found = []
            for png in folder.glob(f"{glob.escape(prefix)}*s.png") if folder.is_dir() else []:
                m = _PROOF_NAME.match(png.name[len(prefix):])
                if m:
                    found.append((float(m["t"]), png))
            if not found:
                continue
            t_in, png = max(found, key=lambda x: x[0])
            plan_start = float(world.get("start") or 0)
            labels = [str(s) for s in world.get("shots") or []]
            first = labels[0] if labels else ""
            placed = next((s for s in shots if str(s.get("label", "")) == first), None)
            start = float(placed["at"]) if placed else plan_start
            t = round(start + t_in, 3)
            shot = next((s for s in shots if float(s["at"]) <= t < float(s["at"]) + float(s["seconds"])),
                        None)
            row = {"t": t, "label": str((shot or {}).get("label", "")),
                   "section": (shot or {}).get("section", ""), "world": str(world.get("id")),
                   "shots": labels, "proof": str(png), "proof_t": t_in, "asset": "effect",
                   "why": "proof frame", "frame": f"proof-{png.name}"}
            if placed and abs(start - plan_start) > PROOF_DRIFT:
                row["start_drift"] = {"plan_start": plan_start, "build_start": start}
            rows.append(row)
    return rows


def mismatch_text(row: dict, diff: float) -> str:
    """The sentence both stages file: the world, the film time and the difference."""
    drift = row.get("start_drift")
    return (f"the film at {row['t']:g}s differs from the author's proof frame of "
            f"world {row['world']} ({row['proof_t']:g}s in): stage difference "
            f"{diff:.1f} against {PROOF_DIFF:g}"
            + (f"; the plan starts the world at {drift['plan_start']:g}s but the "
               f"build placed its first shot at {drift['build_start']:g}s"
               if drift else ""))

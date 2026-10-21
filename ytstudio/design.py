"""The design system the composer draws from, read from the documents.

The composer must never carry a colour, a size or a layout number of its own:
the moment it does, `styles.json` stops being the source of truth and the
film drifts from its direction one hardcoded hex at a time. So everything a
template needs comes through here -- the palette with semantic roles, a type
scale rooted in the frame height, the three bands of the frame, caption
chunks cut on real word timings, and the figures a line of text carries.
"""

from __future__ import annotations

import math
import re
import textwrap
from dataclasses import dataclass, field
from xml.etree import ElementTree

HEX = re.compile(r"#([0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?)\b")

#: Words that give a swatch its job, in the order roles are claimed. `muted`
#: comes before `ink` on purpose: "texto de apoio" is support, not body text.
#: Each is a stem matched at the start of a word ("escolh" finds "escolha"),
#: and none names a participant or a subject: those are one film's nouns,
#: learned from its own palette by `Design.words`.
ROLE_WORDS: dict[str, tuple[str, ...]] = {
    "background": ("fundo", "background", "ground", "base", "canvas", "bg"),
    "object": ("objeto", "object", "cartão", "cartao", "card", "principal",
               "primary", "item", "elemento", "element"),
    "choice": ("seleção", "selecao", "escolh", "pick", "choice", "choos",
               "select", "eleit"),
    "positive": ("ganh", "win", "won", "sucesso", "success", "correct", "certo",
                 "acert", "aprovad", "right"),
    "negative": ("perd", "lose", "lost", "los", "erro", "error", "wrong",
                 "errad", "danger", "perigo", "vazi", "empty", "fail", "falh",
                 "reprovad"),
    "muted": ("apoio", "muted", "secund", "support", "subtle", "cinza", "grey",
              "gray", "dim"),
    "accent": ("destaque", "accent", "highlight", "realce", "âmbar", "amber",
               "ouro", "gold"),
    "ink": ("texto", "text", "ink", "type", "tipografia", "label", "rótulo",
            "rotulo", "legenda", "caption"),
}

#: Colour names and filler that a palette label carries but that are not a
#: film's vocabulary: "bode/perder em coral" teaches "bode", not "coral".
NOT_VOCABULARY = {
    "azul", "verde", "vermelho", "vermelha", "coral", "creme", "cinza", "branco",
    "preto", "amarelo", "laranja", "roxo", "rosa", "ardósia", "ardosia", "escuro",
    "escura", "claro", "clara", "blue", "green", "red", "cream", "grey", "gray",
    "white", "black", "yellow", "orange", "purple", "pink", "navy", "slate",
    "dark", "light", "deep", "soft", "warm", "cool", "tom", "tone", "cor", "colour",
    "color", "fundo", "background",
}

ROLES = tuple(ROLE_WORDS)

#: A role's stems, each anchored at a word start: inside a word they matched
#: "bright" as `right`, "glossy" as `los` and "layout" as `you`.
ROLE_STEMS = {role: re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, stems)) + ")")
              for role, stems in ROLE_WORDS.items()}

#: The scale, as fractions of the frame height. Monotone by construction.
TYPE_SCALE = {"caption": 1 / 30, "label": 1 / 24, "body": 1 / 18,
              "title": 1 / 12, "display": 1 / 7}

#: The frame in bands, as fractions of its height: supers, stage, captions.
SUPER_FRACTION, CAPTION_FRACTION = 230 / 1080, 820 / 1080

#: A paint with no colour at all, for outlines: the one hex the composer may
#: name, and it names it through here.
TRANSPARENT = "#00000000"

FALLBACK = [("#101418", "background"), ("#F2F5F7", "object"), ("#4FA3D9", "choice"),
            ("#3DDC97", "positive"), ("#E5484D", "negative"), ("#8A94A6", "muted"),
            ("#F5B700", "accent"), ("#F2F5F7", "ink")]


@dataclass(frozen=True)
class Swatch:
    hex: str
    label: str = ""
    use: str = ""

    @property
    def words(self) -> str:
        return f"{self.label} {self.use}".lower()

    @property
    def luminance(self) -> float:
        r, g, b = (int(self.hex[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _norm(hexcode: str) -> str:
    return "#" + hexcode.lstrip("#").upper()


def parse_palette(source) -> list[Swatch]:
    """Swatches from prose ("portas em creme (#F1E9D2)") or from a structured
    list of {hex, use}. Order is kept; it is the fallback for roles."""
    if not source:
        return []
    if isinstance(source, dict):
        source = [source]
    if isinstance(source, (list, tuple)):
        out = []
        for row in source:
            if isinstance(row, dict) and HEX.search(str(row.get("hex", ""))):
                out.append(Swatch(_norm(HEX.search(str(row["hex"])).group(0)),
                                  use=str(row.get("use", ""))))
            elif isinstance(row, str) and HEX.search(row):
                out.extend(parse_palette(row))
        return out
    text = str(source)
    out, cursor = [], 0
    for match in HEX.finditer(text):
        label = text[cursor:match.start()]
        label = re.sub(r"[\"'()\[\],;:]+", " ", label)
        label = re.sub(r"\b(em|in|de|do|da|para|e|and|the)\b", " ", label, flags=re.I)
        out.append(Swatch(_norm(match.group(0)), label=" ".join(label.split())))
        cursor = match.end()
    return out


def assign(swatches: list[Swatch]) -> dict[str, Swatch]:
    """Each role's swatch: by the words around it, then by position."""
    out: dict[str, Swatch] = {}
    taken: set[int] = set()
    for role in ROLES:
        for i, swatch in enumerate(swatches):
            if i in taken:
                continue
            if ROLE_STEMS[role].search(swatch.words):
                out[role], _ = swatch, taken.add(i)
                break
    # Position fills what the words left open: a brief lists colours in the
    # order a reader meets them, background first.
    free = [s for i, s in enumerate(swatches) if i not in taken]
    for role in ("background", "object", "choice", "positive", "negative", "muted"):
        if role not in out and free:
            out[role] = free.pop(0)
    if "ink" not in out:
        light = [s for s in swatches if s is not out.get("background")]
        out["ink"] = (max(light, key=lambda s: s.luminance) if light
                      else Swatch("#FFFFFF", use="ink"))
    for hexcode, role in FALLBACK:
        out.setdefault(role, Swatch(hexcode, use=role))
    return out


def roles(swatches: list[Swatch]) -> dict[str, str]:
    return {role: sw.hex for role, sw in assign(swatches).items()}


@dataclass
class Design:
    """What the composer is allowed to know about how the film looks."""
    treatment: str = ""
    swatches: list[Swatch] = field(default_factory=list)
    typography: str = ""
    motion: str = ""
    source: str = "fallback"
    height: int = 1080
    _assigned: dict[str, Swatch] = field(default_factory=dict)

    @classmethod
    def from_styles(cls, styles: dict | None, *, height: int = 1080) -> "Design":
        rows = (styles or {}).get("styles") or []
        if not rows:
            d = cls(height=height, swatches=[Swatch(h, use=u) for h, u in FALLBACK])
            d._assigned = assign(d.swatches)
            return d
        primary = rows[0]
        swatches = parse_palette(primary.get("palette"))
        d = cls(treatment=str(primary.get("name", "")), swatches=swatches,
                typography=str(primary.get("typography", "")),
                motion=str(primary.get("motion", "")),
                source="styles.json" if swatches else "fallback", height=height)
        d._assigned = assign(swatches)
        return d

    def role(self, name: str) -> str:
        sw = self._assigned.get(name)
        return sw.hex if sw else dict((r, h) for h, r in FALLBACK)[name]

    def words(self, role: str) -> frozenset[str]:
        """What this film calls the role: the generic lexicon plus the nouns
        its own palette put beside the colour ("bode/perder" teaches "bode")."""
        learned = set()
        sw = self._assigned.get(role)
        if sw is not None:
            for token in re.split(r"[^\wáéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]+", sw.words):
                if len(token) >= 3 and token not in NOT_VOCABULARY:
                    learned.add(token)
        return frozenset(set(ROLE_WORDS.get(role, ())) | learned)

    def size(self, kind: str) -> int:
        return max(12, round(self.height * TYPE_SCALE[kind]))

    def tokens(self) -> dict[str, str]:
        """The film's palette as <styles> tokens, one per role, for var(--role).

        This is the one place the design crosses into the document: the
        authoring agent paints with role names and never sees a hex, so a
        palette change in styles.json re-colours the film without a rewrite.
        """
        return {f"--{role}": self.role(role) for role in
                ("background", "object", "choice", "positive", "negative",
                 "muted", "accent", "ink")}


@dataclass(frozen=True)
class Band:
    top: int
    bottom: int

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class Bands:
    super: Band
    stage: Band
    caption: Band


def bands(height: int) -> Bands:
    super_bottom = round(height * SUPER_FRACTION)
    caption_top = round(height * CAPTION_FRACTION)
    return Bands(Band(0, super_bottom), Band(super_bottom, caption_top),
                 Band(caption_top, height))


def collides(box: tuple[float, float, float, float], band: Band) -> bool:
    """Does a (x, y, w, h) box enter the band at all?"""
    _, y, _, h = box
    return y < band.bottom and y + h > band.top


@dataclass(frozen=True)
class Chunk:
    start: float
    end: float
    text: str
    parts: tuple = ()     # (start, end) of each word, when the engine timed them


#: The delivery gate's caption rules, in one place: the sidecar track and the
#: burned-in captions are both cut to these.
CAPTION_LINE_CHARS = 42
CAPTION_LINES = 2
CAPTION_CPS = 17.0
CAPTION_MIN_SECONDS = 0.7


def wrap_lines(text: str, width: int = CAPTION_LINE_CHARS) -> list[str]:
    return textwrap.wrap(text, width=width, break_long_words=False) or [text]


def need_seconds(lines: list[str]) -> float:
    """Shortest legal on-screen time for these lines at the gate's reading speed."""
    return math.ceil(sum(len(x) for x in lines) / CAPTION_CPS * 1000) / 1000 + 0.002


def _halves(c: Chunk, k: int) -> tuple[Chunk, Chunk]:
    """A chunk cut after its k-th word; each half keeps its own words' times."""
    tokens = c.text.split()
    if len(c.parts) == len(tokens):
        left, right = c.parts[:k], c.parts[k:]
        return (Chunk(left[0][0], left[-1][1], " ".join(tokens[:k]), left),
                Chunk(right[0][0], right[-1][1], " ".join(tokens[k:]), right))
    total = sum(len(t) + 1 for t in tokens)
    cut = round(c.start + (c.end - c.start)
                * sum(len(t) + 1 for t in tokens[:k]) / total, 3)
    return (Chunk(c.start, cut, " ".join(tokens[:k])),
            Chunk(cut, c.end, " ".join(tokens[k:])))


def _place(c: Chunk, lo: float, hi: float | None) -> tuple[list[Chunk], int, int]:
    """One chunk as legal cues between `lo` (the previous cue's end) and `hi`
    (the next start, or the film's end): lengthen it into the gap after, then
    start it earlier into the gap before, then cut it at a word boundary.
    Returns the cues, how many stay illegal, and how many were cut short."""
    need = max(CAPTION_MIN_SECONDS, need_seconds(wrap_lines(c.text)))
    end = max(c.end, c.start + need)
    if hi is not None and end > hi - 0.001:
        end = max(c.start + 0.05, hi - 0.001)
    start = min(c.start, end - need)
    cut = int(end < c.end)
    if start >= lo:
        return [Chunk(round(start, 3), round(end, 3), c.text, c.parts)], 0, cut
    tokens = c.text.split()
    best = None
    for k in sorted(range(1, len(tokens)),
                    key=lambda k: abs(len(" ".join(tokens[:k])) - len(c.text) / 2)):
        left, right = _halves(c, k)
        first, bad1, cut1 = _place(left, lo, right.start)
        rest, bad2, cut2 = _place(right, first[-1].end, hi)
        got = (first + rest, bad1 + bad2, cut1 + cut2)
        if got[1] == 0:
            return got
        if best is None or got[1] < best[1]:
            best = got
    if best is not None:
        return best
    start = max(lo, min(c.start, end - need))      # one word: as long as the gaps allow
    return [Chunk(round(start, 3), round(end, 3), c.text, c.parts)], 1, cut


def caption_cues(takes: list[dict],
                 film_end: float | None = None) -> tuple[list[Chunk], int, int]:
    """Every take's chunks as legal cues: ordered, not overlapping, readable.

    A cue too fast or too short is lengthened into the gap before the next
    one, never past it; failing that it starts earlier into the gap before it
    (never before the previous cue's end); failing that it is cut at a word
    boundary into cues that each fit. Nothing runs past `film_end`. Returns the
    cues, how many were cut short by the one after them, and how many could not
    be made legal (a single word too fast for its gaps). The sidecar writer and
    the build's caption track both place these.
    """
    chunks = sorted((c for t in takes for c in caption_chunks(t)), key=lambda c: c.start)
    cues: list[Chunk] = []
    clamped = illegal = 0
    for i, c in enumerate(chunks):
        nxt = chunks[i + 1].start if i + 1 < len(chunks) else film_end
        placed, bad, cut = _place(c, cues[-1].end if cues else 0.0, nxt)
        cues += placed
        illegal += bad
        clamped += cut
    return [Chunk(c.start, c.end, c.text) for c in cues], clamped, illegal


def caption_chunks(take: dict, limit: int = CAPTION_LINE_CHARS * CAPTION_LINES) -> list[Chunk]:
    """A take as cues short enough to read, timed by its own words.

    One cue per take is what clipped a caption mid-sentence: the renderer cuts
    what does not fit rather than wrapping it. A chunk takes words while it
    still wraps into CAPTION_LINES lines of CAPTION_LINE_CHARS (and within
    `limit` characters). Word timings keep each chunk's own clock; without
    them the take's duration is shared by character count.
    """
    text = " ".join(str(take.get("text", "")).split())
    at, seconds = float(take.get("at", 0)), float(take.get("seconds", 0))
    words = take.get("words") or []
    tokens = text.split()
    if not tokens:
        return []
    width = min(CAPTION_LINE_CHARS, limit)
    groups: list[list[int]] = [[]]
    for i, token in enumerate(tokens):
        trial = " ".join(tokens[j] for j in groups[-1] + [i])
        if groups[-1] and (len(trial) > limit
                           or len(wrap_lines(trial, width)) > CAPTION_LINES):
            groups.append([i])
        else:
            groups[-1].append(i)
    out = []
    if len(words) == len(tokens):
        for group in groups:
            out.append(Chunk(round(at + float(words[group[0]]["start"]), 3),
                             round(at + float(words[group[-1]]["end"]), 3),
                             " ".join(tokens[i] for i in group),
                             tuple((round(at + float(words[i]["start"]), 3),
                                    round(at + float(words[i]["end"]), 3))
                                   for i in group)))
        return out
    total = sum(len(t) + 1 for t in tokens)
    cursor = 0.0
    for n, group in enumerate(groups):
        share = sum(len(tokens[i]) + 1 for i in group) / total * seconds
        end = at + seconds if n == len(groups) - 1 else round(at + cursor + share, 3)
        out.append(Chunk(round(at + cursor, 3), end, " ".join(tokens[i] for i in group)))
        cursor += share
    return out


@dataclass(frozen=True)
class Figure:
    literal: str
    value: float
    kind: str


_FIGURE = re.compile(
    r"(?P<frac>\b\d+/\d+\b)"
    r"|(?P<range>\d+(?:[.,]\d+)?\s*[–\-]\s*\d+(?:[.,]\d+)?\s*%)"
    r"|(?P<pct>\d+(?:[.,]\d+)?\s*%)"
    r"|(?P<num>\d{1,3}(?:\.\d{3})+|\d+(?:,\d+)?)")


def _num(text: str) -> float:
    text = text.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
        return float(text.replace(".", ""))
    return float(text.replace(",", "."))


def figures(text: str) -> list[Figure]:
    """The numbers a line carries, in reading order, as something drawable."""
    out = []
    for m in _FIGURE.finditer(text or ""):
        if m.group("frac"):
            a, b = m.group("frac").split("/")
            if float(b):
                out.append(Figure(m.group("frac"), float(a) / float(b), "fraction"))
        elif m.group("range"):
            lo, hi = re.split(r"\s*[–\-]\s*", m.group("range").rstrip("% "))
            out.append(Figure(m.group("range").strip(), (_num(lo) + _num(hi)) / 200, "range"))
        elif m.group("pct"):
            out.append(Figure(m.group("pct").strip(),
                              _num(m.group("pct").rstrip("% ")) / 100, "percent"))
        else:
            out.append(Figure(m.group("num"), _num(m.group("num")), "number"))
    return out


def band_findings(document: bytes, height: int) -> list[str]:
    """Nodes with an explicit box that enter the caption band.

    For the authoring agent's check: captions are burned into the bottom band,
    so anything placed there collides. Only nodes that state their own y and
    height can be judged statically; children of a layout group are placed by
    the engine and are skipped here -- the engine's own validation and a
    rendered frame are the authority for those.
    """
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        return [f"error: not parseable XML: {exc}"]
    band = bands(height).caption
    out = []

    def walk(node, under_layout: bool):
        for child in node:
            tag = child.tag.rsplit("}", 1)[-1]
            laid_out = under_layout or (tag == "group" and
                                        (child.get("layout") or "none") != "none")
            if tag in ("shape", "layer", "group") and not under_layout:
                y, h = child.get("y"), child.get("height")
                if y is not None and h is not None:
                    try:
                        if collides((0.0, float(y), 0.0, float(h)), band):
                            out.append(f"layout: {tag} '{child.get('id', '?')}' at y={y} "
                                       f"h={h} enters the caption band (y >= {band.top})")
                    except ValueError:
                        pass
            walk(child, laid_out)

    walk(root, False)
    return out

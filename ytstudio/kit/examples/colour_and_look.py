"""One colour pipeline for the whole document: a working space, a tone mapper,
an exposure and a single CDL look -- set once, so every section is graded alike.

What it shows:
- `colorManagement` as the document's one pipeline: `workingSpace="acescg"`,
  `toneMapping` (aces, aces2, agx, filmic, reinhard or none) and `exposure` in
  stops, applied after the looks;
- a `look` child with an ASC CDL: per-channel `slope`, `offset`, `power`, a
  `saturation` and a `mix` for its strength, built from named numbers so a
  review that asks for "a little less warm" changes one value;
- `colorManagement/@looks`, which must list the look's id: a look that is only
  declared is never applied;
- a ramp of swatches, so the effect of the grade is visible on known values;
- one pipeline per film: a section's own document sets none, so no section is
  graded apart from the rest.

Every number is a placeholder for what your own film's treatment decides; a
film that wants its colours as authored sets neither a look nor a tone mapper.
"""
from srkit import FRAME, S, save

DURATION = 4.0
TONE_MAPPING = "aces"
EXPOSURE = 0.0                      # stops
SLOPE = (1.0, 1.0, 1.0)             # gain per channel: red, green, blue
OFFSET = (0.0, 0.0, 0.0)            # lift per channel
POWER = (1.0, 1.0, 1.0)             # gamma per channel
SATURATION = 1.0
STRENGTH = 1.0                      # 0 leaves the frame unchanged

scene = S.Scene(version="auto")
scene.add_project(width=FRAME["width"], height=FRAME["height"], fps=str(FRAME["fps"]),
                  duration=DURATION, background="#202020")


def triple(values):
    return ",".join(f"{v:g}" for v in values)


pipeline = scene.add_colorManagement(workingSpace="acescg", toneMapping=TONE_MAPPING,
                                     exposure=EXPOSURE, looks="the-look")
pipeline.add_look(id="the-look", space="acescct", slope=triple(SLOPE),
                  offset=triple(OFFSET), power=triple(POWER), saturation=SATURATION,
                  mix=STRENGTH)

stage = scene.add_symbols().add_symbol(id="ramp", width=FRAME["width"],
                                       height=FRAME["height"], duration=DURATION)
STEPS = 8
width = FRAME["width"] // STEPS
for i in range(STEPS):
    level = round(255 * i / (STEPS - 1))
    stage.add_shape(id=f"grey-{i}", shape="rect", x=i * width, y=200, width=width,
                    height=300, fill=f"#{level:02X}{level:02X}{level:02X}")
    stage.add_shape(id=f"tint-{i}", shape="rect", x=i * width, y=500, width=width,
                    height=200, fill=f"#{level:02X}{level // 2:02X}{255 - level:02X}")

scene.add_composition().add_instance(id="use-ramp", symbol="ramp", start=0, end=DURATION)
save(scene, "colour_and_look.scene.xml")

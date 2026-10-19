"""A real model lit by a real environment: a glTF from the props, an HDRI dome,
and a program that reads the model's own bounds to size and ground it.

What it shows:
- a `<mesh>` asset and `object3D primitive="mesh"`: the model keeps its own
  materials and texture maps (set `material=` only to replace them all);
- `light type="dome" environment=...`: image-based light and reflections from
  an HDRI, with `environmentVisible` deciding whether it is also the background;
- what a program can do that a document cannot: open the glTF, read the
  POSITION bounds, and compute the scale that makes the model a chosen size and
  the y that stands it on the ground -- so swapping the model changes one path;
- a path-traced camera whose focus is set on the model's centre.

Paths are relative to the scene file. Point MODEL and HDRI at entries of the
project's props.json (or set KIT_MODEL / KIT_HDRI); every number is a placeholder.
"""
import json
import math
import os

from srkit import FRAME, S, save

MODEL = os.environ.get("KIT_MODEL", "props/model/example/example_2k.gltf")
HDRI = os.environ.get("KIT_HDRI", "props/hdri/example/example_2k.hdr")
SIZE = 120.0              # the model's largest extent, in scene units
DURATION = 3.0

if not os.path.isfile(MODEL) or not os.path.isfile(HDRI):
    raise SystemExit(f"set MODEL and HDRI to files from props.json (have {MODEL!r}, {HDRI!r})")


#: The engine imports a y-up, metre-based model with 100 scene units per metre
#: and a half turn about x (its y grows downward), before the object's own scale.
UNITS_PER_METRE = 100.0


def gltf_bounds(path: str) -> tuple[list[float], list[float]]:
    """The model's bounds in metres, y up: every POSITION accessor moved by the
    translation and scale of the nodes above it (a rotated node needs its matrix)."""
    doc = json.load(open(path, encoding="utf-8"))
    lo, hi = [math.inf] * 3, [-math.inf] * 3

    def visit(index: int, offset: list[float], factor: list[float]) -> None:
        nonlocal lo, hi
        node = doc["nodes"][index]
        s = [f * k for f, k in zip(factor, node.get("scale", [1, 1, 1]))]
        o = [a + f * t for a, f, t in zip(offset, factor, node.get("translation", [0, 0, 0]))]
        if "mesh" in node:
            for prim in doc["meshes"][node["mesh"]]["primitives"]:
                acc = doc["accessors"][prim["attributes"]["POSITION"]]
                lo = [min(a, c + k * m) for a, c, k, m in zip(lo, o, s, acc["min"])]
                hi = [max(a, c + k * m) for a, c, k, m in zip(hi, o, s, acc["max"])]
        for child in node.get("children", []):
            visit(child, o, s)

    for root in doc["scenes"][doc.get("scene", 0)]["nodes"]:
        visit(root, [0, 0, 0], [1, 1, 1])
    return lo, hi


lo, hi = gltf_bounds(MODEL)
scale = SIZE / (UNITS_PER_METRE * max(h - l for h, l in zip(hi, lo)))
# After the half turn a vertex at height h metres sits at y = -h * 100 * scale, so
# moving the object by lo[1] * 100 * scale puts its lowest point on the ground (y = 0).
ground_y = lo[1] * UNITS_PER_METRE * scale
centre_y = ground_y - (hi[1] - lo[1]) * UNITS_PER_METRE * scale / 2

scene = S.Scene(version="auto")
scene.add_project(width=FRAME["width"], height=FRAME["height"], fps=str(FRAME["fps"]),
                  duration=DURATION)
scene.add_assets().add_mesh(id="mesh-model", src=MODEL)
scene.add_materials().add_material(id="m-ground", baseColor="#8A8580", roughness=0.7)

world = scene.add_symbols().add_symbol(id="world", width=FRAME["width"],
                                       height=FRAME["height"], duration=DURATION)
world.add_object3D(id="ground", primitive="plane", material="m-ground", x=0, y=0, z=0,
                   width=3000, height=3000, rotationX=90)
model = world.add_object3D(id="hero-model", primitive="mesh", mesh="mesh-model",
                           x=0, y=round(ground_y, 3), z=0, scaleX=round(scale, 4),
                           scaleY=round(scale, 4), scaleZ=round(scale, 4))
turn = model.add_animate(property="rotationY")      # keep the factory's return value
turn.add_key(time="0", value="-20")
turn.add_key(time=str(DURATION), value="25", interpolation="ease-in-out")

cam = (0.0, centre_y - 70, -4.5 * SIZE)
world.add_camera(id="cam", active=True, x=cam[0], y=round(cam[1], 3), z=cam[2], pitch=-9,
                 focalLength=50, sensorWidth=36, depthOfField=True, fStop=4,
                 focusDistance=round(math.dist(cam, (0.0, centre_y, 0.0)), 3),
                 renderer="pathtrace", pathSamples=96, maxBounces=6, denoise=True,
                 near=1, far=10000)

effects = scene.add_effects()
effects.add_effect(id="fx-tone", type="tonemap", tonemapper="agx")
scene.add_composition().add_instance(id="use-world", symbol="world", start=0, end=DURATION,
                                     effects="fx-tone")
lights = scene.add_lights()
# environmentVisible also draws the HDRI behind the scene; false leaves a black void
lights.add_light(id="env", type="dome", environment=HDRI, intensity=1,
                 environmentVisible=True)
# the dome lights everything; one shadow-casting key makes the contact readable
lights.add_light(id="key", type="rect-area", color="#FFF4E8", intensity=50, x=-200,
                 y=-260, z=-160, yaw=40, pitch=-45, width=160, height=160, castShadow=True)

save(scene, "model_and_environment.scene.xml")

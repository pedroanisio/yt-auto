"""A path-traced 3D world whose geometry and camera move are computed, not typed.

What it shows:
- `camera/@renderer="pathtrace"` with samples, bounces and the denoiser, and a
  physical lens (focal length, f-stop) whose focus distance is computed from
  where the camera and the subject are, every key;
- light from a dome (here a procedural sky; an HDRI goes in `environment=`) and
  a rect-area key -- the path tracer samples the area light's real shape;
- materials the path tracer applies: base colour, metallic, roughness,
  transmission with an index of refraction, emission (see kit/ENGINE.md for the
  attributes it ignores);
- placement by a loop and a seeded generator, and an orbit sampled from a
  function into keys, so a review that asks for "a wider arc" changes one number;
- a post chain applied to the instance: tone mapping, then grain and vignette.

Every number is a placeholder for what your own film needs.
"""
import math
import random

from srkit import FRAME, S, save

DURATION = 4.0
SEED = 7
RING = 9                 # objects around the subject
ARC_DEGREES = 50.0       # how far the camera travels around the subject
KEYS_PER_SECOND = 6

rng = random.Random(SEED)
scene = S.Scene(version="auto")
# fps is a rational in the schema ("30", "30000/1001"), so it is written as a string
scene.add_project(width=FRAME["width"], height=FRAME["height"], fps=str(FRAME["fps"]),
                  duration=DURATION, seed=SEED)

materials = scene.add_materials()
materials.add_material(id="m-ground", baseColor="#7A7570", roughness=0.85)
materials.add_material(id="m-metal", baseColor="#C9C2B8", metallic=1, roughness=0.22)
materials.add_material(id="m-glass", baseColor="#FFFFFF", transmission=1, ior=1.5,
                       roughness=0.0)
# The path tracer counts an emissive surface only when a path happens to hit it;
# it never aims at one. A small, bright emitter therefore lights its surroundings
# as scattered specks: light the scene with lights, and use emission for what
# itself glows (rendered here: the specks around the filament's base).
materials.add_material(id="m-glow", baseColor="#FFD9A8", emissive="#FFD9A8",
                       emissiveStrength=3)
# a family of dielectrics that differ only in roughness: made in a loop
for i in range(3):
    materials.add_material(id=f"m-satin-{i}", baseColor="#3E5C76",
                           roughness=round(0.15 + 0.3 * i, 2))

world = scene.add_symbols().add_symbol(id="world", width=FRAME["width"],
                                       height=FRAME["height"], duration=DURATION)

SUBJECT = (0.0, -40.0, 0.0)          # y grows downward: negative y is up
world.add_object3D(id="ground", primitive="plane", material="m-ground", x=0, y=0, z=0,
                   width=2000, height=2000, rotationX=90)
world.add_object3D(id="hero-subject", primitive="sphere", material="m-metal",
                   x=SUBJECT[0], y=SUBJECT[1], z=SUBJECT[2], radius=40, segments=96)
world.add_object3D(id="lens", primitive="sphere", material="m-glass",
                   x=95, y=-22, z=-40, radius=22, segments=96)
world.add_object3D(id="filament", primitive="cylinder", material="m-glow",
                   x=-90, y=-60, z=60, radius=2.5, height=120, castShadow=False)

for i in range(RING):
    angle = 2 * math.pi * i / RING + rng.uniform(-0.12, 0.12)
    radius = rng.uniform(150, 210)
    size = rng.uniform(14, 30)
    world.add_object3D(id=f"ring-{i}", primitive="box", material=f"m-satin-{i % 3}",
                       x=round(radius * math.cos(angle), 2), y=round(-size / 2, 2),
                       z=round(radius * math.sin(angle), 2), width=round(size, 2),
                       height=round(size, 2), depth=round(size, 2),
                       rotationY=round(math.degrees(angle), 1))


def orbit(t: float) -> tuple[float, float, float, float]:
    """Camera position and yaw at time t: an eased arc around the subject."""
    u = 0.5 - 0.5 * math.cos(math.pi * t / DURATION)          # ease in and out
    a = math.radians(-ARC_DEGREES / 2 + ARC_DEGREES * u)
    dist, height = 330.0, -95.0
    x = SUBJECT[0] + dist * math.sin(a)
    z = SUBJECT[2] - dist * math.cos(a)
    yaw = -math.degrees(a)
    return x, height, z, yaw


camera = world.add_camera(id="cam", active=True, focalLength=50, sensorWidth=36,
                          depthOfField=True, fStop=2.8, renderer="pathtrace",
                          pathSamples=64, maxBounces=6, denoise=True, near=1, far=5000,
                          pitch=-12)
tracks = {name: camera.add_animate(property=name) for name in ("x", "y", "z", "yaw",
                                                                "focusDistance")}
steps = int(DURATION * KEYS_PER_SECOND)
for k in range(steps + 1):
    t = round(DURATION * k / steps, 4)
    x, y, z, yaw = orbit(t)
    focus = math.dist((x, y, z), SUBJECT)
    for name, value in (("x", x), ("y", y), ("z", z), ("yaw", yaw), ("focusDistance", focus)):
        tracks[name].add_key(time=str(t), value=f"{value:.3f}", interpolation="linear")

effects = scene.add_effects()
effects.add_effect(id="fx-tone", type="tonemap", tonemapper="agx", exposure=0)
effects.add_effect(id="fx-grain", type="film-grain", amount=0.35, size=1)
effects.add_effect(id="fx-vignette", type="vignette", amount=0.35)
scene.add_composition().add_instance(id="use-world", symbol="world", start=0, end=DURATION,
                                     effects="fx-tone fx-grain fx-vignette")

lights = scene.add_lights()
# environmentVisible draws the sky as the background; without it the dome only lights
lights.add_light(id="sky", type="dome", sky="gradient", skyZenith="#5D7FA8",
                 skyHorizon="#D9CBB8", skyGround="#3A3430", intensity=0.45,
                 environmentVisible=True)
# A light casts no shadow unless it says so (castShadow defaults to false), and
# an object with no shadow under it reads as floating, whatever its y.
lights.add_light(id="key", type="rect-area", color="#FFF1DE", intensity=90,
                 x=-260, y=-320, z=-180, yaw=35, pitch=-45, width=220, height=140,
                 castShadow=True)

save(scene, "world_pathtraced.scene.xml")

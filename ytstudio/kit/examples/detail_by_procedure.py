"""Thousands of elements from one node, a surface from a formula, and the
layered materials the rasterizer applies.

What it shows:
- `object3D/@instances` with `<expression>`s that read `index` and `count`:
  one node drawn as a 48 x 48 grid whose heights come from `noise()`, so the
  detail costs one element, not two thousand;
- `primitive="heightfield"`: a ground whose relief is an expression of x and z;
- a generator asset as a material map (`#id`): fractal noise varying roughness;
- the raster renderer's own tools -- ambient occlusion, screen-space
  reflections, contact shadows, a sized shadow map -- and the material layers
  only it applies: clearcoat, anisotropy, iridescence (kit/ENGINE.md);
- a dolly computed from a function and sampled into keys.

Every number is a placeholder for what your own film needs.
"""
import math

from srkit import FRAME, S, save

DURATION = 4.0
GRID = 48                  # elements per side of the instanced grid
PITCH = 9.0                # distance between neighbouring elements
KEYS_PER_SECOND = 6

scene = S.Scene(version="auto")
scene.add_project(width=FRAME["width"], height=FRAME["height"], fps=str(FRAME["fps"]),
                  duration=DURATION, seed=11, antialias3d=4)

assets = scene.add_assets()
assets.add_generator(id="gen-rough", kind="fractal-noise", width=1024, height=1024,
                     paint="#404040", paint2="#E0E0E0", scale=60, octaves=5, seed=3)

materials = scene.add_materials()
materials.add_material(id="m-relief", baseColor="#6E6A63", roughness=1,
                       metallicRoughnessMap="#gen-rough")
materials.add_material(id="m-cell", baseColor="#2F4A5E", roughness=0.35, clearcoat=1,
                       clearcoatRoughness=0.08)
materials.add_material(id="m-brushed", baseColor="#B8B4AE", metallic=1, roughness=0.3,
                       anisotropy=0.8, anisotropyRotation=0)
# a metal's reflectance is its base colour: a dark base shows no film at all
materials.add_material(id="m-film", baseColor="#C8CCD0", metallic=1, roughness=0.08,
                       iridescence=1, iridescenceIor=1.4)

world = scene.add_symbols().add_symbol(id="world", width=FRAME["width"],
                                       height=FRAME["height"], duration=DURATION)

relief = world.add_object3D(id="relief", primitive="heightfield", material="m-relief",
                            x=0, y=0, z=0)
relief.add_heightfield(height="6 * noise(x * 0.012, z * 0.012) + 2 * noise(x * 0.05, z * 0.05)",
                       width=1600, depth=1600, xSamples=256, zSamples=256)

# One node, GRID*GRID copies: each copy computes its own place and height. A box
# scales about its centre, so y is computed from the same stretch to keep every
# copy standing on the ground (y grows downward) rather than hanging in the air.
half = (GRID - 1) * PITCH / 2
SIDE = 6.0
column, row = f"(index % {GRID})", f"Math.floor(index / {GRID})"
stretch = f"(1 + 5 * Math.max(0, noise({column} * 0.15, {row} * 0.15)))"
cells = world.add_object3D(id="hero-cells", primitive="box", material="m-cell",
                           x=0, y=0, z=0, width=SIDE, height=SIDE, depth=SIDE,
                           instances=GRID * GRID)
cells.add_expression(property="x").content = f"{column} * {PITCH} - {half}"
cells.add_expression(property="z").content = f"{row} * {PITCH} - {half}"
cells.add_expression(property="scaleY").content = stretch
cells.add_expression(property="y").content = f"-{SIDE / 2} * {stretch}"

# resting on the ground: centre y is minus half the extent along y
world.add_object3D(id="rail", primitive="cylinder", material="m-brushed", x=-260, y=-6,
                   z=40, radius=6, height=300, rotation=90, segments=64)
world.add_object3D(id="disc", primitive="cylinder", material="m-film", x=250, y=-2,
                   z=-60, radius=46, height=4, segments=128)


def dolly(t: float) -> tuple[float, float]:
    """Camera z and height at time t: a slow push that settles."""
    u = 1 - (1 - t / DURATION) ** 3                       # ease out
    return -820 + 220 * u, -300 + 60 * u


camera = world.add_camera(id="cam", active=True, x=0, focalLength=35, pitch=-20,
                          near=1, far=6000, renderer="raster", ambientOcclusion=True,
                          aoRadius=12, screenSpaceReflections=True)
z_track, y_track = camera.add_animate(property="z"), camera.add_animate(property="y")
steps = int(DURATION * KEYS_PER_SECOND)
for k in range(steps + 1):
    t = round(DURATION * k / steps, 4)
    z, y = dolly(t)
    z_track.add_key(time=str(t), value=f"{z:.3f}", interpolation="linear")
    y_track.add_key(time=str(t), value=f"{y:.3f}", interpolation="linear")

effects = scene.add_effects()
effects.add_effect(id="fx-tone", type="tonemap", tonemapper="aces")
scene.add_composition().add_instance(id="use-world", symbol="world", start=0, end=DURATION,
                                     effects="fx-tone")

lights = scene.add_lights()
lights.add_light(id="sky", type="dome", sky="gradient", skyZenith="#6A88A8",
                 skyHorizon="#D8D0C4", skyGround="#3B3631", intensity=0.6,
                 environmentVisible=True)
# castShadow defaults to false: say it, or nothing is grounded
lights.add_light(id="sun", type="directional", color="#FFF0DC", intensity=3, yaw=215,
                 pitch=-32, castShadow=True, shadowSoftness=2, shadowMapSize=4096,
                 contactShadows=True)

save(scene, "detail_by_procedure.scene.xml")

"""A surface built from a structure file: coordinates become blobs of one clay object.

What it shows:
- real data as geometry, not as a silhouette: every coordinate of a downloaded
  structure file (PDB-style ATOM/HETATM records, or plain `x y z` lines as in an
  XYZ file) becomes one `<blob>` sphere of a single `object3D primitive="clay"`,
  whose signed distance fields join with a smooth union (`blend`);
- a stated blob budget: the render cost of a clay object grows with its blob count
  and its `resolution` (cells along the longest side), so past BLOB_BUDGET the
  points are thinned by an even stride, deterministically, and the program says so;
- what a program can do that a document cannot: parse a file with the standard
  library, centre and scale the points to a chosen size, and size the blobs from the
  point density, so swapping the file changes one path.

Point KIT_STRUCTURE at a file from the project's downloads; every number is a
placeholder. Time one still (`python3 kit.py frame`) before raising the budget or
the resolution: it reports both as the cost drivers.
"""
import os

from srkit import FRAME, S, save

STRUCTURE = os.environ.get("KIT_STRUCTURE", "downloads/structure.pdb")
BLOB_BUDGET = 400         # blobs at most; more points are thinned by an even stride
RESOLUTION = 64           # clay cells along the longest side (8 to 256)
SIZE = 120.0              # the surface's largest extent, in scene units
DURATION = 3.0

if not os.path.isfile(STRUCTURE):
    raise SystemExit(f"set KIT_STRUCTURE to a structure file from downloads/ "
                     f"(have {STRUCTURE!r}, which is not a file)")


def read_points(path: str) -> list[tuple[float, float, float]]:
    """x, y, z of every coordinate line: ATOM/HETATM columns, else the last three numbers."""
    points = []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            if line.startswith(("ATOM", "HETATM")):
                points.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
                continue
            tokens = line.split()
            if len(tokens) < 3 or (tokens[0].isalpha() and len(tokens[0]) > 2):
                continue                    # a header, a remark or a record that is not a point
            points.append(tuple(float(t) for t in tokens[-3:]))
        except ValueError:
            continue
    return points


points = read_points(STRUCTURE)
if not points:
    raise SystemExit(f"{STRUCTURE!r} holds no coordinates (ATOM/HETATM records or x y z lines)")
total = len(points)
if total > BLOB_BUDGET:
    points = [points[i * total // BLOB_BUDGET] for i in range(BLOB_BUDGET)]
print(f"{total} coordinate(s) read, {len(points)} blob(s) drawn (budget {BLOB_BUDGET})")

lo = [min(p[i] for p in points) for i in range(3)]
hi = [max(p[i] for p in points) for i in range(3)]
centre = [(a + b) / 2 for a, b in zip(lo, hi)]
scale = SIZE / (max(b - a for a, b in zip(lo, hi)) or 1.0)
radius = max(2.0, 0.9 * SIZE / len(points) ** (1 / 3))     # spheres overlap as density rises

scene = S.Scene(version="auto")
scene.add_project(width=FRAME["width"], height=FRAME["height"], fps=str(FRAME["fps"]),
                  duration=DURATION)
mats = scene.add_materials()
mats.add_material(id="m-ground", baseColor="#8A8580", roughness=0.7)
mats.add_material(id="m-surface", baseColor="#B8A58C", roughness=0.55)

world = scene.add_symbols().add_symbol(id="world", width=FRAME["width"],
                                       height=FRAME["height"], duration=DURATION)
world.add_object3D(id="ground", primitive="plane", material="m-ground", x=0, y=0, z=0,
                   width=3000, height=3000, rotationX=90)
surface = world.add_object3D(id="hero-surface", primitive="clay", material="m-surface",
                             resolution=RESOLUTION, x=0, y=-SIZE, z=0)
for i, p in enumerate(points):
    # the scene's y grows downward, a structure's grows upward
    x, y, z = ((p[0] - centre[0]) * scale, -(p[1] - centre[1]) * scale, (p[2] - centre[2]) * scale)
    surface.add_blob(id=f"b{i}", x=round(x, 3), y=round(y, 3), z=round(z, 3),
                     radius=round(radius, 3), blend=round(radius * 0.5, 3))
turn = surface.add_animate(property="rotationY")
turn.add_key(time="0", value="-20")
turn.add_key(time=str(DURATION), value="25", interpolation="ease-in-out")

world.add_camera(id="cam", active=True, x=0, y=-SIZE, z=-4 * SIZE, focalLength=50,
                 sensorWidth=36, renderer="raster", near=1, far=10000)
scene.add_composition().add_instance(id="use-world", symbol="world", start=0, end=DURATION)
lights = scene.add_lights()
lights.add_light(id="key", type="rect-area", color="#FFF4E8", intensity=50, x=-200,
                 y=-260, z=-160, yaw=40, pitch=-45, width=160, height=160, castShadow=True)

save(scene, "structure_to_surface.scene.xml")

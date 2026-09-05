#!/usr/bin/env python3
"""Create a quad-only tuxedo through the Maya Codex Bridge allowlisted API.

This script never imports Maya and never drives the desktop.  It builds explicit
polygon data locally, then submits typed create/assign requests to the running
Maya bridge.
"""

import argparse
import importlib.util
import json
import math
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MCP_SERVER_PATH = PACKAGE_ROOT / "mcp" / "mcp_server.py"


def _load_bridge_client():
    spec = importlib.util.spec_from_file_location("maya_codex_bridge_client", MCP_SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _add_quad(mesh, a, b, c, d):
    mesh["faces"].append([a, b, c, d])


def _open_jacket(name, center, y0, y1, rx0, rx1, rz, front_sign, radial_segments=32, height_segments=14, thickness=0.09):
    """Open-front, double-sided elliptical tailcoat torso with quad rim faces."""
    mesh = {"name": name, "vertices": [], "faces": []}
    gap = math.radians(24.0)
    ring_size = radial_segments + 1

    def ring_index(layer, row, column):
        return layer * (height_segments + 1) * ring_size + row * ring_size + column

    for inner in (False, True):
        for row in range(height_segments + 1):
            t = row / height_segments
            smooth = t * t * (3.0 - 2.0 * t)
            rx = rx0 + (rx1 - rx0) * smooth - (thickness if inner else 0.0)
            local_rz = rz * (0.94 + 0.06 * math.sin(math.pi * t)) - (thickness if inner else 0.0)
            y = y0 + (y1 - y0) * t
            for column in range(ring_size):
                angle = gap + (math.tau - 2.0 * gap) * column / radial_segments
                x = center[0] + rx * math.sin(angle)
                z = center[2] + front_sign * local_rz * math.cos(angle)
                mesh["vertices"].append([x, y, z])

    for row in range(height_segments):
        for column in range(radial_segments):
            _add_quad(mesh, ring_index(0, row, column), ring_index(0, row, column + 1), ring_index(0, row + 1, column + 1), ring_index(0, row + 1, column))
            _add_quad(mesh, ring_index(1, row, column + 1), ring_index(1, row, column), ring_index(1, row + 1, column), ring_index(1, row + 1, column + 1))

    # Bottom and collar rims.
    for column in range(radial_segments):
        _add_quad(mesh, ring_index(0, 0, column + 1), ring_index(0, 0, column), ring_index(1, 0, column), ring_index(1, 0, column + 1))
        _add_quad(mesh, ring_index(0, height_segments, column), ring_index(0, height_segments, column + 1), ring_index(1, height_segments, column + 1), ring_index(1, height_segments, column))

    # Two vertical front-opening rims.
    for row in range(height_segments):
        _add_quad(mesh, ring_index(0, row, 0), ring_index(0, row + 1, 0), ring_index(1, row + 1, 0), ring_index(1, row, 0))
        edge = radial_segments
        _add_quad(mesh, ring_index(0, row + 1, edge), ring_index(0, row, edge), ring_index(1, row, edge), ring_index(1, row + 1, edge))
    return mesh


def _sleeve(name, start, end, radius_y, radius_z, radial_segments=16, length_segments=14):
    mesh = {"name": name, "vertices": [], "faces": []}
    direction = [end[index] - start[index] for index in range(3)]
    for row in range(length_segments + 1):
        t = row / length_segments
        taper = 1.0 - 0.24 * t
        center = [start[index] + direction[index] * t for index in range(3)]
        for column in range(radial_segments):
            angle = math.tau * column / radial_segments
            mesh["vertices"].append([
                center[0],
                center[1] + radius_y * taper * math.cos(angle),
                center[2] + radius_z * taper * math.sin(angle),
            ])
    for row in range(length_segments):
        for column in range(radial_segments):
            next_column = (column + 1) % radial_segments
            a = row * radial_segments + column
            b = row * radial_segments + next_column
            c = (row + 1) * radial_segments + next_column
            d = (row + 1) * radial_segments + column
            _add_quad(mesh, a, b, c, d)
    return mesh


def _thick_tail(name, center_x, top_y, bottom_y, back_z, outward, width, depth, width_segments=6, height_segments=14):
    mesh = {"name": name, "vertices": [], "faces": []}
    row_size = width_segments + 1

    def index(layer, row, column):
        return layer * (height_segments + 1) * row_size + row * row_size + column

    for layer in (0, 1):
        for row in range(height_segments + 1):
            t = row / height_segments
            y = top_y + (bottom_y - top_y) * t
            row_center = center_x + outward * width * 0.34 * (t ** 1.35)
            row_width = width * (1.0 - 0.58 * (t ** 1.55))
            curve_z = depth * (0.18 + 0.32 * t * t)
            z = back_z + (depth * 0.5 if layer == 0 else -depth * 0.5) - curve_z
            for column in range(row_size):
                across = column / width_segments - 0.5
                mesh["vertices"].append([row_center + across * row_width, y, z])

    for row in range(height_segments):
        for column in range(width_segments):
            _add_quad(mesh, index(0, row, column), index(0, row, column + 1), index(0, row + 1, column + 1), index(0, row + 1, column))
            _add_quad(mesh, index(1, row, column + 1), index(1, row, column), index(1, row + 1, column), index(1, row + 1, column + 1))

    # Four perimeter strips close the slab with quads.
    for row in range(height_segments):
        _add_quad(mesh, index(0, row, 0), index(0, row + 1, 0), index(1, row + 1, 0), index(1, row, 0))
        edge = width_segments
        _add_quad(mesh, index(0, row + 1, edge), index(0, row, edge), index(1, row, edge), index(1, row + 1, edge))
    for column in range(width_segments):
        _add_quad(mesh, index(0, 0, column + 1), index(0, 0, column), index(1, 0, column), index(1, 0, column + 1))
        _add_quad(mesh, index(0, height_segments, column), index(0, height_segments, column + 1), index(1, height_segments, column + 1), index(1, height_segments, column))
    return mesh


def _surface_panel(name, rows, columns, point_function):
    mesh = {"name": name, "vertices": [], "faces": []}
    for row in range(rows + 1):
        v = row / rows
        for column in range(columns + 1):
            u = column / columns
            mesh["vertices"].append(list(point_function(u, v)))
    for row in range(rows):
        for column in range(columns):
            a = row * (columns + 1) + column
            _add_quad(mesh, a, a + 1, a + columns + 2, a + columns + 1)
    return mesh


def _box(name, center, size):
    x, y, z = center
    sx, sy, sz = [value * 0.5 for value in size]
    vertices = [
        [x - sx, y - sy, z - sz], [x + sx, y - sy, z - sz], [x + sx, y + sy, z - sz], [x - sx, y + sy, z - sz],
        [x - sx, y - sy, z + sz], [x + sx, y - sy, z + sz], [x + sx, y + sy, z + sz], [x - sx, y + sy, z + sz],
    ]
    faces = [[0, 1, 2, 3], [5, 4, 7, 6], [4, 0, 3, 7], [1, 5, 6, 2], [3, 2, 6, 7], [4, 5, 1, 0]]
    return {"name": name, "vertices": vertices, "faces": faces}


def _build_meshes(bounds, front_axis):
    minimum = bounds["min"]
    size = bounds["size"]
    center = bounds["center"]
    front_sign = 1.0 if front_axis == "+Z" else -1.0
    height = size[1]
    span = size[0]
    depth = size[2]
    torso_center_z = center[2] + front_sign * depth * 0.03
    jacket_bottom = minimum[1] + height * 0.315
    jacket_top = minimum[1] + height * 0.665
    waist_y = minimum[1] + height * 0.34
    shoulder_y = minimum[1] + height * 0.61
    front_z = torso_center_z + front_sign * depth * 0.235
    overlay_z = front_z + front_sign * depth * 0.018
    back_z = torso_center_z - front_sign * depth * 0.235
    rx_bottom = span * 0.142
    rx_top = span * 0.177
    rz = depth * 0.215

    meshes = []
    material_groups = {"black": [], "white": [], "cream": [], "red": [], "gold": []}

    jacket = _open_jacket("MN_Tuxedo_Jacket", [center[0], center[1], torso_center_z], jacket_bottom, jacket_top, rx_bottom, rx_top, rz, front_sign)
    meshes.append(jacket)
    material_groups["black"].append(jacket["name"])

    for side, side_sign in (("L", -1.0), ("R", 1.0)):
        start = [center[0] + side_sign * span * 0.155, shoulder_y, torso_center_z]
        end = [center[0] + side_sign * span * 0.405, shoulder_y - height * 0.055, torso_center_z]
        sleeve = _sleeve(f"MN_Tuxedo_Sleeve_{side}", start, end, height * 0.060, depth * 0.115)
        meshes.append(sleeve)
        material_groups["black"].append(sleeve["name"])

        tail = _thick_tail(
            f"MN_Tuxedo_Tail_{side}",
            center[0] + side_sign * span * 0.075,
            waist_y,
            minimum[1] + height * 0.075,
            back_z,
            side_sign,
            span * 0.185,
            depth * 0.035,
        )
        meshes.append(tail)
        material_groups["black"].append(tail["name"])

    # White shirt bib: broad at shoulders, narrow at the waist.
    shirt = _surface_panel(
        "MN_Tuxedo_Shirt",
        10,
        8,
        lambda u, v: (
            center[0] + (u - 0.5) * span * (0.108 - 0.038 * v),
            jacket_top - v * height * 0.245,
            overlay_z + front_sign * depth * 0.010,
        ),
    )
    meshes.append(shirt)
    material_groups["white"].append(shirt["name"])

    # Two cream waistcoat panels leave a narrow center seam.
    for side, side_sign in (("L", -1.0), ("R", 1.0)):
        vest = _surface_panel(
            f"MN_Tuxedo_Vest_{side}",
            8,
            6,
            lambda u, v, side_sign=side_sign: (
                center[0] + side_sign * span * (0.010 + u * (0.070 - 0.015 * v)),
                minimum[1] + height * (0.515 - 0.175 * v),
                overlay_z + front_sign * depth * 0.017,
            ),
        )
        meshes.append(vest)
        material_groups["cream"].append(vest["name"])

    # Long pointed cream lapels, matching the white piping in the concept art.
    for side, side_sign in (("L", -1.0), ("R", 1.0)):
        lapel = _surface_panel(
            f"MN_Tuxedo_Lapel_{side}",
            8,
            4,
            lambda u, v, side_sign=side_sign: (
                center[0] + side_sign * span * ((0.030 + 0.115 * v) + u * (0.030 + 0.018 * math.sin(math.pi * v))),
                jacket_top - v * height * 0.265,
                overlay_z + front_sign * depth * 0.026,
            ),
        )
        meshes.append(lapel)
        material_groups["cream"].append(lapel["name"])

    bow_y = minimum[1] + height * 0.625
    bow_z = overlay_z + front_sign * depth * 0.042
    for side, side_sign in (("L", -1.0), ("R", 1.0)):
        bow = _box(f"MN_Tuxedo_Bow_{side}", [center[0] + side_sign * span * 0.034, bow_y, bow_z], [span * 0.065, height * 0.055, depth * 0.050])
        meshes.append(bow)
        material_groups["red"].append(bow["name"])
    knot = _box("MN_Tuxedo_Bow_Knot", [center[0], bow_y, bow_z + front_sign * depth * 0.012], [span * 0.028, height * 0.050, depth * 0.058])
    meshes.append(knot)
    material_groups["red"].append(knot["name"])

    for index in range(3):
        button = _box(
            f"MN_Tuxedo_Button_{index + 1}",
            [center[0], minimum[1] + height * (0.47 - index * 0.055), overlay_z + front_sign * depth * 0.045],
            [span * 0.020, height * 0.026, depth * 0.030],
        )
        meshes.append(button)
        material_groups["gold"].append(button["name"])

    return meshes, material_groups


def _validate_meshes(meshes):
    names = set()
    face_count = 0
    for mesh in meshes:
        if mesh["name"] in names:
            raise ValueError(f"Duplicate mesh name: {mesh['name']}")
        names.add(mesh["name"])
        vertex_count = len(mesh["vertices"])
        for face in mesh["faces"]:
            if len(face) != 4:
                raise ValueError(f"{mesh['name']} contains a non-quad face")
            if any(index < 0 or index >= vertex_count for index in face):
                raise ValueError(f"{mesh['name']} contains an invalid face index")
        face_count += len(mesh["faces"])
    if not 1800 <= face_count <= 2200:
        raise ValueError(f"Tuxedo target is about 2000 quads, generated {face_count}")
    return face_count


def _scene_meshes(bridge):
    summary = bridge._bridge_call("maya_scene_summary", {"limit": 500})
    return {entry["transform"]: entry for entry in summary.get("meshes", [])}


def _create_mesh_compatibly(bridge, mesh):
    """Create a mesh and recover its transform from a Maya 2027 bridge v0.1 response bug.

    The original bridge created the mesh successfully but failed while resolving
    the returned transform.  Comparing the scene before/after lets this one run
    finish safely without arbitrary Maya code or desktop control.
    """
    before = _scene_meshes(bridge)
    try:
        return bridge._bridge_call("maya_create_mesh", mesh)["created"]
    except RuntimeError as error:
        if "Maya 创建网格后未返回 transform" not in str(error):
            raise
        after = _scene_meshes(bridge)
        created = [name for name in after if name not in before]
        if len(created) != 1:
            raise RuntimeError(f"Could not identify the new Maya mesh after bridge compatibility recovery: {created}") from error
        entry = after[created[0]]
        if entry.get("faces") != len(mesh["faces"]):
            raise RuntimeError(f"Recovered Maya mesh has {entry.get('faces')} faces; expected {len(mesh['faces'])}") from error
        return created[0]


def main():
    parser = argparse.ArgumentParser(description="Create the MonkeyNote tuxedo in the selected Maya character scene.")
    parser.add_argument("--front-axis", required=True, choices=["+Z", "-Z"], help="Direction the selected character faces in Maya.")
    parser.add_argument("--dry-run", action="store_true", help="Generate and validate topology without changing Maya.")
    parser.add_argument("--existing-jacket", help="Reuse an already-created 988-quad jacket transform after a recoverable first-run bridge error.")
    args = parser.parse_args()
    bridge = _load_bridge_client()
    selection = bridge._bridge_call("maya_get_selection", {})
    meshes = selection.get("meshes", [])
    if len(meshes) != 1:
        raise SystemExit("Select exactly one character body polygon mesh in Maya.")
    character = meshes[0]
    generated, material_groups = _build_meshes(character["bounds"], args.front_axis)
    quad_count = _validate_meshes(generated)
    report = {
        "character": character["transform"],
        "frontAxis": args.front_axis,
        "plannedMeshes": len(generated),
        "plannedQuadFaces": quad_count,
        "skinCluster": character.get("skinCluster"),
    }
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    created_by_planned_name = {}
    start_index = 0
    if args.existing_jacket:
        scene = _scene_meshes(bridge)
        existing = scene.get(args.existing_jacket)
        if not existing:
            raise SystemExit(f"Existing jacket was not found: {args.existing_jacket}")
        if existing.get("faces") != len(generated[0]["faces"]) or existing.get("skinCluster"):
            raise SystemExit("Existing jacket does not match the expected unskinned 988-quad torso shell.")
        created_by_planned_name[generated[0]["name"]] = args.existing_jacket
        start_index = 1
    for mesh in generated[start_index:]:
        created_by_planned_name[mesh["name"]] = _create_mesh_compatibly(bridge, mesh)

    material_specs = {
        "black": ("MN_Tuxedo_Black", [0.018, 0.022, 0.028]),
        "white": ("MN_Tuxedo_ShirtWhite", [0.92, 0.88, 0.76]),
        "cream": ("MN_Tuxedo_PipingCream", [0.78, 0.70, 0.53]),
        "red": ("MN_Tuxedo_BowRed", [0.55, 0.025, 0.018]),
        "gold": ("MN_Tuxedo_ButtonGold", [0.72, 0.42, 0.08]),
    }
    for group, planned_names in material_groups.items():
        material, color = material_specs[group]
        bridge._bridge_call("maya_assign_material", {
            "objects": [created_by_planned_name[name] for name in planned_names],
            "material": material,
            "color": color,
        })

    report["created"] = list(created_by_planned_name.values())
    report["materials"] = [spec[0] for spec in material_specs.values()]
    report["note"] = "Meshes are separate and unskinned. Inspect fit and intersections before binding or exporting."
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Offline regression tests for UV and file-texture bridge actions.

The test supplies tiny Maya API fakes, so it can run outside Maya without
opening or changing a scene.
"""

import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1] / "maya" / "maya_codex_bridge.py"


def _load_bridge():
    maya = types.ModuleType("maya")
    maya_api = types.ModuleType("maya.api")
    om2 = types.ModuleType("maya.api.OpenMaya")
    cmds = types.ModuleType("maya.cmds")
    mel = types.ModuleType("maya.mel")
    utils = types.ModuleType("maya.utils")
    om1 = types.ModuleType("maya.OpenMaya")
    ompx = types.ModuleType("maya.OpenMayaMPx")
    ompx.MPxCommand = type("MPxCommand", (), {})
    ompx.MFnPlugin = type("MFnPlugin", (), {})
    ompx.asMPxPtr = lambda value: value
    om1.MGlobal = type("MGlobal", (), {"displayInfo": staticmethod(lambda _value: None)})
    maya_api.OpenMaya = om2
    maya.api = maya_api
    maya.cmds = cmds
    maya.mel = mel
    maya.utils = utils
    maya.OpenMaya = om1
    maya.OpenMayaMPx = ompx
    for name, module in {
        "maya": maya,
        "maya.api": maya_api,
        "maya.api.OpenMaya": om2,
        "maya.cmds": cmds,
        "maya.mel": mel,
        "maya.utils": utils,
        "maya.OpenMaya": om1,
        "maya.OpenMayaMPx": ompx,
    }.items():
        sys.modules[name] = module
    spec = importlib.util.spec_from_file_location("maya_codex_bridge_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeMeshFn:
    def __init__(self):
        self.uv_sets = ["map1"]
        self.current = "map1"
        self.uvs = {"map1": ([0.0, 1.0, 1.0, 0.0], [0.0, 0.0, 1.0, 1.0])}
        self.assignments = {"map1": ([4], [0, 1, 2, 3])}
        self.polygon_counts = [4]

    def getUVSetNames(self):
        return tuple(self.uv_sets)

    def currentUVSetName(self):
        return self.current

    def createUVSet(self, name):
        self.uv_sets.append(name)
        self.uvs[name] = ([], [])
        self.assignments[name] = ([0], [])
        return name

    def getUVs(self, uv_set):
        return self.uvs[uv_set]

    def getAssignedUVs(self, uv_set):
        return self.assignments[uv_set]

    def getVertices(self):
        return self.polygon_counts, [0, 1, 2, 3]

    def clearUVs(self, uv_set):
        self.uvs[uv_set] = ([], [])
        self.assignments[uv_set] = ([0], [])

    def setUVs(self, u_values, v_values, uv_set):
        self.uvs[uv_set] = (list(u_values), list(v_values))

    def assignUVs(self, counts, ids, uv_set):
        self.assignments[uv_set] = (list(counts), list(ids))

    def numUVs(self, uv_set):
        return len(self.uvs[uv_set][0])


class FakeCmds:
    def __init__(self):
        self.nodes = {"|mesh": "transform"}
        self.attributes = {}
        self.incoming = {}
        self.selection = ["|before"]
        self.assignments = []

    def objExists(self, name):
        return name in self.nodes

    def nodeType(self, name):
        return self.nodes[name]

    def shadingNode(self, node_type, **kwargs):
        name = kwargs["name"]
        self.nodes[name] = node_type
        if node_type == "file":
            self.attributes[f"{name}.fileTextureName"] = ""
            self.attributes[f"{name}.colorSpace"] = "sRGB"
        return name

    def sets(self, *args, **kwargs):
        if kwargs.get("renderable"):
            name = kwargs["name"]
            self.nodes[name] = "shadingEngine"
            return name
        self.assignments.append((list(args[0]), kwargs["forceElement"]))
        return None

    def listConnections(self, attribute, **_kwargs):
        source = self.incoming.get(attribute)
        return [source] if source else []

    def connectAttr(self, source, destination, **_kwargs):
        if destination in self.incoming:
            raise RuntimeError(f"already connected: {destination}")
        self.incoming[destination] = source

    def setAttr(self, attribute, value, **_kwargs):
        self.attributes[attribute] = value

    def getAttr(self, attribute):
        return self.attributes.get(attribute)

    def colorManagementPrefs(self, **_kwargs):
        return ["sRGB", "Raw"]

    def ls(self, **kwargs):
        if kwargs.get("selection"):
            return list(self.selection)
        return []

    def select(self, values=None, **kwargs):
        if kwargs.get("clear"):
            self.selection = []
        elif kwargs.get("replace"):
            self.selection = list(values) if isinstance(values, list) else [values]

    def uvSnapshot(self, **kwargs):
        Path(kwargs["name"]).write_bytes(b"fake-png")


def main():
    bridge = _load_bridge()
    mesh_fn = FakeMeshFn()
    fake_cmds = FakeCmds()
    bridge.cmds = fake_cmds
    bridge._mesh_transform = lambda _value, _label="网格": "|mesh"
    bridge._mesh_function = lambda _transform: mesh_fn
    bridge._payload_mesh_or_selection = lambda _payload, _label="网格": "|mesh"

    readback = bridge._get_uvs({"mesh": "|mesh"})
    assert readback["uvs"] == [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
    assert readback["faceUvs"] == [[0, 1, 2, 3]]

    written = bridge._set_uvs({
        "mesh": "|mesh",
        "uvs": [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]],
        "face_uvs": [[0, 1, 2, 3]],
    })
    assert written["uvCount"] == 4
    assert mesh_fn.uvs["map1"] == ([0.0, 0.5, 0.5, 0.0], [0.0, 0.0, 0.5, 0.5])
    try:
        bridge._set_uvs({"mesh": "|mesh", "uvs": [[0.0, 0.0]], "face_uvs": [[0]]})
    except bridge.BridgeError as error:
        assert "4 个 UV ID" in str(error)
    else:
        raise AssertionError("Topology mismatch should fail")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        export_root = root / "exports"
        texture_root = root / "textures"
        outside_root = root / "outside"
        export_root.mkdir()
        texture_root.mkdir()
        outside_root.mkdir()
        texture = texture_root / "albedo.png"
        texture.write_bytes(b"fake-png")
        outside = outside_root / "secret.png"
        outside.write_bytes(b"fake-png")
        config = root / "config.json"
        config.write_text(json.dumps({
            "token": "test",
            "port": 17321,
            "export_root": str(export_root),
            "texture_roots": [str(texture_root)],
        }), encoding="utf-8")
        os.environ["MAYA_CODEX_BRIDGE_CONFIG"] = str(config)

        assert bridge._safe_texture_file(str(texture)) == texture.resolve()
        try:
            bridge._safe_texture_file(str(outside))
        except bridge.BridgeError as error:
            assert "texture_roots" in str(error)
        else:
            raise AssertionError("Texture path outside allowlist should fail")

        assigned = bridge._assign_file_texture({
            "objects": ["|mesh"],
            "material": "coat_mat",
            "texture_path": str(texture),
            "color_space": "sRGB",
        })
        assert assigned["texturePath"] == str(texture.resolve())
        assert fake_cmds.incoming["coat_mat.color"] == "coat_mat_file.outColor"
        assert fake_cmds.assignments == [(["|mesh"], "coat_matSG")]

        snapshot = bridge._export_uv_snapshot({"mesh": "|mesh", "filename": "coat_uv", "width": 512, "height": 256})
        assert Path(snapshot["exported"]).is_file()
        assert fake_cmds.selection == ["|before"]
        try:
            bridge._export_uv_snapshot({"mesh": "|mesh", "filename": "coat_uv.png"})
        except bridge.BridgeError as error:
            assert "不会覆盖" in str(error)
        else:
            raise AssertionError("Existing UV snapshot should not be overwritten")

    print(json.dumps({"ok": True, "tested": ["get_uvs", "set_uvs", "export_uv_snapshot", "assign_file_texture"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

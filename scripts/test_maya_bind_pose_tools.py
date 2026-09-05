#!/usr/bin/env python3
"""Offline regression tests for constrained bind-pose diagnostics and repair."""

import importlib.util
import sys
import types
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1] / "maya" / "maya_codex_bridge.py"
IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


class FakeMatrix:
    def __init__(self, values):
        self.values = list(values)

    def inverse(self):
        return FakeMatrix(self.values)

    def __iter__(self):
        return iter(self.values)


def _load_bridge():
    maya = types.ModuleType("maya")
    maya_api = types.ModuleType("maya.api")
    om2 = types.ModuleType("maya.api.OpenMaya")
    om2.MMatrix = FakeMatrix
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
    spec = importlib.util.spec_from_file_location("maya_codex_bridge_bind_pose_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCmds:
    group = "|Group"
    deform = "|Group|DeformationSystem"
    root = "|Group|DeformationSystem|Root_M"
    spine = "|Group|DeformationSystem|Root_M|Spine1_M"
    elbow = "|Group|DeformationSystem|Root_M|Spine1_M|Elbow_L"
    tail = "|Group|DeformationSystem|Root_M|Tail_L"
    tail2 = "|Group|DeformationSystem|Root_M|Tail_L|Tail_L_2"
    mesh = "|Geometry|Coat"
    cluster = "skinCluster1"
    pose = "bindPose1"
    other_pose = "bindPose2"

    def __init__(self):
        self.types = {
            self.group: "transform",
            self.deform: "transform",
            self.root: "joint",
            self.spine: "joint",
            self.elbow: "joint",
            self.tail: "joint",
            self.tail2: "joint",
            self.mesh: "transform",
            self.cluster: "skinCluster",
            self.pose: "dagPose",
            self.other_pose: "dagPose",
        }
        self.parents = {
            self.deform: self.group,
            self.root: self.deform,
            self.spine: self.root,
            self.elbow: self.spine,
            self.tail: self.root,
            self.tail2: self.tail,
        }
        self.members = {self.group, self.deform, self.root, self.spine, self.elbow, self.tail, self.tail2}
        self.influences = [self.elbow, self.tail2]
        self.bind_pose_links = set()
        self.other_bind_pose_links = set()
        self.selection = [self.mesh]
        self.fail_connect_bind_pose = False
        self.undo_called = False
        self.undo_open = False

    def objExists(self, name):
        if name.endswith(".bindPose"):
            node = name.rsplit(".", 1)[0]
            return node in self.types and self.types[node] == "joint"
        return name in self.types

    def nodeType(self, name):
        return self.types[name]

    def ls(self, *values, **kwargs):
        if kwargs.get("type") == "dagPose":
            return [self.pose]
        if kwargs.get("selection"):
            return list(self.selection)
        if values:
            return [value for value in values if value in self.types]
        return []

    def listRelatives(self, name, **kwargs):
        if kwargs.get("parent") and name in self.parents:
            return [self.parents[name]]
        return []

    def skinCluster(self, cluster, **kwargs):
        assert cluster == self.cluster
        if kwargs.get("query") and kwargs.get("influence"):
            return list(self.influences)
        raise AssertionError(kwargs)

    def getAttr(self, attribute, **kwargs):
        if attribute == f"{self.pose}.bindPose":
            return True
        if attribute == f"{self.pose}.members" and kwargs.get("multiIndices"):
            return list(range(len(self.members)))
        if attribute.startswith(f"{self.pose}.global["):
            return False
        if attribute.startswith(f"{self.pose}.worldMatrix["):
            return [tuple(IDENTITY)]
        if attribute.startswith(f"{self.other_pose}.worldMatrix["):
            return [tuple(IDENTITY)]
        if attribute == f"{self.cluster}.matrix" and kwargs.get("multiIndices"):
            return [0, 1]
        if attribute.startswith(f"{self.cluster}.bindPreMatrix["):
            return [tuple(IDENTITY)]
        if attribute.endswith(".bindPose"):
            return [tuple(IDENTITY)]
        if attribute.endswith(".worldMatrix[0]"):
            return [tuple(IDENTITY)]
        raise AssertionError((attribute, kwargs))

    def listConnections(self, attribute, **kwargs):
        if attribute == f"{self.cluster}.bindPose":
            return [self.pose]
        if attribute == f"{self.cluster}.matrix[0]":
            return [self.elbow]
        if attribute == f"{self.cluster}.matrix[1]":
            return [self.tail2]
        if attribute.startswith(f"{self.pose}.members["):
            index = int(attribute.rsplit("[", 1)[1][:-1])
            members = sorted(self.members)
            return [f"{members[index]}.message"] if index < len(members) else []
        if attribute.startswith(f"{self.pose}.parents["):
            index = int(attribute.rsplit("[", 1)[1][:-1])
            members = sorted(self.members)
            if index < len(members) and members[index] in self.parents:
                return [f"{self.parents[members[index]]}.message"]
            return []
        if attribute.endswith(".bindPose") and kwargs.get("destination"):
            joint = attribute.rsplit(".", 1)[0]
            destinations = []
            if joint in self.bind_pose_links:
                index = sorted(self.members).index(joint)
                destinations.append(f"{self.pose}.worldMatrix[{index}]")
            if joint in self.other_bind_pose_links:
                destinations.append(f"{self.other_pose}.worldMatrix[7]")
            return destinations
        if attribute.startswith(f"{self.pose}.worldMatrix[") and kwargs.get("source"):
            index = int(attribute.rsplit("[", 1)[1][:-1])
            members = sorted(self.members)
            if index < len(members) and members[index] in self.bind_pose_links:
                return [f"{members[index]}.bindPose"]
            return []
        return []

    def dagPose(self, *objects, **kwargs):
        if kwargs.get("query") and kwargs.get("members"):
            assert objects == (self.pose,)
            return sorted(self.members)
        if kwargs.get("query") and kwargs.get("atPose"):
            assert objects == (self.pose,)
            return []
        if kwargs.get("addToPose"):
            assert kwargs.get("name") == self.pose
            assert kwargs.get("selection") is True
            assert objects
            assert all(isinstance(value, str) for value in objects)
            assert list(objects) == self.selection
            self.members.update(member for member in objects)
            return self.pose
        raise AssertionError((objects, kwargs))

    def select(self, objects=None, **kwargs):
        if kwargs.get("clear"):
            self.selection = []
            return
        assert kwargs.get("replace")
        self.selection = list(objects) if isinstance(objects, list) else [objects]

    def undoInfo(self, **kwargs):
        if kwargs.get("openChunk"):
            self.undo_open = True
        if kwargs.get("closeChunk"):
            self.undo_open = False

    def undo(self):
        self.undo_called = True

    def connectAttr(self, source, destination, **_kwargs):
        if source.endswith(".bindPose") and destination.startswith(f"{self.pose}.worldMatrix["):
            if self.fail_connect_bind_pose:
                raise RuntimeError("simulated bindPose connection failure")
            self.bind_pose_links.add(source.rsplit(".", 1)[0])
            return
        raise AssertionError((source, destination))


def main():
    bridge = _load_bridge()
    fake = FakeCmds()
    bridge.cmds = fake
    bridge._payload_mesh_or_selection = lambda _payload, _label="网格": fake.mesh
    bridge._mesh_transform = lambda _value, _label="网格": fake.mesh
    bridge._skin_cluster = lambda _mesh: fake.cluster

    diagnostic = bridge._bind_pose_diagnostics({"mesh": fake.mesh})
    assert diagnostic["skinClusterBindPoses"] == [fake.pose]
    assert diagnostic["bindMatrixMismatches"] == []
    assert diagnostic["repairPlan"]["repairable"] is True
    assert diagnostic["repairPlan"]["expectedMissingMembers"] == [fake.elbow, fake.tail2]
    assert diagnostic["recommendedPoseGraph"]["memberArrayIndexError"] is None
    assert diagnostic["recommendedPoseGraph"]["requiredMemberSlots"]
    assert diagnostic["recommendedPoseGraph"]["missingNodeConnections"] == []
    assert [entry["joint"] for entry in diagnostic["jointBindPoseLinkStatus"] if entry["repairableLink"]] == [fake.elbow, fake.tail2]

    multi_pose = FakeCmds()
    multi_pose.other_bind_pose_links.add(multi_pose.elbow)
    bridge.cmds = multi_pose
    multi_status = bridge._joint_bind_pose_link_status(
        multi_pose.cluster,
        multi_pose.pose,
        [multi_pose.elbow],
        1e-5,
    )[0]
    assert multi_status["connectedToTarget"] is False
    assert multi_status["repairableLink"] is True
    assert multi_status["blockers"] == []

    bridge.cmds = fake

    try:
        bridge._repair_bind_pose({
            "mesh": fake.mesh,
            "pose": fake.pose,
            "expected_missing_members": [fake.elbow, fake.tail2],
            "confirm_current_pose_is_bind_pose": False,
        })
    except bridge.BridgeError as error:
        assert "必须明确为 true" in str(error)
    else:
        raise AssertionError("repair must require explicit confirmation")

    repaired = bridge._repair_bind_pose({
        "mesh": fake.mesh,
        "pose": fake.pose,
        "expected_missing_members": [fake.elbow, fake.tail2],
        "connect_skin_cluster": False,
        "confirm_current_pose_is_bind_pose": True,
    })
    assert repaired["verified"] is True
    assert repaired["weightsChanged"] is False
    assert repaired["jointMatricesChanged"] is False
    assert fake.elbow in fake.bind_pose_links and fake.tail2 in fake.bind_pose_links
    assert fake.selection == [fake.mesh]
    assert fake.undo_called is False

    after = bridge._bind_pose_diagnostics({"mesh": fake.mesh})
    assert after["repairPlan"]["expectedMissingMembers"] == []

    failing = FakeCmds()
    failing.fail_connect_bind_pose = True
    bridge.cmds = failing
    bridge._payload_mesh_or_selection = lambda _payload, _label="网格": failing.mesh
    bridge._mesh_transform = lambda _value, _label="网格": failing.mesh
    bridge._skin_cluster = lambda _mesh: failing.cluster
    try:
        bridge._repair_bind_pose({
            "mesh": failing.mesh,
            "pose": failing.pose,
            "expected_missing_members": [failing.elbow, failing.tail2],
            "connect_skin_cluster": False,
            "confirm_current_pose_is_bind_pose": True,
        })
    except bridge.BridgeError as error:
        assert "已尝试撤销" in str(error)
    else:
        raise AssertionError("repair failure must be reported")
    assert failing.selection == [failing.mesh]
    assert failing.undo_called is True
    assert failing.undo_open is False
    print({"ok": True, "tested": ["bind_pose_diagnostics", "repair_bind_pose"]})


if __name__ == "__main__":
    main()

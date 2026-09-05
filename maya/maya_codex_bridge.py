"""Authenticated local socket bridge for Autodesk Maya 2027.

Load this Python plug-in in Maya's Plug-in Manager.  It starts a loopback-only
server and routes a small allowlisted set of requests to Maya's Python API on
Maya's main thread.  It does not use desktop automation or execute arbitrary
MEL/Python received from a client.
"""

from __future__ import annotations

import hmac
import json
import math
import os
import re
import socketserver
import threading
from pathlib import Path

import maya.api.OpenMaya as om2
import maya.cmds as cmds
import maya.mel as mel
import maya.utils as maya_utils
import maya.OpenMaya as om1
import maya.OpenMayaMPx as ompx


PLUGIN_VERSION = "0.2.6"
DEFAULT_CONFIG_PATH = Path.home() / ".maya_codex_bridge" / "config.json"
MAX_REQUEST_BYTES = 12_000_000
MAX_VERTICES = 200_000
MAX_FACES = 300_000
MAX_UVS = 100_000
SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SAFE_TEXTURE_EXTENSIONS = {".bmp", ".dds", ".exr", ".hdr", ".iff", ".jpeg", ".jpg", ".png", ".psd", ".tga", ".tif", ".tiff", ".tx"}
PLACE2D_CONNECTIONS = [
    ("coverage", "coverage"),
    ("translateFrame", "translateFrame"),
    ("rotateFrame", "rotateFrame"),
    ("mirrorU", "mirrorU"),
    ("mirrorV", "mirrorV"),
    ("stagger", "stagger"),
    ("wrapU", "wrapU"),
    ("wrapV", "wrapV"),
    ("repeatUV", "repeatUV"),
    ("offset", "offset"),
    ("rotateUV", "rotateUV"),
    ("noiseUV", "noiseUV"),
    ("vertexUvOne", "vertexUvOne"),
    ("vertexUvTwo", "vertexUvTwo"),
    ("vertexUvThree", "vertexUvThree"),
    ("vertexCameraOne", "vertexCameraOne"),
    ("outUV", "uvCoord"),
    ("outUvFilterSize", "uvFilterSize"),
]
_SERVICE = None


class BridgeError(RuntimeError):
    """A user-facing bridge error that is safe to return to Codex."""


def _config_path():
    return Path(os.environ.get("MAYA_CODEX_BRIDGE_CONFIG", str(DEFAULT_CONFIG_PATH))).expanduser()


def _load_config():
    path = _config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BridgeError(f"找不到 Bridge 配置文件：{path}。请先运行安装脚本。") from error
    except json.JSONDecodeError as error:
        raise BridgeError(f"Bridge 配置文件不是有效 JSON：{path}") from error
    if not isinstance(data.get("token"), str) or not data["token"]:
        raise BridgeError("Bridge 配置中缺少 token。请重新运行安装脚本。")
    try:
        data["port"] = int(data.get("port", 17321))
    except (TypeError, ValueError) as error:
        raise BridgeError("Bridge 配置中的 port 必须是整数。") from error
    if not 1024 <= data["port"] <= 65535:
        raise BridgeError("Bridge 配置中的 port 必须介于 1024 和 65535 之间。")
    export_root = Path(data.get("export_root", Path.home() / "Documents" / "MayaCodexBridgeExports")).expanduser().resolve()
    export_root.mkdir(parents=True, exist_ok=True)
    data["export_root"] = str(export_root)
    texture_roots = data.get("texture_roots", [str(export_root)])
    if not isinstance(texture_roots, list) or not texture_roots or any(not isinstance(value, str) or not value for value in texture_roots):
        raise BridgeError("Bridge 配置中的 texture_roots 必须是至少包含一个文件夹路径的数组。")
    data["texture_roots"] = [str(Path(value).expanduser().resolve()) for value in texture_roots]
    return data


def _safe_name(value, label="名称"):
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        raise BridgeError(f"{label}只能使用字母、数字和下划线，且必须以字母或下划线开头。")
    return value


def _existing_transform(value, label="对象"):
    if not isinstance(value, str) or not cmds.objExists(value):
        raise BridgeError(f"{label}不存在：{value}")
    node_type = cmds.nodeType(value)
    if node_type not in ("transform", "joint"):
        parents = cmds.listRelatives(value, parent=True, fullPath=True) or []
        if parents and cmds.nodeType(parents[0]) in ("transform", "joint"):
            value = parents[0]
        else:
            raise BridgeError(f"{label}必须是 transform 或 joint：{value}")
    return (cmds.ls(value, long=True) or [value])[0]


def _mesh_transform(value, label="网格"):
    transform = _existing_transform(value, label)
    shapes = cmds.listRelatives(transform, shapes=True, noIntermediate=True, fullPath=True) or []
    if not any(cmds.nodeType(shape) == "mesh" for shape in shapes):
        raise BridgeError(f"{label}不是包含 polygon mesh 的 transform：{value}")
    return transform


def _number_vector(value, label, minimum=None, maximum=None):
    if not isinstance(value, list) or len(value) != 3:
        raise BridgeError(f"{label}必须是三个数字组成的数组。")
    result = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            raise BridgeError(f"{label}必须是三个数字组成的数组。")
        item = float(item)
        if not math.isfinite(item):
            raise BridgeError(f"{label}中的数字必须是有限值。")
        if minimum is not None and item < minimum:
            raise BridgeError(f"{label}不能小于 {minimum}。")
        if maximum is not None and item > maximum:
            raise BridgeError(f"{label}不能大于 {maximum}。")
        result.append(item)
    return result


def _uv_pair(value, label):
    if not isinstance(value, list) or len(value) != 2:
        raise BridgeError(f"{label}必须是两个数字组成的 [u, v] 数组。")
    result = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(float(item)):
            raise BridgeError(f"{label}必须是两个有限数字组成的 [u, v] 数组。")
        result.append(float(item))
    return result


def _rgb_bytes(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise BridgeError(f"{label}必须是三个 0 到 255 的整数组成的数组。")
    if any(not isinstance(item, int) or isinstance(item, bool) or not 0 <= item <= 255 for item in value):
        raise BridgeError(f"{label}必须是三个 0 到 255 的整数组成的数组。")
    return list(value)


def _mesh_function(transform):
    selection = om2.MSelectionList()
    selection.add(transform)
    dag_path = selection.getDagPath(0)
    if dag_path.node().hasFn(om2.MFn.kTransform):
        dag_path.extendToShape()
    return om2.MFnMesh(dag_path)


def _resolve_uv_set(mesh_fn, requested, create_if_missing=False):
    uv_sets = list(mesh_fn.getUVSetNames())
    if requested is None:
        if not uv_sets:
            raise BridgeError("网格没有 UV Set；请明确提供 uv_set 并设置 create_if_missing=true。")
        current = mesh_fn.currentUVSetName()
        if current not in uv_sets:
            raise BridgeError("无法确定网格的当前 UV Set。")
        return current, uv_sets, False
    uv_set = _safe_name(requested, "UV Set 名称")
    if uv_set in uv_sets:
        return uv_set, uv_sets, False
    if not create_if_missing:
        raise BridgeError(f"UV Set 不存在：{uv_set}。如需创建，请设置 create_if_missing=true。")
    created = mesh_fn.createUVSet(uv_set)
    if created != uv_set:
        raise BridgeError(f"Maya 未能按指定名称创建 UV Set：请求 {uv_set}，实际 {created}。")
    return uv_set, list(mesh_fn.getUVSetNames()), True


def _safe_export_destination(filename, suffix):
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise BridgeError("filename 只能是文件名，不能包含文件夹路径。")
    if not filename.lower().endswith(suffix):
        filename = f"{filename}{suffix}"
    if not re.fullmatch(rf"[A-Za-z0-9_.-]+{re.escape(suffix)}", filename, flags=re.IGNORECASE):
        raise BridgeError(f"filename 只能包含字母、数字、下划线、连字符和点，并以 {suffix} 结束。")
    config = _load_config()
    export_root = Path(config["export_root"]).resolve()
    destination = (export_root / filename).resolve()
    if os.path.commonpath([str(export_root), str(destination)]) != str(export_root):
        raise BridgeError("导出路径不在 Bridge 配置的安全目录内。")
    return destination


def _safe_texture_file(value):
    if not isinstance(value, str) or not value:
        raise BridgeError("texture_path 必须是允许目录内现有贴图文件的绝对路径。")
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        raise BridgeError("texture_path 必须是绝对路径。")
    try:
        resolved = requested.resolve(strict=True)
    except OSError as error:
        raise BridgeError(f"贴图文件不存在或无法访问：{requested}") from error
    if not resolved.is_file():
        raise BridgeError(f"texture_path 不是文件：{resolved}")
    if resolved.suffix.lower() not in SAFE_TEXTURE_EXTENSIONS:
        allowed = ", ".join(sorted(SAFE_TEXTURE_EXTENSIONS))
        raise BridgeError(f"不支持的贴图扩展名：{resolved.suffix}。允许：{allowed}")
    roots = [Path(value).resolve() for value in _load_config()["texture_roots"]]
    if not any(os.path.commonpath([str(root), str(resolved)]) == str(root) for root in roots):
        raise BridgeError(f"贴图文件不在 Bridge 配置的 texture_roots 内：{resolved}")
    return resolved


def _skin_cluster(mesh):
    history = cmds.listHistory(mesh, pruneDagObjects=True) or []
    clusters = cmds.ls(history, type="skinCluster") or []
    return clusters[0] if clusters else None


def _long_node_name(value):
    matches = cmds.ls(value, long=True) or []
    return matches[0] if len(matches) == 1 else value


def _matrix_values(value, label):
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != 16:
        raise BridgeError(f"无法读取 {label} 的 4x4 矩阵。")
    return [float(item) for item in value]


def _matrix_delta(first, second):
    return max(abs(left - right) for left, right in zip(first, second))


def _dag_ancestors(node):
    result = []
    current = _long_node_name(node)
    while current and cmds.objExists(current):
        if cmds.nodeType(current) in ("transform", "joint"):
            result.append(current)
        parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
        current = parents[0] if parents else None
    return result


def _dag_pose_member_slots(pose):
    try:
        indices = [int(value) for value in (cmds.getAttr(f"{pose}.members", multiIndices=True) or [])]
    except RuntimeError as error:
        raise BridgeError(f"无法读取 bindPose {pose} 的 members 数组：{error}") from error
    slots = {}
    duplicate_members = {}
    for index in sorted(indices):
        sources = cmds.listConnections(
            f"{pose}.members[{index}]",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        for source in sources:
            node = source.split(".", 1)[0]
            if not cmds.objExists(node):
                continue
            member = _long_node_name(node)
            if member in slots and slots[member] != index:
                duplicate_members.setdefault(member, [slots[member]]).append(index)
            else:
                slots[member] = index
            break
    return slots, {member: sorted(set(values)) for member, values in duplicate_members.items()}


def _dag_pose_members(pose):
    slots, _duplicates = _dag_pose_member_slots(pose)
    return sorted(slots)


def _safe_plug_connections(plug, source, destination, node_type=None):
    kwargs = {
        "source": source,
        "destination": destination,
        "plugs": True,
    }
    if node_type:
        kwargs["type"] = node_type
    try:
        return sorted(set(cmds.listConnections(plug, **kwargs) or []))
    except RuntimeError:
        return []


def _plug_node(plug):
    node = plug.split(".", 1)[0]
    return _long_node_name(node) if cmds.objExists(node) else node


def _plug_matches(plug, node, attribute):
    return _plug_node(plug) == _long_node_name(node) and plug.split(".", 1)[-1] == attribute


def _joint_bind_pose_link_status(cluster, pose, influences, tolerance):
    influence_indices = _skin_influence_indices(cluster)
    member_slots, duplicate_members = _dag_pose_member_slots(pose)
    status = []
    for influence in influences:
        slot = member_slots.get(influence)
        influence_index = influence_indices.get(influence)
        entry = {
            "joint": influence,
            "memberSlot": slot,
            "skinClusterIndex": influence_index,
            "connectedToTarget": False,
            "repairableLink": False,
            "blockers": [],
        }
        if influence in duplicate_members:
            entry["blockers"].append("该影响对象在 bindPose members 数组中出现多次")
        if slot is None:
            entry["blockers"].append("bindPose members 数组没有该影响对象")
        if influence_index is None:
            entry["blockers"].append("无法解析 skinCluster influence index")
        bind_pose_plug = f"{influence}.bindPose"
        if cmds.nodeType(influence) != "joint" or not cmds.objExists(bind_pose_plug):
            entry["blockers"].append("影响对象不是带 bindPose 属性的 joint")
            status.append(entry)
            continue

        destinations = _safe_plug_connections(
            bind_pose_plug,
            source=False,
            destination=True,
            node_type="dagPose",
        )
        entry["bindPoseDestinations"] = destinations
        target_attribute = f"worldMatrix[{slot}]" if slot is not None else None
        target_plug = f"{pose}.{target_attribute}" if target_attribute else None
        entry["targetWorldMatrix"] = target_plug
        target_sources = _safe_plug_connections(
            target_plug, source=True, destination=False
        ) if target_plug else []
        entry["targetSources"] = target_sources
        entry["connectedToTarget"] = bool(target_attribute) and any(
            _plug_matches(destination, pose, target_attribute)
            for destination in destinations
        )
        other_destinations = [
            destination for destination in destinations
            if not target_attribute or not _plug_matches(destination, pose, target_attribute)
        ]
        entry["otherBindPoseDestinations"] = other_destinations
        unexpected_sources = [
            source for source in target_sources
            if not _plug_matches(source, influence, "bindPose")
        ]
        if unexpected_sources:
            entry["blockers"].append("目标 dagPose worldMatrix 已由其他属性驱动")

        if influence_index is not None and slot is not None:
            try:
                inverse_bind = om2.MMatrix(_matrix_values(
                    cmds.getAttr(f"{cluster}.bindPreMatrix[{influence_index}]"),
                    f"{cluster}.bindPreMatrix[{influence_index}]",
                ))
                bind_world = [float(value) for value in inverse_bind.inverse()]
                joint_bind_pose = _matrix_values(
                    cmds.getAttr(bind_pose_plug), bind_pose_plug
                )
                pose_world = _matrix_values(
                    cmds.getAttr(target_plug), target_plug
                )
                entry["jointBindPoseMatrixDelta"] = _matrix_delta(bind_world, joint_bind_pose)
                entry["poseWorldMatrixDelta"] = _matrix_delta(bind_world, pose_world)
                other_destination_status = []
                for destination in other_destinations:
                    destination_attribute = destination.split(".", 1)[-1]
                    destination_node = destination.split(".", 1)[0]
                    destination_entry = {"plug": destination, "matrixDelta": None, "valid": False}
                    if (
                        cmds.objExists(destination_node)
                        and cmds.nodeType(destination_node) == "dagPose"
                        and destination_attribute.startswith("worldMatrix[")
                    ):
                        try:
                            destination_matrix = _matrix_values(
                                cmds.getAttr(destination), destination
                            )
                            destination_entry["matrixDelta"] = _matrix_delta(bind_world, destination_matrix)
                            destination_entry["valid"] = destination_entry["matrixDelta"] <= tolerance
                        except (RuntimeError, TypeError, ValueError):
                            destination_entry["valid"] = False
                    other_destination_status.append(destination_entry)
                entry["otherDestinationStatus"] = other_destination_status
                if any(not item["valid"] for item in other_destination_status):
                    entry["blockers"].append("其他 dagPose worldMatrix 连接与真实绑定矩阵不一致")
                if entry["jointBindPoseMatrixDelta"] > tolerance:
                    entry["blockers"].append("joint.bindPose 矩阵与 skinCluster 真实绑定矩阵不一致")
                if entry["poseWorldMatrixDelta"] > tolerance:
                    entry["blockers"].append("dagPose worldMatrix 与 skinCluster 真实绑定矩阵不一致")
            except (RuntimeError, TypeError, ValueError) as error:
                entry["blockers"].append(f"无法验证 bindPose 矩阵：{error}")

        entry["repairableLink"] = (
            not entry["connectedToTarget"] and not entry["blockers"]
        )
        status.append(entry)
    return status


def _dag_pose_graph_details(pose, required_members, missing_members):
    """Read the exact dagPose array wiring without changing the scene."""
    try:
        indices = [int(value) for value in (cmds.getAttr(f"{pose}.members", multiIndices=True) or [])]
        index_error = None
    except RuntimeError as error:
        indices = []
        index_error = str(error)

    required_set = set(required_members)
    member_slots = []
    for index in sorted(indices):
        member_sources = _safe_plug_connections(
            f"{pose}.members[{index}]", source=True, destination=False
        )
        source_nodes = {
            _long_node_name(plug.split(".", 1)[0])
            for plug in member_sources
            if plug and cmds.objExists(plug.split(".", 1)[0])
        }
        if not source_nodes.intersection(required_set):
            continue
        try:
            global_value = bool(cmds.getAttr(f"{pose}.global[{index}]"))
        except RuntimeError:
            global_value = None
        member_slots.append({
            "index": index,
            "memberSources": member_sources,
            "parentSources": _safe_plug_connections(
                f"{pose}.parents[{index}]", source=True, destination=False
            ),
            "global": global_value,
        })

    missing_node_connections = []
    for member in missing_members:
        bind_pose_sources = []
        bind_pose_plug = f"{member}.bindPose"
        if cmds.objExists(bind_pose_plug):
            bind_pose_sources = _safe_plug_connections(
                bind_pose_plug, source=True, destination=False, node_type="dagPose"
            )
        missing_node_connections.append({
            "member": member,
            "messageDestinations": _safe_plug_connections(
                f"{member}.message", source=False, destination=True, node_type="dagPose"
            ),
            "bindPoseSources": bind_pose_sources,
        })

    return {
        "memberArrayIndices": sorted(indices),
        "memberArrayIndexError": index_error,
        "requiredMemberSlots": member_slots,
        "missingNodeConnections": missing_node_connections,
    }


def _bind_pose_nodes():
    result = []
    for pose in cmds.ls(type="dagPose", long=True) or []:
        try:
            if bool(cmds.getAttr(f"{pose}.bindPose")):
                result.append(pose)
        except RuntimeError:
            continue
    return sorted(result)


def _skin_cluster_pose_connections(cluster):
    poses = cmds.listConnections(
        f"{cluster}.bindPose",
        source=True,
        destination=False,
        type="dagPose",
    ) or []
    return sorted({_long_node_name(pose) for pose in poses})


def _skin_influence_indices(cluster):
    result = {}
    indices = cmds.getAttr(f"{cluster}.matrix", multiIndices=True) or []
    for index in indices:
        sources = cmds.listConnections(
            f"{cluster}.matrix[{int(index)}]",
            source=True,
            destination=False,
        ) or []
        for source in sources:
            if cmds.objExists(source) and cmds.nodeType(source) in ("joint", "transform"):
                result[_long_node_name(source)] = int(index)
                break
    return result


def _skin_bind_matrix_status(cluster, influences, tolerance):
    indices = _skin_influence_indices(cluster)
    status = []
    for influence in influences:
        index = indices.get(influence)
        if index is None:
            status.append({"joint": influence, "index": None, "verified": False, "reason": "无法解析 skinCluster influence index"})
            continue
        try:
            inverse_bind = om2.MMatrix(_matrix_values(
                cmds.getAttr(f"{cluster}.bindPreMatrix[{index}]"),
                f"{cluster}.bindPreMatrix[{index}]",
            ))
            bind_world = [float(value) for value in inverse_bind.inverse()]
            current_world = _matrix_values(cmds.getAttr(f"{influence}.worldMatrix[0]"), f"{influence}.worldMatrix[0]")
            delta = _matrix_delta(bind_world, current_world)
            status.append({"joint": influence, "index": index, "verified": True, "atBindMatrix": delta <= tolerance, "maxDelta": delta})
        except (RuntimeError, TypeError, ValueError) as error:
            status.append({"joint": influence, "index": index, "verified": False, "reason": str(error)})
    return status


def _mesh_bounds(mesh):
    bounds = cmds.exactWorldBoundingBox(mesh)
    if len(bounds) != 6:
        raise BridgeError(f"无法读取 {mesh} 的边界。")
    minimum = [float(bounds[0]), float(bounds[1]), float(bounds[2])]
    maximum = [float(bounds[3]), float(bounds[4]), float(bounds[5])]
    size = [maximum[index] - minimum[index] for index in range(3)]
    if any(value <= 0.00001 for value in size):
        raise BridgeError("角色网格的包围盒无效；请确认选择的是可见的 polygon mesh。")
    return {"min": minimum, "max": maximum, "size": size, "center": [(minimum[index] + maximum[index]) / 2.0 for index in range(3)]}


def _selection_meshes():
    result = []
    for value in cmds.ls(selection=True, long=True) or []:
        try:
            transform = _mesh_transform(value)
        except BridgeError:
            continue
        if transform not in result:
            result.append(transform)
    return result


def _status(_payload):
    config = _load_config()
    return {
        "mayaVersion": cmds.about(version=True),
        "apiVersion": cmds.about(apiVersion=True),
        "scene": cmds.file(query=True, sceneName=True) or "未保存场景",
        "selectionCount": len(cmds.ls(selection=True) or []),
        "bridgeVersion": PLUGIN_VERSION,
        "transport": "loopback socket + Maya Python API",
        "port": config["port"],
        "exportRoot": config["export_root"],
        "textureRoots": config["texture_roots"],
    }


def _get_selection(_payload):
    selection = cmds.ls(selection=True, long=True) or []
    meshes = []
    for transform in _selection_meshes():
        vertex_count = int(cmds.polyEvaluate(transform, vertex=True))
        face_count = int(cmds.polyEvaluate(transform, face=True))
        meshes.append({
            "transform": transform,
            "vertices": vertex_count,
            "faces": face_count,
            "bounds": _mesh_bounds(transform),
            "skinCluster": _skin_cluster(transform),
        })
    return {"selection": selection, "meshes": meshes}


def _scene_summary(payload):
    limit = payload.get("limit", 100)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise BridgeError("limit 必须是 1 到 500 的整数。")
    transforms = []
    for shape in cmds.ls(type="mesh", long=True, noIntermediate=True) or []:
        parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if parent and parent[0] not in transforms:
            transforms.append(parent[0])
    entries = []
    for transform in transforms[:limit]:
        entries.append({
            "transform": transform,
            "vertices": int(cmds.polyEvaluate(transform, vertex=True)),
            "faces": int(cmds.polyEvaluate(transform, face=True)),
            "skinCluster": _skin_cluster(transform),
        })
    return {"meshCount": len(transforms), "returned": len(entries), "meshes": entries}


def _payload_mesh_or_selection(payload, label="网格"):
    value = payload.get("mesh")
    if value is not None:
        return _mesh_transform(value, label)
    selected = _selection_meshes()
    if len(selected) != 1:
        raise BridgeError(f"请只选择一个{label}，或明确提供 mesh。")
    return selected[0]


def _mesh_geometry(payload):
    transform = _payload_mesh_or_selection(payload, "身体网格")
    mesh_fn = _mesh_function(transform)
    points = mesh_fn.getPoints(om2.MSpace.kWorld)
    polygon_counts, polygon_connects = mesh_fn.getVertices()
    faces = []
    cursor = 0
    for count in polygon_counts:
        count = int(count)
        faces.append([int(value) for value in polygon_connects[cursor:cursor + count]])
        cursor += count
    return {
        "transform": transform,
        "vertices": [[float(point.x), float(point.y), float(point.z)] for point in points],
        "faces": faces,
        "bounds": _mesh_bounds(transform),
        "skinCluster": _skin_cluster(transform),
    }


def _get_uvs(payload):
    transform = _payload_mesh_or_selection(payload, "UV 网格")
    mesh_fn = _mesh_function(transform)
    uv_set, uv_sets, _created = _resolve_uv_set(mesh_fn, payload.get("uv_set"))
    u_values, v_values = mesh_fn.getUVs(uv_set)
    if len(u_values) > MAX_UVS:
        raise BridgeError(f"UV 数量超过安全读取上限 {MAX_UVS}：{len(u_values)}")
    face_counts, uv_ids = mesh_fn.getAssignedUVs(uv_set)
    face_uvs = []
    cursor = 0
    for count in face_counts:
        count = int(count)
        face_uvs.append([int(value) for value in uv_ids[cursor:cursor + count]])
        cursor += count
    if cursor != len(uv_ids):
        raise BridgeError("Maya 返回的 UV 面角索引数量与面计数不一致。")
    return {
        "transform": transform,
        "uvSet": uv_set,
        "currentUVSet": mesh_fn.currentUVSetName(),
        "uvSets": uv_sets,
        "uvCount": len(u_values),
        "uvs": [[float(u_values[index]), float(v_values[index])] for index in range(len(u_values))],
        "faceCount": len(face_uvs),
        "faceUvs": face_uvs,
    }


def _set_uvs(payload):
    transform = _mesh_transform(payload.get("mesh"), "UV 网格")
    mesh_fn = _mesh_function(transform)
    create_if_missing = payload.get("create_if_missing", False)
    if not isinstance(create_if_missing, bool):
        raise BridgeError("create_if_missing 必须是布尔值。")
    requested_uv_set = payload.get("uv_set")
    if create_if_missing and requested_uv_set is None:
        raise BridgeError("create_if_missing=true 时必须明确提供 uv_set。")

    uvs = payload.get("uvs")
    if not isinstance(uvs, list) or not 1 <= len(uvs) <= MAX_UVS:
        raise BridgeError(f"uvs 必须包含 1 到 {MAX_UVS} 个 [u, v] 坐标。")
    parsed_uvs = [_uv_pair(value, f"uvs[{index}]") for index, value in enumerate(uvs)]

    face_uvs = payload.get("face_uvs")
    polygon_counts, _polygon_connects = mesh_fn.getVertices()
    if not isinstance(face_uvs, list) or len(face_uvs) != len(polygon_counts):
        raise BridgeError(f"face_uvs 必须与网格面数一致：需要 {len(polygon_counts)} 项。")
    assignment_counts = []
    assignment_ids = []
    assigned_face_count = 0
    for face_index, (values, polygon_count) in enumerate(zip(face_uvs, polygon_counts)):
        polygon_count = int(polygon_count)
        if not isinstance(values, list) or len(values) not in (0, polygon_count):
            raise BridgeError(f"face_uvs[{face_index}] 必须为空，或包含该面的 {polygon_count} 个 UV ID。")
        assignment_counts.append(len(values))
        if values:
            assigned_face_count += 1
        for uv_id in values:
            if not isinstance(uv_id, int) or isinstance(uv_id, bool) or not 0 <= uv_id < len(parsed_uvs):
                raise BridgeError(f"face_uvs[{face_index}] 包含无效 UV ID：{uv_id}")
            assignment_ids.append(uv_id)
    if not assignment_ids:
        raise BridgeError("face_uvs 没有任何 UV 面角分配。")

    uv_set, uv_sets, created = _resolve_uv_set(mesh_fn, requested_uv_set, create_if_missing)
    old_u = old_v = old_counts = old_ids = None
    if not created:
        old_u, old_v = mesh_fn.getUVs(uv_set)
        old_counts, old_ids = mesh_fn.getAssignedUVs(uv_set)
    try:
        mesh_fn.clearUVs(uv_set)
        mesh_fn.setUVs([value[0] for value in parsed_uvs], [value[1] for value in parsed_uvs], uv_set)
        mesh_fn.assignUVs(assignment_counts, assignment_ids, uv_set)
    except Exception as error:
        try:
            if created:
                cmds.polyUVSet(transform, delete=True, uvSet=uv_set)
            else:
                mesh_fn.clearUVs(uv_set)
                if old_u is not None and len(old_u):
                    mesh_fn.setUVs(old_u, old_v, uv_set)
                    mesh_fn.assignUVs(old_counts, old_ids, uv_set)
        except Exception:
            pass
        raise BridgeError(f"设置 UV 失败，已尝试恢复原 UV Set：{error}") from error
    return {
        "transform": transform,
        "uvSet": uv_set,
        "createdUVSet": created,
        "uvSets": uv_sets,
        "uvCount": len(parsed_uvs),
        "faceCount": len(face_uvs),
        "assignedFaceCount": assigned_face_count,
    }


def _export_uv_snapshot(payload):
    transform = _mesh_transform(payload.get("mesh"), "UV 快照网格")
    mesh_fn = _mesh_function(transform)
    uv_set, _uv_sets, _created = _resolve_uv_set(mesh_fn, payload.get("uv_set"))
    if not mesh_fn.numUVs(uv_set):
        raise BridgeError(f"UV Set {uv_set} 没有 UV，无法导出快照。")
    destination = _safe_export_destination(payload.get("filename"), ".png")
    if destination.exists():
        raise BridgeError(f"UV 快照已存在，Bridge 不会覆盖：{destination}")
    width = payload.get("width", 2048)
    height = payload.get("height", 2048)
    if not isinstance(width, int) or isinstance(width, bool) or not 64 <= width <= 8192:
        raise BridgeError("width 必须是 64 到 8192 的整数。")
    if not isinstance(height, int) or isinstance(height, bool) or not 64 <= height <= 8192:
        raise BridgeError("height 必须是 64 到 8192 的整数。")
    wire_color = _rgb_bytes(payload.get("wire_color", [255, 255, 255]), "wire_color")
    entire_uv_range = payload.get("entire_uv_range", False)
    if not isinstance(entire_uv_range, bool):
        raise BridgeError("entire_uv_range 必须是布尔值。")
    range_keys = ("u_min", "u_max", "v_min", "v_max")
    provided_range = [key in payload for key in range_keys]
    if any(provided_range) and not all(provided_range):
        raise BridgeError("自定义 UV 范围必须同时提供 u_min、u_max、v_min、v_max。")
    snapshot_options = {
        "name": str(destination),
        "fileFormat": "png",
        "overwrite": False,
        "antiAliased": True,
        "xResolution": width,
        "yResolution": height,
        "redColor": wire_color[0],
        "greenColor": wire_color[1],
        "blueColor": wire_color[2],
        "uvSetName": uv_set,
    }
    if all(provided_range):
        values = {}
        for key in range_keys:
            value = payload[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                raise BridgeError(f"{key} 必须是有限数字。")
            values[key] = float(value)
        if values["u_min"] >= values["u_max"] or values["v_min"] >= values["v_max"]:
            raise BridgeError("UV 快照范围要求 min 小于 max。")
        snapshot_options.update({
            "uMin": values["u_min"],
            "uMax": values["u_max"],
            "vMin": values["v_min"],
            "vMax": values["v_max"],
        })
    else:
        snapshot_options["entireUVRange"] = entire_uv_range
    previous_selection = cmds.ls(selection=True, long=True) or []
    cmds.select(transform, replace=True)
    try:
        cmds.uvSnapshot(**snapshot_options)
    except RuntimeError as error:
        raise BridgeError(f"UV 快照导出失败：{error}") from error
    finally:
        if previous_selection:
            cmds.select(previous_selection, replace=True)
        else:
            cmds.select(clear=True)
    if not destination.is_file():
        raise BridgeError("Maya 未生成预期的 UV 快照文件。")
    return {
        "exported": str(destination),
        "mesh": transform,
        "uvSet": uv_set,
        "width": width,
        "height": height,
        "entireUVRange": entire_uv_range if not all(provided_range) else False,
    }


def _skin_joints(payload):
    transform = _payload_mesh_or_selection(payload, "蒙皮身体网格")
    cluster = _skin_cluster(transform)
    if not cluster:
        raise BridgeError(f"{transform} 没有 skinCluster。")
    influences = cmds.skinCluster(cluster, query=True, influence=True) or []
    joints = []
    for influence in influences:
        if not cmds.objExists(influence) or cmds.nodeType(influence) != "joint":
            continue
        long_name = (cmds.ls(influence, long=True) or [influence])[0]
        parents = cmds.listRelatives(long_name, parent=True, fullPath=True) or []
        children = cmds.listRelatives(long_name, children=True, type="joint", fullPath=True) or []
        position = cmds.xform(long_name, query=True, worldSpace=True, translation=True)
        joints.append({
            "joint": long_name,
            "shortName": long_name.rsplit("|", 1)[-1],
            "parent": parents[0] if parents else None,
            "children": children,
            "position": [float(value) for value in position],
        })
    return {"mesh": transform, "skinCluster": cluster, "jointCount": len(joints), "joints": joints}


def _bind_pose_diagnostics(payload):
    transform = _payload_mesh_or_selection(payload, "绑定姿势诊断网格")
    cluster = _skin_cluster(transform)
    if not cluster:
        raise BridgeError(f"{transform} 没有 skinCluster。")
    tolerance = payload.get("matrix_tolerance", 1e-5)
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or not 1e-8 <= float(tolerance) <= 0.01:
        raise BridgeError("matrix_tolerance 必须是 1e-8 到 0.01 之间的数字。")
    tolerance = float(tolerance)

    influences = []
    for influence in cmds.skinCluster(cluster, query=True, influence=True) or []:
        if cmds.objExists(influence) and cmds.nodeType(influence) in ("joint", "transform"):
            influences.append(_long_node_name(influence))
    influences = sorted(set(influences))
    if not influences:
        raise BridgeError(f"{cluster} 没有可读取的影响对象。")

    required_members = sorted({member for influence in influences for member in _dag_ancestors(influence)})
    connected_poses = _skin_cluster_pose_connections(cluster)
    bind_matrix_status = _skin_bind_matrix_status(cluster, influences, tolerance)
    bind_matrix_mismatches = [
        entry for entry in bind_matrix_status
        if not entry.get("verified") or not entry.get("atBindMatrix", False)
    ]

    pose_entries = []
    member_to_poses = {}
    for pose in _bind_pose_nodes():
        members = _dag_pose_members(pose)
        member_set = set(members)
        for member in members:
            member_to_poses.setdefault(member, []).append(pose)
        try:
            at_pose_mismatches = cmds.dagPose(pose, query=True, atPose=True) or []
            at_pose_mismatches = sorted({_long_node_name(value) for value in at_pose_mismatches if cmds.objExists(value)})
            at_pose_error = None
        except RuntimeError as error:
            at_pose_mismatches = []
            at_pose_error = str(error)
        missing_influences = sorted(set(influences) - member_set)
        missing_required = sorted(set(required_members) - member_set)
        pose_entries.append({
            "pose": pose,
            "connectedToSkinCluster": pose in connected_poses,
            "memberCount": len(members),
            "influenceOverlapCount": len(set(influences) & member_set),
            "requiredOverlapCount": len(set(required_members) & member_set),
            "members": members,
            "missingInfluences": missing_influences,
            "missingRequiredMembers": missing_required,
            "atPoseMismatches": at_pose_mismatches,
            "atPoseQueryError": at_pose_error,
        })

    recommended_pose = None
    if len(connected_poses) == 1:
        recommended_pose = connected_poses[0]
    elif pose_entries:
        ranked = sorted(
            pose_entries,
            key=lambda entry: (entry["influenceOverlapCount"], entry["requiredOverlapCount"]),
            reverse=True,
        )
        if len(ranked) == 1 or (
            ranked[0]["influenceOverlapCount"], ranked[0]["requiredOverlapCount"]
        ) != (
            ranked[1]["influenceOverlapCount"], ranked[1]["requiredOverlapCount"]
        ):
            recommended_pose = ranked[0]["pose"]

    issues = []
    if not connected_poses:
        issues.append("skinCluster 没有连接 bindPose")
    elif len(connected_poses) > 1:
        issues.append("skinCluster 连接了多个 bindPose")
    if bind_matrix_mismatches:
        issues.append("一个或多个影响对象不在 skinCluster 的真实绑定矩阵")

    repair_plan = None
    bind_pose_link_status = []
    if recommended_pose:
        entry = next((item for item in pose_entries if item["pose"] == recommended_pose), None)
        blockers = []
        if entry is None:
            blockers.append("推荐的 bindPose 不存在")
        else:
            bind_pose_link_status = _joint_bind_pose_link_status(
                cluster,
                recommended_pose,
                influences,
                tolerance,
            )
            if entry["atPoseQueryError"]:
                blockers.append("无法验证现有 bindPose 成员是否处于已记录姿势")
            elif entry["atPoseMismatches"]:
                blockers.append("现有 bindPose 成员没有全部处于已记录姿势")
            if bind_matrix_mismatches:
                blockers.append("影响对象没有全部处于 skinCluster 的真实绑定矩阵")
            conflicting = {
                member: sorted(pose for pose in member_to_poses.get(member, []) if pose != recommended_pose)
                for member in entry["missingRequiredMembers"]
                if any(pose != recommended_pose for pose in member_to_poses.get(member, []))
            }
            if conflicting:
                blockers.append("缺失成员已经属于其他 bindPose")
            if connected_poses and connected_poses != [recommended_pose]:
                blockers.append("skinCluster 当前连接与推荐 bindPose 冲突")
            link_blockers = [
                f"{item['joint']}：{'；'.join(item['blockers'])}"
                for item in bind_pose_link_status
                if not item["connectedToTarget"] and item["blockers"]
            ]
            if link_blockers:
                blockers.append("joint.bindPose 连接检查失败：" + " | ".join(link_blockers))
            missing_links = sorted(
                item["joint"] for item in bind_pose_link_status
                if not item["connectedToTarget"]
            )
            target_world_matrices = {
                item["joint"]: item["targetWorldMatrix"]
                for item in bind_pose_link_status
                if not item["connectedToTarget"] and item["targetWorldMatrix"]
            }
            repair_plan = {
                "pose": recommended_pose,
                "repairMode": "connectJointBindPoseLinks",
                "expectedMissingMembers": missing_links,
                "targetWorldMatrices": target_world_matrices,
                "connectSkinCluster": not connected_poses,
                "conflictingMemberships": conflicting,
                "blockers": blockers,
                "repairable": bool(missing_links or not connected_poses) and not blockers,
            }
            if entry["missingRequiredMembers"]:
                issues.append(f"{recommended_pose} 缺少 {len(entry['missingRequiredMembers'])} 个影响对象或父级成员")
            if missing_links:
                issues.append(f"{recommended_pose} 缺少 {len(missing_links)} 个 joint.bindPose 到 worldMatrix 的连接")
    elif pose_entries:
        issues.append("多个 bindPose 候选得分相同，无法安全自动选择")
    else:
        issues.append("场景中没有 bindPose 节点")

    pose_graph = None
    if recommended_pose:
        recommended_entry = next(
            (item for item in pose_entries if item["pose"] == recommended_pose),
            None,
        )
        if recommended_entry is not None:
            pose_graph = _dag_pose_graph_details(
                recommended_pose,
                required_members,
                recommended_entry["missingRequiredMembers"],
            )

    return {
        "mesh": transform,
        "skinCluster": cluster,
        "matrixTolerance": tolerance,
        "influenceCount": len(influences),
        "influences": influences,
        "requiredMemberCount": len(required_members),
        "requiredMembers": required_members,
        "skinClusterBindPoses": connected_poses,
        "bindMatrixStatus": bind_matrix_status,
        "bindMatrixMismatches": bind_matrix_mismatches,
        "bindPoses": pose_entries,
        "recommendedPose": recommended_pose,
        "recommendedPoseGraph": pose_graph,
        "jointBindPoseLinkStatus": bind_pose_link_status,
        "issues": issues,
        "repairPlan": repair_plan,
    }


def _repair_bind_pose(payload):
    if payload.get("confirm_current_pose_is_bind_pose") is not True:
        raise BridgeError("confirm_current_pose_is_bind_pose 必须明确为 true。")
    pose_value = payload.get("pose")
    if not isinstance(pose_value, str) or not cmds.objExists(pose_value) or cmds.nodeType(pose_value) != "dagPose":
        raise BridgeError(f"pose 必须是现有 dagPose：{pose_value}")
    pose = _long_node_name(pose_value)
    expected = payload.get("expected_missing_members")
    if not isinstance(expected, list) or any(not isinstance(value, str) or not value for value in expected):
        raise BridgeError("expected_missing_members 必须是诊断结果返回的字符串数组。")
    expected_members = []
    for value in expected:
        if not cmds.objExists(value):
            raise BridgeError(f"预期缺失成员不存在：{value}")
        member = _existing_transform(value, "预期缺失成员")
        if member not in expected_members:
            expected_members.append(member)
    expected_members = sorted(expected_members)
    connect_skin_cluster = payload.get("connect_skin_cluster", False)
    if not isinstance(connect_skin_cluster, bool):
        raise BridgeError("connect_skin_cluster 必须是布尔值。")

    diagnostic_payload = {"mesh": payload.get("mesh")}
    if "matrix_tolerance" in payload:
        diagnostic_payload["matrix_tolerance"] = payload["matrix_tolerance"]
    before = _bind_pose_diagnostics(diagnostic_payload)
    plan = before.get("repairPlan")
    if not plan or plan.get("pose") != pose:
        raise BridgeError("诊断结果没有把该 pose 识别为唯一安全候选；拒绝修改。")
    current_missing = sorted(plan.get("expectedMissingMembers") or [])
    if expected_members != current_missing:
        raise BridgeError("场景状态已变化，expected_missing_members 与当前诊断不一致；请重新诊断。")
    if plan.get("blockers"):
        raise BridgeError("bindPose 修复被安全检查阻止：" + "；".join(plan["blockers"]))
    if bool(plan.get("connectSkinCluster")) != connect_skin_cluster:
        required = "true" if plan.get("connectSkinCluster") else "false"
        raise BridgeError(f"connect_skin_cluster 必须与诊断修复计划一致：{required}。")
    if not plan.get("repairable"):
        raise BridgeError("当前诊断没有可执行的 bindPose 修复。")

    tracked_nodes = before["requiredMembers"]
    matrices_before = {
        node: _matrix_values(cmds.getAttr(f"{node}.worldMatrix[0]"), f"{node}.worldMatrix[0]")
        for node in tracked_nodes
    }
    cluster = before["skinCluster"]
    previous_selection = cmds.ls(selection=True, long=True) or []
    cmds.undoInfo(openChunk=True, chunkName="MayaCodexBridgeRepairBindPose")
    attempted_change = False
    operation_error = None
    try:
        if expected_members:
            attempted_change = True
            targets = plan.get("targetWorldMatrices") or {}
            for member in expected_members:
                target = targets.get(member)
                if not target:
                    raise BridgeError(f"诊断结果没有提供 {member} 的目标 worldMatrix。")
                cmds.connectAttr(f"{member}.bindPose", target, force=False)
        if connect_skin_cluster:
            attempted_change = True
            cmds.connectAttr(f"{pose}.message", f"{cluster}.bindPose", force=False)
    except Exception as error:
        operation_error = error
    finally:
        try:
            if previous_selection:
                cmds.select(previous_selection, replace=True)
            else:
                cmds.select(clear=True)
        except Exception as error:
            if operation_error is None:
                operation_error = error
        finally:
            cmds.undoInfo(closeChunk=True)
    if operation_error is not None:
        if attempted_change:
            cmds.undo()
        raise BridgeError(f"bindPose 修复失败，已尝试撤销：{operation_error}") from operation_error

    try:
        matrices_after = {
            node: _matrix_values(cmds.getAttr(f"{node}.worldMatrix[0]"), f"{node}.worldMatrix[0]")
            for node in tracked_nodes
        }
        moved = [node for node in tracked_nodes if _matrix_delta(matrices_before[node], matrices_after[node]) > before["matrixTolerance"]]
        after = _bind_pose_diagnostics(diagnostic_payload)
        after_plan = after.get("repairPlan") or {}
        remaining = sorted(after_plan.get("expectedMissingMembers") or [])
    except Exception as error:
        cmds.undo()
        raise BridgeError(f"bindPose 修复后的验证失败，已撤销：{error}") from error
    if moved or remaining or after["bindMatrixMismatches"] or pose not in after["skinClusterBindPoses"]:
        cmds.undo()
        details = []
        if moved:
            details.append(f"检测到 {len(moved)} 个 DAG 节点矩阵变化")
        if remaining:
            details.append(f"仍缺少 {len(remaining)} 个 joint.bindPose 连接")
        if after["bindMatrixMismatches"]:
            details.append("仍有影响对象不在真实绑定矩阵")
        if pose not in after["skinClusterBindPoses"]:
            details.append("skinCluster 仍未连接目标 bindPose")
        raise BridgeError("bindPose 修复验证失败，已撤销：" + "；".join(details))

    return {
        "mesh": before["mesh"],
        "skinCluster": cluster,
        "pose": pose,
        "addedMembers": [],
        "connectedJointBindPoseLinks": expected_members,
        "connectedSkinCluster": connect_skin_cluster,
        "jointMatricesChanged": False,
        "weightsChanged": False,
        "remainingMissingMembers": remaining,
        "verified": True,
    }


def _create_mesh(payload):
    name = _safe_name(payload.get("name"), "新网格名称")
    vertices = payload.get("vertices")
    faces = payload.get("faces")
    if not isinstance(vertices, list) or not 3 <= len(vertices) <= MAX_VERTICES:
        raise BridgeError(f"vertices 必须包含 3 到 {MAX_VERTICES} 个顶点。")
    if not isinstance(faces, list) or not 1 <= len(faces) <= MAX_FACES:
        raise BridgeError(f"faces 必须包含 1 到 {MAX_FACES} 个面。")
    points = om2.MPointArray()
    for index, vertex in enumerate(vertices):
        try:
            x, y, z = _number_vector(vertex, f"vertices[{index}]")
        except BridgeError as error:
            raise BridgeError(str(error)) from error
        points.append(om2.MPoint(x, y, z))
    counts = om2.MIntArray()
    connects = om2.MIntArray()
    for face_index, face in enumerate(faces):
        if not isinstance(face, list) or len(face) < 3:
            raise BridgeError(f"faces[{face_index}] 至少需要 3 个顶点索引。")
        counts.append(len(face))
        for vertex_index in face:
            if not isinstance(vertex_index, int) or isinstance(vertex_index, bool) or not 0 <= vertex_index < len(vertices):
                raise BridgeError(f"faces[{face_index}] 包含无效顶点索引：{vertex_index}")
            connects.append(vertex_index)
    mesh_fn = om2.MFnMesh()
    shape_object = mesh_fn.create(points, counts, connects)
    created_name = om2.MFnDependencyNode(shape_object).name()
    if cmds.nodeType(created_name) == "transform":
        transform = created_name
    else:
        transforms = cmds.listRelatives(created_name, parent=True, fullPath=True) or []
        if not transforms:
            raise BridgeError("Maya 创建网格后未返回 transform。")
        transform = transforms[0]
    transform = cmds.rename(transform, name)
    transform = (cmds.ls(transform, long=True) or [transform])[0]
    cmds.polySoftEdge(transform, angle=180, constructionHistory=False)
    parent = payload.get("parent")
    if parent is not None:
        parent = _existing_transform(parent, "父对象")
        transform = cmds.parent(transform, parent, absolute=True)[0]
        transform = (cmds.ls(transform, long=True) or [transform])[0]
    return {"created": transform, "vertices": len(vertices), "faces": len(faces)}


def _create_group(payload):
    name = _safe_name(payload.get("name"), "组名称")
    group = cmds.group(empty=True, name=name)
    return {"created": (cmds.ls(group, long=True) or [group])[0]}


def _delete_failed_tuxedo_draft(payload):
    if payload.get("confirm") is not True:
        raise BridgeError("删除失败草稿需要 confirm=true。")
    expected_faces = [988, 224, 208, 224, 208, 80, 48, 48, 32, 32, 6, 6, 6, 6, 6, 6]
    verified = []
    for index, face_count in enumerate(expected_faces, start=1):
        short_name = f"polySurface{index}"
        matches = cmds.ls(short_name, long=True) or []
        if len(matches) != 1 or matches[0] != f"|{short_name}":
            raise BridgeError(f"失败草稿指纹不匹配：找不到唯一根对象 |{short_name}。不会删除任何内容。")
        transform = _mesh_transform(matches[0], "失败草稿对象")
        actual_faces = int(cmds.polyEvaluate(transform, face=True))
        if actual_faces != face_count or _skin_cluster(transform):
            raise BridgeError(f"失败草稿指纹不匹配：{transform} 面数或蒙皮状态不符。不会删除任何内容。")
        verified.append(transform)
    cmds.delete(verified)
    return {"deleted": verified, "count": len(verified)}


def _create_cube(name, group, center, scale, rotation=None):
    object_name = cmds.polyCube(width=1, height=1, depth=1, constructionHistory=False, name=name)[0]
    cmds.setAttr(f"{object_name}.translate", *center, type="double3")
    cmds.setAttr(f"{object_name}.scale", *scale, type="double3")
    if rotation:
        cmds.setAttr(f"{object_name}.rotate", *rotation, type="double3")
    cmds.parent(object_name, group, absolute=True)
    return (cmds.ls(object_name, long=True) or [object_name])[0]


def _create_tuxedo_blockout(payload):
    character_value = payload.get("character")
    if character_value is None:
        selected = _selection_meshes()
        if len(selected) != 1:
            raise BridgeError("请选中且只选中一个角色 polygon mesh，或明确提供 character。")
        character = selected[0]
    else:
        character = _mesh_transform(character_value, "角色")
    group_name = _safe_name(payload.get("name", "tuxedo_blockout"), "燕尾服组名称")
    front_axis = payload.get("front_axis", "+Z")
    if front_axis not in ("+Z", "-Z"):
        raise BridgeError("front_axis 只能是 +Z 或 -Z。")
    front_sign = 1.0 if front_axis == "+Z" else -1.0
    bounds = _mesh_bounds(character)
    x_size, y_size, z_size = bounds["size"]
    x_center, y_center, z_center = bounds["center"]
    minimum_y, maximum_y = bounds["min"][1], bounds["max"][1]
    group = cmds.group(empty=True, name=group_name)
    group = (cmds.ls(group, long=True) or [group])[0]

    # This is intentionally a blockout: all parts remain separate and editable
    # before modelling them to the selected character's topology.
    jacket_center_y = minimum_y + y_size * 0.58
    jacket_height = y_size * 0.46
    shell_depth = z_size * 0.72
    front_z = z_center + front_sign * z_size * 0.39
    back_z = z_center - front_sign * z_size * 0.39
    pieces = []
    pieces.append(_create_cube("tuxedo_jacket", group, [x_center, jacket_center_y, z_center], [x_size * 1.10, jacket_height, shell_depth]))
    pieces.append(_create_cube("tuxedo_vest", group, [x_center, jacket_center_y, front_z], [x_size * 0.70, jacket_height * 0.78, z_size * 0.05]))
    pieces.append(_create_cube("tuxedo_shirt_front", group, [x_center, jacket_center_y + jacket_height * 0.02, front_z + front_sign * z_size * 0.055], [x_size * 0.43, jacket_height * 0.70, z_size * 0.035]))
    lapel_rotation = [0.0, 0.0, -18.0]
    pieces.append(_create_cube("tuxedo_lapel_L", group, [x_center - x_size * 0.20, jacket_center_y + jacket_height * 0.20, front_z + front_sign * z_size * 0.09], [x_size * 0.17, jacket_height * 0.55, z_size * 0.04], lapel_rotation))
    pieces.append(_create_cube("tuxedo_lapel_R", group, [x_center + x_size * 0.20, jacket_center_y + jacket_height * 0.20, front_z + front_sign * z_size * 0.09], [x_size * 0.17, jacket_height * 0.55, z_size * 0.04], [0.0, 0.0, 18.0]))
    tail_y = minimum_y + y_size * 0.27
    pieces.append(_create_cube("tuxedo_tail_L", group, [x_center - x_size * 0.23, tail_y, back_z], [x_size * 0.27, y_size * 0.40, z_size * 0.13]))
    pieces.append(_create_cube("tuxedo_tail_R", group, [x_center + x_size * 0.23, tail_y, back_z], [x_size * 0.27, y_size * 0.40, z_size * 0.13]))
    bow_center_y = minimum_y + y_size * 0.78
    for suffix, direction in (("L", -1.0), ("R", 1.0)):
        bow = cmds.polySphere(radius=1, subdivisionsAxis=12, subdivisionsHeight=8, constructionHistory=False, name=f"tuxedo_bow_{suffix}")[0]
        cmds.setAttr(f"{bow}.translate", x_center + direction * x_size * 0.11, bow_center_y, front_z + front_sign * z_size * 0.12, type="double3")
        cmds.setAttr(f"{bow}.scale", x_size * 0.16, y_size * 0.06, z_size * 0.08, type="double3")
        cmds.parent(bow, group, absolute=True)
        pieces.append((cmds.ls(bow, long=True) or [bow])[0])
    knot = cmds.polySphere(radius=1, subdivisionsAxis=12, subdivisionsHeight=8, constructionHistory=False, name="tuxedo_bow_knot")[0]
    cmds.setAttr(f"{knot}.translate", x_center, bow_center_y, front_z + front_sign * z_size * 0.13, type="double3")
    cmds.setAttr(f"{knot}.scale", x_size * 0.055, y_size * 0.07, z_size * 0.075, type="double3")
    cmds.parent(knot, group, absolute=True)
    pieces.append((cmds.ls(knot, long=True) or [knot])[0])
    return {"character": character, "group": group, "parts": pieces, "note": "已创建可编辑的初始燕尾服块面。请先检查角色朝向、比例和穿插，再用 maya_create_mesh 制作贴合拓扑。"}


def _assign_material(payload):
    material = _safe_name(payload.get("material"), "材质名称")
    objects = payload.get("objects")
    if not isinstance(objects, list) or not objects:
        raise BridgeError("objects 必须包含至少一个网格。")
    meshes = [_mesh_transform(value, "材质对象") for value in objects]
    color = _number_vector(payload.get("color"), "color", 0.0, 1.0)
    if cmds.objExists(material):
        if cmds.nodeType(material) != "lambert":
            raise BridgeError(f"同名节点 {material} 已存在，且不是 Lambert 材质。")
    else:
        material = cmds.shadingNode("lambert", asShader=True, name=material)
    cmds.setAttr(f"{material}.color", *color, type="double3")
    shading_group = f"{material}SG"
    if not cmds.objExists(shading_group):
        shading_group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=shading_group)
        cmds.connectAttr(f"{material}.outColor", f"{shading_group}.surfaceShader", force=True)
    cmds.sets(meshes, edit=True, forceElement=shading_group)
    return {"material": material, "shadingGroup": shading_group, "objects": meshes, "color": color}


def _incoming_connection(destination):
    connections = cmds.listConnections(destination, source=True, destination=False, plugs=True) or []
    return connections[0] if connections else None


def _require_available_connection(source, destination):
    existing = _incoming_connection(destination)
    if existing and existing != source:
        raise BridgeError(f"属性 {destination} 已由其他节点连接：{existing}")


def _connect_if_needed(source, destination):
    if _incoming_connection(destination) != source:
        cmds.connectAttr(source, destination, force=False)


def _assign_file_texture(payload):
    objects = payload.get("objects")
    if not isinstance(objects, list) or not objects:
        raise BridgeError("objects 必须包含至少一个网格。")
    meshes = [_mesh_transform(value, "贴图对象") for value in objects]
    material = _safe_name(payload.get("material"), "材质名称")
    file_node = _safe_name(payload.get("file_node", f"{material}_file"), "文件贴图节点名称")
    place_node = _safe_name(f"{file_node}_place2d", "2D 放置节点名称")
    texture_path = _safe_texture_file(payload.get("texture_path"))
    color_space = payload.get("color_space")
    if color_space is not None and (not isinstance(color_space, str) or not color_space.strip() or len(color_space) > 128):
        raise BridgeError("color_space 必须是 1 到 128 个字符的 Maya 色彩空间名称。")
    if color_space is not None:
        color_space = color_space.strip()
        available_color_spaces = cmds.colorManagementPrefs(query=True, inputSpaceNames=True) or []
        if color_space not in available_color_spaces:
            raise BridgeError(f"Maya 当前 OCIO 配置中没有输入色彩空间 {color_space!r}。可用值：{available_color_spaces}")

    material_exists = cmds.objExists(material)
    if material_exists and cmds.nodeType(material) != "lambert":
        raise BridgeError(f"同名节点 {material} 已存在，且不是 Lambert 材质。")
    file_exists = cmds.objExists(file_node)
    if file_exists:
        if cmds.nodeType(file_node) != "file":
            raise BridgeError(f"同名节点 {file_node} 已存在，且不是 file 贴图节点。")
        existing_path = cmds.getAttr(f"{file_node}.fileTextureName") or ""
        if existing_path and Path(existing_path).expanduser().resolve() != texture_path:
            raise BridgeError(f"文件节点 {file_node} 已指向其他贴图，Bridge 不会覆盖：{existing_path}")
    place_exists = cmds.objExists(place_node)
    if place_exists and cmds.nodeType(place_node) != "place2dTexture":
        raise BridgeError(f"同名节点 {place_node} 已存在，且不是 place2dTexture。")

    shading_group = f"{material}SG"
    if cmds.objExists(shading_group) and cmds.nodeType(shading_group) != "shadingEngine":
        raise BridgeError(f"同名节点 {shading_group} 已存在，且不是 shadingEngine。")
    if material_exists:
        _require_available_connection(f"{file_node}.outColor", f"{material}.color")
    if cmds.objExists(shading_group):
        _require_available_connection(f"{material}.outColor", f"{shading_group}.surfaceShader")
    if file_exists:
        for source_attr, destination_attr in PLACE2D_CONNECTIONS:
            _require_available_connection(f"{place_node}.{source_attr}", f"{file_node}.{destination_attr}")

    created = []
    if not material_exists:
        material = cmds.shadingNode("lambert", asShader=True, name=material)
        created.append(material)
    if not cmds.objExists(shading_group):
        shading_group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=shading_group)
        created.append(shading_group)
    if not file_exists:
        file_node = cmds.shadingNode("file", asTexture=True, isColorManaged=True, name=file_node)
        created.append(file_node)
    if not place_exists:
        place_node = cmds.shadingNode("place2dTexture", asUtility=True, name=place_node)
        created.append(place_node)

    _require_available_connection(f"{material}.outColor", f"{shading_group}.surfaceShader")
    _require_available_connection(f"{file_node}.outColor", f"{material}.color")
    for source_attr, destination_attr in PLACE2D_CONNECTIONS:
        _require_available_connection(f"{place_node}.{source_attr}", f"{file_node}.{destination_attr}")

    cmds.setAttr(f"{file_node}.fileTextureName", str(texture_path), type="string")
    if color_space is not None:
        try:
            cmds.setAttr(f"{file_node}.colorSpace", color_space, type="string")
        except RuntimeError as error:
            raise BridgeError(f"无法把色彩空间设置为 {color_space!r}；请使用当前 Maya OCIO 配置中存在的名称。原始错误：{error}") from error
    _connect_if_needed(f"{material}.outColor", f"{shading_group}.surfaceShader")
    _connect_if_needed(f"{file_node}.outColor", f"{material}.color")
    for source_attr, destination_attr in PLACE2D_CONNECTIONS:
        _connect_if_needed(f"{place_node}.{source_attr}", f"{file_node}.{destination_attr}")
    cmds.sets(meshes, edit=True, forceElement=shading_group)
    return {
        "material": material,
        "shadingGroup": shading_group,
        "fileNode": file_node,
        "place2dTexture": place_node,
        "texturePath": str(texture_path),
        "colorSpace": cmds.getAttr(f"{file_node}.colorSpace"),
        "objects": meshes,
        "createdNodes": created,
    }


def _set_transform(payload):
    transform = _existing_transform(payload.get("object"), "对象")
    changed = {}
    for key, attribute in (("translate", "translate"), ("rotate", "rotate"), ("scale", "scale")):
        if key in payload:
            value = _number_vector(payload[key], key)
            if key == "scale" and any(item <= 0.0 for item in value):
                raise BridgeError("scale 的每一项都必须大于 0。")
            cmds.setAttr(f"{transform}.{attribute}", *value, type="double3")
            changed[key] = value
    if not changed:
        raise BridgeError("请至少提供 translate、rotate 或 scale 中的一项。")
    return {"object": transform, "changed": changed}


def _parent(payload):
    children = payload.get("children")
    if not isinstance(children, list) or not children:
        raise BridgeError("children 必须包含至少一个 transform。")
    parent = _existing_transform(payload.get("parent"), "父对象")
    resolved = [_existing_transform(child, "子对象") for child in children]
    parented = cmds.parent(resolved, parent, absolute=True)
    return {"parent": parent, "children": cmds.ls(parented, long=True) or parented}


def _bind_skin(payload):
    meshes = payload.get("meshes")
    joints = payload.get("joints")
    if not isinstance(meshes, list) or not meshes or not isinstance(joints, list) or not joints:
        raise BridgeError("meshes 与 joints 都必须至少包含一项。")
    resolved_meshes = [_mesh_transform(mesh, "服装网格") for mesh in meshes]
    resolved_joints = [_existing_transform(joint, "骨骼") for joint in joints]
    if any(cmds.nodeType(joint) != "joint" for joint in resolved_joints):
        raise BridgeError("joints 中每一项都必须是 Maya joint。")
    maximum = payload.get("max_influences", 4)
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 8:
        raise BridgeError("max_influences 必须是 1 到 8 的整数。")
    created = []
    for mesh in resolved_meshes:
        if _skin_cluster(mesh):
            raise BridgeError(f"{mesh} 已有 skinCluster；桥不会覆盖现有蒙皮。")
        short_name = mesh.rsplit("|", 1)[-1]
        cluster = cmds.skinCluster(resolved_joints, mesh, toSelectedBones=True, bindMethod=0, skinMethod=0, normalizeWeights=1, maximumInfluences=maximum, obeyMaxInfluences=True, name=f"{short_name}_skinCluster")[0]
        created.append({"mesh": mesh, "skinCluster": cluster})
    return {"created": created, "joints": resolved_joints}


def _copy_skin_weights(payload):
    source = _mesh_transform(payload.get("source_mesh"), "源角色网格")
    source_cluster = _skin_cluster(source)
    if not source_cluster:
        raise BridgeError(f"源角色网格 {source} 没有 skinCluster。")
    targets = payload.get("target_meshes")
    if not isinstance(targets, list) or not targets:
        raise BridgeError("target_meshes 必须包含至少一个已蒙皮服装网格。")
    copied = []
    for target_value in targets:
        target = _mesh_transform(target_value, "目标服装网格")
        target_cluster = _skin_cluster(target)
        if not target_cluster:
            raise BridgeError(f"目标服装网格 {target} 没有 skinCluster；请先调用 maya_bind_skin。")
        cmds.copySkinWeights(sourceSkin=source_cluster, destinationSkin=target_cluster, noMirror=True, surfaceAssociation="closestPoint", influenceAssociation=["oneToOne", "closestJoint"], normalize=True)
        copied.append({"mesh": target, "skinCluster": target_cluster})
    return {"source": source, "sourceSkinCluster": source_cluster, "copied": copied}


def _export_fbx(payload):
    filename = payload.get("filename")
    requested = payload.get("objects")
    if requested is None:
        objects = [_existing_transform(value, "导出对象") for value in (cmds.ls(selection=True, long=True) or [])]
    elif isinstance(requested, list) and requested:
        objects = [_existing_transform(value, "导出对象") for value in requested]
    else:
        raise BridgeError("objects 为空；请提供至少一个 transform，或先在 Maya 中选中对象。")
    destination = _safe_export_destination(filename, ".fbx")
    try:
        cmds.loadPlugin("fbxmaya", quiet=True)
    except RuntimeError as error:
        raise BridgeError("无法加载 Maya FBX 插件 fbxmaya。请在 Plug-in Manager 中启用 FBX。") from error
    previous_selection = cmds.ls(selection=True, long=True) or []
    cmds.select(objects, replace=True)
    escaped = str(destination).replace('\\', '\\\\').replace('"', '\\"')
    try:
        mel.eval(f'FBXExport -f "{escaped}" -s;')
    except RuntimeError as error:
        raise BridgeError(f"FBX 导出失败：{error}") from error
    finally:
        if previous_selection:
            cmds.select(previous_selection, replace=True)
        else:
            cmds.select(clear=True)
    return {"exported": str(destination), "objects": objects}


ACTION_HANDLERS = {
    "maya_status": _status,
    "maya_get_selection": _get_selection,
    "maya_scene_summary": _scene_summary,
    "maya_get_mesh_geometry": _mesh_geometry,
    "maya_get_skin_joints": _skin_joints,
    "maya_get_bind_pose_diagnostics": _bind_pose_diagnostics,
    "maya_repair_bind_pose": _repair_bind_pose,
    "maya_get_uvs": _get_uvs,
    "maya_set_uvs": _set_uvs,
    "maya_export_uv_snapshot": _export_uv_snapshot,
    "maya_create_mesh": _create_mesh,
    "maya_create_group": _create_group,
    "maya_delete_failed_tuxedo_draft": _delete_failed_tuxedo_draft,
    "maya_create_tuxedo_blockout": _create_tuxedo_blockout,
    "maya_assign_material": _assign_material,
    "maya_assign_file_texture": _assign_file_texture,
    "maya_set_transform": _set_transform,
    "maya_parent": _parent,
    "maya_bind_skin": _bind_skin,
    "maya_copy_skin_weights": _copy_skin_weights,
    "maya_export_fbx": _export_fbx,
}


def _execute_action(action, payload):
    if action not in ACTION_HANDLERS:
        raise BridgeError(f"不允许的 Bridge 操作：{action}")
    if not isinstance(payload, dict):
        raise BridgeError("payload 必须是对象。")
    return ACTION_HANDLERS[action](payload)


class _BridgeRequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if not raw:
            return
        if len(raw) > MAX_REQUEST_BYTES:
            self._respond(False, error="请求超过安全大小限制。")
            return
        try:
            request = json.loads(raw.decode("utf-8"))
            config = _load_config()
            if not hmac.compare_digest(str(request.get("token", "")), config["token"]):
                raise BridgeError("Bridge 鉴权失败。")
            action = request.get("action")
            payload = request.get("payload", {})
            result = maya_utils.executeInMainThreadWithResult(lambda: _execute_action(action, payload))
            self._respond(True, result=result)
        except (BridgeError, ValueError, TypeError, json.JSONDecodeError, RuntimeError) as error:
            self._respond(False, error=str(error))
        except Exception as error:  # Avoid crashing Maya for an unexpected client request.
            self._respond(False, error=f"Bridge 内部错误：{type(error).__name__}: {error}")

    def _respond(self, ok, result=None, error=None):
        response = {"ok": ok}
        if ok:
            response["result"] = result if result is not None else {}
        else:
            response["error"] = error or "Bridge 操作失败。"
        self.wfile.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        self.wfile.flush()


class _LoopbackServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _BridgeService:
    def __init__(self, port):
        self._server = _LoopbackServer(("127.0.0.1", port), _BridgeRequestHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="MayaCodexBridge", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


class _BridgeStatusCommand(ompx.MPxCommand):
    def doIt(self, _args):
        status = _status({})
        self.setResult(json.dumps(status, ensure_ascii=False))


def _command_creator():
    return ompx.asMPxPtr(_BridgeStatusCommand())


def initializePlugin(plugin_object):
    global _SERVICE
    # Maya passes the scripted plug-in entry point an API 1.0 MObject.  Mesh
    # creation below remains on API 2.0, but command registration must use the
    # matching API 1.0 proxy classes or Maya 2027 rejects the plug-in at load.
    plugin = ompx.MFnPlugin(plugin_object, "MonkeyNote local tools", PLUGIN_VERSION, "Any")
    plugin.registerCommand("mayaCodexBridgeStatus", _command_creator)
    config = _load_config()
    _SERVICE = _BridgeService(config["port"])
    _SERVICE.start()
    om1.MGlobal.displayInfo(f"Maya Codex Bridge {PLUGIN_VERSION} 已启动：仅本机 127.0.0.1:{config['port']}，使用 Maya Python API。")


def uninitializePlugin(plugin_object):
    global _SERVICE
    if _SERVICE is not None:
        _SERVICE.stop()
        _SERVICE = None
    plugin = ompx.MFnPlugin(plugin_object)
    plugin.deregisterCommand("mayaCodexBridgeStatus")
    om1.MGlobal.displayInfo("Maya Codex Bridge 已停止。")

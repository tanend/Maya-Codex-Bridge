#!/usr/bin/env python3
"""MCP stdio server for the local Maya Codex Bridge.

The server deliberately exposes a small, typed tool surface.  It never accepts
arbitrary MEL or Python from the model; Maya-side operations are allowlisted in
maya_codex_bridge.py.
"""

import json
import os
import socket
import sys
from pathlib import Path


MAX_MCP_MESSAGE_BYTES = 12_000_000
DEFAULT_CONFIG_PATH = Path.home() / ".maya_codex_bridge" / "config.json"
DEFAULT_PROTOCOL_VERSION = "2026-07-28"
DEBUG_LOG_PATH = os.environ.get("MAYA_CODEX_MCP_DEBUG_LOG", "")
_stdio_framing = "newline"


TOOLS = [
    {
        "name": "maya_status",
        "description": "Read Maya version, current scene, connection state, and configured FBX export folder. Read-only.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "maya_get_selection",
        "description": "Read the current Maya selection, mesh statistics, bounds, and skin-cluster summary. Read-only.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "maya_scene_summary",
        "description": "Read a compact list of mesh transforms in the current Maya scene. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Maximum mesh transforms returned; defaults to 100."}},
        },
    },
    {
        "name": "maya_get_mesh_geometry",
        "description": "Read world-space vertices and polygon faces for one supplied or selected polygon mesh. Read-only; intended for fitted clothing generation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mesh": {"type": "string", "description": "Optional mesh transform. Defaults to exactly one selected mesh."}
            },
        },
    },
    {
        "name": "maya_get_skin_joints",
        "description": "Read the joints influencing one supplied or selected skinned mesh, including hierarchy and world positions. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mesh": {"type": "string", "description": "Optional skinned mesh transform. Defaults to exactly one selected mesh."}
            },
        },
    },
    {
        "name": "maya_get_bind_pose_diagnostics",
        "description": "Read a skinned mesh's skinCluster bind-pose connection, all bindPose members, required influence ancestors, and bindPreMatrix agreement. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mesh": {"type": "string", "description": "Optional skinned mesh transform. Defaults to exactly one selected mesh."},
                "matrix_tolerance": {"type": "number", "minimum": 1e-8, "maximum": 0.01, "description": "Maximum element delta for bind-matrix comparison; defaults to 1e-5."}
            },
        },
    },
    {
        "name": "maya_repair_bind_pose",
        "description": "Add only the exact bindPose members identified by a fresh diagnostic and optionally restore a missing skinCluster bindPose connection. It refuses pose, membership, matrix, or scene-state conflicts and never changes weights or joint transforms.",
        "inputSchema": {
            "type": "object",
            "required": ["mesh", "pose", "expected_missing_members", "confirm_current_pose_is_bind_pose"],
            "properties": {
                "mesh": {"type": "string", "description": "Explicit skinned mesh transform to repair."},
                "pose": {"type": "string", "description": "Exact existing bindPose returned by the diagnostic."},
                "expected_missing_members": {"type": "array", "items": {"type": "string"}, "description": "Exact missing-member array returned by the latest diagnostic."},
                "connect_skin_cluster": {"type": "boolean", "description": "Must match the diagnostic repair plan; defaults to false."},
                "confirm_current_pose_is_bind_pose": {"type": "boolean", "description": "Must be true after the user confirms the rig is at its intended bind pose."},
                "matrix_tolerance": {"type": "number", "minimum": 1e-8, "maximum": 0.01, "description": "Optional diagnostic tolerance; defaults to 1e-5."}
            },
        },
    },
    {
        "name": "maya_get_uvs",
        "description": "Read UV sets, unique UV coordinates, and per-face-vertex UV assignments for one supplied or selected polygon mesh. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mesh": {"type": "string", "description": "Optional mesh transform. Defaults to exactly one selected mesh."},
                "uv_set": {"type": "string", "description": "Optional UV set name. Defaults to the mesh's current UV set."}
            },
        },
    },
    {
        "name": "maya_set_uvs",
        "description": "Replace one UV set on an explicit polygon mesh using unique UV coordinates plus one UV-id array per polygon. Topology is validated before the existing UV assignments are changed.",
        "inputSchema": {
            "type": "object",
            "required": ["mesh", "uvs", "face_uvs"],
            "properties": {
                "mesh": {"type": "string", "description": "Explicit polygon mesh transform to modify."},
                "uv_set": {"type": "string", "description": "Optional UV set name. Defaults to the current UV set."},
                "create_if_missing": {"type": "boolean", "description": "Create the explicitly named UV set when it does not exist. Defaults to false."},
                "uvs": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100000,
                    "description": "Unique UV coordinates as [u, v] pairs.",
                    "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}}
                },
                "face_uvs": {
                    "type": "array",
                    "maxItems": 300000,
                    "description": "One UV-id array per polygon, in polygon vertex order. Each array must be empty or match that polygon's vertex count.",
                    "items": {"type": "array", "items": {"type": "integer", "minimum": 0}}
                }
            },
        },
    },
    {
        "name": "maya_export_uv_snapshot",
        "description": "Export a PNG UV wireframe snapshot for one explicit mesh. Output is confined to the configured bridge export folder and an existing file is never overwritten.",
        "inputSchema": {
            "type": "object",
            "required": ["mesh", "filename"],
            "properties": {
                "mesh": {"type": "string", "description": "Explicit polygon mesh transform to snapshot."},
                "filename": {"type": "string", "description": "Simple PNG file name only; .png is added when omitted."},
                "uv_set": {"type": "string", "description": "Optional UV set name. Defaults to the current UV set."},
                "width": {"type": "integer", "minimum": 64, "maximum": 8192, "description": "Image width; defaults to 2048."},
                "height": {"type": "integer", "minimum": 64, "maximum": 8192, "description": "Image height; defaults to 2048."},
                "wire_color": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "integer", "minimum": 0, "maximum": 255}, "description": "RGB wire color; defaults to white."},
                "entire_uv_range": {"type": "boolean", "description": "Fit all UV tiles instead of the default 0-1 tile. Defaults to false."},
                "u_min": {"type": "number"},
                "u_max": {"type": "number"},
                "v_min": {"type": "number"},
                "v_max": {"type": "number"}
            },
        },
    },
    {
        "name": "maya_create_mesh",
        "description": "Create one polygon mesh from explicit vertices and polygon faces through the Maya API. This adds a new mesh and never edits an existing one.",
        "inputSchema": {
            "type": "object",
            "required": ["name", "vertices", "faces"],
            "properties": {
                "name": {"type": "string", "description": "New transform name; letters, digits, and underscores only."},
                "vertices": {"type": "array", "description": "Array of [x, y, z] positions in Maya world units.", "items": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}}},
                "faces": {"type": "array", "description": "Array of polygon index arrays; every face has at least 3 vertex indices.", "items": {"type": "array", "minItems": 3, "items": {"type": "integer", "minimum": 0}}},
                "parent": {"type": "string", "description": "Optional existing transform to parent the new mesh under."}
            },
        },
    },
    {
        "name": "maya_create_group",
        "description": "Create one empty transform group with a safe name. This adds a new group and does not modify existing nodes.",
        "inputSchema": {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string", "description": "New group name; letters, digits, and underscores only."}},
        },
    },
    {
        "name": "maya_delete_failed_tuxedo_draft",
        "description": "Delete only the exact 16 unskinned polySurface draft meshes created by bridge v0.1, after verifying their fixed face-count fingerprint. It refuses any mismatch and never deletes character nodes, materials, or arbitrary objects.",
        "inputSchema": {
            "type": "object",
            "required": ["confirm"],
            "properties": {"confirm": {"type": "boolean", "description": "Must be true after the user requests replacement of the failed draft."}},
        },
    },
    {
        "name": "maya_create_tuxedo_blockout",
        "description": "Create a separate, editable tuxedo blockout around one selected character mesh. It adds jacket, tails, vest, shirt front, lapels, and bow tie as simple meshes; it does not modify the character or rig it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "character": {"type": "string", "description": "Optional character mesh transform. Defaults to the selected mesh transform."},
                "name": {"type": "string", "description": "Optional group name; defaults to tuxedo_blockout."},
                "front_axis": {"type": "string", "enum": ["+Z", "-Z"], "description": "Character-facing direction. Defaults to +Z."}
            },
        },
    },
    {
        "name": "maya_assign_material",
        "description": "Create or reuse a Lambert material with an RGB color and assign it to mesh transforms. This changes only material assignments on the supplied objects.",
        "inputSchema": {
            "type": "object",
            "required": ["objects", "material", "color"],
            "properties": {
                "objects": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "material": {"type": "string", "description": "Material name; letters, digits, and underscores only."},
                "color": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "minimum": 0, "maximum": 1}}
            },
        },
    },
    {
        "name": "maya_assign_file_texture",
        "description": "Create or safely reuse a Lambert material, file texture, and 2D placement node, connect the texture to material color, and assign it to explicit mesh transforms. The texture file must be inside a configured texture root.",
        "inputSchema": {
            "type": "object",
            "required": ["objects", "material", "texture_path"],
            "properties": {
                "objects": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "material": {"type": "string", "description": "Lambert material name; letters, digits, and underscores only."},
                "texture_path": {"type": "string", "description": "Absolute image path inside a configured texture root."},
                "file_node": {"type": "string", "description": "Optional file node name; defaults to <material>_file."},
                "color_space": {"type": "string", "description": "Optional Maya input color-space name, such as sRGB or Raw."}
            },
        },
    },
    {
        "name": "maya_set_transform",
        "description": "Set translation, rotation, or scale on one transform. This changes only the supplied transform attributes.",
        "inputSchema": {
            "type": "object",
            "required": ["object"],
            "properties": {
                "object": {"type": "string"},
                "translate": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
                "rotate": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
                "scale": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}}
            },
        },
    },
    {
        "name": "maya_parent",
        "description": "Parent one or more supplied transforms under another supplied transform without changing world-space positions.",
        "inputSchema": {
            "type": "object",
            "required": ["children", "parent"],
            "properties": {
                "children": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "parent": {"type": "string"}
            },
        },
    },
    {
        "name": "maya_bind_skin",
        "description": "Create new skin clusters for supplied clothing meshes using supplied joint transforms. Existing skin clusters are left untouched.",
        "inputSchema": {
            "type": "object",
            "required": ["meshes", "joints"],
            "properties": {
                "meshes": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "joints": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "max_influences": {"type": "integer", "minimum": 1, "maximum": 8, "description": "Defaults to 4."}
            },
        },
    },
    {
        "name": "maya_copy_skin_weights",
        "description": "Copy skin weights from one skinned character mesh to supplied already-skinned clothing meshes using closest-point matching.",
        "inputSchema": {
            "type": "object",
            "required": ["source_mesh", "target_meshes"],
            "properties": {
                "source_mesh": {"type": "string"},
                "target_meshes": {"type": "array", "minItems": 1, "items": {"type": "string"}}
            },
        },
    },
    {
        "name": "maya_export_fbx",
        "description": "Export supplied objects, or the current selection, as FBX. filename must be a simple file name and output is confined to the bridge's configured export folder.",
        "inputSchema": {
            "type": "object",
            "required": ["filename"],
            "properties": {
                "filename": {"type": "string", "description": "File name only; .fbx is added when omitted."},
                "objects": {"type": "array", "items": {"type": "string"}, "description": "Optional transforms to export. Defaults to current selection."}
            },
        },
    },
]


def _config_path():
    return Path(os.environ.get("MAYA_CODEX_BRIDGE_CONFIG", str(DEFAULT_CONFIG_PATH))).expanduser()


def _load_config():
    path = _config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"找不到 Bridge 配置：{path}。请先运行 scripts/install_macos.py 并在 Maya 中加载插件。") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Bridge 配置不是有效 JSON：{path}") from error
    if not isinstance(data.get("token"), str) or not data["token"]:
        raise RuntimeError("Bridge 配置中没有有效 token。请重新运行安装脚本以生成新的本机令牌。")
    return data


def _bridge_call(action, payload):
    config = _load_config()
    port = int(config.get("port", 17321))
    request = json.dumps({"token": config["token"], "action": action, "payload": payload}, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.settimeout(30)
            client.sendall(request)
            response = client.makefile("rb").readline(MAX_MCP_MESSAGE_BYTES + 1)
    except OSError as error:
        raise RuntimeError(f"无法连接 Maya Bridge (127.0.0.1:{port})。请确认 Maya 2027 已打开且 maya_codex_bridge.py 已 Loaded。原始错误：{error}") from error
    if not response:
        raise RuntimeError("Maya Bridge 没有返回响应。请在 Maya Script Editor 中检查 Bridge 报错。")
    if len(response) > MAX_MCP_MESSAGE_BYTES:
        raise RuntimeError("Maya Bridge 响应超过安全大小限制。")
    try:
        decoded = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Maya Bridge 返回了无效响应。") from error
    if not decoded.get("ok"):
        raise RuntimeError(decoded.get("error", "Maya Bridge 操作失败。"))
    return decoded.get("result", {})


def _tool_result(name, arguments):
    known_tools = {tool["name"] for tool in TOOLS}
    if name not in known_tools:
        return {"content": [{"type": "text", "text": f"未知 Maya 工具：{name}"}], "isError": True}
    try:
        result = _bridge_call(name, arguments or {})
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "isError": False}
    except (RuntimeError, ValueError, TypeError) as error:
        return {"content": [{"type": "text", "text": str(error)}], "isError": True}


def _debug(event, **fields):
    if not DEBUG_LOG_PATH:
        return
    record = {"event": event, **fields}
    try:
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _read_message():
    global _stdio_framing
    while True:
        raw = sys.stdin.buffer.readline(MAX_MCP_MESSAGE_BYTES + 1)
        if not raw:
            return None
        if len(raw) > MAX_MCP_MESSAGE_BYTES:
            raise ValueError("MCP stdio 消息超过安全大小限制。")
        if raw.strip():
            break

    if raw.lower().startswith(b"content-length:"):
        _stdio_framing = "content-length"
        headers = {}
        while True:
            line = raw.decode("utf-8").strip()
            if not line:
                break
            if ":" not in line:
                raise ValueError("MCP stdio 消息头格式无效。")
            key, value = line.split(":", 1)
            headers[key.lower()] = value.strip()
            raw = sys.stdin.buffer.readline(MAX_MCP_MESSAGE_BYTES + 1)
            if not raw:
                raise ValueError("MCP stdio 消息头未完整读取。")
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError as error:
            raise ValueError("MCP stdio 的 Content-Length 不是整数。") from error
        if length <= 0 or length > MAX_MCP_MESSAGE_BYTES:
            raise ValueError("MCP stdio 的 Content-Length 无效或超过安全限制。")
        body = sys.stdin.buffer.read(length)
        if len(body) != length:
            raise ValueError("MCP stdio 消息体未完整读取。")
        return json.loads(body.decode("utf-8"))

    _stdio_framing = "newline"
    return json.loads(raw.decode("utf-8"))


def _write_message(payload):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_MCP_MESSAGE_BYTES:
        raise ValueError("MCP stdio 响应超过安全大小限制。")
    if _stdio_framing == "content-length":
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
        sys.stdout.buffer.write(body)
    else:
        sys.stdout.buffer.write(body + b"\n")
    sys.stdout.buffer.flush()


def _handle(message):
    method = message.get("method")
    params = message.get("params", {})
    if method == "initialize":
        requested_version = params.get("protocolVersion")
        protocol_version = requested_version if isinstance(requested_version, str) and requested_version else DEFAULT_PROTOCOL_VERSION
        return {"protocolVersion": protocol_version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "maya-codex-bridge", "version": "0.2.0"}}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return _tool_result(params.get("name", ""), params.get("arguments", {}))
    if method == "ping":
        return {}
    return {"error": {"code": -32601, "message": f"Method not found: {method}"}}


def main():
    _debug("server_started", pid=os.getpid())
    while True:
        try:
            message = _read_message()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            _write_message({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(error)}})
            continue
        if message is None:
            _debug("stdin_closed")
            return
        _debug("message_received", framing=_stdio_framing, method=message.get("method"), id=message.get("id"))
        response = _handle(message)
        if "id" in message:
            if "error" in response:
                _write_message({"jsonrpc": "2.0", "id": message["id"], "error": response["error"]})
            else:
                _write_message({"jsonrpc": "2.0", "id": message["id"], "result": response})
            _debug("response_sent", framing=_stdio_framing, id=message.get("id"))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Regression test for the local plugin's standard newline-delimited MCP stdio transport."""

import json
import subprocess
import sys
from pathlib import Path


SERVER = Path(__file__).resolve().parents[1] / "mcp" / "mcp_server.py"


def request(process, payload):
    process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read()
        raise AssertionError(f"MCP server closed without a response. stderr={stderr!r}")
    return json.loads(line)


def main():
    process = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        initialized = request(process, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "maya-bridge-test", "version": "1"},
            },
        })
        assert initialized["result"]["protocolVersion"] == "2026-07-28", initialized

        process.stdin.write(json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }, separators=(",", ":")) + "\n")
        process.stdin.flush()

        tools = request(process, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        names = {tool["name"] for tool in tools["result"]["tools"]}
        required = {
            "maya_status",
            "maya_get_selection",
            "maya_get_mesh_geometry",
            "maya_get_bind_pose_diagnostics",
            "maya_repair_bind_pose",
            "maya_create_mesh",
            "maya_get_uvs",
            "maya_set_uvs",
            "maya_export_uv_snapshot",
            "maya_assign_file_texture",
        }
        assert required.issubset(names), names
        schemas = {tool["name"]: tool["inputSchema"] for tool in tools["result"]["tools"]}
        assert schemas["maya_set_uvs"]["required"] == ["mesh", "uvs", "face_uvs"]
        assert schemas["maya_export_uv_snapshot"]["properties"]["width"]["maximum"] == 8192
        assert "texture_path" in schemas["maya_assign_file_texture"]["required"]
        assert schemas["maya_repair_bind_pose"]["required"] == [
            "mesh",
            "pose",
            "expected_missing_members",
            "confirm_current_pose_is_bind_pose",
        ]
        print(json.dumps({
            "ok": True,
            "protocolVersion": initialized["result"]["protocolVersion"],
            "toolCount": len(names),
        }, ensure_ascii=False))
    finally:
        process.terminate()
        process.wait(timeout=3)


if __name__ == "__main__":
    main()

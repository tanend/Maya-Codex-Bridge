# Maya Codex Bridge for Maya 2027

[中文说明](README.zh-CN.md) · [MIT License](LICENSE)

This is a local, authenticated bridge for constrained modeling work in **Autodesk Maya 2027**. Codex talks to it through MCP; the bridge talks to Maya through Maya's Python API on its main thread. It does **not** control Maya through the desktop, mouse, keyboard, or screen coordinates.

## What it can do

- Read the current scene, selection, mesh bounds, topology, geometry, skin-cluster state, and influencing joints.
- Diagnose skinCluster-to-bindPose connections, pose membership, required influence ancestors, and agreement with each influence's bindPreMatrix.
- Read complete UV sets as unique coordinates plus per-face-vertex assignments, and replace a validated UV set on an explicit mesh.
- Export non-overwriting PNG UV snapshots to the configured export folder.
- Create explicit polygon meshes and transform groups. A tuxedo blockout remains available as a narrowly scoped legacy helper.
- Create/assign simple Lambert materials, connect allowlisted local image textures through a file/place2dTexture network, and adjust transforms.
- Parent created assets, bind only previously unskinned meshes to supplied existing joints, and copy skin weights from a source mesh.
- Repair only the exact missing bindPose members reported by a fresh diagnostic, with explicit bind-pose confirmation and rollback if matrices or validation change unexpectedly.
- Export only explicitly supplied objects to FBX inside a dedicated export folder.

It exposes no arbitrary MEL/Python execution, scene saving, review screenshots, unrestricted component editing, or export outside the configured folder. Write tools can change existing objects, materials, UVs, transforms, hierarchy, and destination skin weights; review targets before using them. FBX exports and UV snapshots refuse existing output files, and file textures must resolve beneath a configured `texture_roots` directory. Its one delete action checks a fixed name/topology fingerprint for a failed tuxedo draft from bridge v0.1; that check is not proof of asset ownership and is not a general delete tool.

Bind-pose repair is deliberately narrow: it never deletes or replaces a dagPose, moves a joint, changes skin weights, or silently switches a skinCluster to a conflicting pose. Run `maya_get_bind_pose_diagnostics` first, place the rig at its intended bind pose, then pass the returned pose and exact missing-member list to `maya_repair_bind_pose` with explicit confirmation.

## Install Maya-side bridge (macOS)

Requirements: macOS, an installed Autodesk Maya 2027, a local Python 3 interpreter, and a local MCP client such as Codex. The installer and MCP server use the Python standard library; no pip dependencies are required. Windows, Linux, other Maya versions, and other MCP clients have not been validated by this package. FBX export additionally requires Maya's `fbxmaya` plug-in.

Clone the repository and open its folder:

```bash
git clone https://github.com/tanend/Maya-Codex-Bridge.git
cd Maya-Codex-Bridge
```

Alternatively, download and extract the repository ZIP, then open a terminal in the folder containing `maya/`, `mcp/`, and `scripts/`. Install the Maya-side bridge:

```bash
python3 scripts/install_macos.py --maya-version 2027
```

The installer writes the following two local items:

- `~/Library/Preferences/Autodesk/maya/2027/plug-ins/maya_codex_bridge.py`
- `~/.maya_codex_bridge/config.json` — a private random token, port, permitted export folder, and permitted texture input folders.

It creates `~/Documents/MayaCodexBridgeExports` as the only FBX/UV-snapshot export directory and, by default, the only texture input directory. To allow project textures from one or more existing folders, repeat `--texture-root` during install or update:

```bash
python3 scripts/install_macos.py --maya-version 2027 --replace \
  --texture-root /absolute/path/to/project/sourceimages
```

The configuration file is created with owner-only read/write permissions when macOS permits it. Texture paths are resolved before the directory check, so symlinks cannot escape the allowlist.

Open Maya 2027, then use `Windows → Settings/Preferences → Plug-in Manager`. Load `maya_codex_bridge.py` and enable **Auto load**. The command `mayaCodexBridgeStatus` in Maya's Script Editor can confirm that it started.

If updating a prior installation, review the change and run the installer with `--replace`; it otherwise refuses to overwrite the existing Maya plug-in.

## Connect Codex

### Direct MCP setup (no private marketplace required)

From this package folder, with the Codex CLI installed:

```bash
codex mcp add maya-codex-bridge -- python3 "$PWD/mcp/mcp_server.py"
codex mcp get maya-codex-bridge
```

This registers an absolute server path. Keep the source folder at that location. If the desktop client cannot locate `python3`, configure its absolute interpreter path. Restart the client or start a new task after registration. See the [official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp).

Direct MCP registration loads tools, but does not install the bundled Skill. Before modeling, ask the agent to read `skills/maya-modeling/SKILL.md` from this package. The separate workspace `maya-bridge-workflow` Skill is optional and is not included here.

### Optional Codex plugin packaging

The `.mcp.json` file launches `mcp/mcp_server.py` through the macOS `python3` command. It uses the same local configuration file as the Maya plug-in, then sends authenticated requests to `127.0.0.1:17321`.

The `.codex-plugin/plugin.json` manifest packages the MCP server and `maya-modeling` Skill together. If your Codex installation has this source registered in a personal plugin marketplace, install **Maya Codex Bridge** there, then start a new task. This source does not include a public marketplace registration. Choose either direct MCP setup or plugin installation to avoid duplicate tool registrations.

The bridge uses authenticated loopback TCP on this Mac, not an internet endpoint. Its port is a private bridge protocol, not an HTTP MCP URL. The AI client may send tool results, including scene names, paths, and geometry, to its model provider; local bridge transport does not imply offline AI processing. The token is generated locally and is not an OpenAI API key. Never publish the private configuration file or debug logs containing scene data.

The bundled stdio server uses standard newline-delimited JSON-RPC and accepts legacy `Content-Length` framing for compatibility. To verify the handshake and tool discovery after changing the Codex-side server, run:

```bash
python3 scripts/test_mcp_stdio.py
```

If a new Codex task can see the `maya-modeling` skill but exposes no `maya_*` tools, reinstall the personal plugin so Codex refreshes its cached MCP server. A Codex-side-only update does not require reloading the already-running Maya plug-in.

## Start a modeling task

1. Open the intended Maya scene and select the target object when the task operates on an existing asset.
2. In a new Codex task, load the bundled `maya-modeling` Skill, or ask the agent to read its file when using direct MCP setup.
3. Call `maya_status` and `maya_get_selection`; use `maya_scene_summary` if the target is unclear.
4. Read real mesh geometry and skin joints only when the asset depends on an existing surface or rig.
5. Confirm the asset type, axes, units, scope, polygon budget, references, and authorized stages before making changes.
6. Work through blockout, silhouette/volume, construction/topology, detail, and QA gates. Only perform materials, skinning, or FBX export when explicitly requested.

Task-specific facts such as a character name, facing axis, garment construction, or polygon budget belong in the project's task document, not in the general plugin Skill. The tuxedo blockout helper deliberately creates simple geometry and must never be described as a production asset.

## UV and texture workflow

1. Use `maya_get_uvs` to read the current or named UV Set. Its `uvs` array contains unique `[u, v]` values; `faceUvs` contains one UV-id array per polygon in polygon-vertex order.
2. Pass edited coordinates and assignments to `maya_set_uvs` as `uvs` and `face_uvs`. The mesh must be explicit, face counts are topology-checked, and a missing UV Set is created only with an explicit name plus `create_if_missing=true`.
3. Use `maya_export_uv_snapshot` with an explicit mesh and a simple PNG filename. The result is written under `export_root`; choose a new filename if one already exists.
4. Put an image beneath one of the configured `texture_roots`, then use `maya_assign_file_texture` with explicit objects, a Lambert material name, and the absolute image path. An existing file node that points elsewhere or a material input already driven by another node is rejected instead of overwritten.

UV writing and texture assignment change the Maya scene and still require the normal read-only baseline plus a confirmed target. Neither operation saves the scene.

## Troubleshooting and updates

| Symptom | Check |
| --- | --- |
| No `maya_*` tools | Check MCP registration, the absolute server path, and the Python executable; restart the client. For plugin installs, check the installed cache/version. |
| Tools exist but connection fails | Open Maya, load the plugin, and check whether another Maya process owns port 17321. The default setup targets one running bridge instance. |
| Missing configuration / authentication failure | Run the installer and ensure Maya and the MCP server use the same local configuration. Do not paste the token into an issue. |
| Texture path rejected | Use an existing folder passed with `--texture-root`; resolved symlinks must stay within allowed roots. |
| Output already exists | Choose a new filename; FBX and UV snapshots do not overwrite. |
| Bind-pose repair refused | Read fresh diagnostics and resolve the reported conflict; confirmation does not override matrix or membership checks. |

For Maya-side updates, review the source, rerun the installer with `--replace`, then restart Maya or unload/reload the bridge. Configuration changes also require reloading the bridge. Repeated `--texture-root` values replace the configured list; supply all desired roots. An existing configuration preserves its `export_root`, even if the installer receives a different `--export-root`; inspect and edit that field locally if changing an existing installation.

To stop using the bridge, disable Auto load and unload it in Maya, then remove its MCP entry or uninstall its Codex plugin. Exported assets are separate from the installation.

## Development and verification

Run from this package folder:

```bash
python3 scripts/test_mcp_stdio.py
python3 scripts/test_maya_bind_pose_tools.py
python3 scripts/test_maya_uv_texture_tools.py
```

These are offline tests: MCP initialization/tool discovery, and fake-Maya regression tests for bind-pose and UV/texture logic. They do not open Maya or prove viewport quality, real-rig behavior, FBX round trips, or cross-platform compatibility. All three passed during documentation review on 2026-09-05; tool discovery returned 21 tools.

The architecture is `MCP client → stdio server → authenticated loopback TCP → Maya main-thread dispatch → allowlisted handlers`. Add new tools to both `mcp/mcp_server.py` and the Maya-side handler map, validate inputs and targets, document mutations, and add relevant regression coverage. Skill instructions guide the agent but are not an OS sandbox or an independent permission system.

`scripts/create_tuxedo_asset.py`, `maya_create_tuxedo_blockout`, and `maya_delete_failed_tuxedo_draft` are legacy asset-specific helpers, not general getting-started examples. Review their assumptions before use.

## Related projects and project status

Maya MCP bridges already exist. This project's focus is a small allowlisted surface, local authentication, constrained file paths, and guarded bind-pose workflows, rather than being the first or most comprehensive bridge.

- [GG_MayaMCP](https://github.com/GimbalGoats/GG_MayaMCP): typed tools over Maya commandPort, Codex configuration, raw execution disabled by default.
- [maya-mcp-server](https://github.com/chadrik/maya-mcp-server): multi-session discovery and arbitrary Python execution.
- [dcc-mcp-maya](https://github.com/dcc-mcp/dcc-mcp-maya): a broader DCC integration with a gateway and multi-instance support.

Descriptions reflect upstream READMEs checked on 2026-09-05, not comparative testing or a security audit. This is an independent project, not an official Autodesk or OpenAI product. This tool was built for personal learning and modeling workflows and is shared for anyone who finds it useful. Updates follow the maintainer's own needs; feature requests and reproducible bug reports are welcome, without a guaranteed support schedule.

## License

Copyright (c) 2026 tanend. Released under the [MIT License](LICENSE).

Commercial use, modification, and redistribution are permitted, provided the copyright notice and permission notice are retained in copies or substantial portions of the software. The software is provided as is, without warranty; see LICENSE for the full terms.

This license covers the code and documentation in this repository. Autodesk Maya is not included; users must obtain a Maya license appropriate for their use. This repository does not grant commercial-use rights to Maya or to assets created under an educational license.

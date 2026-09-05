#!/usr/bin/env python3
"""Install the Maya-side Python plug-in and create local bridge configuration.

This script intentionally does not install packages, contact a network service,
or edit Maya.env.  Maya's per-version plug-ins directory is already searched by
Maya on macOS.  Existing plug-in files are not overwritten unless --replace is
passed explicitly.
"""

import argparse
import json
import os
import secrets
import shutil
import stat
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = PACKAGE_ROOT / "maya" / "maya_codex_bridge.py"
BRIDGE_HOME = Path.home() / ".maya_codex_bridge"
CONFIG_PATH = BRIDGE_HOME / "config.json"


def _write_config(export_root, requested_texture_roots):
    BRIDGE_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    export_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        config.setdefault("port", 17321)
        config.setdefault("export_root", str(export_root))
        if requested_texture_roots:
            config["texture_roots"] = [str(path) for path in requested_texture_roots]
        else:
            config.setdefault("texture_roots", [str(export_root)])
        if not config.get("token"):
            config["token"] = secrets.token_urlsafe(32)
        message = "已保留现有本机令牌"
    else:
        config = {
            "token": secrets.token_urlsafe(32),
            "port": 17321,
            "export_root": str(export_root),
            "texture_roots": [str(path) for path in requested_texture_roots] if requested_texture_roots else [str(export_root)],
        }
        message = "已生成新的本机令牌"
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        CONFIG_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return config, message


def main():
    parser = argparse.ArgumentParser(description="Install the Maya Codex Bridge plug-in for macOS.")
    parser.add_argument("--maya-version", default="2027", help="Maya version folder, default: 2027")
    parser.add_argument("--replace", action="store_true", help="Replace an existing maya_codex_bridge.py after you have reviewed it.")
    parser.add_argument("--export-root", type=Path, default=Path.home() / "Documents" / "MayaCodexBridgeExports", help="Folder to which the bridge may export FBX files and UV snapshots.")
    parser.add_argument("--texture-root", type=Path, action="append", default=[], help="Folder from which file textures may be assigned. Repeat to allow multiple roots; defaults to the export folder.")
    args = parser.parse_args()
    if not PLUGIN_SOURCE.is_file():
        raise SystemExit(f"Bridge source is missing: {PLUGIN_SOURCE}")
    texture_roots = [path.expanduser().resolve() for path in args.texture_root]
    for texture_root in texture_roots:
        if not texture_root.is_dir():
            raise SystemExit(f"Texture root is not an existing folder: {texture_root}")
    target_dir = Path.home() / "Library" / "Preferences" / "Autodesk" / "maya" / str(args.maya_version) / "plug-ins"
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = target_dir / PLUGIN_SOURCE.name
    if target.exists() and not args.replace:
        raise SystemExit(f"Bridge already exists: {target}\nReview it, then rerun with --replace to update it.")
    shutil.copy2(PLUGIN_SOURCE, target)
    config, config_message = _write_config(args.export_root.expanduser().resolve(), texture_roots)
    print(f"Installed Maya plug-in: {target}")
    print(f"Bridge config: {CONFIG_PATH} ({config_message}; port {config['port']})")
    print(f"Allowed FBX and UV snapshot export folder: {config['export_root']}")
    print(f"Allowed texture folders: {', '.join(config['texture_roots'])}")
    print("Next: start Maya 2027, open Windows > Settings/Preferences > Plug-in Manager,")
    print(f"then load {target.name} and enable Auto load.")


if __name__ == "__main__":
    main()

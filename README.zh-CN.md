# Maya Codex Bridge

[English](README.md) · [MIT 许可证](LICENSE)

让 Codex 通过 MCP 检查和操作 Autodesk Maya 2027。插件使用本机令牌认证和明确的操作白名单，适合逐步建模、网格检查、UV 数据编辑、蒙皮诊断与受限 FBX 导出。

它通过 Maya API 在主线程处理操作，不依赖鼠标、键盘或屏幕坐标。项目侧重可检查、范围明确的操作，不承诺一句话生成生产级资产。

## 当前能力

| 范围 | 能力 |
| --- | --- |
| 场景检查 | Maya 状态、选择、网格摘要、边界、顶点与面、蒙皮关节 |
| 建模 | 从明确的顶点与面创建网格、创建组、调整变换与父子层级 |
| UV 与材质 | 读取/写入显式 UV 数据、导出 UV 线框 PNG、Lambert 材质、白名单目录内的文件贴图 |
| 蒙皮 | 将尚未蒙皮的网格绑定到现有关节、复制权重 |
| Bind pose | 检查 skinCluster、dagPose 成员与绑定矩阵；只按最新诊断修复符合条件的缺失成员或连接 |
| 交付 | 在限定目录内导出 FBX 和 UV 快照，拒绝覆盖已有文件 |

当前提供 21 个工具，其中包含两个燕尾服历史工具。不提供任意 Python/MEL 执行、通用删除、场景保存、视口截图、自动拓扑或自动 UV 展开/打包。UV 写入需要调用方提供坐标与面角索引。

白名单不代表只读：写入工具可能改变已有对象、UV、材质、变换、层级及目标权重。Skill 规定的确认流程是使用约定，并不是独立的权限系统。历史删除工具使用固定名称/拓扑指纹检查，不能证明对象确实由桥接创建。

## 安装环境

- 当前面向 macOS + Maya 2027；未验证 Windows、Linux 和其他 Maya 版本。
- 本机需有 `python3`，安装器和 MCP 服务仅使用 Python 标准库，无需安装 pip 依赖。
- 需要本地 MCP 客户端；下方提供 Codex CLI 接入方式。
- FBX 导出需要 Maya 的 `fbxmaya` 插件。

## 快速开始

克隆仓库并进入插件目录：

```bash
git clone https://github.com/tanend/Maya-Codex-Bridge.git
cd Maya-Codex-Bridge
```

也可以下载仓库 ZIP 并解压，在包含 `maya/`、`mcp/`、`scripts/` 的插件目录打开终端。

### 1. 安装 Maya 端

```bash
python3 scripts/install_macos.py --maya-version 2027
```

安装器创建：

- `~/Library/Preferences/Autodesk/maya/2027/plug-ins/maya_codex_bridge.py`
- `~/.maya_codex_bridge/config.json`：本机令牌、端口和目录配置，不要上传。
- `~/Documents/MayaCodexBridgeExports`：默认导出目录，也作为默认贴图输入目录。

在 Maya 的 `Windows → Settings/Preferences → Plug-in Manager` 加载 `maya_codex_bridge.py`，勾选 **Auto load**。

已有插件时，安装器默认拒绝覆盖；审阅更新后使用 `--replace`，随后重启 Maya 或重新加载插件。

### 2. 接入 Codex

在同一插件目录执行：

```bash
codex mcp add maya-codex-bridge -- python3 "$PWD/mcp/mcp_server.py"
codex mcp get maya-codex-bridge
```

此方式不依赖作者的个人插件市场。保持源码目录位置不变，然后重启客户端或新建任务。如果客户端找不到 `python3`，将其配置为本机 Python 解释器的绝对路径。配置方式参见 [OpenAI 官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp)。

直接注册 MCP 只加载工具，不会安装 Skill。使用时请让 Codex 读取本插件的 `skills/maya-modeling/SKILL.md`。本包不包含作者工作区另行维护的 `maya-bridge-workflow` Skill，也不以它为安装前提。

源码同时保留 `.codex-plugin/plugin.json`，供已配置插件市场的用户安装 MCP + Skill 组合包。两种方式选择一种，避免重复注册；当前尚无随本包提供的公共市场条目。

### 3. 先做只读检查

在 Maya 打开目标场景、选中目标对象，然后向 Codex 输入：

> 请先读取本插件的 maya-modeling Skill，调用 maya_status 和 maya_get_selection，报告所选对象的完整路径、几何和蒙皮状态。这次只检查，不修改场景。

建模时先明确对象、单位、方向、预算和本轮范围，再分阶段执行。材质、蒙皮、修复和导出分别确认。Bind-pose 修复必须先取得最新诊断，并由用户确认当前是预期绑定姿势；矩阵、成员归属或场景状态冲突时应停止。

## 贴图目录与配置

```bash
python3 scripts/install_macos.py --maya-version 2027 --replace \
  --texture-root /absolute/path/to/project/sourceimages
```

将示例路径换成真实存在的目录。多个目录重复传入 `--texture-root`；更新时会替换整个列表，应一次提供所有需要的目录。符号链接解析后仍须位于白名单目录内。

已有配置会保留 `export_root`，重新传入 `--export-root` 不会替换它；如需调整，手动编辑本机配置中的该字段并重新加载 Maya 插件。不要将整个用户目录设为贴图根目录。

## 常见问题

| 问题 | 处理 |
| --- | --- |
| 没有 Maya 工具 | 检查 MCP 注册、源码绝对路径、Python 路径并重启客户端；插件安装方式还需核对缓存版本 |
| 工具存在但无法连接 | 确认 Maya 已启动且插件 Loaded；检查其他 Maya 进程是否占用默认端口 17321 |
| 令牌或配置错误 | 确认两端读取同一份本机配置，不要把令牌贴进公开 Issue |
| 贴图路径被拒绝 | 检查目录白名单与符号链接实际路径 |
| 导出文件已存在 | 换一个文件名，不覆盖旧文件 |
| 修复被拒绝 | 根据新诊断排查冲突，不通过放宽矩阵检查强行修复 |

默认配置连接一个运行中的桥接实例。`127.0.0.1:17321` 是内部 TCP 桥接端口，不能当成 HTTP MCP 地址填写。

桥接通信仅在本机进行，但 AI 客户端可能把工具返回的场景名、路径和几何发送给模型服务。因此“本机桥接”不等于“AI 完全离线”。令牌由安装器生成，不是 OpenAI API Key。

停用时，在 Maya 关闭 Auto load 并卸载插件，再移除 MCP 注册或卸载 Codex 插件。导出的资产不属于安装文件。

## 测试与限制

在插件目录执行：

```bash
python3 scripts/test_mcp_stdio.py
python3 scripts/test_maya_bind_pose_tools.py
python3 scripts/test_maya_uv_texture_tools.py
```

2026-09-05 文档核对时，三项测试通过，工具发现返回 21 个工具。它们验证 MCP 握手/工具发现，以及使用模拟 Maya API 的 bind-pose、UV 和贴图逻辑；不连接真实 Maya，不能替代真实场景、变形姿势、视口和引擎导入验证。

`scripts/create_tuxedo_asset.py` 和两个燕尾服工具包含特定资产假设，不适合作为通用入门示例。

## 类似项目

截至 2026-09-05，已存在多种 Maya MCP 实现：

- [GG_MayaMCP](https://github.com/GimbalGoats/GG_MayaMCP)：经 commandPort 提供类型化工具，支持 Codex 配置，默认关闭任意代码执行。
- [chadrik/maya-mcp-server](https://github.com/chadrik/maya-mcp-server)：侧重多会话发现与任意 Python 执行。
- [dcc-mcp-maya](https://github.com/dcc-mcp/dcc-mcp-maya)：更广泛的 DCC 集成，提供网关和多实例能力。

以上来自各项目 README，不是实测排名或安全审计。本项目可强调小范围工具接口、本机认证、路径约束和绑定姿势修复流程，不应宣称“首个”“唯一”或“绝对安全”。

本项目独立于 Autodesk 和 OpenAI。这是为个人学习和建模需求开发的工具，分享给有类似需要的人参考和使用；按作者自身需求更新，欢迎反馈可复现的问题，不承诺固定维护周期。

## 许可证

Copyright (c) 2026 tanend。本项目采用 [MIT 许可证](LICENSE)。

**允许商业使用、修改和再分发，但须在软件副本或实质性部分中保留版权声明及许可声明。** 软件按原样提供，不附带担保，完整条款见 LICENSE。

此许可证适用于本仓库的代码和文档，不包含 Autodesk Maya 的使用授权。使用者需自行取得符合其用途的 Maya 授权；本项目不会赋予 Maya 教育授权或教育授权下制作的资产商业使用权。

---
name: maya-modeling
description: Use and troubleshoot Maya Codex Bridge for safe, general Maya 2027 scene inspection, polygon modeling, UVs, file textures, materials, rig-aware assets, skinning, bind-pose diagnosis and guarded repair, and constrained FBX export. Trigger for Maya connection failures or missing maya tools, and for character accessories, garments, props, hard-surface objects, static environment assets, mesh QA, topology, UV layout or snapshots, texture assignment, skin weights, FBX bind-pose warnings, or Maya-to-engine delivery.
---

# Maya Modeling Through the Bridge

Use only the bridge's currently exposed `maya_*` MCP tools for Maya reads and changes. Never use desktop automation, mouse/keyboard injection, arbitrary Maya Python, MEL, or unbounded filesystem access. If a needed operation is not exposed, report the capability gap instead of inventing a tool or bypass.

## Establish a read-only baseline

1. Explicitly look up and call `maya_status`; deferred tools may be absent from the initial catalog. If the entry is truly unavailable, inspect `codex mcp get maya-codex-bridge`. Reinstall only when missing or disabled.
2. Call `maya_get_selection`; use `maya_scene_summary` when the target is unclear. Use returned DAG paths rather than hardcoded names.
3. Call `maya_get_mesh_geometry` for work derived from an existing mesh. Call `maya_get_skin_joints` only when rigging or deformation matters.
4. For FBX bind-pose warnings, call `maya_get_bind_pose_diagnostics` on the explicit skinned mesh. Treat bindPreMatrix mismatches, multiple pose connections, ambiguous candidates, and members owned by another bindPose as blockers.
5. For UV or texture work, call `maya_get_uvs` on the confirmed mesh before changing UVs or assigning a texture. Record the UV Set, face count, existing assignments, material target, color-space intent, and permitted texture root.
6. Confirm target objects, units, forward/up axes, symmetry, topology, skin state, references, budget, and the authorized stage before changing the scene.

Connection and inspection requests are read-only. They do not authorize creation, binding, saving, deletion, or export.

## Choose the strategy by asset type

- For fitted or deforming assets, derive proportions from actual source vertices and relevant joints. Preserve useful deformation regions and never alter the source character without explicit approval.
- For props, mechanical forms, and blockouts, primitives or explicit parametric meshes may be appropriate. Preserve scale, axes, assembly logic, pivots, silhouette, and shading continuity.
- For static environment assets, confirm module size, seams, pivots, reuse, collision/LOD expectations, visible surfaces, and engine coordinates.
- Treat task-specific constraints as local data. Do not generalize a character name, facing axis, garment construction, or polygon budget to other work.

## Work in gated stages

1. Confirm scope and deliverables.
2. Build a blockout for proportion and main structure only.
3. Resolve silhouette and volume from front, side, back, and three-quarter views.
4. Stabilize construction and topology, including deformation or shading needs.
5. Add only details that affect silhouette, structure, or the intended viewing distance.
6. Run QA: names, hierarchy, transforms, boundaries, normals, manifold state, overlaps, face types, density, counts, UV status, and task-specific criteria.
7. Perform materials, skinning, animation checks, saving, or FBX export only when explicitly requested.

## UV and file-texture rules

- Treat `maya_get_uvs` output as indexed data: `uvs` stores unique coordinates and `faceUvs` stores per-polygon UV IDs in face-vertex order.
- Before `maya_set_uvs`, confirm the explicit mesh, UV Set, unchanged polygon topology, intended seams, tile range, texel-density target, and whether creating a missing UV Set is authorized. Read the result back afterward.
- `maya_export_uv_snapshot` writes only a new PNG beneath the configured export folder. List the intended filename first; do not request overwrite.
- Before `maya_assign_file_texture`, confirm explicit target meshes, Lambert material/file-node names, texture color space, and that the resolved image is beneath a configured `texture_roots` folder. The tool refuses to replace a different existing file-node path or material input connection.
- UV edits and texture assignment do not save the scene. A UV snapshot verifies wire layout only; it does not verify texture appearance, shading, UDIM loading, or engine color management.

Prioritize form before edge flow and detail. Address at most three major issues per iteration. If six iterations do not improve a stage, stop random edits and explain whether the region should be rebuilt or the plan revised.

## Visual verification

Use reproducible front, side, back, and three-quarter views when screenshot/camera tools are available. When they are not exposed, do not use desktop automation or claim visual verification; ask the user for viewport captures or a stage approval. Never call a blockout production-ready.

## Safety and delivery

- Change only confirmed objects. Do not rename or replace source characters, skeletons, blend shapes, scenes, or existing exports.
- Do not save, overwrite, or delete user assets automatically. Any delete tool remains limited to its documented fingerprint and requires the user's request.
- Before skin binding, confirm targets have no skin cluster and use only approved existing joints. Treat copied weights as an initial result requiring pose validation.
- Before `maya_repair_bind_pose`, require a fresh diagnostic, the exact pose and missing-member list it returned, and explicit confirmation that the rig is at its intended bind pose. The repair must refuse matrix mismatches, competing pose membership, ambiguous candidates, or changed scene state; it must not delete poses, move joints, or change weights.
- Before FBX export, list the exact transforms and filename. Export only confirmed objects to the bridge's confined export directory.
- Report created/changed meshes, materials, skin clusters, vertices/edges/faces/triangles, intentional non-quads, verified checks, unverified checks, and the absolute export path when applicable.

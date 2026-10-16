# Architecture

This page explains how `WindowFrame_CC.py` is built, so that someone else can take it over and improve it. Function names are given instead of line numbers, because line numbers change with every edit.

## Design decisions

- **One single file.** Everything (logging, theme, geometry, window) lives in `WindowFrame_CC.py`. There are no local imports and no external packages. This makes the plugin trivial to install (copy one file) and to share.
- **`#! python 3` on line 1.** Without it Rhino's `_RunPythonScript` starts IronPython 2.7 instead of the Python 3 of Rhino 8.
- **Python 3.9 syntax only** (no `match`, no `X | Y` type hints), the safe level for Rhino 8.
- **RhinoCommon, not `rhinoscriptsyntax`.** Geometry is built with `Rhino.Geometry` objects directly.
- **Geometry and window are kept apart.** The `CORE` code never touches the window, so the geometry logic can be read, changed and logged on its own.
- **Refuse rather than succeed in the wrong place.** If a frame cannot be built reliably, that outline is skipped and the reason is reported, instead of producing wrong geometry.

## File layout

The file is divided into banner-commented sections, in this order:

| Section | What it holds |
|---|---|
| `HEADER / IMPORTS` | Imports (`Rhino`, `Rhino.Geometry as rg`, `scriptcontext`, `Eto` for the window). |
| `CONFIG` | Constants: plugin name, default widths and depth, option lists (`WIDTH_MODES`, `DEPTH_MODES`, `OUTPUT_MODES`, `GROUP_MODES`), thresholds such as `SIDE_ANGLE_DEG`. Resolves the plugin folder and the `logs` folder. |
| `LOGGING` | One log file per window session in `logs/`, capped at 500 MB in total (oldest files removed first). `log(tag, msg)` and `log_exc(where)` never raise, so logging cannot break a run. |
| `THEME` | Colour and font tokens (`TOKENS`, `color()`, `font()`) used by the window. |
| `CORE` | All the geometry logic (below). Starts with `Settings` and `RunContext`, small vector helpers, then the pipeline functions. |
| `UI` | The Eto window: small widget classes (`FlatButton`, `OptionGroup`, `Toggle`, ...) and `WindowFrameUI`, which owns the window, reads the settings, and runs the pipeline on Run. |
| `ENTRY POINT` | `main()`: checks Rhino 8+ / Python 3, keeps a single window instance (stored in `scriptcontext.sticky`), and shows it. |

## Data objects

- `Settings` (dataclass): everything the user sets in the window (widths, offset mode, depth and direction, output mode, grouping, close-open option). `widths()` returns the width per member kind.
- `RunContext` (dataclass): per-run values: model tolerance, angle tolerance, the mm-to-document-units scale, and the active camera.
- `FrameError` / `SkipError`: raised with a user-facing message when a contour or an input object cannot be used. They are caught per item, so one failure never stops the whole run.

## The pipeline (what happens on Run)

```
selected objects
   |
   v
collect_contours()        read outlines, join touching curves, close open ones, clean
   |   extract_contours()   one branch per geometry type (curve, extrusion, brep, surface, mesh, SubD, hatch)
   |   close_outline()      snap a tiny gap, or add a straight segment
   |   clean_contour()      merge collinear / co-circular pieces, rebuild runs between real corners
   v
frame_for_contour()       full pipeline for ONE outline
   |   frame_axes()         plane normal, "up" direction, outline centre, planar or not
   |   split_segments()     cut the outline into smooth segments
   |   inward_sign()        which side of the outline is "inside"
   |   classify()           each segment becomes side / top / bottom
   |   -> distances         per segment, outer and inner offset distances from width + offset mode
   |
   +-- Solid, planar outline ----> build_boundary() x2 (outer and inner curve)
   |                               build_frame()  planar face, then CreateExtrusion
   |
   +-- Solid, non-planar --------> solid_skin()  one continuous skin (front/back/inner/outer faces)
   |                               fallback: build_members() + union_members()
   |
   +-- Mitered 45 / 90 ----------> build_members()  one brep per side
   v
breps  ->  UI: add to document, layer, grouping, single undo record, report
```

Key ideas inside the steps:

- **Side / top / bottom** (`classify`): "up" is world Z projected into the outline plane. A segment within 45 degrees of up is a side; otherwise top if above the outline centre, bottom if below. Each class gets its own width.
- **Offsetting** (`offset_per_segment`, `_offset_single`, `_joint`): each segment is offset by its own distance, then neighbours are joined at their corner. `build_boundary` assembles the offset curves into one boundary and records which method succeeded.
- **Members** (`member_groups`, `split_joints`, `build_members`): for the mitered outputs the outline is split into members (a new member starts at a corner of 15 degrees or more, or where the class changes). `miter_plane`, `butt_planes` and `joint_corners` compute the cutting planes of the joints; at a butt joint the longer member runs through. Curved members are built from 4 edge curves through 48+ sections with ruled side faces (`_edge_curves`, `_ruled_sides`, `_member_rail`). A closed outline with no corner goes through `_member_ring` (two halves).
- **Depth** (`depth_range`): Front / Back / Center decide where the extrusion starts relative to the outline plane. "Front" follows the active camera, falling back to a world rule when the camera is edge-on (`RunContext.cam_dir`, `CAMERA_DOT_MIN`).
- **Closing a solid** (`_close_solid`, `_log_naked`): joins faces into a closed brep and logs naked edges when it cannot.

## Logs

Every window session writes `logs/WindowFrame_CC_<date>_<time>.log`. Lines look like `TAG | message` with tags such as `ITEM` (what is happening to an outline), `API` (result of a RhinoCommon call), `SKIP`, `ERROR`. When reporting a bug, attach the matching log.

## Where to start if you want to...

| Goal | Look at |
|---|---|
| Add a new input geometry type | `extract_contours` |
| Add a custom moulding profile (non-rectangular section) | `build_frame`, `build_members`, `solid_skin` |
| Change how top / side / bottom are recognized | `classify` |
| Add a new output mode | `OUTPUT_MODES`, `frame_for_contour`, the `Settings` dataclass, the output option group in `WindowFrameUI` |
| Improve a joint | `miter_plane`, `butt_planes`, `joint_corners` |
| Change the look of the window | `TOKENS` in the THEME section, then the widget classes in UI |
